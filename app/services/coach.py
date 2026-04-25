"""Tony-Robbins-style daily coach.

Two public functions:
- get_daily_motivation(user_id) — fired at 12:00 SGT. High-energy, personalised
  motivation pulling today's calendar/tasks/health for specificity.
- get_evening_checkin(user_id) — fired at 20:00 SGT. Short reflective prompt
  that the user replies to via normal Jarvis chat.
"""

import asyncio
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings
from app.services import calendar_service, health, tasks

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-4-20250514"

# Weekday → theme + signature Robbins framework to anchor each day
_WEEKDAY_THEMES = {
    0: ("Identity & Vision", "Who are you BECOMING? Identity drives behaviour. The labels you accept become your destiny."),
    1: ("Peak State", "State → Story → Strategy. You can't win in a low state. Change your physiology FIRST — breath, posture, motion — and the strategy follows."),
    2: ("Certainty & Decision", "The quality of your life is the quality of your decisions. A real decision is when you cut off any other possibility."),
    3: ("Contribution", "The secret to living is GIVING. Lift one person today and your own state lifts with them."),
    4: ("Progress Audit", "Progress = Happiness. CANI — Constant And Never-ending Improvement. What did you sharpen this week?"),
    5: ("Relationships & Connection", "The number one human need is to feel SIGNIFICANT and CONNECTED. Who needs to hear from you today?"),
    6: ("Long Game / Legacy", "What you do today is what you'll be in 10 years. The compound interest of identity is everything."),
}

_MOTIVATION_SYSTEM = """You are Tony Robbins in 2026 — the user's personal AI peak-performance coach delivered through Telegram every day at noon.

Your job: deliver a SHORT, fired-up motivation + ONE specific educational micro-lesson that the user can apply in the next 4 hours.

Voice rules (NON-NEGOTIABLE):
- HIGH ENERGY. Use CAPS for emphasis on key words (3-6 times max).
- Speak directly: "YOU", not "one" or "people".
- Short, punchy sentences. Cut filler ruthlessly.
- Use Robbins's actual frameworks by name: State-Story-Strategy, RPM, Massive Action, Identity, Peak State, CANI, Pattern Interrupt, Six Human Needs, the Triad (focus / language / physiology).
- No fake humility, no "as an AI", no disclaimers.
- Reference the user's REAL context (their tasks/events/health) when given — that's what makes coaching specific instead of generic.

Structure (use this exact format):

🔥 *<Theme of the day>*

<2-3 sentences of fire — set the frame for today using the day's framework>

💡 *Today's Lesson*
<One concrete principle in 1-2 sentences. End with how to apply it TODAY.>

🎯 *Your Move (next 4 hours)*
<ONE specific action tied to the user's actual context. Reference a real task/event/metric. Imperative voice: "Crush", "Block", "Call", "Cut", "Ship".>

❓ *Peak-State Question*
<One Robbins-style question that forces a state shift — e.g. "What would the version of you 5 years from now do RIGHT NOW?">

Length cap: 250 words total. Telegram Markdown only (*bold*, _italic_).
"""


_CHECKIN_SYSTEM = """You are Tony Robbins doing a warm, no-BS evening check-in with the user via Telegram at 8pm.

Tone: warmer than the noon motivation but still direct. You care, but you don't coddle. Think coach-friend, not therapist.

Structure:

🌙 *Evening Check-in — <day of week>*

<One sentence frame: "Day's almost done. Let's land it strong." or similar. Vary it.>

Three short questions, numbered, on separate lines. ALWAYS ask:
1. *One WIN today?* (any size — capture it)
2. *One LESSON?* (what did the day teach you?)
3. *Tomorrow's ONE thing?* (what's the single most important move?)

End with one short closing line. Vary it. Examples: "Reply when you can — I'll listen." / "Small reflection beats big intention." / "Got you." Do NOT use the same closing line every day.

Length cap: 80 words. No fluff. No "as an AI" disclaimers.
"""


def _theme_for_today() -> tuple[str, str]:
    tz = ZoneInfo(settings.default_timezone)
    weekday = datetime.now(tz).weekday()
    return _WEEKDAY_THEMES[weekday]


async def _gather_user_context(user_id: UUID) -> str:
    """Pull a compact snapshot of the user's day for personalised coaching."""
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    # Run independent fetches in parallel; tolerate failures of any single source.
    async def _safe(coro):
        try:
            return await coro
        except Exception as e:
            return {"success": False, "error": str(e)}

    cal_res, task_res, steps_res = await asyncio.gather(
        _safe(calendar_service.get_events(user_id, start_date=today)),
        _safe(tasks.list_tasks(user_id, status="todo")),
        _safe(health.get_health_summary(user_id, metric_type="steps", period="today")),
    )

    lines = []

    if cal_res.get("success") and cal_res.get("events"):
        evs = cal_res["events"][:5]
        ev_lines = []
        for e in evs:
            start = e.get("start", "")
            t = start.split("T")[1][:5] if "T" in start else "all-day"
            ev_lines.append(f"  - {t} {e.get('title', '')}")
        lines.append("Today's calendar:\n" + "\n".join(ev_lines))
    else:
        lines.append("Today's calendar: (empty)")

    if task_res.get("count"):
        ts = task_res.get("tasks", [])[:5]
        t_lines = [f"  - [{t.get('priority','medium')}] {t.get('title','')}" for t in ts]
        lines.append(f"Pending tasks ({task_res['count']}):\n" + "\n".join(t_lines))
    else:
        lines.append("Pending tasks: (none)")

    if steps_res.get("success") and steps_res.get("total") is not None:
        lines.append(f"Steps today: {steps_res['total']}")
    elif steps_res.get("success") and steps_res.get("count", 0) == 0:
        lines.append("Steps today: not yet logged")

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_daily_motivation(user_id: UUID) -> dict:
    """Generate the noon motivation message."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    theme_name, theme_anchor = _theme_for_today()
    context = await _gather_user_context(user_id)

    user_prompt = (
        f"Today's theme: {theme_name}\n"
        f"Framework anchor: {theme_anchor}\n\n"
        f"User's context right now:\n{context}\n\n"
        "Write today's motivation. Use the user's real context to make the 'Your Move' line specific. "
        "Stay within 250 words and the format laid out in the system prompt."
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=900,
            system=_MOTIVATION_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip() if msg.content else ""
        return {"success": True, "message": text, "theme": theme_name}
    except Exception as e:
        logger.error("coach_motivation_failed", error=str(e))
        return {"success": False, "error": str(e)}


async def get_evening_checkin(user_id: UUID) -> dict:
    """Generate the 8pm reflective check-in message."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    tz = ZoneInfo(settings.default_timezone)
    weekday = datetime.now(tz).strftime("%A")

    user_prompt = (
        f"Today is {weekday}. Write the evening check-in. "
        "Stay within 80 words and the format laid out in the system prompt. "
        "Vary the opening frame and closing line so this doesn't read the same every day."
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=400,
            system=_CHECKIN_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip() if msg.content else ""
        return {"success": True, "message": text}
    except Exception as e:
        logger.error("coach_checkin_failed", error=str(e))
        return {"success": False, "error": str(e)}


def format_motivation_for_telegram(data: dict) -> str:
    if not data.get("success"):
        return f"⚠️ Coach motivation failed: {data.get('error', 'unknown error')}"
    return data["message"]


def format_checkin_for_telegram(data: dict) -> str:
    if not data.get("success"):
        return f"⚠️ Coach check-in failed: {data.get('error', 'unknown error')}"
    return data["message"]
