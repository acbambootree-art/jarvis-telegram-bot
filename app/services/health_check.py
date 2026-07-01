"""Hourly reliability self-check.

Verifies that Anthropic and Telegram are reachable and functional.
On failure, alerts the owner via whichever channel is still up.
"""

from datetime import datetime, timezone

import anthropic
import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()

# Track alert de-duplication so we don't spam on every 60-min cycle.
_LAST_ALERTS: dict[str, datetime] = {}
_ALERT_COOLDOWN_SECONDS = 6 * 3600  # re-alert after 6h if still broken


def _throttle(key: str) -> bool:
    """Return True if we should send the alert now, False if throttled."""
    now = datetime.now(timezone.utc)
    last = _LAST_ALERTS.get(key)
    if last and (now - last).total_seconds() < _ALERT_COOLDOWN_SECONDS:
        return False
    _LAST_ALERTS[key] = now
    return True


async def _check_anthropic() -> tuple[bool, str]:
    if not settings.anthropic_api_key:
        return False, "no API key configured"
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",  # cheapest option for health check
            max_tokens=8,
            messages=[{"role": "user", "content": "ok?"}],
        )
        if resp.content:
            return True, "ok"
        return False, "empty response"
    except anthropic.NotFoundError as e:
        return False, f"model not found: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _check_telegram() -> tuple[bool, str]:
    if not settings.telegram_bot_token:
        return False, "no bot token configured"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
                timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                return True, "ok"
            return False, f"status={resp.status_code} body={resp.text[:200]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def run_heartbeat() -> dict:
    """Run all health checks. Return a dict of results. Alert on any failure."""
    from app.services.telegram import telegram_service

    started = datetime.now(timezone.utc)
    results = {}
    anth_ok, anth_msg = await _check_anthropic()
    tg_ok, tg_msg = await _check_telegram()

    results["anthropic"] = {"ok": anth_ok, "message": anth_msg}
    results["telegram"] = {"ok": tg_ok, "message": tg_msg}
    results["ts"] = started.isoformat()

    # Alerting: if anything is broken, tell the owner.
    if not anth_ok and _throttle("anthropic"):
        # Anthropic broken but Telegram still works — use Telegram to shout.
        if tg_ok and settings.owner_chat_id:
            try:
                await telegram_service.send_message(
                    settings.owner_chat_id,
                    f"⚠️ *Jarvis health alert*\nAnthropic API failing: `{anth_msg[:400]}`\n"
                    f"Scheduled AI jobs (coach / market intel / etc.) will error until fixed.",
                )
            except Exception as e:
                logger.error("alert_send_failed", error=str(e))
        else:
            logger.error("anthropic_down_no_alert_channel", anth=anth_msg, tg=tg_msg)

    if not tg_ok and _throttle("telegram"):
        # Telegram broken — we can't alert via Telegram. Log loudly so
        # Render dashboard shows it, and the /admin/diag endpoint will
        # surface the last health status.
        logger.error("telegram_down", tg=tg_msg)

    if anth_ok and tg_ok:
        # Clear cooldowns so a next-day failure alerts immediately
        _LAST_ALERTS.pop("anthropic", None)
        _LAST_ALERTS.pop("telegram", None)

    logger.info("heartbeat_complete", **{k: v.get("ok") if isinstance(v, dict) else v for k, v in results.items()})
    return results


# In-memory ring buffer of recent heartbeat runs (for /admin/diag)
RECENT_HEARTBEATS: list[dict] = []


async def run_and_record_heartbeat():
    """Wrapper that runs heartbeat and records the result to a ring buffer."""
    result = await run_heartbeat()
    RECENT_HEARTBEATS.append(result)
    if len(RECENT_HEARTBEATS) > 24:  # keep last 24 runs
        del RECENT_HEARTBEATS[: len(RECENT_HEARTBEATS) - 24]
