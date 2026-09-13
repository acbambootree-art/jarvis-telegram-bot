from __future__ import annotations

import re
from datetime import datetime, timezone

import structlog
import httpx

from app.config import settings

logger = structlog.get_logger()

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


class TelegramSendError(RuntimeError):
    """A message could not be delivered, not even stripped of Markdown.

    Raised rather than returned so a caller cannot record an undelivered
    message as sent by forgetting to check a boolean.
    """


# Ring buffer of recent delivery failures, surfaced by /admin/diag.
RECENT_SEND_FAILURES: list[dict] = []
_MAX_SEND_FAILURES = 20

# Alerts are throttled so one bad batch does not become a flood.
_last_alert_at: datetime | None = None
_ALERT_COOLDOWN_SECONDS = 900


# Bare URLs, not the target half of a [text](url) link.
_BARE_URL = re.compile(r"(?<!\]\()https?://[^\s)]+")


def _escape_urls(text: str) -> str:
    """Escape _ and * inside bare URLs. Legacy Markdown reads the _ in
    ".../mr04026_new-bizsg..." as an unclosed italic and rejects the whole
    message, so it arrives as plain text with raw asterisks."""
    return _BARE_URL.sub(
        lambda m: m.group(0).replace("_", r"\_").replace("*", r"\*"), text
    )


def _record_failure(entry: dict):
    RECENT_SEND_FAILURES.append(entry)
    if len(RECENT_SEND_FAILURES) > _MAX_SEND_FAILURES:
        del RECENT_SEND_FAILURES[: len(RECENT_SEND_FAILURES) - _MAX_SEND_FAILURES]


class TelegramService:
    def __init__(self):
        self.token = settings.telegram_bot_token

    async def send_message(
        self, chat_id: int | str, text: str, _is_alert: bool = False
    ) -> bool:
        """Send a text message. Auto-splits if over 4096 chars.

        Returns True when every chunk was delivered. Raises TelegramSendError
        if any chunk could not be delivered even as plain text — callers must
        not go on to record an undelivered message as sent.
        """
        chunks = self._split_message(text, max_length=4096)
        failures: list[dict] = []
        async with httpx.AsyncClient() as client:
            for chunk in chunks:
                resp = await client.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": _escape_urls(chunk),
                        "parse_mode": "Markdown",
                    },
                )
                if resp.status_code == 200:
                    continue

                logger.error(
                    "telegram_send_failed",
                    status=resp.status_code,
                    body=resp.text[:500],
                    chat_id=chat_id,
                )
                # Always retry without Markdown — parse errors aren't the
                # only thing that breaks Markdown mode. This is the
                # safety net for any 400-class error.
                retry = await client.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk},
                )
                if retry.status_code != 200:
                    logger.error(
                        "telegram_send_retry_failed",
                        status=retry.status_code,
                        body=retry.text[:500],
                        chat_id=chat_id,
                    )
                    failures.append({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "chat_id": str(chat_id),
                        "status": retry.status_code,
                        "body": retry.text[:300],
                        "preview": chunk[:120],
                        "is_alert": _is_alert,
                    })

        if failures:
            for f in failures:
                _record_failure(f)
            if not _is_alert:
                await self._alert_owner(failures[0])
            raise TelegramSendError(
                f"{len(failures)} chunk(s) undelivered to {chat_id}: "
                f"status={failures[0]['status']} {failures[0]['body'][:120]}"
            )
        return True

    async def _alert_owner(self, failure: dict):
        """Best-effort heads-up that something was swallowed.

        Most failures are content-specific (bad Markdown entity, oversized
        payload), so a short plain message usually still gets through. Sent
        with _is_alert so a failing alert cannot recurse.
        """
        global _last_alert_at
        if not settings.owner_chat_id:
            return
        now = datetime.now(timezone.utc)
        if _last_alert_at and (now - _last_alert_at).total_seconds() < _ALERT_COOLDOWN_SECONDS:
            return
        _last_alert_at = now
        note = (
            "⚠️ Jarvis could not deliver a message.\n\n"
            f"Telegram said {failure['status']}: {failure['body'][:200]}\n\n"
            f"It began: {failure['preview']}"
        )
        try:
            await self.send_message(settings.owner_chat_id, note, _is_alert=True)
        except Exception as e:
            logger.error("telegram_alert_failed", error=str(e))

    async def send_typing_action(self, chat_id: int | str):
        """Show typing indicator."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{TELEGRAM_API}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )

    async def download_file(self, file_id: str) -> tuple[bytes, str]:
        """Download any Telegram file by file_id. Returns (bytes, mime_type)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
            file_path = resp.json()["result"]["file_path"]
            file_resp = await client.get(f"https://api.telegram.org/file/bot{self.token}/{file_path}")
            # Rough mime detection from extension
            ext = file_path.lower().split(".")[-1]
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            return file_resp.content, mime

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

        # Capture what the user is replying to (Telegram quote feature)
        reply_to = msg.get("reply_to_message")
        if reply_to:
            quoted = reply_to.get("text") or reply_to.get("caption") or ""
            if quoted:
                result["reply_to_text"] = quoted[:1000]

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
            # Telegram sends multiple resized versions — pick the largest
            photos = msg["photo"]
            biggest = max(photos, key=lambda p: p.get("file_size", 0))
            result["image_id"] = biggest["file_id"]
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
