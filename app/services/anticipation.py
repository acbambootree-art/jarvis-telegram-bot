"""Hourly anticipation sweep.

Scans the user's state for things that likely need attention in the
next hour or two, without the user asking. Sends short Telegram nudges.
De-dupes so the same nudge doesn't fire twice per day.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog

from app.config import settings
from app.services import calendar_service, tasks, telegram

logger = structlog.get_logger()

# In-memory dedup: (date, key) → sent_at
_SENT_TODAY: dict[tuple[str, str], datetime] = {}


def _should_send(key: str) -> bool:
    """Return True if this nudge hasn't been sent today."""
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    tkey = (today, key)
    if tkey in _SENT_TODAY:
        return False
    _SENT_TODAY[tkey] = datetime.now(timezone.utc)
    # Clean out anything older than 2 days
    for k in list(_SENT_TODAY.keys()):
        if k[0] < (datetime.now(tz) - timedelta(days=2)).strftime("%Y-%m-%d"):
            del _SENT_TODAY[k]
    return True


async def _persist_nudge(user_id: UUID, msg: str):
    """Save the nudge to conversation history so Claude sees it as its
    own prior message when the user replies to it."""
    try:
        from app.core.memory import save_message
        await save_message(user_id, "assistant", msg)
    except Exception as e:
        logger.warning("nudge_persist_failed", error=str(e))


async def run_sweep(user_id: UUID) -> list[str]:
    """Run all anticipation checks. Send nudges. Return the list of
    messages actually sent (for logging/diag)."""
    sent = []
    tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(tz)

    # 1) Upcoming meeting in the next 45 min with no prep marker
    try:
        cal_res = await calendar_service.get_events(user_id, start_date=now.strftime("%Y-%m-%d"))
        if cal_res.get("success"):
            for e in cal_res.get("events", []):
                start = e.get("start", "")
                if "T" not in start:
                    continue
                try:
                    ev_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    if ev_start.tzinfo is None:
                        ev_start = ev_start.replace(tzinfo=tz)
                except Exception:
                    continue
                minutes_out = (ev_start - now).total_seconds() / 60
                if 20 <= minutes_out <= 45:
                    title = e.get("title", "meeting")
                    key = f"meeting_prep:{e.get('id','')[:12]}"
                    if _should_send(key):
                        msg = (
                            f"⏰ *{title}* in {int(minutes_out)} min. "
                            f"Want a prep summary of your last thread with them?"
                        )
                        await telegram.telegram_service.send_message(settings.owner_chat_id, msg)
                        await _persist_nudge(user_id, msg)
                        sent.append(msg)
    except Exception as e:
        logger.warning("anticipation_calendar_check_failed", error=str(e))

    # 2) Overdue urgent task not touched today
    try:
        todo_res = await tasks.list_tasks(user_id, status="todo", priority="urgent")
        for t in (todo_res.get("tasks") or [])[:2]:
            key = f"urgent_overdue:{t.get('task_id','')[:12]}"
            if _should_send(key):
                title = t.get("title", "")
                msg = f"🔴 *Urgent task lingering:* {title}\nBlock 30 min now?"
                await telegram.telegram_service.send_message(settings.owner_chat_id, msg)
                sent.append(msg)
    except Exception as e:
        logger.warning("anticipation_task_check_failed", error=str(e))

    return sent


async def run_sweep_for_owner():
    """Wrapper — resolve owner user then sweep."""
    if not settings.owner_chat_id:
        return
    from app.db.database import async_session
    from app.db.repositories import UserRepository

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_or_create(settings.owner_chat_id)
    await run_sweep(user.id)
