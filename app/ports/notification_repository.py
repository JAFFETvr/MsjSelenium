from typing import List
from app.domain.notification import Notification

class NotificationRepository:
    def fetch_pending_for_update(self, limit: int) -> List[Notification]:
        raise NotImplementedError
    def mark_sent(self, notif_id: int) -> None:
        raise NotImplementedError
    def mark_error(self, notif_id: int, err_text: str | None = None) -> None:
        raise NotImplementedError
    def commit(self) -> None:
        raise NotImplementedError
    def rollback(self) -> None:
        raise NotImplementedError
    def close(self) -> None:
        raise NotImplementedError
