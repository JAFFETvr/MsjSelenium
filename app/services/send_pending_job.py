import time, re, logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from app.ports.notification_repository import NotificationRepository
from app.ports.whatsapp_sender import WhatsAppSender
from selenium.common.exceptions import TimeoutException, WebDriverException

E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
log = logging.getLogger(__name__)

def _human_error_message(e: Exception) -> str:
    msg = (str(e) or "").strip().lower()
    if isinstance(e, TimeoutException) or "timeout" in msg:
        return "No se pudo enviar por conexión lenta o WhatsApp no respondió a tiempo."
    if isinstance(e, WebDriverException) and "user data directory is already in use" in msg:
        return "El perfil de Chrome ya estaba abierto. Cierra ventanas previas e inténtalo de nuevo."
    if "whatsapp reporta problema" in msg:
        return "WhatsApp reportó un problema con el número o el chat."
    if "network" in msg or "net::" in msg:
        return "Fallo de red durante el envío."
    if not msg:
        return "Fallo desconocido al enviar."
    return f"Error: {str(e)}"

class SendPendingNotificationsJob:
    def __init__(self, repo: NotificationRepository, sender: WhatsAppSender,
                 batch_size: int = 50, pause_sec: float = 0.25, jitter_sec: float = 0.0,
                 post_send_wait_sec: float = 1.5):
        self.repo = repo
        self.sender = sender
        self.batch_size = batch_size
        self.pause_sec = max(0.0, pause_sec)
        self.jitter_sec = max(0.0, jitter_sec)
        self.post_send_wait_sec = max(0.0, post_send_wait_sec)

    @staticmethod
    def _is_e164(num: str) -> bool:
        return bool(E164_REGEX.fullmatch((num or "").strip()))

    def _sleep_with_jitter(self):
        if self.pause_sec <= 0 and self.jitter_sec <= 0:
            return
        import random
        delta = random.uniform(-self.jitter_sec, self.jitter_sec)
        time.sleep(max(0.0, self.pause_sec + delta))

    def run_once_due_only(self, cutoff_at: datetime) -> Tuple[int, int, int, int]:
        """
        Procesa SOLO mensajes con scheduled_at <= cutoff_at y status 'pending'
        (el filtro está en SQL en el repositorio).
        Devuelve: (procesados, ok, err, skipped)
        """
        processed = ok = err = skipped = 0
        try:
            rows: List[Dict] = self.repo.fetch_due_for_update(self.batch_size, cutoff_at=cutoff_at)
            n = len(rows)
            if n == 0:
                self.repo.rollback()
                log.info("No hay mensajes pendientes por enviar en este momento.")
                return (0, 0, 0, 0)

            log.info(f"Se encontraron {n} mensajes vencidos en este lote (corte {cutoff_at}).")
            for r in rows:
                phone = r.get("phone_number", "")
                sched = r.get("scheduled_at")
                log.info(f"Enviando a {phone} (programado: {sched})")

                try:
                    if not self._is_e164(phone):
                        raise ValueError(f"Número inválido (E.164): {phone}")

                    # sender debe devolver (msg_id, status_hint) cuando es éxito
                    msg_id, status_hint = self.sender.send_text(phone, r.get("message", ""))

                    # Éxito si vemos al menos una palomita (enviado/entregado/leído)
                    if status_hint in ("enviado", "entregado", "leido"):
                        self.repo.mark_sent(r["id"])   # -> status = 'sent'
                        ok += 1
                        processed += 1
                        log.info(f"OK ({status_hint}) id={r['id']} msg_id={msg_id}")
                    else:
                        self.repo.mark_error(r["id"], "No se detectaron palomitas de envío/entrega en WhatsApp.")
                        err += 1
                        processed += 1
                        log.error(f"ERROR sin palomitas id={r['id']}")

                    if self.post_send_wait_sec > 0:
                        time.sleep(self.post_send_wait_sec)

                except Exception as e:
                    reason = _human_error_message(e)
                    self.repo.mark_error(r["id"], reason)   # -> status = 'error'
                    err += 1
                    processed += 1
                    log.error(f"No se pudo enviar (id={r['id']}): {reason}")

                self._sleep_with_jitter()

            self.repo.commit()
        except Exception as e:
            self.repo.rollback()
            log.error(f"No se pudo procesar el lote: {_human_error_message(e)}")
            return (0, 0, 0, 0)

        return (processed, ok, err, skipped)

    def run_until_empty_due(self, cutoff_at: Optional[datetime] = None) -> Tuple[int, int, int, int]:
        """
        Corre en bucle mientras haya 'pending', respetando SIEMPRE el mismo cutoff_at.
        """
        if cutoff_at is None:
            cutoff_at = self.repo.db_now()

        total = ok = err = skipped = 0
        while True:
            p, o, e, s = self.run_once_due_only(cutoff_at=cutoff_at)
            if p <= 0:
                break
            total   += p
            ok      += o
            err     += e
            skipped += s

        return (total, ok, err, skipped)
