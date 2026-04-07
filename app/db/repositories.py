from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    ConversationHistory,
    Expense,
    HealthMetric,
    Note,
    Reminder,
    Task,
    UserSettings,
)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, phone_number: str, timezone: str = "Asia/Singapore") -> UserSettings:
        result = await self.session.execute(
            select(UserSettings).where(UserSettings.phone_number == phone_number)
        )
        user = result.scalar_one_or_none()
        if not user:
            user = UserSettings(phone_number=phone_number, timezone=timezone)
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)
        return user

    async def update_google_tokens(self, user_id: UUID, encrypted_tokens: dict):
        await self.session.execute(
            update(UserSettings).where(UserSettings.id == user_id).values(google_tokens=encrypted_tokens)
        )
        await self.session.commit()

    async def get_by_id(self, user_id: UUID) -> Optional[UserSettings]:
        result = await self.session.execute(select(UserSettings).where(UserSettings.id == user_id))
        return result.scalar_one_or_none()


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_message(self, user_id: UUID, role: str, content: str, metadata: dict = None):
        msg = ConversationHistory(user_id=user_id, role=role, content=content, metadata_=metadata or {})
        self.session.add(msg)
        await self.session.commit()

    async def get_recent(self, user_id: UUID, limit: int = 20) -> list[ConversationHistory]:
        result = await self.session.execute(
            select(ConversationHistory)
            .where(ConversationHistory.user_id == user_id)
            .order_by(ConversationHistory.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def delete_oldest(self, user_id: UUID, keep: int = 50):
        subq = (
            select(ConversationHistory.id)
            .where(ConversationHistory.user_id == user_id)
            .order_by(ConversationHistory.created_at.desc())
            .limit(keep)
            .subquery()
        )
        await self.session.execute(
            delete(ConversationHistory).where(
                and_(ConversationHistory.user_id == user_id, ConversationHistory.id.notin_(select(subq.c.id)))
            )
        )
        await self.session.commit()


class TaskRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: UUID, title: str, **kwargs) -> Task:
        task = Task(user_id=user_id, title=title, **kwargs)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def list_tasks(
        self, user_id: UUID, status: str = None, priority: str = None
    ) -> list[Task]:
        query = select(Task).where(Task.user_id == user_id)
        if status:
            query = query.where(Task.status == status)
        if priority:
            query = query.where(Task.priority == priority)
        query = query.order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(self, task_id: UUID, user_id: UUID, **kwargs) -> Optional[Task]:
        await self.session.execute(
            update(Task).where(and_(Task.id == task_id, Task.user_id == user_id)).values(**kwargs)
        )
        await self.session.commit()
        result = await self.session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def delete(self, task_id: UUID, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(Task).where(and_(Task.id == task_id, Task.user_id == user_id))
        )
        await self.session.commit()
        return result.rowcount > 0

    async def get_by_id(self, task_id: UUID, user_id: UUID) -> Optional[Task]:
        result = await self.session.execute(
            select(Task).where(and_(Task.id == task_id, Task.user_id == user_id))
        )
        return result.scalar_one_or_none()


class NoteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: UUID, content: str, title: str = "", tags: list = None) -> Note:
        note = Note(user_id=user_id, content=content, title=title, tags=tags or [])
        self.session.add(note)
        await self.session.commit()
        await self.session.refresh(note)
        return note

    async def search(self, user_id: UUID, query: str) -> list[Note]:
        result = await self.session.execute(
            select(Note)
            .where(
                and_(
                    Note.user_id == user_id,
                    Note.content.ilike(f"%{query}%"),
                )
            )
            .order_by(Note.created_at.desc())
            .limit(10)
        )
        return list(result.scalars().all())

    async def list_notes(self, user_id: UUID, limit: int = 10) -> list[Note]:
        result = await self.session.execute(
            select(Note).where(Note.user_id == user_id).order_by(Note.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, note_id: UUID, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(Note).where(and_(Note.id == note_id, Note.user_id == user_id))
        )
        await self.session.commit()
        return result.rowcount > 0


class ExpenseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        amount: Decimal,
        category: str = "uncategorized",
        description: str = "",
        currency: str = "SGD",
        expense_date: datetime = None,
    ) -> Expense:
        expense = Expense(
            user_id=user_id,
            amount=amount,
            category=category,
            description=description,
            currency=currency,
            expense_date=expense_date or datetime.utcnow(),
        )
        self.session.add(expense)
        await self.session.commit()
        await self.session.refresh(expense)
        return expense

    async def get_summary(
        self, user_id: UUID, start_date: datetime, end_date: datetime
    ) -> list[dict]:
        result = await self.session.execute(
            select(
                Expense.category,
                func.sum(Expense.amount).label("total"),
                func.count(Expense.id).label("count"),
            )
            .where(
                and_(
                    Expense.user_id == user_id,
                    Expense.expense_date >= start_date,
                    Expense.expense_date <= end_date,
                )
            )
            .group_by(Expense.category)
            .order_by(func.sum(Expense.amount).desc())
        )
        return [{"category": row.category, "total": float(row.total), "count": row.count} for row in result.all()]

    async def list_expenses(
        self, user_id: UUID, limit: int = 20, category: str = None
    ) -> list[Expense]:
        query = select(Expense).where(Expense.user_id == user_id)
        if category:
            query = query.where(Expense.category == category)
        query = query.order_by(Expense.expense_date.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())


class HealthMetricRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        metric_type: str,
        value: Decimal,
        unit: str = "",
        notes: str = "",
        recorded_at: datetime = None,
    ) -> HealthMetric:
        metric = HealthMetric(
            user_id=user_id,
            metric_type=metric_type,
            value=value,
            unit=unit,
            notes=notes,
            recorded_at=recorded_at or datetime.utcnow(),
        )
        self.session.add(metric)
        await self.session.commit()
        await self.session.refresh(metric)
        return metric

    async def get_summary(
        self, user_id: UUID, metric_type: str, start_date: datetime, end_date: datetime
    ) -> list[dict]:
        result = await self.session.execute(
            select(
                func.avg(HealthMetric.value).label("avg"),
                func.min(HealthMetric.value).label("min"),
                func.max(HealthMetric.value).label("max"),
                func.count(HealthMetric.id).label("count"),
                func.sum(HealthMetric.value).label("total"),
            )
            .where(
                and_(
                    HealthMetric.user_id == user_id,
                    HealthMetric.metric_type == metric_type,
                    HealthMetric.recorded_at >= start_date,
                    HealthMetric.recorded_at <= end_date,
                )
            )
        )
        row = result.one()
        return {
            "avg": float(row.avg) if row.avg else 0,
            "min": float(row.min) if row.min else 0,
            "max": float(row.max) if row.max else 0,
            "count": row.count,
            "total": float(row.total) if row.total else 0,
        }

    async def list_metrics(
        self, user_id: UUID, metric_type: str = None, limit: int = 20
    ) -> list[HealthMetric]:
        query = select(HealthMetric).where(HealthMetric.user_id == user_id)
        if metric_type:
            query = query.where(HealthMetric.metric_type == metric_type)
        query = query.order_by(HealthMetric.recorded_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def delete(self, metric_id: UUID, user_id: UUID) -> bool:
        result = await self.session.execute(
            delete(HealthMetric).where(
                and_(HealthMetric.id == metric_id, HealthMetric.user_id == user_id)
            )
        )
        await self.session.commit()
        return result.rowcount > 0


class ReminderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        message: str,
        remind_at: datetime,
        is_recurring: bool = False,
        recurrence_pattern: str = None,
    ) -> Reminder:
        reminder = Reminder(
            user_id=user_id,
            message=message,
            remind_at=remind_at,
            is_recurring=is_recurring,
            recurrence_pattern=recurrence_pattern,
        )
        self.session.add(reminder)
        await self.session.commit()
        await self.session.refresh(reminder)
        return reminder

    async def get_due_reminders(self, now: datetime) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder)
            .where(and_(Reminder.status == "pending", Reminder.remind_at <= now))
            .order_by(Reminder.remind_at.asc())
        )
        return list(result.scalars().all())

    async def mark_sent(self, reminder_id: UUID):
        await self.session.execute(
            update(Reminder).where(Reminder.id == reminder_id).values(status="sent")
        )
        await self.session.commit()

    async def list_pending(self, user_id: UUID) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder)
            .where(and_(Reminder.user_id == user_id, Reminder.status == "pending"))
            .order_by(Reminder.remind_at.asc())
        )
        return list(result.scalars().all())

    async def cancel(self, reminder_id: UUID, user_id: UUID) -> bool:
        result = await self.session.execute(
            update(Reminder)
            .where(and_(Reminder.id == reminder_id, Reminder.user_id == user_id))
            .values(status="cancelled")
        )
        await self.session.commit()
        return result.rowcount > 0
