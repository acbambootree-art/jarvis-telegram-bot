import asyncio
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db.database import async_session
from app.db.repositories import UserRepository
from app.services.briefing import get_daily_briefing
from app.services.reminders import check_and_send_reminders
from app.services.telegram import telegram_service

logger = structlog.get_logger()

scheduler = AsyncIOScheduler()


def start_scheduler():
    """Start the background scheduler with all jobs."""
    # Check reminders every 60 seconds
    scheduler.add_job(
        _run_reminder_check,
        trigger=IntervalTrigger(seconds=60),
        id="reminder_checker",
        replace_existing=True,
    )

    # Daily briefing (in user's timezone)
    hour, minute = settings.briefing_time.split(":")
    tz = ZoneInfo(settings.default_timezone)
    scheduler.add_job(
        _run_daily_briefing,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
        id="daily_briefing",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    scheduler.start()
    logger.info("Scheduler started", reminder_interval="60s", briefing_time=settings.briefing_time)


def stop_scheduler():
    """Shutdown the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


async def _run_reminder_check():
    """Wrapper to run reminder check."""
    try:
        await check_and_send_reminders()
    except Exception as e:
        logger.exception("Reminder check failed", error=str(e))


async def _run_daily_briefing():
    """Send daily briefing to the owner."""
    if not settings.owner_chat_id:
        return

    try:
        # Find the owner user
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)

        # Get briefing data
        briefing_data = await get_daily_briefing(user.id)

        # Format briefing
        text = _format_briefing(briefing_data)

        # Send via Telegram
        await telegram_service.send_message(settings.owner_chat_id, text)
        logger.info("Daily briefing sent")

    except Exception as e:
        logger.exception("Daily briefing failed", error=str(e))


def _format_briefing(data: dict) -> str:
    """Format briefing data into a Telegram message (Markdown)."""
    lines = [f"*Good morning! Here's your briefing for {data.get('date', 'today')}*\n"]

    # Calendar
    cal = data.get("calendar", {})
    lines.append(f"*Calendar* ({cal.get('count', 0)} events)")
    for event in cal.get("events", [])[:5]:
        start = event.get("start", "")
        if "T" in start:
            time_str = start.split("T")[1][:5]
        else:
            time_str = "All day"
        lines.append(f"  • {time_str} {event['title']}")
    if not cal.get("events"):
        lines.append("  No events today")
    lines.append("")

    # Tasks
    task_data = data.get("tasks", {})
    lines.append(f"*Tasks* ({task_data.get('pending_count', 0)} pending)")
    for task in task_data.get("tasks", [])[:5]:
        priority_icon = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(task["priority"], "")
        lines.append(f"  • {priority_icon} {task['title']}")
    if not task_data.get("tasks"):
        lines.append("  All caught up!")
    lines.append("")

    # Email
    email = data.get("email", {})
    unread = email.get("unread_count", 0)
    lines.append(f"*Email* — {unread} unread")
    lines.append("")

    # Reminders
    rem = data.get("reminders", {})
    if rem.get("count", 0) > 0:
        lines.append(f"*Reminders* ({rem['count']} upcoming)")
        for r in rem.get("reminders", [])[:3]:
            lines.append(f"  • {r['message']}")
        lines.append("")

    lines.append("_Reply with any command or ask me anything!_")

    return "\n".join(lines)
