"""Feedback loop — capture 👍/👎 on Jarvis messages and periodically
distil them into user preferences that get auto-loaded into the prompt.
"""

import re
from datetime import datetime, timedelta
from uuid import UUID

import anthropic
import structlog
from sqlalchemy import select

from app.config import settings
from app.db.database import async_session
from app.models.models import FeedbackRating
from app.services import facts
from app.core.claude_helpers import extract_text

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

_THUMBS_UP_RE = re.compile(r"^\s*(👍|thumbs? up|good one|nailed it|helpful|love it|yes|👏|🔥|💯)\s*$", re.IGNORECASE)
_THUMBS_DOWN_RE = re.compile(r"^\s*(👎|thumbs? down|not helpful|bad|nope|no|off|missed|wrong)\s*$", re.IGNORECASE)


def detect_rating(user_text: str) -> str | None:
    if not user_text:
        return None
    if _THUMBS_UP_RE.match(user_text):
        return "up"
    if _THUMBS_DOWN_RE.match(user_text):
        return "down"
    return None


def classify_kind(prev_asst_text: str) -> str:
    if not prev_asst_text:
        return "other"
    if prev_asst_text.startswith("🔥"):
        return "coach_motivation"
    if prev_asst_text.startswith("🌙"):
        return "coach_checkin"
    if prev_asst_text.startswith("📈"):
        return "market_intel"
    if prev_asst_text.startswith("*Good morning"):
        return "briefing"
    if prev_asst_text.startswith("📅 *Week"):
        return "weekly_retro"
    return "reply"


async def record_rating(user_id: UUID, rating: str, prev_asst_text: str) -> dict:
    kind = classify_kind(prev_asst_text)
    async with async_session() as session:
        row = FeedbackRating(
            user_id=user_id,
            rating=rating,
            message_context=(prev_asst_text or "")[:500],
            kind=kind,
        )
        session.add(row)
        await session.commit()
    logger.info("feedback_recorded", rating=rating, kind=kind)
    return {"success": True, "rating": rating, "kind": kind}


async def distil_weekly_prefs(user_id: UUID) -> dict:
    """Read the last 7 days of ratings; if there's a clear signal,
    save a preference fact so future prompts adapt."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    since = datetime.utcnow() - timedelta(days=7)
    async with async_session() as session:
        result = await session.execute(
            select(FeedbackRating)
            .where(FeedbackRating.user_id == user_id)
            .where(FeedbackRating.created_at >= since)
            .order_by(FeedbackRating.created_at.desc())
            .limit(100)
        )
        rows = result.scalars().all()
    if len(rows) < 3:
        return {"success": True, "note": "not enough signal", "count": len(rows)}

    dump = "\n".join(
        f"[{r.rating}] {r.kind}: {(r.message_context or '')[:200]}"
        for r in rows
    )
    prompt = (
        f"Recent ratings (👍/👎) on Jarvis messages:\n\n{dump}\n\n"
        "Distil the pattern into 1-3 SHORT preference facts. Return each on its "
        "own line prefixed with 'PREF: '. If no clear pattern, return 'NONE'."
    )
    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=500,
            system="Return only 'PREF: ...' lines or 'NONE'. No preamble.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = extract_text(msg) or "NONE"
        if text.upper().startswith("NONE"):
            return {"success": True, "note": "no clear pattern", "count": len(rows)}
        # Save each PREF line as a preference fact
        saved = 0
        for line in text.splitlines():
            if line.strip().upper().startswith("PREF:"):
                pref = line.split(":", 1)[1].strip()
                if pref:
                    await facts.save_fact(user_id, pref, category="preference", tags=["auto", "feedback"])
                    saved += 1
        return {"success": True, "saved_prefs": saved, "count": len(rows)}
    except Exception as e:
        logger.error("distil_prefs_failed", error=str(e))
        return {"success": False, "error": str(e)}
