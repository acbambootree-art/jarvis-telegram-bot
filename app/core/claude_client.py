from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings

logger = structlog.get_logger()

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

MODEL = "claude-sonnet-4-20250514"

TOOL_DEFINITIONS = [
    # --- Calendar ---
    {
        "name": "get_events",
        "description": "Get calendar events for a date range. Use when user asks about their schedule, upcoming events, or what's happening on a specific day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format. Defaults to start_date if not provided."},
            },
            "required": ["start_date"],
        },
    },
    {
        "name": "create_event",
        "description": "Create a new calendar event. Use when user wants to schedule, book, or add an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title/summary"},
                "start_time": {"type": "string", "description": "Start datetime in ISO 8601 format (YYYY-MM-DDTHH:MM:SS)"},
                "end_time": {"type": "string", "description": "End datetime in ISO 8601 format. If not specified, defaults to 1 hour after start."},
                "description": {"type": "string", "description": "Event description or notes"},
                "location": {"type": "string", "description": "Event location"},
            },
            "required": ["title", "start_time"],
        },
    },
    {
        "name": "update_event",
        "description": "Update an existing calendar event. Use when user wants to change, reschedule, or modify an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The event ID to update"},
                "title": {"type": "string"},
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Delete a calendar event. Use when user wants to cancel or remove an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The event ID to delete"},
            },
            "required": ["event_id"],
        },
    },
    # --- Gmail ---
    {
        "name": "search_emails",
        "description": "Search emails using Gmail search syntax. Use when user asks about emails, messages, or correspondence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query (e.g., 'from:john@example.com', 'is:unread', 'subject:meeting')"},
                "max_results": {"type": "integer", "description": "Max emails to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_email",
        "description": "Read the full content of a specific email by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The email message ID"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "draft_reply",
        "description": "Draft a reply to an email. Does NOT send it — saves as draft for user review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "The email message ID to reply to"},
                "body": {"type": "string", "description": "The reply body text"},
            },
            "required": ["email_id", "body"],
        },
    },
    {
        "name": "get_unread_count",
        "description": "Get the count of unread emails. Use when user asks how many unread emails they have.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Tasks ---
    {
        "name": "list_tasks",
        "description": "List user's tasks. Can filter by status (todo, in_progress, done, cancelled) and priority (low, medium, high, urgent).",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "cancelled"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            },
        },
    },
    {
        "name": "create_task",
        "description": "Create a new task. Use when user wants to add a todo item or task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task details"},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "default": "medium"},
                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS format"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags/labels for the task"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": "Update a task's status, priority, title, or other fields.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task UUID"},
                "title": {"type": "string"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "cancelled"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "due_date": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "delete_task",
        "description": "Delete a task permanently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task UUID to delete"},
            },
            "required": ["task_id"],
        },
    },
    # --- Reminders ---
    {
        "name": "set_reminder",
        "description": "Set a reminder that will be sent as a Telegram message at the specified time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The reminder message"},
                "remind_at": {"type": "string", "description": "When to send the reminder (ISO 8601 datetime or natural language like 'in 30 minutes', 'tomorrow at 9am')"},
                "is_recurring": {"type": "boolean", "default": False},
                "recurrence_pattern": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List all pending (upcoming) reminders.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "The reminder UUID to cancel"},
            },
            "required": ["reminder_id"],
        },
    },
    # --- Notes ---
    {
        "name": "save_note",
        "description": "Save a note to the knowledge base. Use when user wants to remember, save, or store information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The note content"},
                "title": {"type": "string", "description": "Optional note title"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for organization"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "search_notes",
        "description": "Search notes by keyword. Use when user asks to find or recall saved information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_notes",
        "description": "List recent notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "delete_note",
        "description": "Delete a note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string"},
            },
            "required": ["note_id"],
        },
    },
    # --- Expenses ---
    {
        "name": "log_expense",
        "description": "Log an expense. Use when user mentions spending money, buying something, or paying for something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Amount spent"},
                "category": {"type": "string", "description": "Category (food, transport, shopping, entertainment, bills, health, education, other)"},
                "description": {"type": "string", "description": "What was purchased"},
                "currency": {"type": "string", "default": "SGD"},
                "expense_date": {"type": "string", "description": "Date of expense (YYYY-MM-DD). Defaults to today."},
            },
            "required": ["amount", "category"],
        },
    },
    {
        "name": "get_expense_summary",
        "description": "Get expense summary for a period. Use when user asks about spending, budget, or how much they spent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "this_week", "this_month", "last_month", "custom"]},
                "start_date": {"type": "string", "description": "Start date for custom period (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "End date for custom period (YYYY-MM-DD)"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "list_expenses",
        "description": "List recent expenses with details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
                "category": {"type": "string"},
            },
        },
    },
    # --- Health ---
    {
        "name": "log_health_metric",
        "description": "Log a health metric (steps, weight, sleep, heart rate, calories, distance, water, blood pressure, body fat, etc). Use when user reports health data like 'I walked 8000 steps today' or 'my weight is 72kg'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_type": {"type": "string", "description": "Type of metric: steps, weight, sleep, heart_rate, calories, distance, water, blood_pressure_systolic, blood_pressure_diastolic, body_fat, or any custom type"},
                "value": {"type": "number", "description": "The numeric value"},
                "unit": {"type": "string", "description": "Unit of measurement (auto-detected if omitted). E.g., steps, kg, hours, bpm, kcal, km, ml"},
                "notes": {"type": "string", "description": "Optional notes about this measurement"},
                "recorded_at": {"type": "string", "description": "When this was recorded (YYYY-MM-DD or natural language). Defaults to now."},
            },
            "required": ["metric_type", "value"],
        },
    },
    {
        "name": "get_health_summary",
        "description": "Get a summary of a health metric over a period. Use when user asks about their health trends, averages, or stats like 'how many steps this week' or 'what's my average weight this month'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_type": {"type": "string", "description": "Type of metric to summarize (steps, weight, sleep, heart_rate, etc.)"},
                "period": {"type": "string", "enum": ["today", "this_week", "this_month", "last_month", "custom"]},
                "start_date": {"type": "string", "description": "Start date for custom period (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "End date for custom period (YYYY-MM-DD)"},
            },
            "required": ["metric_type"],
        },
    },
    {
        "name": "list_health_metrics",
        "description": "List recent health metric entries. Use when user wants to see their logged health data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_type": {"type": "string", "description": "Filter by metric type (optional)"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    # --- Research ---
    {
        "name": "web_search",
        "description": "Search the web for information. Use when user asks you to research, look up, or find out about something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    # --- Briefing ---
    {
        "name": "get_daily_briefing",
        "description": "Get a comprehensive daily briefing including calendar events, pending tasks, unread emails, and upcoming reminders. Use when user asks for their briefing, daily summary, or morning update.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Ziwei Doushu (紫微斗数) ---
    {
        "name": "get_ziwei_fortune",
        "description": "Get a Ziwei Doushu (紫微斗数, Purple Star Astrology) fortune reading. Uses the owner's real birth chart. Use when the user asks about their luck, fortune, destiny, stars, horoscope, Ziwei, or any Chinese astrology reading. Supports different time scopes (today, this month, this year, this decade) and life topics (career, love, wealth, health, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "Time period for the reading",
                    "enum": ["today", "this_month", "this_year", "this_decade", "natal"],
                    "default": "today",
                },
                "topic": {
                    "type": "string",
                    "description": "Life area to focus on (optional — omit for a general reading)",
                    "enum": ["general", "career", "love", "wealth", "health", "travel", "property", "family", "friends"],
                },
                "date": {
                    "type": "string",
                    "description": "Specific date to read in YYYY-MM-DD format (optional, defaults to today). Use for questions like 'how is next Monday' or 'what about March 15'.",
                },
            },
        },
    },
]


