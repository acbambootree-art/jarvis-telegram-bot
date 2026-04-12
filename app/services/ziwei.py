"""Ziwei Doushu (紫微斗数) daily reading + on-demand fortune service.

Uses izthon for real birth-chart + horoscope calculation,
then Claude for concise natural-language interpretation.
"""

from datetime import datetime
from uuid import UUID
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

# Map user-facing topics to natal palace names (izthon en-US labels)
_TOPIC_PALACE_MAP = {
    "career": "Career",       # 官禄宫
    "love": "Spouse",          # 夫妻宫
    "wealth": "Wealth",        # 财帛宫
    "health": "Health",        # 疾厄宫
    "travel": "Travel",        # 迁移宫
    "property": "Property",    # 田宅宫
    "family": "Parents",       # 父母宫
    "friends": "Friends",      # 交友宫
}

_time_index = _HOUR_TO_INDEX.get(settings.ziwei_birth_hour, 3)
_gender_cn = _GENDER_MAP.get(settings.ziwei_gender.lower(), "男")
_solar_date = (
    f"{settings.ziwei_birth_year}-"
    f"{settings.ziwei_birth_month}-"
    f"{settings.ziwei_birth_day}"
)

_astrolabe = by_solar(_solar_date, _time_index, _gender_cn, language="en-US")

# ---- interpretation prompts ------------------------------------------------

_BRIEFING_SYSTEM = """\
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

_FORTUNE_SYSTEM = """\
You are a master Ziwei Doushu (紫微斗数) astrologer. You will receive \
COMPUTED chart data (birth chart + horoscope overlays) for a querent. \
Use ONLY the data provided to write the fortune reading requested.

Rules:
- Reference the actual stars, palaces, and four transformations (四化) \
  from the data — do not invent stars or palaces not in the data.
- Explain what the star positions and transformations mean in plain \
  language for the requested life area and time period.
- Be specific and actionable: timing, what to do, what to avoid.
- Tone: warm, practical, conversational — like a knowledgeable friend.
- Use Telegram Markdown: *bold* for star/palace names, _italic_ for emphasis.
- Keep it concise: 100-150 words for focused topics, up to 200 for \
  general or natal readings.
