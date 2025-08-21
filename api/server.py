from fastapi import FastAPI
from infra.config import env_str, env_int
from infra.db.postgres_repository import PostgresNotificationRepository
from infra.whatsapp.selenium_sender import SeleniumWhatsAppSender
from api.routers.notifications import router as notifications_router
import threading

def create_app() -> FastAPI:
    app = FastAPI(title="Mensajería WhatsApp API", version="1.0")

    @app.on_event("startup")
    def _startup():
        db_url  = env_str("DATABASE_URL")
        profile = env_str("CHROME_PROFILE_DIR", "C:\\Users\\Pan3s\\whatsapp-bot-profile")
        timeout = env_int("SELENIUM_TIMEOUT", 20)

        app.state.repo   = PostgresNotificationRepository(db_url)
        app.state.sender = SeleniumWhatsAppSender(profile, timeout)
        app.state.timers = {}                # timers por id
        app.state.timers_lock = threading.Lock()
        print("[API] Startup OK.")

    @app.on_event("shutdown")
    def _shutdown():
        with app.state.timers_lock:
            for _, t in list(app.state.timers.items()):
                try: t.cancel()
                except: pass
            app.state.timers.clear()
        try: app.state.sender.close()
        except: pass
        try: app.state.repo.close()
        except: pass
        print("[API] Shutdown OK.")

    app.include_router(notifications_router, prefix="/notifications", tags=["notifications"])

    @app.get("/health")
    def health():
        return {"ok": True}

    return app

app = create_app()
