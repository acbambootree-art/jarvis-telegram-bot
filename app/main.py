from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db.database import close_db, init_db
from app.scheduler.jobs import start_scheduler, stop_scheduler
from app.services.telegram import telegram_service
from app.utils.helpers import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    setup_logging(settings.log_level)

    # Initialize database tables
    await init_db()

    # Start background scheduler
    start_scheduler()

    # Register Telegram webhook (if deployed with a public URL)
    if settings.app_base_url.startswith("https://"):
        await telegram_service.set_webhook(settings.app_base_url)

    yield

    # Cleanup
    stop_scheduler()
    await close_db()


app = FastAPI(title="Jarvis AI Assistant", version="1.0.0", lifespan=lifespan)


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "service": "jarvis-ai-assistant"}


# Register routers
from app.api.auth import router as auth_router
from app.api.webhook import router as webhook_router

app.include_router(webhook_router)
app.include_router(auth_router)
