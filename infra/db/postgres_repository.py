from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from app.domain.notification import Notification
from app.ports.notification_repository import NotificationRepository

_INVISIBLES = ["\u202A", "\u202C", "\u200E", "\u200F", "\u2066", "\u2067", "\u2069", "\uFEFF"]

def clean_phone(s: str) -> str:
    if not s:
        return s
    for ch in _INVISIBLES:
        s = s.replace(ch, "")
    return s.strip()

class PostgresNotificationRepository(NotificationRepository):
    def __init__(self, db_url: str):
        self.conn = psycopg2.connect(db_url)
        self.conn.autocommit = False

    # ====== listar sin lock (para menú) ======
    def list_pending(self, limit: int) -> List[Notification]:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone_number, message, scheduled_at, status, sent_at
                  FROM notifications
                 WHERE status = 'Pendiente'
                 ORDER BY scheduled_at NULLS FIRST, id ASC
                 LIMIT %s;
            """, (limit,))
            rows = cur.fetchall() or []
        for r in rows:
            r["phone_number"] = clean_phone(r.get("phone_number", ""))
        return [
            Notification(
                id=r["id"], phone_number=r["phone_number"], message=r["message"],
                scheduled_at=r["scheduled_at"], status=r["status"], sent_at=r["sent_at"]
            ) for r in rows
        ]

    # ====== fetch 1 con lock (solo si vencida o sin fecha) ======
    def fetch_pending_for_update(self, limit: int) -> List[Notification]:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone_number, message, scheduled_at, status, sent_at
                  FROM notifications
                 WHERE status = 'Pendiente'
                   AND (scheduled_at IS NULL OR scheduled_at <= NOW())
                 ORDER BY scheduled_at NULLS FIRST, id ASC
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s;
            """, (limit,))
            rows = cur.fetchall() or []
        for r in rows:
            r["phone_number"] = clean_phone(r.get("phone_number", ""))
        return [
            Notification(
                id=r["id"], phone_number=r["phone_number"], message=r["message"],
                scheduled_at=r["scheduled_at"], status=r["status"], sent_at=r["sent_at"]
            ) for r in rows
        ]

    # ====== fetch 1 con lock (IGNORA horario) — para Enviar AHORA/TIMER ======
    def fetch_one_for_update_any(self, notif_id: int) -> Optional[Notification]:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone_number, message, scheduled_at, status, sent_at
                  FROM notifications
                 WHERE id = %s
                   AND status = 'Pendiente'
                 FOR UPDATE SKIP LOCKED;
            """, (notif_id,))
            r = cur.fetchone()
        if not r:
            return None
        r["phone_number"] = clean_phone(r.get("phone_number", ""))
        return Notification(
            id=r["id"], phone_number=r["phone_number"], message=r["message"],
            scheduled_at=r["scheduled_at"], status=r["status"], sent_at=r["sent_at"]
        )

    # ====== reprogramar SOLO scheduled_at ======
    def reschedule(self, notif_id: int, when_local) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE notifications
                   SET scheduled_at = %s
                 WHERE id = %s
                   AND status = 'Pendiente';
            """, (when_local, notif_id))

    def mark_sent(self, notif_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE notifications
                   SET status='Enviado', sent_at=NOW()
                 WHERE id=%s;
            """, (notif_id,))

    def mark_error(self, notif_id: int, err_text: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE notifications
                   SET status='Error'
                 WHERE id=%s;
            """, (notif_id,))

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
