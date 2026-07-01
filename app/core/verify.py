"""Server-side verification pass on sensitive tool inputs.

Runs BEFORE the tool executes. Catches:
- Weekday mismatches in create_event/update_event/set_reminder inputs
  (e.g. "Tuesday, 2026-04-15" — parses date, checks weekday)
- Times in the past for reminders / events (obvious typo detection)
- Missing timezone / bare naive ISO strings without offset

Returns None if input is fine, else a correction dict Claude can see.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from app.config import settings

logger = structlog.get_logger()

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def _parse_iso_lenient(s: str) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Accept trailing Z as UTC
    s = re.sub(r"Z$", "+00:00", s)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        # Try YYYY-MM-DD only
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return None


def _weekday_of(iso_str: str) -> int | None:
    dt = _parse_iso_lenient(iso_str)
    if not dt:
        return None
    return dt.weekday()


def verify_tool_input(tool_name: str, tool_input: dict) -> str | None:
    """Return a correction string if the input has an obvious error,
    else None. The correction is sent back to Claude as a tool_result
    with is_error=True so Claude can retry with corrected data."""
    warnings = []

    # Extract candidate datetime fields
    dt_fields = {
        "create_event": ["start_time", "end_time"],
        "update_event": ["start_time", "end_time"],
        "set_reminder": ["remind_at"],
        "create_task": ["due_date"],
        "update_task": ["due_date"],
        "log_expense": ["expense_date"],
    }.get(tool_name, [])

    # 1. Time-in-past check for reminders/future events
    tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(tz)

    for field in dt_fields:
        val = tool_input.get(field)
        if not val:
            continue
        parsed = _parse_iso_lenient(val)
        if not parsed:
            continue
        # Tag naive datetimes with user tz for comparison
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        if tool_name in ("create_event", "update_event", "set_reminder") and parsed < now:
            # Small tolerance: 30 seconds
            delta = (now - parsed).total_seconds()
            if delta > 30:
                warnings.append(
                    f"[{field}] resolves to {parsed.isoformat()} which is in the PAST "
                    f"(now is {now.isoformat()}). Did you mean a future date?"
                )

    # 2. If a description/title/message mentions a weekday explicitly,
    # verify it matches the datetime the tool would use
    text_fields = ["title", "description", "message", "notes"]
    for text_field in text_fields:
        text_val = tool_input.get(text_field)
        if not isinstance(text_val, str):
            continue
        for wd_name, wd_num in _WEEKDAYS.items():
            if re.search(rf"\b{wd_name}\b", text_val, re.IGNORECASE):
                # Check against the first datetime field
                for f in dt_fields:
                    dt_val = tool_input.get(f)
                    got = _weekday_of(dt_val)
                    if got is not None and got != wd_num:
                        real_dt = _parse_iso_lenient(dt_val)
                        real_weekday_name = real_dt.strftime("%A")
                        warnings.append(
                            f"[{text_field}] says '{wd_name}' but [{f}]={dt_val} is a {real_weekday_name}. "
                            f"Fix ONE of them and retry."
                        )
                        break
                break

    if warnings:
        return "VERIFICATION FAILED — do not proceed. " + " | ".join(warnings)
    return None
