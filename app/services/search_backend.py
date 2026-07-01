"""Pluggable web search backend.

Preference order (best → fallback):
1. Exa    — if EXA_API_KEY set (semantic search, best quality)
2. Brave  — if BRAVE_SEARCH_API_KEY set (fast, good coverage)
3. DuckDuckGo HTML scraping — always available fallback
"""

from typing import Any

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()


def active_backend() -> str:
    if settings.exa_api_key:
        return "exa"
    if settings.brave_search_api_key:
        return "brave"
    return "duckduckgo"


async def search(query: str, max_results: int = 6) -> list[dict[str, Any]]:
    """Return a list of {title, url, snippet} dicts from the best backend."""
    backend = active_backend()
    try:
        if backend == "exa":
            return await _exa_search(query, max_results)
        if backend == "brave":
            return await _brave_search(query, max_results)
        return await _duckduckgo_search(query, max_results)
    except Exception as e:
        logger.error("search_backend_failed", backend=backend, error=str(e))
        # Fall through to DuckDuckGo if a paid backend errors
        if backend != "duckduckgo":
            try:
                return await _duckduckgo_search(query, max_results)
            except Exception:
                pass
        return []


async def _exa_search(query: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": settings.exa_api_key, "Content-Type": "application/json"},
            json={
                "query": query,
                "numResults": max_results,
                "contents": {"text": {"maxCharacters": 500}},
                "useAutoprompt": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("exa_bad_status", status=resp.status_code, body=resp.text[:200])
            return []
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": (r.get("text") or "")[:500],
            }
            for r in data.get("results", [])[:max_results]
        ]


async def _brave_search(query: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "X-Subscription-Token": settings.brave_search_api_key,
                "Accept": "application/json",
            },
            params={"q": query, "count": max_results},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("brave_bad_status", status=resp.status_code, body=resp.text[:200])
            return []
        data = resp.json()
        results = data.get("web", {}).get("results", [])[:max_results]
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
            for r in results
        ]


async def _duckduckgo_search(query: str, max_results: int) -> list[dict]:
    # Delegate to existing scraper
    from app.services.research import _duckduckgo_search as _ddg
    return await _ddg(query, max_results)
