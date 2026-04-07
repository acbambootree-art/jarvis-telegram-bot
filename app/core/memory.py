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
