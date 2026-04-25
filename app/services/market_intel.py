"""Daily market intelligence — surface expanding-market opportunities.

Runs targeted web searches across four categories, aggregates the
results, and asks Claude to synthesise them through the lens of
'find positive-sum expanding markets, avoid zero-sum stagnation'.
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings
from app.services.research import _duckduckgo_search

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-4-20250514"

# Search queries — refreshed with current year/quarter so results stay fresh
def _build_queries() -> dict[str, str]:
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz)
    year = today.year
    month = today.strftime("%B")
    return {
        "tech_breakthroughs": f"major technological breakthroughs {month} {year}",
        "emerging_industries": f"fastest growing emerging industries {year} expanding markets",
        "open_source": f"trending open source projects {year} new launches",
        "business_models": f"new business models {year} platform network effects positive sum",
    }


_SYSTEM_PROMPT = """You are a strategic market analyst writing a daily intelligence brief for an entrepreneur.

The reader's strategic frame is FIXED:
- They want to position in *expanding markets* where the pie is growing
- They prefer *cooperation and positive-sum dynamics* over zero-sum competition
- They avoid stagnant industries where survival means taking from rivals
- They look for leverage: small effort → large payoff because the market itself is rising

Your job each day:
1. Filter the raw search snippets — discard hype, vendor PR, obvious clickbait
2. For each of the four categories, identify the 1-2 most strategically interesting items
3. For each item, in ONE sentence: what's the positive-sum angle? (network effect? compounding ecosystem? rising tide?)
4. End with a "Today's positioning thought" — one concrete, novel angle they could exploit this week

Format for Telegram (Markdown):
- Use *bold* sparingly for category headers
- Bullet points
- Be terse — no fluff, no disclaimers, no "in summary"
- Total under 500 words
- If a category had nothing useful, say so briefly and move on
"""


async def _search_one(query: str) -> list[dict]:
    try:
        return await _duckduckgo_search(query, max_results=6)
    except Exception as e:
        logger.error("market_intel_search_failed", query=query, error=str(e))
        return []


async def get_daily_market_intel() -> dict:
    """Run searches, synthesise via Claude, return brief."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    queries = _build_queries()
    # Run all four searches in parallel
    search_results = await asyncio.gather(
        *[_search_one(q) for q in queries.values()]
    )
    categorised = dict(zip(queries.keys(), search_results))

    # Build one big prompt with all snippets
    sections = []
    for cat, results in categorised.items():
        if not results:
            sections.append(f"### {cat}\n(no results)\n")
            continue
        body = "\n".join(
            f"- {r['title']}\n  {r.get('snippet','')[:300]}\n  {r['url']}"
            for r in results[:5]
        )
        sections.append(f"### {cat}\n{body}\n")

    raw = "\n".join(sections)

    user_prompt = (
        f"Today's date: {datetime.now(ZoneInfo(settings.default_timezone)).strftime('%A, %Y-%m-%d')}\n\n"
        "Raw search results follow. Synthesise the daily brief.\n\n"
        f"{raw}"
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip() if msg.content else ""
        return {
            "success": True,
            "brief": text,
            "categories_searched": list(queries.keys()),
            "total_sources": sum(len(r) for r in search_results),
        }
    except Exception as e:
        logger.error("market_intel_synthesis_failed", error=str(e))
        return {"success": False, "error": str(e)}


def format_for_telegram(data: dict) -> str:
    if not data.get("success"):
        return f"⚠️ Market intel failed: {data.get('error', 'unknown error')}"
    today = datetime.now(ZoneInfo(settings.default_timezone)).strftime("%a %d %b")
    header = f"📈 *Market Intel — {today}*\n_Expanding markets · positive-sum · leverage_\n\n"
    return header + data["brief"]
