"""Vision — process user-submitted photos.

Given an image and optional caption, use Claude's vision to detect what
the image contains and route it:
- Receipt → log_expense
- Business card → save_fact + upsert_entity
- Whiteboard / handwritten notes → create_task for each item
- Anything else → describe + let Jarvis decide next step
"""

import base64
from uuid import UUID

import anthropic
import structlog

from app.config import settings
from app.core.claude_helpers import extract_text

logger = structlog.get_logger()

_claude = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
_MODEL = "claude-sonnet-5"

_VISION_SYSTEM = """You are Jarvis's vision assistant. Given an image
(and optional user caption), classify it and extract structured data.

Return STRICT JSON only, one of these shapes:

RECEIPT:
{"kind": "receipt", "amount": <number>, "currency": "SGD|USD|...", "category": "food|transport|shopping|entertainment|bills|health|education|other", "description": "<vendor + brief item>", "date": "YYYY-MM-DD or empty"}

BUSINESS_CARD:
{"kind": "business_card", "name": "<full name>", "role": "<title>", "company": "<company>", "email": "", "phone": "", "notes": ""}

WHITEBOARD_TASKS:
{"kind": "tasks", "tasks": [{"title": "...", "priority": "urgent|high|medium|low"}]}

OTHER:
{"kind": "other", "description": "<one-sentence what this shows>", "suggested_action": "<what Jarvis might do with this>"}

If it's clearly a receipt, use receipt shape even if some fields are blank.
Prefer specific kinds over "other" when you can.
"""


async def analyse_image(image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "") -> dict:
    if not _claude:
        return {"success": False, "error": "claude not configured"}
    encoded = base64.standard_b64encode(image_bytes).decode()
    user_content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": encoded}},
    ]
    if caption:
        user_content.append({"type": "text", "text": f"User caption: {caption}"})
    else:
        user_content.append({"type": "text", "text": "Classify and extract."})

    try:
        msg = _claude.messages.create(
            model=_MODEL,
            max_tokens=1000,
            system=_VISION_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        text = extract_text(msg) or ""
        import json, re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"success": False, "error": "no JSON in vision response", "raw": text}
        data = json.loads(m.group(0))
        return {"success": True, "kind": data.get("kind", "other"), "data": data}
    except Exception as e:
        logger.error("vision_analyse_failed", error=str(e))
        return {"success": False, "error": str(e)}


async def handle_image_message(user_id: UUID, image_bytes: bytes, mime_type: str, caption: str = "") -> str:
    """Process an incoming image end-to-end: classify, take action, return
    a short Telegram-ready reply."""
    from app.services import entities, expenses, facts, tasks as tasks_svc

    analysis = await analyse_image(image_bytes, mime_type, caption)
    if not analysis.get("success"):
        return f"⚠️ Couldn't process image: {analysis.get('error','unknown')}"
    kind = analysis["kind"]
    data = analysis["data"]

    if kind == "receipt":
        amount = data.get("amount")
        if amount is None:
            return f"📷 Looks like a receipt but couldn't read the amount. Try a clearer photo?"
        result = await expenses.log_expense(
            user_id,
            amount=float(amount),
            category=data.get("category", "other"),
            description=data.get("description", "(from photo)"),
            currency=data.get("currency", "SGD"),
            expense_date=data.get("date") or None,
        )
        if result.get("success"):
            return (
                f"💸 Logged: *{data.get('description','expense')}* — "
                f"{data.get('currency','SGD')} {amount} ({data.get('category','other')})"
            )
        return f"⚠️ Vision worked but expense save failed: {result.get('error','')}"

    if kind == "business_card":
        name = data.get("name", "").strip()
        if not name:
            return "📷 Looked like a business card but no name detected."
        # Save as entity + fact
        attributes = {k: v for k, v in data.items() if k not in ("kind", "name") and v}
        ent = await entities.upsert_entity(user_id, name=name, kind="person", attributes=attributes)
        summary = ", ".join(f"{k}: {v}" for k, v in attributes.items() if v)
        await facts.save_fact(user_id, f"{name} — {summary}", category="contact", tags=["business_card"])
        return f"📇 Saved contact: *{name}* ({attributes.get('company','')} · {attributes.get('role','')})"

    if kind == "tasks":
        task_items = data.get("tasks", [])
        created = 0
        for t in task_items[:15]:
            title = t.get("title", "").strip()
            if not title:
                continue
            await tasks_svc.create_task(user_id, title=title, priority=t.get("priority", "medium"))
            created += 1
        return f"📝 Added *{created}* task(s) from the whiteboard."

    # Other
    desc = data.get("description", "")
    suggested = data.get("suggested_action", "")
    return f"📷 {desc}\n💡 {suggested}" if suggested else f"📷 {desc}"
