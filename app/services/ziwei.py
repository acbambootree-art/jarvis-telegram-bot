"""Ziwei Doushu (紫微斗数) daily reading service.

Uses izthon for real birth-chart + daily-horoscope calculation,
then Claude for a concise natural-language interpretation.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import structlog
from izthon.astro import by_solar

from app.config import settings

logger = structlog.get_logger()

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# ---- birth chart (built once at import) ------------------------------------

# time_index mapping: 0=子(23-01), 1=丑(01-03), 2=寅(03-05), 3=卯(05-07), ...
_HOUR_TO_INDEX = {
    23: 0, 0: 0,
    1: 1, 2: 1,
    3: 2, 4: 2,
    5: 3, 6: 3,
    7: 4, 8: 4,
    9: 5, 10: 5,
    11: 6, 12: 6,
    13: 7, 14: 7,
    15: 8, 16: 8,
    17: 9, 18: 9,
    19: 10, 20: 10,
    21: 11, 22: 11,
}

_GENDER_MAP = {"male": "男", "female": "女"}

_time_index = _HOUR_TO_INDEX.get(settings.ziwei_birth_hour, 3)
_gender_cn = _GENDER_MAP.get(settings.ziwei_gender.lower(), "男")
_solar_date = (
    f"{settings.ziwei_birth_year}-"
    f"{settings.ziwei_birth_month}-"
    f"{settings.ziwei_birth_day}"
)

_astrolabe = by_solar(_solar_date, _time_index, _gender_cn, language="en-US")

# ---- helpers ---------------------------------------------------------------

INTERPRET_SYSTEM_PROMPT = """\
You are a master Ziwei Doushu (紫微斗数) astrologer. You will receive \
COMPUTED chart data for a querent's daily horoscope overlay. Use this \
real data to write a SHORT daily fortune reading (4-5 sentences, under \
80 words).

Rules:
- Reference the actual stars, palaces, and transformations from the data.
- Cover: the day's overall energy, one opportunity, one caution.
- Tone: warm, practical, conversational — like a knowledgeable friend.
- Output plain text only (no markdown, no bullet points, no headers).
"""


def _extract_horoscope_summary(today_str: str) -> dict:
    """Run the horoscope for *today_str* and return a structured summary."""
    h = _astrolabe.horoscope(today_str)

    def _stars_in_palace(palace_idx, scope_stars):
        """Return star names placed in *palace_idx* by a given scope."""
        if not scope_stars or palace_idx < 0 or palace_idx >= len(scope_stars):
            return []
        return [s.name for s in scope_stars[palace_idx]]

    daily = h.daily
    yearly = h.yearly
    decadal = h.decadal

    # Which natal palace does today's energy land on?
    daily_palace = _astrolabe.palace(daily.index)
    natal_stars = [s.name for s in daily_palace.major_stars]

    return {
        "solar_date": h.solar_date,
        "lunar_date": h.lunar_date,
        "nominal_age": h.age.nominal_age,
        # Decadal (大限)
        "decadal_palace": _astrolabe.palace(decadal.index).name if decadal.index >= 0 else "N/A",
        "decadal_stem_branch": f"{decadal.heavenly_stem}{decadal.earthly_branch}",
        "decadal_mutagen": decadal.mutagen,
        # Yearly (流年)
        "yearly_palace": _astrolabe.palace(yearly.index).name,
        "yearly_stem_branch": f"{yearly.heavenly_stem}{yearly.earthly_branch}",
        "yearly_mutagen": yearly.mutagen,
        # Daily (流日)
        "daily_palace": daily_palace.name,
        "daily_stem_branch": f"{daily.heavenly_stem}{daily.earthly_branch}",
        "daily_mutagen": daily.mutagen,
        "daily_palace_natal_stars": natal_stars,
        "daily_stars": _stars_in_palace(daily.index, daily.stars),
        # Birth chart context
        "five_elements_class": _astrolabe.five_elements_class,
        "soul_palace_branch": _astrolabe.earthly_branch_of_soul_palace,
    }


# ---- public API ------------------------------------------------------------

async def get_daily_reading() -> dict:
    """Generate today's Ziwei Doushu reading for the owner."""
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz)
    today_str = today.strftime("%Y-%m-%d")
    today_display = today.strftime("%A, %B %d, %Y")

    try:
        summary = _extract_horoscope_summary(today_str)

        user_prompt = (
            f"Querent born {_solar_date} at {settings.ziwei_birth_hour:02d}:"
            f"{settings.ziwei_birth_minute:02d}, {settings.ziwei_gender}.\n"
            f"Date: {today_display}\n\n"
            f"Chart data:\n"
            f"  Five Elements Class: {summary['five_elements_class']}\n"
            f"  Age (nominal): {summary['nominal_age']}\n"
            f"  Decadal Palace: {summary['decadal_palace']} "
            f"({summary['decadal_stem_branch']}), "
            f"Transformations: {', '.join(summary['decadal_mutagen'])}\n"
            f"  Yearly Palace: {summary['yearly_palace']} "
            f"({summary['yearly_stem_branch']}), "
            f"Transformations: {', '.join(summary['yearly_mutagen'])}\n"
            f"  Daily Palace: {summary['daily_palace']} "
            f"({summary['daily_stem_branch']}), "
            f"Transformations: {', '.join(summary['daily_mutagen'])}\n"
            f"  Natal stars in today's palace: "
            f"{', '.join(summary['daily_palace_natal_stars']) or 'none'}\n"
            f"  Daily flow stars: "
            f"{', '.join(summary['daily_stars']) or 'none'}\n\n"
            f"Write the daily reading."
        )

        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system=INTERPRET_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        reading = response.content[0].text.strip()

        return {
            "success": True,
            "date": today_display,
            "reading": reading,
            "chart_summary": summary,
        }

    except Exception as e:
        logger.exception("Ziwei daily reading failed", error=str(e))
        return {
            "success": False,
            "date": today_display,
            "reading": "",
            "error": str(e),
        }
