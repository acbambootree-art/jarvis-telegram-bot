"""Weekly retrospective — Sunday 21:00 SGT.

Pulls last 7 days across every domain, sends to Claude with extended
thinking, delivers a 5-line insight-driven retro.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings
from app.services import checkin_memory, expenses, health, tasks

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

_SYSTEM = """You are Jarvis's weekly retro coach. Given a 7-day snapshot
of the user's data across all domains, deliver a SHORT retrospective in
this exact format:

📅 *Week in review — <date range>*

🎯 *Pattern:* <1 sentence — the single most important pattern you see>

✅ *Wins:* <1-2 concrete wins from tasks/check-ins>

⚠️ *Drift:* <1 pattern to correct — priorities dropped, streaks broken,
           expense trend, health decline>

🚀 *Next week's ONE thing:* <a single crisp priority tied to the pattern>

Rules:
- Cap total at 150 words
- No hedging, no "consider". Be direct.
- Reference actual numbers when useful (e.g. "steps down 32%")
- If a domain has no data, skip it silently. Don't explain absences.
"""


async def _safe(coro):
    try:
        return await coro
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _gather_week(user_id: UUID) -> str:
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz)
    week_start = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    today_s = today.strftime("%Y-%m-%d")

    done_tasks, todo_tasks, steps, weight, week_exp = await asyncio.gather(
        _safe(tasks.list_tasks(user_id, status="done")),
        _safe(tasks.list_tasks(user_id, status="todo")),
        _safe(health.get_health_summary(user_id, metric_type="steps", period="this_week")),
        _safe(health.get_health_summary(user_id, metric_type="weight", period="this_month")),
        _safe(expenses.get_expense_summary(user_id, period="this_week")),
    )

    # Last 7 check-ins
    from app.db.database import async_session
    from app.models.models import CheckinResponse
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(CheckinResponse)
            .where(CheckinResponse.user_id == user_id)
            .where(CheckinResponse.reply_date >= week_start)
            .order_by(CheckinResponse.reply_date.asc())
        )
        checkins = result.scalars().all()

    lines = [f"# WEEK SNAPSHOT — {week_start} → {today_s}"]

    if done_tasks.get("count"):
        lines.append(f"\n## Completed tasks this period: {done_tasks['count']}")
        for t in done_tasks.get("tasks", [])[:10]:
            lines.append(f"  - {t.get('title','')}")

    if todo_tasks.get("count"):
        lines.append(f"\n## Still pending: {todo_tasks['count']}")
        for t in todo_tasks.get("tasks", [])[:5]:
            lines.append(f"  - [{t.get('priority','med')}] {t.get('title','')}")

    if steps.get("success"):
        lines.append(f"\n## Steps: total={steps.get('total','?')}  daily avg={steps.get('average','?')}")
    if weight.get("success"):
        lines.append(f"## Weight: latest={weight.get('latest','?')} change={weight.get('change','?')}")

    if week_exp.get("success"):
        lines.append(f"\n## This week's spend: {week_exp.get('total','?')} {week_exp.get('currency','SGD')}")
        for k, v in list((week_exp.get('by_category') or {}).items())[:5]:
            lines.append(f"    {k}: {v}")

    if checkins:
        lines.append(f"\n## Check-in streak: {len(checkins)} of 7 days")
        for c in checkins[-4:]:
            lines.append(f"  {c.reply_date}: win={c.win[:60] if c.win else ''}  priority={c.priority[:60] if c.priority else ''}")

    return "\n".join(lines)


async def generate_weekly_retro(user_id: UUID) -> dict:
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    snapshot = await _gather_week(user_id)
    prompt = f"{snapshot}\n\nDeliver the weekly retro now."

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=6000,
            thinking={"type": "enabled", "budget_tokens": 4000},
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text_parts = []
        for b in msg.content:
            if getattr(b, "type", None) == "thinking":
                continue
            if hasattr(b, "text"):
                text_parts.append(b.text)
        return {"success": True, "retro": "\n".join(text_parts).strip()}
    except Exception as e:
        logger.error("weekly_retro_failed", error=str(e))
        return {"success": False, "error": str(e)}
