from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog

from app.config import settings
from app.services import calendar_service, gmail_service, tasks, reminders
from app.services.ziwei import get_daily_reading as get_ziwei_reading

logger = structlog.get_logger()


async def get_daily_briefing(user_id: UUID) -> dict:
    """Aggregate data from all services for a daily briefing."""
    tz = ZoneInfo(settings.default_timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    briefing_data = {}

    # Calendar events for today
    try:
        cal_result = await calendar_service.get_events(user_id, start_date=today)
        if cal_result["success"]:
            briefing_data["calendar"] = {
                "count": cal_result["count"],
                "events": cal_result["events"],
            }
        else:
            briefing_data["calendar"] = {"count": 0, "events": [], "note": "Calendar not connected"}
    except Exception as e:
        briefing_data["calendar"] = {"count": 0, "events": [], "error": str(e)}

    # Pending tasks
    try:
        task_result = await tasks.list_tasks(user_id, status="todo")
        briefing_data["tasks"] = {
            "pending_count": task_result["count"],
            "tasks": task_result["tasks"][:5],  # Top 5 tasks
        }
    except Exception as e:
        briefing_data["tasks"] = {"pending_count": 0, "tasks": [], "error": str(e)}

    # Upcoming reminders
    try:
        reminder_result = await reminders.list_reminders(user_id)
        briefing_data["reminders"] = {
            "count": reminder_result["count"],
            "reminders": reminder_result["reminders"][:5],
        }
    except Exception as e:
        briefing_data["reminders"] = {"count": 0, "reminders": [], "error": str(e)}

    # Unread emails
    try:
        email_result = await gmail_service.get_unread_count(user_id)
        if email_result["success"]:
            briefing_data["email"] = {"unread_count": email_result["unread_count"]}
        else:
            briefing_data["email"] = {"unread_count": 0, "note": "Gmail not connected"}
    except Exception as e:
        briefing_data["email"] = {"unread_count": 0, "error": str(e)}

    # Ziwei Doushu daily reading
    try:
        ziwei_result = await get_ziwei_reading()
        if ziwei_result["success"]:
            briefing_data["ziwei"] = {"reading": ziwei_result["reading"]}
        else:
            briefing_data["ziwei"] = {"reading": "", "error": ziwei_result.get("error", "")}
    except Exception as e:
        briefing_data["ziwei"] = {"reading": "", "error": str(e)}

    briefing_data["date"] = today
    briefing_data["success"] = True

    return briefing_data
