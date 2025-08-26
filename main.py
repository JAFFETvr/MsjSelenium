import logging, os, sys, time, datetime as dt
from infra.config import env_str, env_int, env_float
from infra.db.postgres_repository import PostgresNotificationRepository
from infra.whatsapp.selenium_sender import SeleniumWhatsAppSender
from app.services.send_pending_job import SendPendingNotificationsJob

# Solo nuestros logs en INFO; terceros en WARNING
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s"
)
for noisy in ("selenium", "urllib3", "webdriver_manager", "WDM", "tensorflow", "absl", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

def seconds_until(hour: int, minute: int) -> float:
    now = dt.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + dt.timedelta(days=1)
    return (target - now).total_seconds()

def main():
    db_url   = env_str("DATABASE_URL")
    profile  = env_str("CHROME_PROFILE_DIR", "C:\\Users\\Pan3s\\whatsapp-bot-profile")
    timeout  = env_int("SELENIUM_TIMEOUT", 20)
    batch    = env_int("BATCH_SIZE", 50)
    pause    = env_float("PAUSE_SEC", 0.5)

    # ⇩⇩⇩ AQUÍ DEFINES LA HORA DIARIA DE ENVÍO ⇩⇩⇩
    send_hour   = env_int("DISPATCH_HOUR", 10)     # 0-23
    send_minute = env_int("DISPATCH_MINUTE", 20)   # 0-59
    # ⇧⇧⇧ CAMBIA ESTOS DOS VALORES EN EL .env SI QUIERES OTRA HORA ⇧⇧⇧

    repo   = PostgresNotificationRepository(db_url)
    sender = SeleniumWhatsAppSender(profile, timeout)
    job    = SendPendingNotificationsJob(repo=repo, sender=sender,
                                         batch_size=batch, pause_sec=pause)

    print(f"Servicio en modo EJECUCIÓN DIARIA a las {send_hour:02d}:{send_minute:02d} (hora local).")

    try:
        while True:
            wait = seconds_until(send_hour, send_minute)
            logging.info(f"Dormir {int(wait)}s hasta la siguiente corrida…")
            time.sleep(wait)
            total = job.run_until_empty_due()
            logging.info(f"Corrida completada. Total procesado: {total}")
    finally:
        try: sender.close()
        except: pass
        try: repo.close()
        except: pass

if __name__ == "__main__":
    sys.path.append(os.path.dirname(__file__))
    main()
