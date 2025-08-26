import time
import re
from typing import Dict, List
from app.ports.notification_repository import NotificationRepository
from app.ports.whatsapp_sender import WhatsAppSender

E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")

class SendPendingNotificationsJob:
    def __init__(self, repo: NotificationRepository, sender: WhatsAppSender,
                 batch_size: int = 50, pause_sec: float = 0.25):
        self.repo = repo
        self.sender = sender
        self.batch_size = batch_size
        self.pause_sec = pause_sec

    @staticmethod
    def _is_e164(num: str) -> bool:
        return bool(E164_REGEX.fullmatch((num or "").strip()))

    def run_once_due_only(self) -> int:
        processed = 0
        try:
            rows: List[Dict] = self.repo.fetch_due_for_update(self.batch_size)
            if not rows:
                self.repo.rollback()
                print("[JOB] No hay PENDIENTE vencidas por ahora.")
                return 0

            for r in rows:
                processed += 1
                print(f"[JOB] id={r['id']} → {r['phone_number']}")
                try:
                    if not self._is_e164(r["phone_number"]):
                        raise ValueError(f"Número inválido (E.164): {r['phone_number']}")
                    msg_id = self.sender.send_text(r["phone_number"], r["message"])
                    self.repo.mark_sent(r["id"])
                    print(f"[JOB] OK id={r['id']} msg_id={msg_id}")
                except Exception as e:
                    self.repo.mark_error(r["id"], str(e))
                    print(f"[JOB] ERROR id={r['id']}: {e}")
                time.sleep(self.pause_sec)

            self.repo.commit()
        except Exception as e:
            print(f"[JOB] FALLÓ LOTE: {e}")
            self.repo.rollback()
        return processed

    def run_until_empty_due(self) -> int:
        total = 0
        while True:
            n = self.run_once_due_only()
            if n <= 0:
                break
            total += n
        print(f"[JOB] Corrida diaria terminada. Total procesado: {total}")
        return total
