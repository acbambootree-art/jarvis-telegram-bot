import asyncio
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.core.memory import save_message
from app.db.database import async_session
from app.db.repositories import UserRepository
from app.services.briefing import get_daily_briefing
from app.services.coach import (
    format_checkin_for_telegram,
    format_motivation_for_telegram,
    get_daily_motivation,
    get_evening_checkin,
)
from app.services.market_intel import format_for_telegram as format_market_intel
from app.services.market_intel import get_daily_market_intel
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

    # Daily market intelligence at 10:00 user-timezone
    scheduler.add_job(
        _run_market_intel,
        trigger=CronTrigger(hour=10, minute=0, timezone=tz),
        id="market_intel",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    # Tony-Robbins-style coach: noon motivation + 8pm check-in
    scheduler.add_job(
        _run_coach_motivation,
        trigger=CronTrigger(hour=12, minute=0, timezone=tz),
        id="coach_motivation",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        _run_coach_checkin,
        trigger=CronTrigger(hour=20, minute=0, timezone=tz),
        id="coach_checkin",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started",
        reminder_interval="60s",
        briefing_time=settings.briefing_time,
        market_intel_time="10:00",
        coach_motivation_time="12:00",
        coach_checkin_time="20:00",
    )


def stop_scheduler():
    """Shutdown the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


_reminder_tick_count = 0


async def _run_reminder_check():
    """Wrapper to run reminder check. Logs a heartbeat every 10 minutes."""
    global _reminder_tick_count
    try:
        _reminder_tick_count += 1
        # Heartbeat every 10 ticks (~10 min) so we can verify the scheduler
        # is actually alive in Render logs without spamming every minute.
        if _reminder_tick_count % 10 == 1:
            logger.info("reminder_check_heartbeat", tick=_reminder_tick_count)
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


async def _run_market_intel():
    """Generate and send the daily market intelligence brief."""
    if not settings.owner_chat_id:
        return
    try:
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)
        data = await get_daily_market_intel()
        text = format_market_intel(data)
        await telegram_service.send_message(settings.owner_chat_id, text)
        # Persist into conversation history so the next brief's
        # repetition guard can see what was already covered.
        if data.get("success"):
            await save_message(user.id, "assistant", text)
        logger.info("market_intel_sent", success=data.get("success"))
    except Exception as e:
        logger.exception("Market intel job failed", error=str(e))


async def _run_coach_motivation():
    """Send the noon Tony-Robbins-style motivation."""
    if not settings.owner_chat_id:
        return
    try:
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)
        data = await get_daily_motivation(user.id)
        text = format_motivation_for_telegram(data)
        await telegram_service.send_message(settings.owner_chat_id, text)
        # Persist into conversation history so Jarvis can have follow-up
        # discussions about the motivation.
        if data.get("success"):
            await save_message(user.id, "assistant", text)
        logger.info("coach_motivation_sent", success=data.get("success"))
    except Exception as e:
        logger.exception("Coach motivation job failed", error=str(e))


async def _run_coach_checkin():
    """Send the 8pm reflective check-in."""
    if not settings.owner_chat_id:
        return
    try:
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)
        data = await get_evening_checkin(user.id)
        text = format_checkin_for_telegram(data)
        await telegram_service.send_message(settings.owner_chat_id, text)
        # Persist into conversation history so when the user replies,
        # Claude sees this as the previous assistant message and can
        # give Tony-Robbins coach feedback per the system prompt rule.
        if data.get("success"):
            await save_message(user.id, "assistant", text)
        logger.info("coach_checkin_sent", success=data.get("success"))
    except Exception as e:
        logger.exception("Coach check-in job failed", error=str(e))


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

    # Tasks (lettered list: A.) B.) C.) ...)
    task_data = data.get("tasks", {})
    lines.append(f"*Tasks* ({task_data.get('pending_count', 0)} pending)")
    task_list = task_data.get("tasks", [])[:26]  # cap at 26 letters A-Z
    for idx, task in enumerate(task_list):
        letter = chr(ord("A") + idx)
        priority_icon = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(task["priority"], "")
        lines.append(f"  {letter}.) {priority_icon} {task['title']}")
    if not task_list:
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

    # Ze Ri (择日) — Chinese Almanac
    zeri = data.get("zeri", {})
    zeri_text = zeri.get("formatted", "")
    if zeri_text:
        lines.append("📜 *Ze Ri 择日 — Today's Almanac*")
        lines.append(f"  {zeri_text}")
        lines.append("")

    # Ziwei Doushu
    ziwei = data.get("ziwei", {})
    reading = ziwei.get("reading", "")
    if reading:
        lines.append("🔮 *Ziwei Doushu Daily Reading*")
        lines.append(f"  {reading}")
        lines.append("")

    lines.append("_Reply with any command or ask me anything!_")

    return "\n".join(lines)
