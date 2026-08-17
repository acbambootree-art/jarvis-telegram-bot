import asyncio
from datetime import datetime
from uuid import UUID

import structlog

from app.db.database import async_session
from app.db.repositories import ConversationRepository

logger = structlog.get_logger()


async def load_conversation_history(user_id: UUID, limit: int = 20) -> list[dict]:
    """Load recent conversation history as Claude-compatible messages."""
    async with async_session() as session:
        repo = ConversationRepository(session)
        messages = await repo.get_recent(user_id, limit=limit)

    return [{"role": msg.role, "content": msg.content} for msg in messages]


async def save_message(user_id: UUID, role: str, content: str):
    """Save a message to conversation history."""
    async with async_session() as session:
        repo = ConversationRepository(session)
        await repo.save_message(user_id, role, content)


async def cleanup_old_messages(user_id: UUID, keep: int = 50):
    """Remove old messages beyond the keep limit."""
    async with async_session() as session:
        repo = ConversationRepository(session)
        await repo.delete_oldest(user_id, keep=keep)


# --- rolling summary ----------------------------------------------------
# The live window is the last WINDOW messages. Anything older used to be
# dropped outright, so Jarvis re-asked things it had already been told.
# Once SUMMARISE_AFTER messages have piled up past the window, they get
# folded into a running summary that rides along in the system prompt.

WINDOW = 20
SUMMARISE_AFTER = 10
# Kept well above the largest limit any caller asks for — market_intel reads
# back 80 messages — so pruning never starves an existing reader.
KEEP_MESSAGES = 200
_SUMMARY_MODEL = "claude-haiku-4-5"

_SUMMARY_PROMPT = """You keep the running memory of an assistant's conversation with its user.

Rewrite the memory below so it also covers the newer exchanges, staying under 250 words.

Keep: decisions made, commitments either side gave, preferences and working style the
user revealed, people and projects named and how they relate, open threads and anything
promised but not yet done. Write them as plain statements about the user, in the order
they matter, not as a narrative of the conversation.

Drop: pleasantries, anything already actioned and closed, and anything that was only
true at the time (today's weather, a one-off lookup).

Return only the memory itself."""


async def load_conversation_summary(user_id: UUID) -> str:
    """The running memory of everything older than the live window."""
    async with async_session() as session:
        repo = ConversationRepository(session)
        row = await repo.get_summary(user_id)
    return row.content if row else ""


_summarising: set[UUID] = set()


async def update_summary_if_needed(user_id: UUID) -> bool:
    """Fold messages that fell out of the live window into the summary.

    Safe to fire and forget — any failure leaves the existing summary and
    messages untouched, and the next turn retries.
    """
    # Two messages in quick succession would otherwise start two of these,
    # each folding in the same backlog and racing to overwrite the summary.
    if user_id in _summarising:
        return False
    _summarising.add(user_id)
    try:
        async with async_session() as session:
            repo = ConversationRepository(session)
            previous = await repo.get_summary(user_id)
            # Only look at messages the existing summary does not already
            # cover — otherwise every turn past WINDOW re-folds the whole
            # backlog, burning a model call each time and degrading the
            # summary by repeatedly rewriting it from its own output.
            covered_until = None
            if previous:
                raw = (previous.metadata_ or {}).get("covered_until")
                if raw:
                    try:
                        covered_until = datetime.fromisoformat(raw)
                    except ValueError:
                        logger.warning("summary_covered_until_unparseable", value=raw)
            stale = await repo.get_older_than(user_id, keep_recent=WINDOW, after=covered_until)
            if len(stale) < SUMMARISE_AFTER:
                return False

        transcript = "\n".join(f"{m.role}: {m.content}" for m in stale)
        existing = previous.content if previous else "(nothing recorded yet)"

        from app.core.claude_client import client  # local: avoids an import cycle

        # The SDK call is blocking; off-thread it so this background task
        # cannot stall the event loop while another message is coming in.
        msg = await asyncio.to_thread(
            client.messages.create,
            model=_SUMMARY_MODEL,
            max_tokens=1024,
            system=_SUMMARY_PROMPT,
            messages=[{
                "role": "user",
                "content": f"CURRENT MEMORY:\n{existing}\n\nNEWER EXCHANGES:\n{transcript}",
            }],
        )
        summary = "\n".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        if not summary:
            return False

        async with async_session() as session:
            repo = ConversationRepository(session)
            await repo.save_summary(user_id, summary, covered_until=stale[-1].created_at)
        # Summarised messages are redundant now, but other readers still page
        # back through raw history, so trim only the far tail.
        await cleanup_old_messages(user_id, keep=KEEP_MESSAGES)
        logger.info("conversation_summarised", folded=len(stale), chars=len(summary))
        return True
    except Exception as e:
        logger.warning("summary_update_failed", error=str(e))
        return False
    finally:
        _summarising.discard(user_id)
