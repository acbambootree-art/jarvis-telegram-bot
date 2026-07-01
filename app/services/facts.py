"""Persistent long-term facts about the user, their contacts, preferences.

Loaded into Jarvis's system prompt on every turn so it remembers who
Cynthia is, that the user hates being called by their full name,
where the office is, etc.
"""

from uuid import UUID

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy import delete as sa_delete

from app.db.database import async_session
from app.models.models import UserFact

logger = structlog.get_logger()

CATEGORIES = ("contact", "preference", "decision", "context", "other")


async def save_fact(user_id: UUID, content: str, category: str = "context", tags: list[str] | None = None) -> dict:
    if category not in CATEGORIES:
        category = "other"
    async with async_session() as session:
        fact = UserFact(user_id=user_id, category=category, content=content, tags=tags or [])
        session.add(fact)
        await session.commit()
        await session.refresh(fact)
    logger.info("fact_saved", fact_id=str(fact.id), category=category)
    return {"success": True, "fact_id": str(fact.id), "category": category, "content": content}


async def list_facts(user_id: UUID, category: str | None = None, limit: int = 50) -> dict:
    async with async_session() as session:
        query = select(UserFact).where(UserFact.user_id == user_id)
        if category:
            query = query.where(UserFact.category == category)
        query = query.order_by(UserFact.updated_at.desc()).limit(limit)
        result = await session.execute(query)
        facts = result.scalars().all()
    return {
        "success": True,
        "count": len(facts),
        "facts": [
            {"fact_id": str(f.id), "category": f.category, "content": f.content, "tags": f.tags or []}
            for f in facts
        ],
    }


async def search_facts(user_id: UUID, query: str, limit: int = 20) -> dict:
    """Search facts by keyword against content and tags."""
    q = f"%{query}%"
    async with async_session() as session:
        result = await session.execute(
            select(UserFact)
            .where(
                and_(
                    UserFact.user_id == user_id,
                    or_(UserFact.content.ilike(q)),
                )
            )
            .order_by(UserFact.updated_at.desc())
            .limit(limit)
        )
        facts = result.scalars().all()
    return {
        "success": True,
        "count": len(facts),
        "facts": [
            {"fact_id": str(f.id), "category": f.category, "content": f.content, "tags": f.tags or []}
            for f in facts
        ],
    }


async def delete_fact(user_id: UUID, fact_id: str) -> dict:
    async with async_session() as session:
        await session.execute(
            sa_delete(UserFact).where(and_(UserFact.id == UUID(fact_id), UserFact.user_id == user_id))
        )
        await session.commit()
    return {"success": True, "message": "Fact deleted"}


async def load_facts_for_prompt(user_id: UUID, limit: int = 60) -> str:
    """Return a compact plain-text digest of the user's facts for
    inclusion in Jarvis's system prompt."""
    data = await list_facts(user_id, limit=limit)
    if not data["facts"]:
        return ""
    by_cat: dict[str, list[str]] = {}
    for f in data["facts"]:
        by_cat.setdefault(f["category"], []).append(f["content"])
    parts = []
    for cat in ("contact", "preference", "decision", "context", "other"):
        items = by_cat.get(cat)
        if not items:
            continue
        parts.append(f"  {cat}:")
        for it in items[:15]:
            parts.append(f"    - {it}")
    return "\n".join(parts)