"""

# ---- chart data extraction -------------------------------------------------


def _stars_in_palace(palace_idx, scope_stars):
    """Return star names placed in *palace_idx* by a given scope."""
    if not scope_stars or palace_idx < 0 or palace_idx >= len(scope_stars):
        return []
    return [s.name for s in scope_stars[palace_idx]]


def _palace_summary(palace) -> dict:
    """Extract a summary dict for a natal palace."""
    return {
        "name": palace.name,
        "heavenly_stem": palace.heavenly_stem,
        "earthly_branch": palace.earthly_branch,
        "major_stars": [s.name for s in palace.major_stars],
        "minor_stars": [s.name for s in palace.minor_stars],
        "is_body_palace": getattr(palace, "is_body_palace", False),
    }


def _extract_horoscope_summary(date_str: str) -> dict:
    """Run the horoscope for *date_str* and return a structured summary."""
    h = _astrolabe.horoscope(date_str)

    daily = h.daily
    monthly = h.monthly
    yearly = h.yearly
    decadal = h.decadal

    daily_palace = _astrolabe.palace(daily.index)

    return {
        "solar_date": h.solar_date,
        "lunar_date": h.lunar_date,
        "nominal_age": h.age.nominal_age,
        # Decadal (大限)
        "decadal_palace": _astrolabe.palace(decadal.index).name if decadal.index >= 0 else "N/A",
        "decadal_stem_branch": f"{decadal.heavenly_stem}{decadal.earthly_branch}",
        "decadal_mutagen": decadal.mutagen,
        "decadal_stars": _stars_in_palace(decadal.index, decadal.stars),
        # Yearly (流年)
        "yearly_palace": _astrolabe.palace(yearly.index).name,
        "yearly_stem_branch": f"{yearly.heavenly_stem}{yearly.earthly_branch}",
        "yearly_mutagen": yearly.mutagen,
        "yearly_stars": _stars_in_palace(yearly.index, yearly.stars),
        # Monthly (流月)
        "monthly_palace": _astrolabe.palace(monthly.index).name,
        "monthly_stem_branch": f"{monthly.heavenly_stem}{monthly.earthly_branch}",
        "monthly_mutagen": monthly.mutagen,
        "monthly_stars": _stars_in_palace(monthly.index, monthly.stars),
        # Daily (流日)
        "daily_palace": daily_palace.name,
        "daily_stem_branch": f"{daily.heavenly_stem}{daily.earthly_branch}",
        "daily_mutagen": daily.mutagen,
        "daily_palace_natal_stars": [s.name for s in daily_palace.major_stars],
        "daily_stars": _stars_in_palace(daily.index, daily.stars),
        # Birth chart context
        "five_elements_class": _astrolabe.five_elements_class,
        "soul_palace_branch": _astrolabe.earthly_branch_of_soul_palace,
    }


def _natal_chart_summary() -> dict:
    """Return a summary of the full natal chart (12 palaces)."""
    palaces = []
    for i in range(12):
        p = _astrolabe.palace(i)
        palaces.append(_palace_summary(p))
    return {
        "solar_date": _solar_date,
        "gender": settings.ziwei_gender,
        "five_elements_class": _astrolabe.five_elements_class,
        "soul": _astrolabe.soul,
        "body": _astrolabe.body,
        "soul_palace_branch": _astrolabe.earthly_branch_of_soul_palace,
        "body_palace_branch": _astrolabe.earthly_branch_of_body_palace,
        "zodiac": _astrolabe.zodiac,
        "sign": _astrolabe.sign,
        "palaces": palaces,
    }


def _format_scope_block(label: str, palace: str, stem_branch: str,
                        mutagen: list, stars: list) -> str:
    """Format one horoscope scope (decadal/yearly/monthly/daily) as text."""
    lines = [
        f"  {label}: Palace={palace} ({stem_branch})",
        f"    Transformations (四化): {', '.join(mutagen) if mutagen else 'none'}",
        f"    Flow stars: {', '.join(stars) if stars else 'none'}",
    ]
    return "\n".join(lines)


def _format_chart_prompt(summary: dict, scope: str, topic: str,
                         date_display: str) -> str:
    """Build the user prompt with chart data for Claude interpretation."""
    lines = [
        f"Querent born {_solar_date} at {settings.ziwei_birth_hour:02d}:"
        f"{settings.ziwei_birth_minute:02d}, {settings.ziwei_gender}.",
        f"Date: {date_display}",
        f"Scope: {scope}",
        f"Topic: {topic}",
        "",
        "Chart data:",
        f"  Five Elements Class: {summary['five_elements_class']}",
        f"  Nominal age: {summary['nominal_age']}",
        "",
        _format_scope_block("Decadal (大限)", summary["decadal_palace"],
                            summary["decadal_stem_branch"],
                            summary["decadal_mutagen"],
                            summary.get("decadal_stars", [])),
        _format_scope_block("Yearly (流年)", summary["yearly_palace"],
                            summary["yearly_stem_branch"],
                            summary["yearly_mutagen"],
                            summary.get("yearly_stars", [])),
        _format_scope_block("Monthly (流月)", summary["monthly_palace"],
                            summary["monthly_stem_branch"],
                            summary["monthly_mutagen"],
                            summary.get("monthly_stars", [])),
        _format_scope_block("Daily (流日)", summary["daily_palace"],
                            summary["daily_stem_branch"],
                            summary["daily_mutagen"],
                            summary.get("daily_stars", [])),
        "",
        f"  Natal stars in today's palace ({summary['daily_palace']}): "
        f"{', '.join(summary['daily_palace_natal_stars']) or 'none'}",
    ]

    # If a specific topic was requested, add that natal palace's data
    if topic != "general":
        palace_name = _TOPIC_PALACE_MAP.get(topic)
        if palace_name:
            try:
                tp = _astrolabe.palace(palace_name)
                lines.append("")
                lines.append(f"  Natal {topic} palace ({palace_name}):")
                lines.append(f"    Major stars: {', '.join(s.name for s in tp.major_stars) or 'none'}")
                lines.append(f"    Minor stars: {', '.join(s.name for s in tp.minor_stars) or 'none'}")
                lines.append(f"    Stem/Branch: {tp.heavenly_stem}{tp.earthly_branch}")
            except (ValueError, IndexError):
                pass

    scope_instructions = {
        "today": "Focus on today's daily energy and what to expect.",
        "this_month": "Focus on the monthly outlook — key themes and timing this month.",
        "this_year": "Focus on the yearly fortune — major themes, opportunities, and challenges for the year.",
        "this_decade": "Focus on the decadal (大限) fortune — the big-picture life phase the querent is in.",
        "natal": "Analyze the natal chart — the querent's innate personality, strengths, and life themes.",
    }

    lines.append("")
    lines.append(scope_instructions.get(scope, scope_instructions["today"]))
    if topic != "general":
        lines.append(f"Focus specifically on the '{topic}' area of life.")

    return "\n".join(lines)


# ---- public API: daily briefing reading ------------------------------------

async def get_daily_reading() -> dict:
    """Generate today's Ziwei Doushu reading for the daily briefing."""
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
            system=_BRIEFING_SYSTEM,
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


