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


# ---------------------------------------------------------------------------
# Daily theme rotation — keeps query angles fresh across the week so two
# briefs in the same week don't lean on the same broad keywords.
# ---------------------------------------------------------------------------

_THEME_ROTATION = {
    0: {  # Monday — AI / ML
        "tech_breakthroughs": "AI machine learning breakthroughs this week",
        "emerging_industries": "AI-native startups expanding markets",
        "open_source": "trending AI ML open source projects this week",
        "business_models": "AI agent business models usage-based pricing",
    },
    1: {  # Tuesday — Biotech / Health / Longevity
        "tech_breakthroughs": "biotech breakthroughs longevity this week",
        "emerging_industries": "longevity health diagnostics emerging market",
        "open_source": "open source bioinformatics health new projects this month",
        "business_models": "personalised health subscription business models",
    },
    2: {  # Wednesday — Climate / Energy / Hardware
        "tech_breakthroughs": "climate tech battery solar breakthrough this week",
        "emerging_industries": "clean energy emerging markets growth",
        "open_source": "open source climate hardware electronics new launches",
        "business_models": "energy as a service climate fintech business models",
    },
    3: {  # Thursday — Fintech / Web3 / Capital
        "tech_breakthroughs": "fintech infrastructure stablecoin breakthrough this week",
        "emerging_industries": "embedded finance emerging market southeast asia",
        "open_source": "open source fintech defi tooling launched this month",
        "business_models": "platform fintech new revenue models 2026",
    },
    4: {  # Friday — Robotics / Manufacturing / Supply
        "tech_breakthroughs": "robotics automation breakthrough this week",
        "emerging_industries": "advanced manufacturing supply chain emerging growth",
        "open_source": "open source robotics simulation tools recent",
        "business_models": "robots as a service manufacturing business models",
    },
    5: {  # Saturday — Creator / Community / Software
        "tech_breakthroughs": "developer tooling breakthrough recent week",
        "emerging_industries": "creator economy community platforms growth",
        "open_source": "trending github repos this week",
        "business_models": "community-led growth network effect business models",
    },
    6: {  # Sunday — Deep tech wildcard
        "tech_breakthroughs": "deep tech space quantum breakthrough this week",
        "emerging_industries": "frontier industries growth 2026",
        "open_source": "underrated open source projects gaining traction this month",
        "business_models": "novel marketplace business models 2026",
    },
}


def _build_queries() -> dict[str, str]:
    tz = ZoneInfo(settings.default_timezone)
    weekday = datetime.now(tz).weekday()
    return _THEME_ROTATION[weekday]


def _theme_label() -> str:
    tz = ZoneInfo(settings.default_timezone)
    weekday = datetime.now(tz).weekday()
    labels = {
        0: "AI / ML focus",
        1: "Biotech & Longevity focus",
        2: "Climate & Energy focus",
        3: "Fintech & Capital focus",
        4: "Robotics & Manufacturing focus",
        5: "Creator & Community focus",
        6: "Deep Tech wildcard",
    }
    return labels[weekday]


_SYSTEM_PROMPT = """You are a strategic market analyst writing a daily intelligence brief for an entrepreneur based in Singapore.

The reader's strategic frame is FIXED:
- They want to position in *expanding markets* where the pie is growing
- They prefer *cooperation and positive-sum dynamics* over zero-sum competition
- They avoid stagnant industries where survival means taking from rivals
- They look for leverage: small effort → large payoff because the market itself is rising

CRITICAL link rules (NON-NEGOTIABLE):
- NEVER write "linked above", "the report linked", "see link", "click here", "as referenced", or any phrase that implies a clickable link without showing the actual URL.
- When you mention a source, ALWAYS paste its full URL inline in parentheses right after the title. e.g. "the McKinsey high-growth arenas report (https://www.mckinsey.com/...)"
- In the "What to do next" section, every action that says "read X" / "watch Y" / "join Z" MUST include the actual URL inline. If you don't have a URL for a source, do NOT recommend reading it — pick a different action.
- If the search snippets don't have a relevant URL for an action, write the action without referencing a specific source.

Freshness rules:
- Prefer items dated within the last 14 days. Skip evergreen-looking content.
- Skip items the user has likely seen before (e.g. mainstream news that's been covered for weeks). Look for the second-tier signal — niche newsletters, GitHub trending, technical blogs, regional news.

Repetition guard:
- If the user message includes a "RECENT BRIEFS RECAP" section, treat those topics, companies, and frameworks as USED. Do NOT recycle them. Pick different angles, different companies, different open-source projects.

Your job each day:
1. Filter the raw search snippets — discard hype, vendor PR, obvious clickbait.
2. For each of the four categories, identify the 1-2 most strategically interesting items NOT seen in the recent recap.
3. For each item, in ONE sentence: what's the positive-sum angle? (network effect? compounding ecosystem? rising tide?)
4. For each item, add a "🇸🇬 Like this" line: a concrete simple Singapore-context analogy a 12-year-old would understand. Use familiar SG brands/places/food (Grab, hawker, MRT, FairPrice, HDB, BTO, kopitiam, durians, bubble tea queues, etc.). One short sentence.
5. End with a "🎯 Today's positioning thought" — one concrete, novel angle they could exploit this week, with a tiny SG analogy if it helps.
6. After the positioning thought, add a "✅ What to do next" section listing 2-3 concrete actions for THIS WEEK. Each action:
   - Specific verb (read, draft, message, sign up, prototype, test, list, join, buy, watch, DM)
   - Small enough to start today (under 2h for the first step)
   - When referencing a source, the actual URL must appear inline in parentheses
   - Tied to an item above

Format for Telegram (Markdown):
- *Bold* category headers, bullet points
- Total under 700 words
- If a category had nothing useful, say so briefly and move on
"""


