import logging, os, sys
from infra.config import env_str, env_int, env_float
from infra.db.postgres_repository import PostgresNotificationRepository
from infra.whatsapp.selenium_sender import SeleniumWhatsAppSender
from app.services.send_pending_job import SendPendingNotificationsJob

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s"
)

def main():
    db_url   = env_str("DATABASE_URL")
    profile  = env_str("CHROME_PROFILE_DIR", "C:\\Users\\Pan3s\\whatsapp-bot-profile")
    timeout  = env_int("SELENIUM_TIMEOUT", 20)
    batch    = env_int("BATCH_SIZE", 50)
    pause    = env_float("PAUSE_SEC", 0.25)
    poll     = env_float("POLL_SEC", 10.0)  # cada cuántos segundos revisar la BD

    repo   = PostgresNotificationRepository(db_url)
    sender = SeleniumWhatsAppSender(profile, timeout)
    job    = SendPendingNotificationsJob(repo=repo, sender=sender, batch_size=batch, pause_sec=pause)

    print("Servicio iniciado: enviará SOLO 'Pendiente' con scheduled_at <= NOW().")
    try:
        job.run_forever(poll)
    finally:
        try: sender.close()
        except: pass
        try: repo.close()
        except: pass

if __name__ == "__main__":
    sys.path.append(os.path.dirname(__file__))
    main()
