import anthropic
import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()


async def web_search(query: str) -> dict:
    """Search the web using DuckDuckGo and summarize results."""
    try:
        results = await _duckduckgo_search(query)
        if not results:
            return {"success": False, "error": "No search results found"}

        # Use Claude to summarize the results
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        results_text = "\n\n".join(
            f"Title: {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}"
            for r in results[:5]
        )

        summary_response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": f"Summarize these search results for the query '{query}'. Be concise and informative. Format for WhatsApp.\n\n{results_text}",
                }
            ],
        )

        summary = summary_response.content[0].text

        return {
            "success": True,
            "query": query,
            "summary": summary,
            "sources": [{"title": r["title"], "url": r["url"]} for r in results[:3]],
        }

    except Exception as e:
        logger.exception("Web search failed", error=str(e))
        return {"success": False, "error": f"Search failed: {str(e)}"}


async def _duckduckgo_search(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo using the HTML endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )

            if resp.status_code != 200:
                return []

            # Parse results from HTML (basic extraction)
            import re

            results = []
            # Find result blocks
            links = re.findall(
                r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>',
                resp.text,
            )
            snippets = re.findall(
                r'<a class="result__snippet"[^>]*>(.*?)</a>',
                resp.text,
            )

            for i, (url, title) in enumerate(links[:max_results]):
                # Clean HTML tags from title
                title = re.sub(r"<[^>]+>", "", title).strip()
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""

                # DuckDuckGo wraps URLs in a redirect
                if "uddg=" in url:
                    from urllib.parse import unquote, parse_qs, urlparse

                    parsed = urlparse(url)
                    params = parse_qs(parsed.query)
                    url = unquote(params.get("uddg", [url])[0])

                results.append({"title": title, "url": url, "snippet": snippet})

            return results

    except Exception as e:
        logger.exception("DuckDuckGo search failed", error=str(e))
        return []
