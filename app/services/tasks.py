from datetime import datetime
from typing import Optional
from uuid import UUID

import dateparser
import structlog

from app.db.database import async_session
from app.db.repositories import TaskRepository

logger = structlog.get_logger()

_OPEN_STATUSES = ("todo", "in_progress")


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

    if status is None:
        # Default to the open list. This has to be the *same* set, in the same
        # order, that close_tasks resolves letters against — if one of them
        # counted in_progress tasks and the other did not, every letter after
        # the first in_progress task would point at the wrong thing.
        tasks = [t for t in tasks if t.status in _OPEN_STATUSES]

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


def _normalise_ref(ref) -> str:
    """'A.)' -> 'A', 'task b' -> 'B', 'call the bank' -> 'call the bank'."""
    stripped = str(ref).strip().strip(".):(").removeprefix("task ").removeprefix("Task ").strip()
    return stripped.upper() if len(stripped) == 1 else stripped.lower()


def resolve_refs(open_tasks: list, refs: list[str]) -> tuple[list, list[dict]]:
    """Map refs onto tasks. Returns (matched tasks, unresolved refs).

    Pure so it can be tested without a database — see tests/test_close_tasks.py.
    """
    # A model that sends refs="A" instead of ["A"] would otherwise have the
    # string iterated character by character — "bank" becoming refs B,A,N,K
    # and silently closing whatever happens to sit at those positions.
    if isinstance(refs, str):
        refs = [refs]

    matched, unresolved, seen = [], [], set()
    for ref in refs:
        key = _normalise_ref(ref)
        task = None
        if len(key) == 1 and key.isalpha():
            idx = ord(key) - ord("A")
            if 0 <= idx < len(open_tasks):
                task = open_tasks[idx]
        else:
            hits = [t for t in open_tasks if key in t.title.lower()]
            if len(hits) == 1:
                task = hits[0]
            elif len(hits) > 1:
                unresolved.append({"ref": ref, "reason": "matches several open tasks",
                                   "candidates": [t.title for t in hits]})
                continue
        if task is None:
            unresolved.append({"ref": ref, "reason": "no open task matches"})
        elif task.id not in seen:
            seen.add(task.id)
            matched.append(task)
    return matched, unresolved


async def close_tasks(user_id: UUID, refs: list[str], status: str = "done") -> dict:
    """Resolve task references and set their status in one call.

    A ref is either a position letter (A = first open task, B = second, ...)
    matching the lettered list shown to the user, or a title substring.
    Resolution happens here rather than in the prompt so the model never has
    to count list positions itself.
    """
    async with async_session() as session:
        repo = TaskRepository(session)
        all_tasks = await repo.list_tasks(user_id)
        open_tasks = [t for t in all_tasks if t.status in _OPEN_STATUSES]

        matched, unresolved = resolve_refs(open_tasks, refs)

        updated = []
        for task in matched:
            await repo.update(task.id, user_id, status=status)
            updated.append({"task_id": str(task.id), "title": task.title, "status": status})

    # Moving a task to in_progress leaves it open; done/cancelled do not.
    closed = 0 if status in _OPEN_STATUSES else len(updated)
    return {
        "success": bool(updated),
        "updated": updated,
        "unresolved": unresolved,
        "open_count": len(open_tasks) - closed,
    }


async def delete_task(user_id: UUID, task_id: str) -> dict:
    async with async_session() as session:
        repo = TaskRepository(session)
        deleted = await repo.delete(UUID(task_id), user_id)

    return {"success": deleted, "message": "Task deleted" if deleted else "Task not found"}
