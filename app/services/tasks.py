from datetime import datetime
from typing import Optional
from uuid import UUID

import dateparser
import structlog

from app.db.database import async_session
from app.db.repositories import TaskRepository

logger = structlog.get_logger()


async def create_task(
    user_id: UUID,
    title: str,
    description: str = "",
    priority: str = "medium",
    due_date: str = None,
    tags: list = None,
) -> dict:
    parsed_due = None
    if due_date:
        parsed_due = dateparser.parse(due_date, settings={"PREFER_DATES_FROM": "future"})

    async with async_session() as session:
        repo = TaskRepository(session)
        task = await repo.create(
            user_id=user_id,
            title=title,
            description=description,
            priority=priority,
            due_date=parsed_due,
            tags=tags or [],
        )
    return {
        "success": True,
        "task_id": str(task.id),
        "title": task.title,
        "priority": task.priority,
        "status": task.status,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


async def list_tasks(
    user_id: UUID, status: str = None, priority: str = None
) -> dict:
    async with async_session() as session:
        repo = TaskRepository(session)
        tasks = await repo.list_tasks(user_id, status=status, priority=priority)

    return {
        "success": True,
        "count": len(tasks),
        "tasks": [
            {
                "task_id": str(t.id),
                "title": t.title,
                "priority": t.priority,
                "status": t.status,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "tags": t.tags or [],
            }
            for t in tasks
        ],
    }


async def update_task(user_id: UUID, task_id: str, **kwargs) -> dict:
    if "due_date" in kwargs and kwargs["due_date"]:
        parsed = dateparser.parse(kwargs["due_date"], settings={"PREFER_DATES_FROM": "future"})
        kwargs["due_date"] = parsed

    async with async_session() as session:
        repo = TaskRepository(session)
        # Remove None values
        updates = {k: v for k, v in kwargs.items() if v is not None}
        task = await repo.update(UUID(task_id), user_id, **updates)

    if not task:
        return {"success": False, "error": "Task not found"}

    return {
        "success": True,
        "task_id": str(task.id),
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
    }


async def delete_task(user_id: UUID, task_id: str) -> dict:
    async with async_session() as session:
        repo = TaskRepository(session)
        deleted = await repo.delete(UUID(task_id), user_id)

    return {"success": deleted, "message": "Task deleted" if deleted else "Task not found"}
