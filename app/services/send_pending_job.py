import time, re, threading
from datetime import datetime
from typing import Optional, Dict
from app.ports.notification_repository import NotificationRepository
from app.ports.whatsapp_sender import WhatsAppSender

# E.164: +<country><digits>, 8..15 dígitos totales
E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")

class SendPendingNotificationsJob:
    def __init__(self, repo: NotificationRepository, sender: WhatsAppSender,
                 batch_size: int = 50, pause_sec: float = 0.25):
        self.repo = repo
        self.sender = sender
        self.batch_size = batch_size
        self.pause_sec = pause_sec
        # Timers activos por notificación (solo los programados explícitamente por el usuario)
        self._timers: Dict[int, threading.Timer] = {}

    @staticmethod
    def _is_e164(num: str) -> bool:
        return bool(E164_REGEX.fullmatch((num or "").strip()))

    # ================= Menú (solo seleccionado) =================
    def run_interactive_menu(self):
        while True:
            rows = self.repo.list_pending(self.batch_size)

            if not rows:
                # En vez de salir, mantenemos el menú vivo
                print("\n[MENU] No hay notificaciones pendientes.")
                print("\nOpciones:")
                print("  r       Refrescar lista")
                print("  q       Salir")
                choice = input("> ").strip().lower()
                if choice == "q":
                    print("Saliendo.")
                    break
                elif choice == "r":
                    continue
                else:
                    print("Opción no válida.")
                    continue

            # Hay pendientes → mostramos menú completo
            print("\n[MENU] Notificaciones pendientes:")
            for i, n in enumerate(rows, start=1):
                prog = n.scheduled_at.strftime("%Y-%m-%d %H:%M:%S") if n.scheduled_at else "N/A"
                print(f"  {i}. id={n.id}  tel={n.phone_number}  programada={prog}  msg={n.message[:40]!r}")

            print("\nOpciones:")
            print("  [número] Operar esa notificación (enviar/programar)")
            print("  r       Refrescar lista")
            print("  q       Salir")
            choice = input("> ").strip().lower()

            if choice == "q":
                print("Saliendo.")
                break
            if choice == "r":
                continue
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(rows):
                    self._item_submenu(rows[idx-1].id)
                else:
                    print("Índice fuera de rango.")
                continue

            print("Opción no válida.")

    def _item_submenu(self, notif_id: int):
        while True:
            print("\nAcciones para la notificación seleccionada:")
            print("  1) Enviar AHORA (solo esta)")
            print("  2) Programar para más tarde (solo esta)")
            print("  c) Cancelar programación activa (si existe)")
            print("  b) Volver")
            sub = input("> ").strip().lower()
            if sub == "1":
                # Cancelar timer si existía, y enviar ya
                self._cancel_timer(notif_id)
                self._send_by_id_force(notif_id)
                # Mensaje útil para que el usuario refresque
                print("[INFO] Presiona 'r' para refrescar la lista.")
                break
            elif sub == "2":
                new_ts = input("Fecha y hora (YYYY-MM-DD HH:MM): ").strip()
                dt = self._parse_datetime_local(new_ts)
                if not dt:
                    print("Formato inválido. Ejemplo válido: 2025-08-21 10:00")
                    continue
                self._schedule_single(notif_id, dt)
                # Tras programar, dejamos que el usuario decida si refresca o no
                print("[INFO] Presiona 'r' para ver el nuevo estado en la lista.")
                break
            elif sub == "c":
                if self._cancel_timer(notif_id):
                    print(f"[MENU] Programación cancelada para id={notif_id}.")
                else:
                    print(f"[MENU] No había programación activa para id={notif_id}.")
            elif sub == "b":
                break
            else:
                print("Opción no válida.")

    @staticmethod
    def _parse_datetime_local(s: str) -> Optional[datetime]:
        try:
            # Interpreta como hora local del sistema
            return datetime.strptime(s, "%Y-%m-%d %H:%M")
        except Exception:
            return None

    # ================= Programación puntual (solo esa notificación) =================
    def _schedule_single(self, notif_id: int, when_local: datetime):
        """
        Programa SOLO esa notificación para ser enviada exactamente a 'when_local'.
        - Actualiza scheduled_at en BD (status sigue 'Pendiente').
        - Crea/renueva un timer que disparará SOLO ese id.
        """
        try:
            # Persistimos el horario
            self.repo.reschedule(notif_id, when_local)
            self.repo.commit()
            print(f"[MENU] Reprogramada id={notif_id} a {when_local.strftime('%Y-%m-%d %H:%M')}.")

            # Si ya había timer, lo cancelamos
            self._cancel_timer(notif_id)

            # Calculamos delay
            now = datetime.now()
            delay = (when_local - now).total_seconds()
            if delay <= 0:
                # Hora pasada o inmediata: enviamos ya
                print(f"[SCHED] Hora ya alcanzada para id={notif_id}, enviando ahora.")
                self._send_by_id_force(notif_id)
                return

            # Creamos timer solo para esa notificación
            t = threading.Timer(delay, self._send_scheduled, args=(notif_id,))
            t.daemon = True
            t.start()
            self._timers[notif_id] = t
            print(f"[SCHED] Programado id={notif_id} en {int(delay)}s (solo ese mensaje).")

        except Exception as e:
            self.repo.rollback()
            print(f"[MENU] Error programando id={notif_id}: {e}")

    def _cancel_timer(self, notif_id: int) -> bool:
        t = self._timers.pop(notif_id, None)
        if t:
            try:
                t.cancel()
            except Exception:
                pass
            return True
        return False

    def _send_scheduled(self, notif_id: int):
        """
        Callback del timer: envía SOLO esa notificación (ignora el resto).
        Tras enviar, elimina el timer del mapa e imprime un aviso claro
        para que el usuario refresque el menú.
        """
        try:
            self._send_by_id_force(notif_id)
        finally:
            # Limpia referencia del timer si aún existe
            self._timers.pop(notif_id, None)
            # Aviso visible junto al prompt que esté abierto
            print(f"\n[SCHED] Envío completado id={notif_id}. Presiona 'r' para refrescar la lista.")

    # ================= Envíos =================
    def _send_by_id_force(self, notif_id: int):
        """
        Envía SOLO esta notificación, aunque tenga scheduled_at en el futuro.
        (Se usa para 'Enviar AHORA' y para el disparo del timer).
        """
        try:
            n = self.repo.fetch_one_for_update_any(notif_id)  # <- sin checar fecha
            if not n:
                self.repo.rollback()
                print(f"[JOB] id={notif_id}: no está disponible o no está en 'Pendiente'.")
                return

            print(f"[JOB] id={n.id} → {n.phone_number}")
            try:
                if not self._is_e164(n.phone_number):
                    raise ValueError(f"Número inválido (E.164): {n.phone_number}")

                msg_id = self.sender.send_text(n.phone_number, n.message)
                self.repo.mark_sent(n.id)
                self.repo.commit()
                print(f"[JOB] OK id={n.id} msg_id={msg_id}")
            except Exception as e:
                self.repo.mark_error(n.id, str(e))
                self.repo.commit()
                print(f"[JOB] ERROR id={n.id}: {e}")
            time.sleep(self.pause_sec)
        except Exception as e:
            print(f"[JOB] FALLÓ ENVÍO id={notif_id}: {e}")
            self.repo.rollback()

    # (Helpers batch por si los necesitas manualmente; no se usan en este modo)
    def run_once(self) -> None:
        try:
            rows = self.repo.fetch_pending_for_update(self.batch_size)
            if not rows:
                self.repo.rollback()
                print("[JOB] No hay pendientes vencidas.")
                return
            self._process_rows(rows)
            self.repo.commit()
        except Exception as e:
            print(f"[JOB] FALLÓ LOTE: {e}")
            self.repo.rollback()

    def run_until_exhausted(self) -> None:
        while True:
            rows = self.repo.fetch_pending_for_update(self.batch_size)
            if not rows:
                self.repo.rollback()
                print("[JOB] No hay más pendientes vencidas. Fin.")
                break
            self._process_rows(rows)
            self.repo.commit()

    def _process_rows(self, rows):
        for n in rows:
            print(f"[JOB] id={n.id} → {n.phone_number}")
            try:
                if not self._is_e164(n.phone_number):
                    raise ValueError(f"Número inválido (E.164): {n.phone_number}")
                msg_id = self.sender.send_text(n.phone_number, n.message)
                self.repo.mark_sent(n.id)
                print(f"[JOB] OK id={n.id} msg_id={msg_id}")
            except Exception as e:
                self.repo.mark_error(n.id, str(e))
                print(f"[JOB] ERROR id={n.id}: {e}")
            time.sleep(self.pause_sec)
