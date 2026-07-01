"""Cross-domain synthesis — reads every domain at once and returns
advisor-level insight, not a data dump.

Called via the synthesize_state tool.  Uses extended thinking.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings
from app.services import calendar_service, expenses, gmail_service, health, tasks
from app.services import checkin_memory, facts
from app.services.zeri import get_daily_almanac

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

_SYSTEM = """You are Jarvis's cross-domain advisor. Given a complete
snapshot of the user's current state (calendar, tasks, health, expenses,
last few check-ins, Bazi almanac, remembered facts), your job is to
CONNECT THE DOTS. Answer the user's question — or if they only asked for
a state review, deliver an advisor-level read.

RULES:
- Notice patterns across domains that no single service would spot
  ("3 missed tasks + steps down 40% = low-state pattern")
- Do not restate the data. Interpret it.
- Be specific and blunt. No hedging, no "consider".
- End with ≤ 3 concrete moves for the next 24 hours. Numbered.
- Cap at 250 words.
"""


async def _safe(coro):
    try:
        return await coro
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _gather(user_id: UUID) -> str:
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    week_ago = (datetime.now(tz) - timedelta(days=7)).strftime("%Y-%m-%d")

    # Parallel data pull
    cal, todo_tasks, done_tasks, steps, weight, unread, week_exp, yesterday_ci = await asyncio.gather(
        _safe(calendar_service.get_events(user_id, start_date=today)),
        _safe(tasks.list_tasks(user_id, status="todo")),
        _safe(tasks.list_tasks(user_id, status="done")),
        _safe(health.get_health_summary(user_id, metric_type="steps", period="this_week")),
        _safe(health.get_health_summary(user_id, metric_type="weight", period="this_month")),
        _safe(gmail_service.get_unread_count(user_id)),
        _safe(expenses.get_expense_summary(user_id, period="this_week")),
        _safe(checkin_memory.get_yesterday(user_id)),
    )

    almanac = get_daily_almanac(today)
    facts_digest = await facts.load_facts_for_prompt(user_id, limit=40)

    lines = [f"# STATE SNAPSHOT — {today}"]

    if cal.get("events"):
        lines.append(f"\n## Today's calendar ({len(cal['events'])} events)")
        for e in cal["events"][:6]:
            t = e.get("start", "")
            if "T" in t:
                t = t.split("T")[1][:5]
            lines.append(f"  - {t} {e.get('title','')}")
    else:
        lines.append("\n## Today's calendar\n  (empty)")

    if todo_tasks.get("count"):
        lines.append(f"\n## Pending tasks ({todo_tasks['count']})")
        for t in todo_tasks.get("tasks", [])[:8]:
            lines.append(f"  - [{t.get('priority','med')}] {t.get('title','')}")

    if done_tasks.get("count"):
        lines.append(f"\n## Completed this period: {done_tasks['count']}")

    if steps.get("success"):
        lines.append(f"\n## Steps (this week): total={steps.get('total','?')}  avg={steps.get('average','?')}")
    if weight.get("success"):
        lines.append(f"## Weight (this month): latest={weight.get('latest','?')}")

    if unread.get("success"):
        lines.append(f"\n## Unread emails: {unread.get('unread_count', 0)}")

    if week_exp.get("success"):
        lines.append(f"\n## This week's spend: {week_exp.get('total','?')} {week_exp.get('currency','SGD')}")
        cats = week_exp.get("by_category", {})
        if cats:
            lines.append("  by category:")
            for k, v in list(cats.items())[:5]:
                lines.append(f"    - {k}: {v}")

    if yesterday_ci.get("found"):
        lines.append(f"\n## Yesterday's check-in")
        lines.append(f"  win: {yesterday_ci.get('win','')}")
        lines.append(f"  lesson: {yesterday_ci.get('lesson','')}")
        lines.append(f"  today's priority they set: {yesterday_ci.get('priority','')}")

    if almanac.get("success"):
        lines.append(f"\n## Today's Bazi (八字) read")
        lines.append(f"  day pillar: {almanac.get('day_ganzi','')}")
        lines.append(f"  personal net: {almanac.get('personal_net','?')}")
        pf = almanac.get("personal_flags", [])
        if pf:
            lines.append(f"  flags: {'; '.join(f.get('en','') for f in pf[:3])}")

    if facts_digest:
        lines.append(f"\n## Remembered facts\n{facts_digest}")

    return "\n".join(lines)


async def synthesize_state(user_id: UUID, question: str | None = None) -> dict:
    """Returns an advisor-level cross-domain read.

    Args:
        question: optional specific question. If None, returns a
            general state review.
    """
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    snapshot = await _gather(user_id)
    user_prompt = (
        f"{snapshot}\n\n---\n\n"
        + (
            f"User question: {question}\n\nAnswer with cross-domain synthesis."
            if question
            else "No specific question — deliver a state read: what patterns do you see, what should the user do in the next 24h?"
        )
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=8000,
            thinking={"type": "enabled", "budget_tokens": 5000},
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Skip thinking blocks, keep visible text
        text_parts = []
        for b in msg.content:
            if getattr(b, "type", None) == "thinking":
                continue
            if hasattr(b, "text"):
                text_parts.append(b.text)
        text = "\n".join(text_parts).strip()
        return {"success": True, "insight": text}
    except Exception as e:
        logger.error("synthesis_failed", error=str(e))
        return {"success": False, "error": str(e)}
