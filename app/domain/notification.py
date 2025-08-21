from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Notification:
    id: int
    phone_number: str
    message: str
    scheduled_at: Optional[datetime] = None
    status: str = "Pendiente"
    sent_at: Optional[datetime] = None
