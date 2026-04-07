from datetime import datetime, timedelta
from uuid import UUID

import dateparser
import structlog

from app.config import settings
from app.db.database import async_session
from app.db.repositories import ReminderRepository
from app.services.telegram import telegram_service

logger = structlog.get_logger()


async def set_reminder(
    user_id: UUID,
    message: str,
    remind_at: str,
    is_recurring: bool = False,
    recurrence_pattern: str = None,
) -> dict:
    parsed_time = dateparser.parse(remind_at, settings={"PREFER_DATES_FROM": "future"})
    if not parsed_time:
        return {"success": False, "error": f"Could not parse time: {remind_at}"}

    if parsed_time < datetime.now():
        return {"success": False, "error": "Reminder time is in the past"}

    async with async_session() as session:
        repo = ReminderRepository(session)
        reminder = await repo.create(
            user_id=user_id,
            message=message,
            remind_at=parsed_time,
            is_recurring=is_recurring,
            recurrence_pattern=recurrence_pattern,
        )

    return {
        "success": True,
        "reminder_id": str(reminder.id),
        "message": reminder.message,
        "remind_at": reminder.remind_at.isoformat(),
        "is_recurring": reminder.is_recurring,
    }


async def list_reminders(user_id: UUID) -> dict:
    async with async_session() as session:
        repo = ReminderRepository(session)
        reminders = await repo.list_pending(user_id)

    return {
        "success": True,
        "count": len(reminders),
        "reminders": [
            {
                "reminder_id": str(r.id),
                "message": r.message,
                "remind_at": r.remind_at.isoformat(),
                "is_recurring": r.is_recurring,
                "recurrence_pattern": r.recurrence_pattern,
            }
            for r in reminders
        ],
    }


async def cancel_reminder(user_id: UUID, reminder_id: str) -> dict:
    async with async_session() as session:
        repo = ReminderRepository(session)
        cancelled = await repo.cancel(UUID(reminder_id), user_id)

    return {"success": cancelled, "message": "Reminder cancelled" if cancelled else "Reminder not found"}


async def check_and_send_reminders():
    """Called by the scheduler every 60 seconds to fire due reminders."""
    now = datetime.utcnow()
    async with async_session() as session:
        repo = ReminderRepository(session)
        due_reminders = await repo.get_due_reminders(now)

        for reminder in due_reminders:
            try:
                # Get user phone number
                from app.db.repositories import UserRepository

                user_repo = UserRepository(session)
                user = await user_repo.get_by_id(reminder.user_id)
                if not user:
                    continue

                # Send reminder via Telegram
                text = f"*Reminder*\n\n{reminder.message}"
                await telegram_service.send_message(user.phone_number, text)

                # Handle recurring
                if reminder.is_recurring and reminder.recurrence_pattern:
                    next_time = _calculate_next_occurrence(reminder.remind_at, reminder.recurrence_pattern)
                    if next_time:
                        await repo.create(
                            user_id=reminder.user_id,
                            message=reminder.message,
                            remind_at=next_time,
                            is_recurring=True,
                            recurrence_pattern=reminder.recurrence_pattern,
                        )

                await repo.mark_sent(reminder.id)
                logger.info("Sent reminder", reminder_id=str(reminder.id))

            except Exception as e:
                logger.exception("Failed to send reminder", reminder_id=str(reminder.id), error=str(e))


def _calculate_next_occurrence(current: datetime, pattern: str) -> "datetime | None":
    if pattern == "daily":
        return current + timedelta(days=1)
    elif pattern == "weekly":
        return current + timedelta(weeks=1)
    elif pattern == "monthly":
        # Add roughly a month
        month = current.month + 1
        year = current.year
        if month > 12:
            month = 1
            year += 1
        return current.replace(year=year, month=month)
    return None