async def _search_one(query: str) -> list[dict]:
    try:
        return await _duckduckgo_search(query, max_results=6)
    except Exception as e:
        logger.error("market_intel_search_failed", query=query, error=str(e))
        return []


async def _recent_briefs_recap() -> str:
    """Pull the last few market-intel briefs from conversation memory so we
    can tell Claude what topics to avoid recycling."""
    try:
        from app.core.memory import load_conversation_history
        from app.db.database import async_session
        from app.db.repositories import UserRepository

        if not settings.owner_chat_id:
            return ""
        async with async_session() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_or_create(settings.owner_chat_id)
        history = await load_conversation_history(user.id, limit=80)
        # Keep only recent briefs (assistant messages starting with the
        # market-intel header).
        briefs = [
            m["content"]
            for m in history
            if m.get("role") == "assistant"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("📈 *Market Intel")
        ][-5:]
        if not briefs:
            return ""
        # Compress each to its first ~600 chars so the recap stays small
        compact = "\n\n---\n\n".join(b[:600] for b in briefs)
        return compact
    except Exception as e:
        logger.warning("recent_briefs_lookup_failed", error=str(e))
        return ""


async def get_daily_market_intel() -> dict:
    """Run searches, synthesise via Claude, return brief."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    queries = _build_queries()
    # Run all four searches in parallel
    search_results, recap = await asyncio.gather(
        asyncio.gather(*[_search_one(q) for q in queries.values()]),
        _recent_briefs_recap(),
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

    recap_block = (
        f"\n\nRECENT BRIEFS RECAP (do NOT recycle these topics, companies, "
        f"or frameworks — pick different ones):\n\n{recap}\n"
        if recap
        else "\n(No recent briefs on file — fresh start.)\n"
    )

    user_prompt = (
        f"Today's date: {datetime.now(ZoneInfo(settings.default_timezone)).strftime('%A, %Y-%m-%d')}\n"
        f"Today's weekly theme: {_theme_label()}\n"
        f"{recap_block}"
        "Raw search results follow. Synthesise the daily brief.\n\n"
        f"{raw}"
    )

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=2000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text.strip() if msg.content else ""
        # Post-process: strip any sneaky "linked above" phantom phrases
        text = _strip_phantom_links(text)
        return {
            "success": True,
            "brief": text,
            "theme": _theme_label(),
            "categories_searched": list(queries.keys()),
            "total_sources": sum(len(r) for r in search_results),
        }
    except Exception as e:
        logger.error("market_intel_synthesis_failed", error=str(e))
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Post-processing — final defence against phantom-link phrasing
# ---------------------------------------------------------------------------

import re as _re

_PHANTOM_LINK_PATTERNS = [
    _re.compile(r"\s*\(linked above\)", _re.IGNORECASE),
    _re.compile(r"\s*\(see (?:link|above|linked)\)", _re.IGNORECASE),
    _re.compile(r"\s*\(link(?:ed)? (?:above|earlier|previously)\)", _re.IGNORECASE),
    _re.compile(r"\bas (?:linked|referenced) above\b", _re.IGNORECASE),
    _re.compile(r"\bclick here\b", _re.IGNORECASE),
]


def _strip_phantom_links(text: str) -> str:
    for pat in _PHANTOM_LINK_PATTERNS:
        text = pat.sub("", text)
    return text


def format_for_telegram(data: dict) -> str:
    if not data.get("success"):
        return f"⚠️ Market intel failed: {data.get('error', 'unknown error')}"
    today = datetime.now(ZoneInfo(settings.default_timezone)).strftime("%a %d %b")
    theme = data.get("theme", "")
    header = f"📈 *Market Intel — {today}*\n_{theme} · positive-sum · leverage_\n\n"
    return header + data["brief"]
