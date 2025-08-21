from fastapi import APIRouter, HTTPException, Request, Query
from typing import List
from datetime import datetime, timedelta
import threading

from api.schemas import NotificationOut, ScheduleIn, ActionResult

router = APIRouter()

def _notif_to_out(r) -> NotificationOut:
    return NotificationOut(
        id=r.id, phone_number=r.phone_number, message=r.message,
        scheduled_at=r.scheduled_at, status=r.status, sent_at=r.sent_at
    )

# ---------- helpers (usan app.state.*) ----------

def _cancel_timer(req: Request, notif_id: int, silent: bool = False) -> bool:
    with req.app.state.timers_lock:
        t = req.app.state.timers.pop(notif_id, None)
    if t:
        try: t.cancel()
        except: pass
        return True
    return False

def _send_single(req: Request, notif_id: int):
    repo = req.app.state.repo
    sender = req.app.state.sender
    try:
        n = repo.fetch_one_for_update_any(notif_id)  # ignora scheduled_at
        if not n:
            repo.rollback()
            raise HTTPException(status_code=404, detail="No está en 'Pendiente' o no existe.")
        phone = (n.phone_number or "").strip()
        if not (phone.startswith("+") and phone[1:].isdigit() and 8 <= len(phone) <= 15):
            raise HTTPException(status_code=400, detail=f"Número inválido (E.164): {n.phone_number}")
        msg_id = sender.send_text(n.phone_number, n.message)
        repo.mark_sent(n.id)
        repo.commit()
        print(f"[API] OK id={n.id} msg_id={msg_id}")
    except HTTPException:
        raise
    except Exception as e:
        try:
            repo.mark_error(notif_id, str(e))
            repo.commit()
        except Exception:
            repo.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def _timer_cb(req: Request, notif_id: int):
    try:
        _send_single(req, notif_id)
    finally:
        with req.app.state.timers_lock:
            req.app.state.timers.pop(notif_id, None)

def _schedule(req: Request, notif_id: int, when_local: datetime) -> str:
    repo = req.app.state.repo
    # persistimos horario (como tu programa normal)
    try:
        repo.reschedule(notif_id, when_local)
        repo.commit()
    except Exception as e:
        repo.rollback()
        raise HTTPException(status_code=500, detail=f"Error guardando horario: {e}")

    # cancelar timer previo si existía
    _cancel_timer(req, notif_id, silent=True)

    delay = (when_local - datetime.now()).total_seconds()
    if delay <= 0:
        _send_single(req, notif_id)
        return "Hora alcanzada: enviado ahora."

    t = threading.Timer(delay, _timer_cb, args=(req, notif_id))
    t.daemon = True
    with req.app.state.timers_lock:
        req.app.state.timers[notif_id] = t
    t.start()
    return f"Programado en {int(delay)}s."

# ---------------- Endpoints ----------------

@router.get("/pending", response_model=List[NotificationOut])
def list_pending(req: Request, limit: int = Query(50, ge=1, le=500)):
    rows = req.app.state.repo.list_pending(limit)
    return [_notif_to_out(r) for r in rows]

@router.post("/{notif_id}/send-now", response_model=ActionResult)
def send_now(req: Request, notif_id: int):
    _cancel_timer(req, notif_id, silent=True)
    _send_single(req, notif_id)
    return ActionResult(ok=True, detail="Envío solicitado (solo esa notificación).")

@router.post("/{notif_id}/schedule", response_model=ActionResult)
def schedule(req: Request, notif_id: int, body: ScheduleIn):
    if not body.when and body.offset_minutes is None:
        raise HTTPException(status_code=400, detail="Envía 'when' o 'offset_minutes'.")
    when_local = body.when or (datetime.now() + timedelta(minutes=body.offset_minutes or 0))
    msg = _schedule(req, notif_id, when_local)
    return ActionResult(ok=True, detail=msg)
