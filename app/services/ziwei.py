"""Ziwei Doushu (紫微斗数) daily reading service.

Uses Claude to generate a concise daily fortune reading grounded in
Ziwei Doushu principles, based on the owner's birth data and today's date.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings

logger = structlog.get_logger()

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

ZIWEI_SYSTEM_PROMPT = """\
You are a master Ziwei Doushu (紫微斗数) astrologer. Given the querent's \
solar birth data and today's date, produce a SHORT daily fortune reading \
(max 4-5 sentences). Cover the day's energy, one area of opportunity, \
and one thing to watch out for. Be specific to the day — do not give \
generic advice that could apply to any day.

Rules:
- Ground the reading in Ziwei Doushu concepts (palaces, stars, \
  transformations, decade/annual/daily overlays) but keep jargon minimal.
- Mention the dominant star(s) influencing the day.
- Tone: warm, practical, conversational — like a knowledgeable friend.
- Output plain text only (no markdown, no bullet points).
- Keep it under 80 words.
"""


async def get_daily_reading() -> dict:
    """Generate today's Ziwei Doushu reading for the owner."""
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz)
    today_str = today.strftime("%A, %B %d, %Y")

    user_prompt = (
        f"Querent: born {settings.ziwei_birth_year}-"
        f"{settings.ziwei_birth_month:02d}-{settings.ziwei_birth_day:02d} "
        f"at {settings.ziwei_birth_hour:02d}:{settings.ziwei_birth_minute:02d}, "
        f"gender {settings.ziwei_gender}, solar calendar.\n"
        f"Today's date: {today_str}.\n"
        f"Give the daily Ziwei Doushu reading for today."
    )

    try:
        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system=ZIWEI_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        reading = response.content[0].text.strip()

        return {
            "success": True,
            "date": today_str,
            "reading": reading,
        }

    except Exception as e:
        logger.exception("Ziwei daily reading failed", error=str(e))
        return {
            "success": False,
            "date": today_str,
            "reading": "",
            "error": str(e),
        }