# ---- public API: on-demand fortune tool ------------------------------------

async def get_ziwei_fortune(
    user_id: UUID,
    scope: str = "today",
    topic: str = "general",
    date: str | None = None,
) -> dict:
    """On-demand Ziwei Doushu fortune reading (called via Jarvis tool)."""
    tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(tz)

    # Resolve the target date
    if date:
        target_str = date
        try:
            target_dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
        except ValueError:
            return {"success": False, "error": f"Invalid date format: {date}. Use YYYY-MM-DD."}
    else:
        target_str = now.strftime("%Y-%m-%d")
        target_dt = now

    date_display = target_dt.strftime("%A, %B %d, %Y")

    try:
        # For natal scope, return chart overview without horoscope overlay
        if scope == "natal":
            natal = _natal_chart_summary()
            palace_lines = []
            for p in natal["palaces"]:
                body_tag = " [Body Palace]" if p["is_body_palace"] else ""
                stars = ", ".join(p["major_stars"]) or "empty"
                palace_lines.append(
                    f"  {p['name']}{body_tag} ({p['heavenly_stem']}"
                    f"{p['earthly_branch']}): {stars}"
                )

            user_prompt = (
                f"Querent born {_solar_date} at {settings.ziwei_birth_hour:02d}:"
                f"{settings.ziwei_birth_minute:02d}, {settings.ziwei_gender}.\n"
                f"Zodiac: {natal['zodiac']}, Sign: {natal['sign']}\n"
                f"Five Elements Class: {natal['five_elements_class']}\n"
                f"Soul star: {natal['soul']}, Body star: {natal['body']}\n\n"
                f"Natal Chart (12 Palaces):\n"
                + "\n".join(palace_lines)
                + "\n\nProvide a natal chart reading."
            )
            if topic != "general":
                user_prompt += f" Focus on the '{topic}' area of life."

            max_tokens = 400
        else:
            summary = _extract_horoscope_summary(target_str)
            user_prompt = _format_chart_prompt(summary, scope, topic, date_display)
            max_tokens = 300

        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=_FORTUNE_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        reading = response.content[0].text.strip()

        result = {
            "success": True,
            "scope": scope,
            "topic": topic,
            "date": date_display,
            "reading": reading,
        }
        if scope != "natal":
            result["chart_summary"] = summary
        return result

    except Exception as e:
        logger.exception("Ziwei fortune reading failed", error=str(e))
        return {"success": False, "error": str(e)}
