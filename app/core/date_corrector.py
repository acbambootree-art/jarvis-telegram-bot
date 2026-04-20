"""Post-processor that fixes Claude's mis-computed weekdays in output text.

Claude (Sonnet) frequently writes phrases like "Monday, April 21, 2026"
where the weekday does not match the date. We parse these patterns,
compute the correct weekday server-side, and rewrite.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger()

_WEEKDAYS_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = {m: i + 1 for i, m in enumerate([
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
])}
_MONTHS_SHORT = {m: i + 1 for i, m in enumerate([
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
])}

# Matches e.g. "Monday, April 21, 2026", "Monday April 21st, 2026", "Mon, Apr 21"
_PATTERN = re.compile(
    r"\b("
    + "|".join(_WEEKDAYS_FULL + _WEEKDAYS_SHORT)
    + r")\b"               # weekday
    + r"(,?\s+)"          # separator
    + r"("                 # month
    + "|".join(list(_MONTHS.keys()) + list(_MONTHS_SHORT.keys()))
    + r")\s+"
    + r"(\d{1,2})(?:st|nd|rd|th)?"  # day with optional suffix
    + r"(?:(,?\s+)(\d{4}))?",        # optional year
    re.IGNORECASE,
)


def correct_weekdays(text: str, timezone: str = "Asia/Singapore") -> str:
    """Scan text for weekday+date mentions; replace mismatched weekdays."""
    if not text:
        return text

    tz = ZoneInfo(timezone)
    current_year = datetime.now(tz).year

    def _sub(match: re.Match) -> str:
        weekday_raw = match.group(1)
        sep1 = match.group(2)
        month_raw = match.group(3)
        day_raw = match.group(4)
        sep2 = match.group(5) or ""
        year_raw = match.group(6)

        month_key = month_raw.capitalize()
        month_num = _MONTHS.get(month_key) or _MONTHS_SHORT.get(month_key[:3].capitalize())
        if not month_num:
            return match.group(0)
        try:
            day_num = int(day_raw)
            year_num = int(year_raw) if year_raw else current_year
            real_dt = datetime(year_num, month_num, day_num)
        except (ValueError, TypeError):
            return match.group(0)

        real_weekday = real_dt.strftime("%A")
        # Detect if the original used short form
        use_short = len(weekday_raw) <= 3
        correct_weekday = real_weekday[:3] if use_short else real_weekday

        # Match capitalisation style (original starts upper? keep upper)
        if weekday_raw[0].isupper():
            correct_weekday = correct_weekday
        else:
            correct_weekday = correct_weekday.lower()

        if correct_weekday.lower() == weekday_raw.lower():
            return match.group(0)

        logger.info(
            "weekday_corrected",
            original=weekday_raw,
            corrected=correct_weekday,
            date=real_dt.strftime("%Y-%m-%d"),
        )

        # Preserve day suffix if present
        day_str = day_raw
        suffix_match = re.search(r"\d+(st|nd|rd|th)", match.group(0), re.IGNORECASE)
        if suffix_match:
            day_str = f"{day_raw}{suffix_match.group(1)}"

        year_part = f"{sep2}{year_raw}" if year_raw else ""
        return f"{correct_weekday}{sep1}{month_raw} {day_str}{year_part}"

    return _PATTERN.sub(_sub, text)