def build_system_prompt(user_timezone: str = "Asia/Singapore") -> str:
    now = datetime.now(ZoneInfo(user_timezone)).strftime("%A, %Y-%m-%d %H:%M:%S %Z")
    return f"""You are Jarvis, a highly capable personal AI assistant available via Telegram. You are concise, proactive, and helpful.

Current datetime: {now}
User timezone: {user_timezone}

PERSONALITY:
- Be concise and direct — this is Telegram, not email
- Use short paragraphs and bullet points
- Use Telegram Markdown: *bold*, _italic_, `code`, ```code blocks```
- Be proactive: if user mentions a date, suggest adding it to calendar
- Be friendly but professional

CAPABILITIES:
- Google Calendar: View, create, update, delete events
- Gmail: Search, read, draft replies (never send without confirmation)
- Tasks: Full task management with priorities and due dates
- Reminders: Time-based reminders delivered via Telegram
- Notes: Save and search personal knowledge base
- Expenses: Track spending with categories and summaries
- Research: Web search and summarization
- Daily Briefing: Aggregated summary of calendar, tasks, emails, reminders

RULES:
- Always confirm before deleting anything
- For calendar events, always clarify the timezone if ambiguous
- When user gives a relative date/time (e.g., "tomorrow", "in 2 hours"), convert it based on the current datetime and timezone
- NEVER try to "correct" day-of-week in emails or other sources — you are bad at computing days of the week from dates. The current datetime above includes the correct weekday; count forward/backward from that anchor
- For expense logging, infer the category from context when possible
- Keep responses under 500 words unless more detail is explicitly requested
- If a tool call fails, explain the error simply and suggest an alternative
- Use emoji sparingly for visual clarity (checkmarks, warning signs, etc.)"""


def create_message(messages: list[dict], user_timezone: str = "Asia/Singapore") -> anthropic.types.Message:
    return client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=build_system_prompt(user_timezone),
        tools=TOOL_DEFINITIONS,
        messages=messages,
    )
