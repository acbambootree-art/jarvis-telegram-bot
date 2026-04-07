import asyncio
import structlog
from fastapi import APIRouter, Request, Response

from app.config import settings
from app.core.router import process_message
from app.services.telegram import telegram_service

logger = structlog.get_logger()
router = APIRouter()


@router.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming Telegram updates."""
    # Verify secret token
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if settings.telegram_webhook_secret and not telegram_service.verify_secret_token(secret_token):
        logger.warning("Invalid Telegram webhook secret")
        return Response(status_code=401)

    body = await request.json()
    message = telegram_service.parse_update(body)

    if not message:
        return {"ok": True}

    # Only process messages from the owner (if configured)
    if settings.owner_chat_id and message["from"] != settings.owner_chat_id:
        logger.info("Ignoring message from non-owner", sender=message["from"])
        return {"ok": True}

    logger.info("Received message", type=message["type"], sender=message["from"])

    # Process asynchronously so we return 200 immediately
    asyncio.create_task(_handle_message(message))

    return {"ok": True}


async def _handle_message(message: dict):
    """Process a message in the background."""
    chat_id = message["chat_id"]
    try:
        # Show typing indicator
        await telegram_service.send_typing_action(chat_id)

        # Process via Claude router
        response_text = await process_message(message)

        # Send response
        await telegram_service.send_message(chat_id, response_text)

    except Exception as e:
        logger.exception("Error processing message", error=str(e))
        await telegram_service.send_message(
            chat_id, "Sorry, I encountered an error processing your message. Please try again."
        )
