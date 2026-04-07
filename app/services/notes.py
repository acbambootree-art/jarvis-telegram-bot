from uuid import UUID

import structlog

from app.db.database import async_session
from app.db.repositories import NoteRepository

logger = structlog.get_logger()


async def save_note(
    user_id: UUID, content: str, title: str = "", tags: list = None
) -> dict:
    async with async_session() as session:
        repo = NoteRepository(session)
        note = await repo.create(user_id=user_id, content=content, title=title, tags=tags or [])

    return {
        "success": True,
        "note_id": str(note.id),
        "title": note.title,
        "content_preview": note.content[:100],
    }


async def search_notes(user_id: UUID, query: str) -> dict:
    async with async_session() as session:
        repo = NoteRepository(session)
        notes = await repo.search(user_id, query)

    return {
        "success": True,
        "count": len(notes),
        "notes": [
            {
                "note_id": str(n.id),
                "title": n.title,
                "content": n.content,
                "tags": n.tags or [],
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ],
    }


async def list_notes(user_id: UUID, limit: int = 10) -> dict:
    async with async_session() as session:
        repo = NoteRepository(session)
        notes = await repo.list_notes(user_id, limit=limit)

    return {
        "success": True,
        "count": len(notes),
        "notes": [
            {
                "note_id": str(n.id),
                "title": n.title,
                "content_preview": n.content[:150],
                "tags": n.tags or [],
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ],
    }


async def delete_note(user_id: UUID, note_id: str) -> dict:
    async with async_session() as session:
        repo = NoteRepository(session)
        deleted = await repo.delete(UUID(note_id), user_id)

    return {"success": deleted, "message": "Note deleted" if deleted else "Note not found"}
