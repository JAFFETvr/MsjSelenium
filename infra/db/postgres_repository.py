from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
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
    """
    Repositorio Postgres alineado a estados en INGLÉS:
      - pending  -> por enviar
      - sent     -> enviado OK
      - error    -> fallo de envío
    Cualquier otro estado (p.ej. 'suspended') queda fuera de los selects.
    """
    def __init__(self, db_url: str):
        self.conn = psycopg2.connect(db_url)
        self.conn.autocommit = False

    def db_now(self) -> datetime:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT NOW() AS now;")
            r = cur.fetchone()
            return r["now"]

    def fetch_due_for_update(self, limit: int, cutoff_at: datetime) -> List[dict]:
        """
        Devuelve filas 'pending' con scheduled_at <= cutoff_at, bloqueándolas para este proceso.
        Ignora cualquier otro estado (ej. 'suspended').
        """
        cutoff_naive = cutoff_at.replace(tzinfo=None) if cutoff_at.tzinfo is not None else cutoff_at
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone_number, message, scheduled_at, status, sent_at
                  FROM notifications
                 WHERE status = 'pending'
                   AND scheduled_at <= %s::timestamp
                 ORDER BY scheduled_at ASC, id ASC
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s;
            """, (cutoff_naive, limit))
            rows = cur.fetchall() or []
        for r in rows:
            r["phone_number"] = clean_phone(r.get("phone_number", ""))
        return rows

    def list_pending(self, limit: int) -> List[Notification]:
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone_number, message, scheduled_at, status, sent_at
                  FROM notifications
                 WHERE status = 'pending'
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

    def fetch_one_for_update_any(self, notif_id: int) -> Optional[Notification]:
        """
        Intenta bloquear una fila específica solo si sigue 'pending'.
        """
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, phone_number, message, scheduled_at, status, sent_at
                  FROM notifications
                 WHERE id = %s
                   AND status = 'pending'
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

    def reschedule(self, notif_id: int, when_local) -> None:
        """
        Solo reprograma si la notificación sigue 'pending'.
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE notifications
                   SET scheduled_at = %s
                 WHERE id = %s
                   AND status = 'pending';
            """, (when_local, notif_id))

    def mark_sent(self, notif_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE notifications
                   SET status='sent', sent_at=NOW(), error_reason = NULL
                 WHERE id=%s;
            """, (notif_id,))

    def mark_error(self, notif_id: int, err_text: str | None = None) -> None:
        reason = (err_text or "").strip() or "Unknown failure while sending."
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE notifications
                   SET status='error', error_reason = %s
                 WHERE id=%s;
            """, (reason, notif_id))

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
