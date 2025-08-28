from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class NotificationOut(BaseModel):
    id: int
    phone_number: str
    message: str
    scheduled_at: Optional[datetime] = None
    status: str
    sent_at: Optional[datetime] = None

class ScheduleIn(BaseModel):
    # Programa a una fecha/hora exacta O en offset de minutos
    when: Optional[datetime] = Field(None, example="2025-08-21T10:30:00")
    offset_minutes: Optional[int] = Field(None, ge=0, le=24*60, example=2)

class ActionResult(BaseModel):
    ok: bool
    detail: str
