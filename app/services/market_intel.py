"""Daily market intelligence — news the owner can act on for their own ventures.

Runs one web search per venture, aggregates the results, and asks Claude
to turn them into a short brief: what happened, why it matters to that
venture, and one action to start today.
"""

import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings
from app.services.search_backend import active_backend, search as backend_search

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"


# ---------------------------------------------------------------------------
# The owner's ventures, priority first. Edit this when the portfolio changes
# (keep _SYSTEM_PROMPT's venture notes in step). Each venture rotates through
# its search angles day by day, so the same query doesn't return the same
# evergreen results every morning.
# ---------------------------------------------------------------------------

_VENTURES = {
    "🤖 AI automation consulting": [
        "Singapore SMEs adopting AI automation",
        "Singapore government AI grant for SMEs Enterprise Singapore",
        "small business AI automation consultant pricing first clients",
    ],
    "🍷 Sea-aged wine": [
        "underwater sea-aged wine buyers market",
        "Singapore luxury wine corporate gifting demand",
        "sea-aged wine producer launch pricing",
    ],
    "🦪 Oyster farm": [
        "Singapore aquaculture oyster farming",
        "oyster farming Southeast Asia prices demand",
        "Singapore Food Agency aquaculture farm grants",
    ],
    "📰 CÈ media": [
        "newsletter growth tactics Instagram followers",
        "Southeast Asia business media newsletter audience growth",
    ],
    "🌳 Durian": [
        "durian export prices Malaysia China",
        "Musang King durian season supply prices",
    ],
}


def _build_queries(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(ZoneInfo(settings.default_timezone))
    day = now.timetuple().tm_yday
    return {
        venture: f"{angles[day % len(angles)]} {now:%B %Y}"
        for venture, angles in _VENTURES.items()
    }


_SYSTEM_PROMPT = """You write a daily intel brief for one Singapore-based founder. Its only job is to help them move their own businesses forward this week. Industry news they cannot act on is noise: drop it.

Their ventures, and what each needs right now:
- AI automation consulting: pre-revenue. Needs first paying clients: who is buying AI help, what they pay, grants that fund it, where those buyers gather.
- Sea-aged wine: pre-revenue. Needs buyers: gifting, restaurants, collectors, pricing, Singapore import and licensing rules.
- Oyster farm: longer-term. Needs farming conditions, grants, food-security policy, prices, disease news.
- CÈ media (ce-media.asia, a Southeast Asia business publication): building audience toward 10,000 Instagram followers. Needs growth tactics working now, partner and sponsor openings.
- Durian: sourcing and content sites. Needs season, supply and export price moves.

Their stuck point is customer acquisition for AI consulting and sea-aged wine. Weight the brief toward those two. Their frame: expanding markets and positive-sum partnerships over zero-sum fights.

Rules (NON-NEGOTIABLE):
- Use only the search results given. Never invent a company, number or URL.
- Every item cites its full URL inline in parentheses. Never write "linked above", "see link" or "click here".
- Prefer items from the last 14 days. Skip evergreen explainers, vendor PR and hype roundups.
- If the user message has a RECENT BRIEFS RECAP, do not repeat those items.
- If a venture has nothing useful today, write the venture header and one line: "Nothing useful today." Silence beats filler.
- Do NOT write a title, date or header line. Start straight with the first venture.

Format for Telegram (Markdown: *bold*, - bullets), under 450 words total:

*<venture name exactly as given>*
- <what happened, one sentence> (<full url>)
- *For you:* <why it matters to this venture, one sentence>
- *Do:* <one action they can start today in under 30 minutes: DM, draft, list, sign up, price, call>

At most 2 items per venture. Then end with:

*🎯 Top move today*
<the single highest-leverage action across all ventures, one or two sentences, and why it beats the others>
"""


async def _search_one(query: str) -> list[dict]:
    try:
        return await backend_search(query, max_results=6)
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
    # Run all searches in parallel
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
        f"\n\nRECENT BRIEFS RECAP (do NOT repeat these items):\n\n{recap}\n"
        if recap
        else "\n(No recent briefs on file — fresh start.)\n"
    )

    user_prompt = (
        f"Today's date: {datetime.now(ZoneInfo(settings.default_timezone)).strftime('%A, %Y-%m-%d')}\n"
        f"{recap_block}"
        "Raw search results follow, one section per venture. Write the daily brief.\n\n"
        f"{raw}"
    )

    try:
        # Sonnet 5 always thinks; thinking shares max_tokens, so a small
        # budget cuts the brief off mid-sentence. Same settings as the
        # light path in app/core/claude_client.py.
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if msg.stop_reason == "max_tokens":
            logger.warning("market_intel_truncated")
        text = extract_text(msg) or ""
        text = _strip_leading_header(text)
        # Post-process: strip any sneaky "linked above" phantom phrases
        text = _strip_phantom_links(text)
        return {
            "success": True,
            "brief": text,
            "categories_searched": list(queries.keys()),
            "total_sources": sum(len(r) for r in search_results),
        }
    except Exception as e:
        logger.error("market_intel_synthesis_failed", error=str(e))
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Post-processing — final defence against phantom-link phrasing and a
# header copied from the recap (format_for_telegram adds the real one)
# ---------------------------------------------------------------------------

from app.core.claude_helpers import extract_text

_PHANTOM_LINK_PATTERNS = [
    re.compile(r"\s*\(linked above\)", re.IGNORECASE),
    re.compile(r"\s*\(see (?:link|above|linked)\)", re.IGNORECASE),
    re.compile(r"\s*\(link(?:ed)? (?:above|earlier|previously)\)", re.IGNORECASE),
    re.compile(r"\bas (?:linked|referenced) above\b", re.IGNORECASE),
    re.compile(r"\bclick here\b", re.IGNORECASE),
]

# "📈 *Market Intel — Sun 13 Sep*" plus an optional "_focus line_" under it
_LEADING_HEADER = re.compile(r"\A\s*📈[^\n]*\n(?:[ \t]*_[^\n]*_[ \t]*\n)?\s*")


def _strip_phantom_links(text: str) -> str:
    for pat in _PHANTOM_LINK_PATTERNS:
        text = pat.sub("", text)
    return text


def _strip_leading_header(text: str) -> str:
    return _LEADING_HEADER.sub("", text)


def format_for_telegram(data: dict) -> str:
    if not data.get("success"):
        return f"⚠️ Market intel failed: {data.get('error', 'unknown error')}"
    today = datetime.now(ZoneInfo(settings.default_timezone)).strftime("%a %d %b")
    header = f"📈 *Market Intel — {today}*\n_Your ventures · customers first_\n\n"
    return header + data["brief"]
