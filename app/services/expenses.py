from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import dateparser
import structlog

from app.config import settings
from app.db.database import async_session
from app.db.repositories import ExpenseRepository

logger = structlog.get_logger()


async def log_expense(
    user_id: UUID,
    amount: float,
    category: str = "uncategorized",
    description: str = "",
    currency: str = "SGD",
    expense_date: str = None,
) -> dict:
    parsed_date = None
    if expense_date:
        parsed_date = dateparser.parse(expense_date)

    async with async_session() as session:
        repo = ExpenseRepository(session)
        expense = await repo.create(
            user_id=user_id,
            amount=Decimal(str(amount)),
            category=category.lower(),
            description=description,
            currency=currency,
            expense_date=parsed_date,
        )

    return {
        "success": True,
        "expense_id": str(expense.id),
        "amount": float(expense.amount),
        "category": expense.category,
        "currency": expense.currency,
        "description": expense.description,
    }


async def get_expense_summary(
    user_id: UUID,
    period: str = "this_month",
    start_date: str = None,
    end_date: str = None,
) -> dict:
    tz = ZoneInfo(settings.default_timezone)
    now = datetime.now(tz)

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "this_week":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "last_month":
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = first_of_month - timedelta(seconds=1)
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "custom" and start_date and end_date:
        start = dateparser.parse(start_date, settings={"TIMEZONE": settings.default_timezone, "RETURN_AS_TIMEZONE_AWARE": True})
        end = dateparser.parse(end_date, settings={"TIMEZONE": settings.default_timezone, "RETURN_AS_TIMEZONE_AWARE": True})
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now

    async with async_session() as session:
        repo = ExpenseRepository(session)
        summary = await repo.get_summary(user_id, start, end)

    total = sum(item["total"] for item in summary)

    return {
        "success": True,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_spent": total,
        "currency": "SGD",
        "by_category": summary,
    }


async def list_expenses(
    user_id: UUID, limit: int = 10, category: str = None
) -> dict:
    async with async_session() as session:
        repo = ExpenseRepository(session)
        expenses = await repo.list_expenses(user_id, limit=limit, category=category)

    return {
        "success": True,
        "count": len(expenses),
        "expenses": [
            {
                "expense_id": str(e.id),
                "amount": float(e.amount),
                "category": e.category,
                "description": e.description,
                "currency": e.currency,
                "date": e.expense_date.isoformat() if e.expense_date else None,
            }
            for e in expenses
        ],
    }
