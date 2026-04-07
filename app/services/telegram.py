from __future__ import annotations

import structlog
import httpx

from app.config import settings

logger = structlog.get_logger()

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


class TelegramService:
    def __init__(self):
        self.token = settings.telegram_bot_token

    async def send_message(self, chat_id: int | str, text: str):
        """Send a text message. Auto-splits if over 4096 chars."""
        chunks = self._split_message(text, max_length=4096)
        async with httpx.AsyncClient() as client:
            for chunk in chunks:
                resp = await client.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                    },
                )
                if resp.status_code != 200:
                    logger.error("Failed to send Telegram message", status=resp.status_code, body=resp.text)
                    # Retry without Markdown if parsing failed
                    if "can't parse" in resp.text.lower():
                        await client.post(
                            f"{TELEGRAM_API}/sendMessage",
                            json={"chat_id": chat_id, "text": chunk},
                        )

    async def send_typing_action(self, chat_id: int | str):
        """Show typing indicator."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{TELEGRAM_API}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )

    async def download_voice(self, file_id: str) -> bytes:
        """Download a voice/audio file from Telegram."""
        async with httpx.AsyncClient() as client:
            # Get file path
            resp = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
            file_path = resp.json()["result"]["file_path"]

            # Download file
            resp = await client.get(f"https://api.telegram.org/file/bot{self.token}/{file_path}")
            return resp.content

    @staticmethod
    def parse_update(body: dict) -> dict | None:
        """Extract message data from a Telegram update. Returns None if not a user message."""
        msg = body.get("message")
        if not msg:
            return None

        chat_id = msg["chat"]["id"]
        user = msg.get("from", {})

        result = {
            "message_id": msg["message_id"],
            "chat_id": chat_id,
            "from": str(chat_id),
            "sender_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            "username": user.get("username", ""),
            "timestamp": msg.get("date"),
            "type": "text",
        }

        if "text" in msg:
            result["text"] = msg["text"]
        elif "voice" in msg:
            result["type"] = "audio"
            result["audio_id"] = msg["voice"]["file_id"]
        elif "audio" in msg:
            result["type"] = "audio"
            result["audio_id"] = msg["audio"]["file_id"]
        elif "photo" in msg:
            result["type"] = "image"
            result["caption"] = msg.get("caption", "")
        elif "document" in msg:
            result["type"] = "document"
            result["caption"] = msg.get("caption", "")
        else:
            return None

        return result

    async def set_webhook(self, url: str):
        """Register the webhook URL with Telegram."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TELEGRAM_API}/setWebhook",
                json={
                    "url": f"{url}/webhook",
                    "secret_token": settings.telegram_webhook_secret,
                    "allowed_updates": ["message"],
                },
            )
            logger.info("Set Telegram webhook", status=resp.status_code, response=resp.json())
            return resp.json()

    @staticmethod
    def verify_secret_token(token: str) -> bool:
        """Verify the X-Telegram-Bot-Api-Secret-Token header."""
        return token == settings.telegram_webhook_secret

    @staticmethod
    def _split_message(text: str, max_length: int = 4096) -> list[str]:
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            split_at = text.rfind("\n", 0, max_length)
            if split_at == -1:
                split_at = text.rfind(" ", 0, max_length)
            if split_at == -1:
                split_at = max_length

            chunks.append(text[:split_at])
            text = text[split_at:].lstrip()

        return chunks


telegram_service = TelegramService()
