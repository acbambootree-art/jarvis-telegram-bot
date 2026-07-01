"""Entity + relationship graph on top of the flat facts store.

Provides:
- upsert_entity: create or update a named entity (deduped by kind+name)
- link_entities: create a labelled directed edge between two entities
- get_entity: fetch by id or name, with its outgoing and incoming links
- list_entities / search_entities
"""

from uuid import UUID

import structlog
from sqlalchemy import and_, or_, select

from app.db.database import async_session
from app.models.models import Entity, EntityRelation

logger = structlog.get_logger()

ENTITY_KINDS = ("person", "project", "company", "place", "decision", "other")


async def upsert_entity(user_id: UUID, name: str, kind: str = "other", attributes: dict | None = None, tags: list[str] | None = None) -> dict:
    if kind not in ENTITY_KINDS:
        kind = "other"
    attributes = attributes or {}
    tags = tags or []
    async with async_session() as session:
        # Case-insensitive name lookup within kind
        existing = await session.execute(
            select(Entity)
            .where(Entity.user_id == user_id)
            .where(Entity.kind == kind)
            .where(Entity.name.ilike(name))
        )
        row = existing.scalar_one_or_none()
        if row:
            # Merge attributes and tags
            merged_attrs = {**(row.attributes or {}), **attributes}
            merged_tags = sorted(set((row.tags or []) + tags))
            row.attributes = merged_attrs
            row.tags = merged_tags
            await session.commit()
            await session.refresh(row)
            return {
                "success": True,
                "entity_id": str(row.id),
                "name": row.name,
                "kind": row.kind,
                "attributes": row.attributes,
                "tags": row.tags,
                "created": False,
            }
        row = Entity(user_id=user_id, kind=kind, name=name, attributes=attributes, tags=tags)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {
        "success": True,
        "entity_id": str(row.id),
        "name": row.name,
        "kind": row.kind,
        "attributes": row.attributes,
        "tags": row.tags,
        "created": True,
    }


async def link_entities(user_id: UUID, from_entity: str, to_entity: str, label: str, attributes: dict | None = None) -> dict:
    """`from_entity` and `to_entity` may be either UUIDs or names.
    Names get looked up (case-insensitive). Missing entities are auto-created as 'other'."""
    async def _resolve(ref: str) -> UUID:
        try:
            return UUID(ref)
        except (ValueError, TypeError):
            pass
        # By name
        async with async_session() as session:
            found = await session.execute(
                select(Entity).where(Entity.user_id == user_id).where(Entity.name.ilike(ref))
            )
            row = found.scalar_one_or_none()
        if row:
            return row.id
        upsert = await upsert_entity(user_id, ref, kind="other")
        return UUID(upsert["entity_id"])

    from_id = await _resolve(from_entity)
    to_id = await _resolve(to_entity)

    async with async_session() as session:
        row = EntityRelation(
            user_id=user_id,
            from_entity_id=from_id,
            to_entity_id=to_id,
            label=label,
            attributes=attributes or {},
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {"success": True, "relation_id": str(row.id), "from": str(from_id), "to": str(to_id), "label": label}


async def get_entity(user_id: UUID, ref: str) -> dict:
    """Fetch an entity by UUID or name, with its outgoing and incoming edges."""
    async with async_session() as session:
        # Try UUID first
        entity = None
        try:
            uid = UUID(ref)
            result = await session.execute(select(Entity).where(Entity.id == uid).where(Entity.user_id == user_id))
            entity = result.scalar_one_or_none()
        except (ValueError, TypeError):
            pass
        if not entity:
            result = await session.execute(
                select(Entity).where(Entity.user_id == user_id).where(Entity.name.ilike(ref))
            )
            entity = result.scalar_one_or_none()
        if not entity:
            return {"success": False, "error": f"entity '{ref}' not found"}
        # Edges
        outgoing = await session.execute(
            select(EntityRelation, Entity)
            .join(Entity, Entity.id == EntityRelation.to_entity_id)
            .where(EntityRelation.user_id == user_id)
            .where(EntityRelation.from_entity_id == entity.id)
        )
        incoming = await session.execute(
            select(EntityRelation, Entity)
            .join(Entity, Entity.id == EntityRelation.from_entity_id)
            .where(EntityRelation.user_id == user_id)
            .where(EntityRelation.to_entity_id == entity.id)
        )
    return {
        "success": True,
        "entity_id": str(entity.id),
        "name": entity.name,
        "kind": entity.kind,
        "attributes": entity.attributes or {},
        "tags": entity.tags or [],
        "outgoing": [{"label": r.label, "to": e.name, "attributes": r.attributes or {}} for r, e in outgoing.all()],
        "incoming": [{"label": r.label, "from": e.name, "attributes": r.attributes or {}} for r, e in incoming.all()],
    }


async def list_entities(user_id: UUID, kind: str | None = None, limit: int = 50) -> dict:
    async with async_session() as session:
        query = select(Entity).where(Entity.user_id == user_id)
        if kind:
            query = query.where(Entity.kind == kind)
        query = query.order_by(Entity.updated_at.desc()).limit(limit)
        result = await session.execute(query)
        rows = result.scalars().all()
    return {
        "success": True,
        "count": len(rows),
        "entities": [
            {"entity_id": str(r.id), "name": r.name, "kind": r.kind, "attributes": r.attributes or {}, "tags": r.tags or []}
            for r in rows
        ],
    }


async def search_entities(user_id: UUID, query: str, limit: int = 20) -> dict:
    q = f"%{query}%"
    async with async_session() as session:
        result = await session.execute(
            select(Entity)
            .where(and_(Entity.user_id == user_id, or_(Entity.name.ilike(q))))
            .order_by(Entity.updated_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
    return {
        "success": True,
        "count": len(rows),
        "entities": [
            {"entity_id": str(r.id), "name": r.name, "kind": r.kind, "attributes": r.attributes or {}}
            for r in rows
        ],
    }
