import os
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("WDM_LOG_LEVEL", "0")           
os.environ.setdefault("CHROME_LOG_FILE", os.devnull)  # logs de Chrome -> /dev/null

import atexit
import logging
import signal
import sys
import time
import datetime as dt
import warnings

warnings.filterwarnings("ignore")

from infra.config import env_str, env_int, env_float
from infra.db.postgres_repository import PostgresNotificationRepository
from infra.whatsapp.selenium_sender import SeleniumWhatsAppSender
from app.services.send_pending_job import SendPendingNotificationsJob

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)
for noisy in ("selenium", "urllib3", "webdriver_manager", "WDM", "tensorflow", "absl", "PIL"):
    logging.getLogger(noisy).setLevel(logging.CRITICAL)
log = logging.getLogger(__name__)

def fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def next_fire_dt(hour: int, minute: int) -> dt.datetime:
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target

def build_job(repo, delivery_wait, stabilize_ui, batch, pause, jitter, post_send_wait):
    profile  = env_str("CHROME_PROFILE_DIR")
    timeout  = env_int("SELENIUM_TIMEOUT", 20)
    sender = SeleniumWhatsAppSender(
        profile_dir=profile,
        timeout=timeout,
        delivery_wait_sec=delivery_wait,
        stabilize_after_send_sec=stabilize_ui,
    )
    job = SendPendingNotificationsJob(
        repo=repo,
        sender=sender,
        batch_size=batch,
        pause_sec=pause,
        jitter_sec=jitter,
        post_send_wait_sec=post_send_wait,
    )
    return sender, job

def main():
    db_url   = env_str("DATABASE_URL")

    batch    = env_int("BATCH_SIZE", 50)
    pause    = env_float("PAUSE_SEC", 0.5)
    jitter   = env_float("JITTER_SEC", 0.0)

    post_send_wait = env_float("POST_SEND_WAIT_SEC", 1.5)

    delivery_wait = env_float("DELIVERY_WAIT_SEC", 10.0)         
    stabilize_ui  = env_float("STABILIZE_AFTER_SEND_SEC", 1.0)   

    send_hour   = env_int("DISPATCH_HOUR", 10)
    send_minute = env_int("DISPATCH_MINUTE", 20)

    repo = PostgresNotificationRepository(db_url)

    def _shutdown(*_):
        try:
            repo.close()
        except Exception:
            pass

    atexit.register(_shutdown)
    signal.signal(signal.SIGINT,  lambda *_: (_shutdown(), sys.exit(0))) 
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_shutdown(), sys.exit(0)))
    except Exception:
        pass

    log.info(f"Envío diario programado a las {send_hour:02d}:{send_minute:02d} (hora local).")

    while True:
        fire_at = next_fire_dt(send_hour, send_minute)
        eta = (fire_at - dt.datetime.now()).total_seconds()
        log.info(f"Próxima ejecución: {fire_at:%Y-%m-%d %H:%M:%S} (en {fmt_eta(eta)})")

        remaining = max(1, int(eta))
        while remaining > 0:
            time.sleep(min(1, remaining))
            remaining -= 1

        sender = None
        job    = None
        try:
            sender, job = build_job(repo, delivery_wait, stabilize_ui, batch, pause, jitter, post_send_wait)

            cutoff_at = repo.db_now()

            total, ok, err, skipped = job.run_until_empty_due(cutoff_at=cutoff_at)
            log.info(f"Envío terminado. Enviados: {ok} • Omitidos: {skipped} • Errores: {err} • Total: {total}")
        finally:
            try:
                if sender:
                    sender.close()
            except Exception:
                pass

if __name__ == "__main__":
    sys.path.append(os.path.dirname(__file__))
    main()
