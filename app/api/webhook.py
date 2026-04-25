import asyncio
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.config import settings
from app.core.router import process_message
from app.db.database import async_session
from app.models.models import Reminder
from app.scheduler.jobs import _reminder_tick_count, _run_daily_briefing, scheduler
from app.services.reminders import check_and_send_reminders
from app.services.telegram import telegram_service

logger = structlog.get_logger()
router = APIRouter()


def _check_admin(request: Request):
    secret = request.headers.get("X-Admin-Secret", "")
    if not settings.telegram_webhook_secret or secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="unauthorised")


@router.post("/admin/trigger-briefing")
async def trigger_briefing(request: Request):
    """Manually trigger the daily briefing. Requires the webhook secret."""
    _check_admin(request)
    asyncio.create_task(_run_daily_briefing())
    return {"ok": True, "message": "briefing dispatched"}


@router.get("/admin/diag")
async def diag(request: Request):
    """Diagnostic endpoint — shows scheduler + reminder state."""
    _check_admin(request)
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Last 10 reminders, any status
        result = await session.execute(
            select(Reminder).order_by(Reminder.created_at.desc()).limit(10)
        )
        reminders = result.scalars().all()

    return {
        "now_utc": now.isoformat(),
        "scheduler_running": scheduler.running,
        "scheduler_jobs": [
            {"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None}
            for j in scheduler.get_jobs()
        ],
        "reminder_tick_count": _reminder_tick_count,
        "owner_chat_id_configured": bool(settings.owner_chat_id),
        "recent_reminders": [
            {
                "id": str(r.id),
                "message": r.message,
                "remind_at": r.remind_at.isoformat() if r.remind_at else None,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "is_overdue": (r.status == "pending" and r.remind_at and r.remind_at <= now),
            }
            for r in reminders
        ],
    }


@router.post("/admin/force-reminder-check")
async def force_reminder_check(request: Request):
    """Manually run the reminder due-check loop right now."""
    _check_admin(request)
    asyncio.create_task(check_and_send_reminders())
    return {"ok": True, "message": "reminder check dispatched"}


@router.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming Telegram updates."""
    # Verify secret token
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if settings.telegram_webhook_secret and not telegram_service.verify_secret_token(secret_token):
        logger.warning("Invalid Telegram webhook secret")
        return Response(status_code=401)

    body = await request.json()
    message = telegram_service.parse_update(body)

    if not message:
        return {"ok": True}

    # Only process messages from the owner (if configured)
    if settings.owner_chat_id and message["from"] != settings.owner_chat_id:
        logger.info("Ignoring message from non-owner", sender=message["from"])
        return {"ok": True}

    logger.info("Received message", type=message["type"], sender=message["from"])

    # Process asynchronously so we return 200 immediately
    asyncio.create_task(_handle_message(message))

    return {"ok": True}


async def _handle_message(message: dict):
    """Process a message in the background."""
    chat_id = message["chat_id"]
    try:
        # Show typing indicator
        await telegram_service.send_typing_action(chat_id)

        # Process via Claude router
        response_text = await process_message(message)

        # Send response
        await telegram_service.send_message(chat_id, response_text)

    except Exception as e:
        logger.exception("Error processing message", error=str(e))
        await telegram_service.send_message(
            chat_id, "Sorry, I encountered an error processing your message. Please try again."
        )
