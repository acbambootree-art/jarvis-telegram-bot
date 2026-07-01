from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import structlog

from app.config import settings

logger = structlog.get_logger()

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

MODEL = "claude-sonnet-5"

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
        "description": "Update a task's status, priority, title, or other fields. IMPORTANT: only change status to 'done' or 'cancelled' when the user explicitly says so — never infer completion from context.",
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
        "description": "Set a reminder that will be sent as a Telegram message at the specified time. CRITICAL: you MUST call this tool whenever the user asks for a reminder. Never claim a reminder is set unless this tool returned success. Always include the returned reminder_id in your confirmation message.",
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
    # --- Cross-domain synthesis ---
    {
        "name": "synthesize_state",
        "description": "The heaviest reasoning tool. Reads calendar + tasks + health + expenses + last check-in + Bazi almanac + remembered facts, connects patterns across all of them via extended thinking, and returns advisor-level insight (not raw data). Use when the user asks 'what should I focus on', 'what am I missing', 'give me a state read', 'plan my week', or any question that needs synthesis across multiple domains. Pass the user's exact question so the advisor can target the answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Optional user question. Omit for a general state review."},
            },
        },
    },
    # --- Persistent facts (long-term memory) ---
    {
        "name": "save_fact",
        "description": "Save a durable fact about the user or their world (contact info, preference, past decision, or context) so Jarvis remembers it in future conversations. Use when the user shares info worth remembering long-term: 'Cynthia does durian imports in Guangzhou', 'I prefer being called CJ', 'we decided to hold on the SG rental'. Loaded automatically into every future turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact in one clear sentence"},
                "category": {"type": "string", "enum": ["contact", "preference", "decision", "context", "other"]},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags to help retrieval later"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "list_facts",
        "description": "List saved facts. Filter by category if the user asks about a specific area.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["contact", "preference", "decision", "context", "other"]},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "search_facts",
        "description": "Search saved facts by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "delete_fact",
        "description": "Delete a fact by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {"fact_id": {"type": "string"}},
            "required": ["fact_id"],
        },
    },
    # --- Entity graph (structured memory: people, projects, decisions) ---
    {
        "name": "upsert_entity",
        "description": "Create or update a named entity (person/project/company/place/decision). Idempotent by name+kind. Use when the user mentions someone or something worth structured tracking, especially when there are attributes to record (role, city, deadline, status).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["person", "project", "company", "place", "decision", "other"]},
                "attributes": {"type": "object", "description": "Arbitrary key/value attributes (role, city, phone, status, deadline, notes, etc.)"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
        },
    },
    {
        "name": "link_entities",
        "description": "Create a directed relationship between two entities. e.g. link_entities('Cynthia', 'DurianCo', 'works_at'). Missing entities are auto-created as 'other'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_entity": {"type": "string", "description": "Source entity name or UUID"},
                "to_entity": {"type": "string", "description": "Target entity name or UUID"},
                "label": {"type": "string", "description": "Relationship label (works_at, reports_to, supplies, partner_of, blocks, etc.)"},
                "attributes": {"type": "object"},
            },
            "required": ["from_entity", "to_entity", "label"],
        },
    },
    {
        "name": "get_entity",
        "description": "Fetch an entity by name or UUID plus its outgoing and incoming relationships. Use when the user references a specific person, project, or decision.",
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "Entity name or UUID"}},
            "required": ["ref"],
        },
    },
    {
        "name": "list_entities",
        "description": "List entities, optionally filtered by kind.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["person", "project", "company", "place", "decision", "other"]},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "search_entities",
        "description": "Search entities by name substring.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
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


def build_system_prompt(user_timezone: str = "Asia/Singapore", facts_digest: str = "") -> str:
    tz = ZoneInfo(user_timezone)
    today = datetime.now(tz)
    now = today.strftime("%A, %Y-%m-%d %H:%M:%S %Z")
    today_str = today.strftime("%A, %B %d, %Y")
    tomorrow_str = (today + timedelta(days=1)).strftime("%A, %B %d, %Y")
    yesterday_str = (today - timedelta(days=1)).strftime("%A, %B %d, %Y")
    # Pre-compute a date reference table so the model never has to calculate weekdays
    date_ref_lines = []
    for offset in range(-7, 35):
        d = today + timedelta(days=offset)
        label = {0: " ← TODAY", 1: " ← TOMORROW", -1: " ← YESTERDAY"}.get(offset, "")
        date_ref_lines.append(f"  {d.strftime('%A, %Y-%m-%d')}{label}")
    date_reference = "\n".join(date_ref_lines)
    return f"""You are Jarvis, a highly capable personal AI assistant available via Telegram. You are concise, proactive, and helpful.

════════════════════════════════════════
CRITICAL DATE FACTS (use these verbatim):
  TODAY     = {today_str}
  TOMORROW  = {tomorrow_str}
  YESTERDAY = {yesterday_str}
════════════════════════════════════════
User timezone: {user_timezone}
Current datetime: {now}

{("REMEMBERED FACTS (from your persistent long-term memory — treat as authoritative context about the user):\n" + facts_digest + "\n") if facts_digest else ""}
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
- NEVER claim an action succeeded unless you actually called the corresponding tool AND it returned success. Specifically for reminders, tasks, calendar events, expenses, notes: confirming "✅ done" without a tool call is a critical failure. If you intend to set a reminder, you MUST call set_reminder before saying it is set
- NEVER mark a task as done, completed, or cancelled unless the user explicitly says so (e.g. "mark X as done", "X is completed", "finished X"). Do NOT infer completion from conversation context, progress updates, or related actions. Tasks remain "todo" or "in_progress" until the user explicitly closes them
- For calendar events, always clarify the timezone if ambiguous
- When user gives a relative date/time (e.g., "tomorrow", "in 2 hours"), convert it based on the current datetime and timezone
- NEVER compute weekdays yourself. When writing any weekday (Monday, Tuesday, etc.) you MUST look it up in the DATE REFERENCE below. If you write "Tomorrow (Monday, April 21)" but the reference says Tuesday, you are wrong — USE THE REFERENCE. NEVER correct day-of-week from emails or other sources; trust the source
- WEEKDAY → DATE: When the user says a weekday alone ("Tuesday", "next Friday", "this Wednesday"), find the matching weekday in the DATE REFERENCE table and use THAT date. "Next <weekday>" means the next occurrence after today. "This <weekday>" means the upcoming one within this calendar week (or current week if today is that day). If the requested weekday isn't visible in the table, say so and ask for the specific date — DO NOT guess
- USER CORRECTING A WEEKDAY: When the user pushes back ("No, that's Tuesday not Wednesday", "I said Tuesday", "Tuesday, not Wednesday"), the WEEKDAY they named is the source of truth. Look up that weekday in the DATE REFERENCE, find the correct YYYY-MM-DD, and ACTUALLY update the calendar event (call update_event with the new start_time/end_time). Do NOT just acknowledge the correction in text while leaving the wrong date in place
- For expense logging, infer the category from context when possible
- When listing tasks (in any reply), format them as a lettered list: "A.) <task>", "B.) <task>", "C.) <task>" — NOT as bullets or numbered list. Cap at 26 (A-Z); if more, say "+ N more" at the end
- TASK COMPLETION SHORTHAND — when the user says "Task A is done", "A done", "mark A done", "B is finished", "task a and b are closed", or any variant naming task(s) by single letter A-Z:
    1. Call list_tasks with status="todo" to get the current pending list (same ordering used everywhere: due_date asc, created_at desc)
    2. Map each letter to the task at that 0-indexed position (A→0, B→1, C→2, etc.)
    3. For each letter mentioned, call update_task with task_id=<that task's id> and status="done"
    4. Confirm with: "✅ Done: A.) <title> · B.) <title>" — one line per completed task
    5. If a letter is out of range (no task at that position), say so and skip it
  EXECUTE IMMEDIATELY — saying "Task A is done" IS the explicit user signal required by the "never auto-complete" rule above. Do NOT ask for clarification. Do NOT say "could you clarify". Do NOT ask the user to name the task by title. The letter shorthand is unambiguous: A means position 0, B means position 1, etc. — even if the list has only 1 or 2 tasks. Just do it.
  Multiple letters in one message are allowed ("A and B closed" → mark both).
  This shorthand only applies to A-Z letter references. If the user names a task by title or partial title instead, find by title match.
- Keep responses under 500 words unless more detail is explicitly requested
- If a tool call fails, explain the error simply and suggest an alternative
- Use emoji sparingly for visual clarity (checkmarks, warning signs, etc.)

DEVIL'S ADVOCATE MODE — when the user says "devil's advocate", "argue the other side", "poke holes", "steel-man against me", "push back", "what am I missing", "counter-argue", "what could go wrong":
  - Do NOT agree, hedge, or soften — flip and attack the user's stated position with the STRONGEST counter-arguments
  - Start with: "🎭 *Devil's advocate:*"
  - Structure: 2-3 concrete failure modes / hidden costs / better alternatives they haven't considered
  - Be specific, not generic ("your unit economics don't survive if CAC>$50" beats "you might not scale")
  - End with ONE hard question that forces them to defend the strongest weak point
  - After this reply, drop back to normal mode on the next turn unless they say "keep pushing"

COACH MODE — when the previous assistant message starts with 🌙 (the 8pm evening check-in) AND the user is now replying with their reflection, respond AS TONY ROBBINS giving direct coach feedback. Rules:
  - Reference their actual win, lesson, and tomorrow's priority by name — don't be generic
  - High energy, CAPS for emphasis on key words (3-5 times)
  - Use ONE Robbins framework that fits what they wrote (State-Story-Strategy, RPM, Massive Action, Identity, Six Human Needs, CANI, the Triad)
  - End with ONE specific challenge for tomorrow tied to their stated priority
  - 120 words max. No fluff, no "as an AI"
  - Format: 🔥 *Coach feedback* header, then 2-3 short paragraphs

When the previous assistant message starts with 🔥 (noon motivation) and the user replies, also stay in coach voice but lighter — answer their actual question while keeping the high-energy frame.

════════════════════════════════════════
DATE REFERENCE (authoritative — ALWAYS look up weekdays here, never compute):
{date_reference}
════════════════════════════════════════
Before you write any sentence containing a weekday name, stop and verify against this table."""


_HARD_QUERY_HINTS = (
    "should i", "help me decide", "plan my", "trade-off", "tradeoff", "pros and cons",
    "priorit", "why", "what should", "compare", "reason", "figure out",
    "synthes", "council", "advis", "strateg", "devil", "steel-man", "steelman",
    "calc", "how much will", "when should", "which is better",
)


def _needs_deep_thinking(messages: list[dict]) -> bool:
    """Decide if this query warrants extended thinking."""
    last_user = None
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                last_user = content.lower()
                break
    if not last_user:
        return False
    # Short/simple messages don't need deep thought
    if len(last_user) < 40:
        return any(h in last_user for h in ("should i", "devil", "council", "synthes", "advis"))
    # Longer or multi-sentence messages: check for hard-query hints
    return any(h in last_user for h in _HARD_QUERY_HINTS)


def create_message(
    messages: list[dict],
    user_timezone: str = "Asia/Singapore",
    facts_digest: str = "",
    force_thinking: bool = False,
) -> anthropic.types.Message:
    # Structured system block with prompt caching. The system prompt +
    # tool schemas together are ~5-8k tokens; caching them (5 min TTL)
    # cuts input cost by ~90% for the common case where the user sends
    # a follow-up within the cache window.
    system_block = [
        {
            "type": "text",
            "text": build_system_prompt(user_timezone, facts_digest=facts_digest),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # Extended thinking: enable when the query is hard (planning /
    # synthesis / tradeoffs / math). Cheap queries skip it.
    kwargs = {}
    if force_thinking or _needs_deep_thinking(messages):
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 5000}
        kwargs["max_tokens"] = 8192
    else:
        kwargs["max_tokens"] = 2048

    return client.messages.create(
        model=MODEL,
        system=system_block,
        tools=TOOL_DEFINITIONS,
        messages=messages,
        **kwargs,
    )
