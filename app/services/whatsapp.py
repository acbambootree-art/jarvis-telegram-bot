"""WhatsApp channel via Twilio.

Disabled unless TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN +
TWILIO_WHATSAPP_FROM are all set in env. When enabled, mirrors the
Telegram flow: incoming messages hit /webhook/whatsapp, get parsed
into the same message dict shape, routed through Claude, and the
reply is sent back via Twilio.
"""

from typing import Optional

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()

TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"


class WhatsAppService:
    def __init__(self):
        self.sid = settings.twilio_account_sid
        self.auth = settings.twilio_auth_token
        self.from_ = settings.twilio_whatsapp_from  # e.g. "whatsapp:+14155238886"

    def enabled(self) -> bool:
        return bool(self.sid and self.auth and self.from_)

    async def send_message(self, to: str, text: str) -> bool:
        """Send a WhatsApp message via Twilio.  `to` must be like
        'whatsapp:+65xxxxxxxx'.  Returns True on success."""
        if not self.enabled():
            logger.warning("whatsapp_not_configured")
            return False
        # Twilio message body limit is 1600 chars; chunk if needed.
        chunks = _split_message(text, max_length=1500)
        all_ok = True
        async with httpx.AsyncClient(auth=(self.sid, self.auth)) as client:
            for chunk in chunks:
                resp = await client.post(
                    f"{TWILIO_API_ROOT}/Accounts/{self.sid}/Messages.json",
                    data={"From": self.from_, "To": to, "Body": chunk},
                    timeout=15,
                )
                if resp.status_code not in (200, 201):
                    logger.error(
                        "twilio_whatsapp_send_failed",
                        status=resp.status_code,
                        body=resp.text[:400],
                        to=to,
                    )
                    all_ok = False
        return all_ok

    @staticmethod
    def parse_twilio_webhook(form: dict) -> Optional[dict]:
        """Convert a Twilio webhook form payload into the standard
        internal message dict shape used by the router."""
        from_ = form.get("From", "")
        body = form.get("Body", "")
        if not from_ or not body:
            return None
        return {
            "message_id": form.get("MessageSid", ""),
            "chat_id": from_,
            "from": from_.replace("whatsapp:", ""),  # normalise to bare phone
            "sender_name": form.get("ProfileName", ""),
            "timestamp": None,
            "type": "text",
            "text": body,
            "channel": "whatsapp",
        }


def _split_message(text: str, max_length: int = 1500) -> list[str]:
    if len(text) <= max_length:
        return [text]
    out = []
    while text:
        if len(text) <= max_length:
            out.append(text)
            break
        cut = text.rfind("\n", 0, max_length)
        if cut == -1:
            cut = text.rfind(" ", 0, max_length)
        if cut == -1:
            cut = max_length
        out.append(text[:cut])
        text = text[cut:].lstrip()
    return out


whatsapp_service = WhatsAppService()
