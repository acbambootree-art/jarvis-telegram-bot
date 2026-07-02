"""Coach check-in memory: captures user's win/lesson/priority after
each 8pm 🌙 check-in, computes streaks, feeds yesterday's priority
back into today's noon motivation for continuity.
"""

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import anthropic
import structlog
from sqlalchemy import select

from app.config import settings
from app.db.database import async_session
from app.models.models import CheckinResponse
from app.core.claude_helpers import extract_text

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

_EXTRACT_SYSTEM = """Given a user's reply to a 3-question evening check-in
(WIN today, LESSON today, TOMORROW's ONE thing), extract each answer
as short plain text. If the user only partially answered, leave the
missing field as an empty string. Return STRICT JSON only:
{"win": "...", "lesson": "...", "priority": "..."}
"""


async def extract_and_store(user_id: UUID, raw_reply: str) -> dict:
    """Parse the user's reply via Claude and save to DB."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    tz = ZoneInfo(settings.default_timezone)
    reply_date = datetime.now(tz).strftime("%Y-%m-%d")

    win = lesson = priority = ""
    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=400,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": raw_reply}],
        )
        text = extract_text(msg) or ""
        import json, re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            win = data.get("win", "")[:500]
            lesson = data.get("lesson", "")[:500]
            priority = data.get("priority", "")[:500]
    except Exception as e:
        logger.warning("checkin_extract_failed", error=str(e))

    async with async_session() as session:
        row = CheckinResponse(
            user_id=user_id,
            reply_date=reply_date,
            win=win,
            lesson=lesson,
            priority=priority,
            raw_reply=raw_reply[:4000],
        )
        session.add(row)
        await session.commit()
    logger.info("checkin_stored", date=reply_date)
    return {"success": True, "win": win, "lesson": lesson, "priority": priority, "date": reply_date}


async def get_yesterday(user_id: UUID) -> dict:
    """Return yesterday's check-in row (if any)."""
    tz = ZoneInfo(settings.default_timezone)
    yesterday = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    async with async_session() as session:
        result = await session.execute(
            select(CheckinResponse)
            .where(CheckinResponse.user_id == user_id)
            .where(CheckinResponse.reply_date == yesterday)
            .order_by(CheckinResponse.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
    if not row:
        return {"found": False}
    return {
        "found": True,
        "date": row.reply_date,
        "win": row.win or "",
        "lesson": row.lesson or "",
        "priority": row.priority or "",
    }


async def get_streak(user_id: UUID) -> int:
    """Count consecutive days ending today (inclusive) that the user
    has replied to the check-in."""
    tz = ZoneInfo(settings.default_timezone)
    async with async_session() as session:
        result = await session.execute(
            select(CheckinResponse.reply_date)
            .where(CheckinResponse.user_id == user_id)
            .order_by(CheckinResponse.reply_date.desc())
            .limit(60)
        )
        dates = {r[0] for r in result.all()}
    if not dates:
        return 0
    streak = 0
    day = datetime.now(tz).date()
    # Allow current day OR yesterday as start (user may not have replied yet today)
    if day.strftime("%Y-%m-%d") not in dates:
        day -= timedelta(days=1)
    while day.strftime("%Y-%m-%d") in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak
