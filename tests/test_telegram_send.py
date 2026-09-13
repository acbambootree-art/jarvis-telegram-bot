"""Checks that an undelivered Telegram message raises instead of passing silently.

Run: python3 tests/test_telegram_send.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import telegram as tg  # noqa: E402


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "" if status_code == 200 else '{"description":"fake failure"}'


class FakeClient:
    """Replays a scripted list of status codes, one per POST."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.calls.append(json)
        return FakeResponse(self.script.pop(0) if self.script else 200)


def run(script):
    """Send one message through a scripted transport. Returns (outcome, client)."""
    client = FakeClient(script)
    tg.httpx.AsyncClient = lambda *a, **k: client
    tg.RECENT_SEND_FAILURES.clear()
    tg._last_alert_at = None
    try:
        asyncio.run(tg.telegram_service.send_message("123", "hello"))
        return "sent", client
    except tg.TelegramSendError:
        return "raised", client


def demo():
    real = tg.httpx.AsyncClient
    try:
        # Markdown accepted first time.
        outcome, client = run([200])
        assert outcome == "sent", outcome
        assert not tg.RECENT_SEND_FAILURES
        assert client.calls[0]["parse_mode"] == "Markdown"

        # Underscores in bare URLs are escaped; link targets and prose are not.
        assert tg._escape_urls("see (https://x.sg/mr04026_new-bizsg.pdf) _hi_") == (
            r"see (https://x.sg/mr04026\_new-bizsg.pdf) _hi_"
        )
        assert tg._escape_urls("[doc](https://x.sg/a_b)") == "[doc](https://x.sg/a_b)"

        # Markdown rejected, plain-text fallback delivers it.
        outcome, client = run([400, 200])
        assert outcome == "sent", outcome
        assert not tg.RECENT_SEND_FAILURES
        assert "parse_mode" not in client.calls[1]

        # Both attempts fail: must raise, record, and alert the owner.
        outcome, client = run([400, 400, 200])
        assert outcome == "raised", outcome
        assert len(tg.RECENT_SEND_FAILURES) == 1, tg.RECENT_SEND_FAILURES
        assert tg.RECENT_SEND_FAILURES[0]["status"] == 400
        assert tg.RECENT_SEND_FAILURES[0]["preview"] == "hello"
        assert len(client.calls) == 3, client.calls
        assert "could not deliver" in client.calls[2]["text"]

        # The alert itself failing must not recurse: two real attempts, two
        # alert attempts, and then it gives up.
        outcome, client = run([400, 400, 400, 400, 400, 400])
        assert outcome == "raised", outcome
        assert len(client.calls) == 4, client.calls
        assert tg.RECENT_SEND_FAILURES[-1]["is_alert"] is True

        # Throttling: a second failure inside the cooldown sends no new alert.
        client = FakeClient([400, 400, 400, 400])
        tg.httpx.AsyncClient = lambda *a, **k: client
        try:
            asyncio.run(tg.telegram_service.send_message("123", "hello"))
        except tg.TelegramSendError:
            pass
        assert len(client.calls) == 2, client.calls
        print("ok")
    finally:
        tg.httpx.AsyncClient = real
        tg.RECENT_SEND_FAILURES.clear()


if __name__ == "__main__":
    demo()
