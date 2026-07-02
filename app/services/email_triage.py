"""Proactive email triage — reads last 24h of unread, groups by
importance, drafts replies for reply-needed items.

Called each morning as part of the daily briefing so the user starts
the day with inbox pre-processed instead of a wall of 200 unread.
"""

import asyncio
from uuid import UUID

import anthropic
import structlog

from app.config import settings
from app.services import gmail_service
from app.core.claude_helpers import extract_text

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

_TRIAGE_SYSTEM = """You are Jarvis's email triage assistant. Given a batch of
unread emails, classify each into ONE bucket and, for reply-needed items,
draft a short reply (≤ 80 words, matches user's usual tone: friendly,
direct, Singapore English).

Buckets:
- REPLY_NEEDED — a human needs to respond; matters this week
- FYI — informational, worth reading but no response
- NOISE — newsletters, promo, notifications, auto-generated; safe to skip

Return STRICT JSON only:
{
  "items": [
    {
      "id": "<email_id>",
      "from": "<sender>",
      "subject": "<subject>",
      "bucket": "REPLY_NEEDED|FYI|NOISE",
      "one_line": "<one-line summary>",
      "draft_reply": "<draft body if REPLY_NEEDED, else empty string>"
    }
  ]
}

Rules:
- If unsure, prefer FYI over REPLY_NEEDED
- draft_reply must be plain text, no signature, no salutation greeting
- one_line max 15 words
"""


async def _fetch_recent_unread(user_id: UUID, max_results: int = 20) -> list[dict]:
    """Fetch unread emails from the last 24 hours."""
    try:
        # Gmail search: unread in last day
        result = await gmail_service.search_emails(user_id, query="is:unread newer_than:1d", max_results=max_results)
        if not result.get("success"):
            return []
        emails = result.get("emails", [])
        # Read full body for each (cap to keep prompt small)
        detailed = []
        for e in emails[:max_results]:
            full = await gmail_service.read_email(user_id, e.get("email_id") or e.get("id"))
            if full.get("success"):
                detailed.append({
                    "id": e.get("email_id") or e.get("id"),
                    "from": full.get("from") or e.get("from", ""),
                    "subject": full.get("subject") or e.get("subject", ""),
                    "body": (full.get("body") or "")[:1200],
                })
        return detailed
    except Exception as e:
        logger.error("email_triage_fetch_failed", error=str(e))
        return []


async def triage_daily(user_id: UUID) -> dict:
    """Return the morning triage summary."""
    if not _claude:
        return {"success": False, "error": "claude not configured"}

    emails = await _fetch_recent_unread(user_id)
    if not emails:
        return {"success": True, "total": 0, "buckets": {"REPLY_NEEDED": [], "FYI": [], "NOISE": []}}

    # Build prompt
    email_dump = "\n\n---\n\n".join(
        f"ID: {e['id']}\nFrom: {e['from']}\nSubject: {e['subject']}\nBody: {e['body']}"
        for e in emails
    )
    prompt = f"Triage these {len(emails)} unread emails from the last 24 hours:\n\n{email_dump}"

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=3000,
            system=_TRIAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = extract_text(msg) or ""
        # Extract JSON
        import json, re
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            return {"success": False, "error": "no JSON in triage response"}
        data = json.loads(json_match.group(0))
        items = data.get("items", [])
        buckets = {"REPLY_NEEDED": [], "FYI": [], "NOISE": []}
        for it in items:
            b = it.get("bucket", "FYI")
            if b not in buckets:
                b = "FYI"
            buckets[b].append(it)
        return {"success": True, "total": len(items), "buckets": buckets}
    except Exception as e:
        logger.error("email_triage_failed", error=str(e))
        return {"success": False, "error": str(e)}


def format_triage_for_briefing(data: dict) -> str:
    if not data.get("success"):
        return ""
    total = data.get("total", 0)
    if total == 0:
        return ""
    buckets = data.get("buckets", {})
    reply = buckets.get("REPLY_NEEDED", [])
    fyi = buckets.get("FYI", [])
    noise = buckets.get("NOISE", [])

    lines = [f"📧 *Overnight ({total} new)*"]
    if reply:
        lines.append(f"  🔴 *{len(reply)} need reply* — drafts ready:")
        for i, it in enumerate(reply[:5]):
            letter = chr(ord("a") + i)
            frm = (it.get("from", "") or "").split("<")[0].strip()[:35]
            lines.append(f"    {letter}) {frm}: _{it.get('one_line','')[:80]}_")
    if fyi:
        lines.append(f"  🟡 *{len(fyi)} FYI:*")
        for it in fyi[:5]:
            frm = (it.get("from", "") or "").split("<")[0].strip()[:30]
            lines.append(f"    • {frm}: {it.get('one_line','')[:80]}")
    if noise:
        lines.append(f"  ⚪ {len(noise)} noise (skip)")
    return "\n".join(lines)
