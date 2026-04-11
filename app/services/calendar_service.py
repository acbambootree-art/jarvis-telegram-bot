from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from googleapiclient.discovery import build

from app.auth.google_oauth import get_google_credentials
from app.config import settings

logger = structlog.get_logger()


async def _get_calendar_service(user_id: UUID):
    creds = await get_google_credentials(user_id)
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds)


async def get_events(user_id: UUID, start_date: str, end_date: str = None) -> dict:
    service = await _get_calendar_service(user_id)
    if not service:
        return {"success": False, "error": "Google Calendar not connected. Please connect your Google account first."}

    # Treat naive date/datetime strings as local time in the configured
    # timezone. Google Calendar accepts RFC3339 timestamps with an offset.
    tz = ZoneInfo(settings.default_timezone)

    start = datetime.fromisoformat(start_date)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)

    if end_date:
        end = datetime.fromisoformat(end_date)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
    else:
        end = start + timedelta(days=1)

    time_min = start.isoformat()
    time_max = end.isoformat()

    try:
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=20,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])

        return {
            "success": True,
            "count": len(events),
            "events": [
                {
                    "event_id": e["id"],
                    "title": e.get("summary", "No title"),
                    "start": e["start"].get("dateTime", e["start"].get("date")),
                    "end": e["end"].get("dateTime", e["end"].get("date")),
                    "location": e.get("location", ""),
                    "description": e.get("description", "")[:200],
                    "status": e.get("status", ""),
                }
                for e in events
            ],
        }
    except Exception as e:
        logger.exception("Failed to get calendar events", error=str(e))
        return {"success": False, "error": str(e)}


async def create_event(
    user_id: UUID,
    title: str,
    start_time: str,
    end_time: str = None,
    description: str = "",
    location: str = "",
) -> dict:
    service = await _get_calendar_service(user_id)
    if not service:
        return {"success": False, "error": "Google Calendar not connected."}

    start = datetime.fromisoformat(start_time)
    if end_time:
        end = datetime.fromisoformat(end_time)
    else:
        end = start + timedelta(hours=1)

    event_body = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Singapore"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Singapore"},
    }
    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location

    try:
        event = service.events().insert(calendarId="primary", body=event_body).execute()
        return {
            "success": True,
            "event_id": event["id"],
            "title": event.get("summary"),
            "start": event["start"].get("dateTime"),
            "end": event["end"].get("dateTime"),
            "link": event.get("htmlLink"),
        }
    except Exception as e:
        logger.exception("Failed to create event", error=str(e))
        return {"success": False, "error": str(e)}


async def update_event(user_id: UUID, event_id: str, **kwargs) -> dict:
    service = await _get_calendar_service(user_id)
    if not service:
        return {"success": False, "error": "Google Calendar not connected."}

    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        if "title" in kwargs:
            event["summary"] = kwargs["title"]
        if "start_time" in kwargs:
            event["start"]["dateTime"] = datetime.fromisoformat(kwargs["start_time"]).isoformat()
        if "end_time" in kwargs:
            event["end"]["dateTime"] = datetime.fromisoformat(kwargs["end_time"]).isoformat()
        if "description" in kwargs:
            event["description"] = kwargs["description"]
        if "location" in kwargs:
            event["location"] = kwargs["location"]

        updated = service.events().update(calendarId="primary", eventId=event_id, body=event).execute()
        return {
            "success": True,
            "event_id": updated["id"],
            "title": updated.get("summary"),
            "start": updated["start"].get("dateTime"),
            "end": updated["end"].get("dateTime"),
        }
    except Exception as e:
        logger.exception("Failed to update event", error=str(e))
        return {"success": False, "error": str(e)}


async def delete_event(user_id: UUID, event_id: str) -> dict:
    service = await _get_calendar_service(user_id)
    if not service:
        return {"success": False, "error": "Google Calendar not connected."}

    try:
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return {"success": True, "message": "Event deleted"}
    except Exception as e:
        logger.exception("Failed to delete event", error=str(e))
        return {"success": False, "error": str(e)}
