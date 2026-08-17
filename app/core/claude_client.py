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
        "description": (
            "List the user's tasks. With no status filter it returns the open ones "
            "(todo and in_progress) — that is the list to show them, and the order "
            "the letters in close_tasks refer to, so prefer it. Pass a status only "
            "when they specifically ask about done or cancelled work; a filtered list "
            "is lettered differently and closing by letter off it will hit the wrong task."
        ),
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
        "name": "close_tasks",
        "description": (
            "Mark one or more open tasks as done (or cancelled) by the letter they were "
            "listed under, or by a fragment of their title. Call this whenever the user "
            "closes tasks by letter — 'A is done', 'B and C finished', 'mark a done' — or "
            "by name — 'the bank one is done'. It resolves letters to the right tasks "
            "itself, so you never need to list tasks first or count positions. It reports "
            "back which titles it closed; use those in your confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Letters (\"A\", \"B\") and/or title fragments (\"bank\"), one per task to close",
                },
                "status": {
                    "type": "string",
                    "enum": ["done", "cancelled", "in_progress"],
                    "default": "done",
                    "description": "What to set them to. Use 'cancelled' when the user drops a task rather than finishing it.",
                },
            },
            "required": ["refs"],
        },
    },
    {
        "name": "delete_task",
        "description": "Delete a task permanently. This erases it — to close a finished task use close_tasks instead.",
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


def build_context_block(
    user_timezone: str = "Asia/Singapore",
    facts_digest: str = "",
    conversation_summary: str = "",
) -> str:
    """The volatile half of the system prompt: clock, facts, running memory.

    Kept separate from — and rendered *after* — the static half so the clock
    ticking does not invalidate the cached prefix every minute.
    """
    tz = ZoneInfo(user_timezone)
    now = datetime.now(tz)
    tomorrow = now + timedelta(days=1)
    block = (
        f"Right now it is {now.strftime('%A, %d %B %Y, %H:%M')} ({user_timezone}).\n"
        f"Today is {now.strftime('%A %d %b %Y')}; tomorrow is {tomorrow.strftime('%A %d %b %Y')}."
    )
    if facts_digest:
        block += (
            "\n\nWhat you already know about them (persistent memory — treat as authoritative):\n"
            + facts_digest
        )
    if conversation_summary:
        block += (
            "\n\nWhere things stand from earlier conversations (older than the messages below —"
            " you already know all this, so do not ask again):\n"
            + conversation_summary
        )
    return block


def build_static_prompt() -> str:
    """The stable half: identity and working rules. Byte-identical every
    request, so it sits in front of the cache breakpoint along with the tools."""
    return """You are Jarvis, a personal assistant for one person, reachable over Telegram.
You manage their calendar, mail, tasks, reminders, notes, expenses and research.

HOW YOU WRITE
You are writing in a chat window on a phone, so keep it short — a sentence or two for
simple things, and only as long as the question actually needs. Skip preambles and
restatements of what they just asked. Telegram Markdown works: *bold*, _italic_, `code`.
The occasional emoji for scanning (✅ ⚠️) is fine; a wall of them is not.
When you list their tasks, letter them "A.) ...", "B.) ..." so they can close tasks by
letter afterwards. Cap at 26 and add "+ N more".

ACTING ON THEIR BEHALF
Only say you did something after the tool call came back successful. A confirmation for
a reminder, event, task or expense that was never actually created is worse than saying
nothing, because they will rely on it and it will not fire.
Check with them first before anything you cannot take back: deleting, sending mail
(drafts are fine unsupervised), or overwriting something that already exists.
Their tasks stay open until they say otherwise. Finishing a related conversation, or
telling you how something went, is not the same as closing the task — wait to be told.

WHEN YOU ARE NOT SURE
Pick the most likely reading, do the thing, and say which reading you took in a few
words — "booked Tue 18th, say the word if you meant the 25th". That beats a
clarifying question for anything reversible. Save the questions for what is not:
if you genuinely cannot tell what they meant and getting it wrong would cost them,
ask, but ask once and make it a short question.
Dates in ordinary English are often ambiguous, so this house convention settles the
common one: a bare "next Tuesday" is the very next Tuesday to come, even when that is
tomorrow, and "this Tuesday" is the one in the current week. Anything vaguer than that
("the Friday after next") is yours to judge. Either way, name the actual date you
landed on — "Tue 18 Aug" — so a wrong read is obvious at a glance and cheap to fix.

WHEN THEY WANT PUSHBACK
Sometimes they want to be argued with rather than helped — they will ask you to poke
holes, take the other side, say what they are missing, or tell them why a plan fails.
Take that seriously: open with "🎭 *Devil's advocate:*", give two or three concrete
failure modes specific to their situation rather than generic caution, and close with
the one question they would least like to answer. Then drop it and go back to normal
unless they ask you to keep going.

WHEN THEY ARE REFLECTING
The 🌙 evening check-in asks what went well, what they learned, and tomorrow's
priority. When they answer it — or otherwise think out loud about how their day went —
reply as a coach in Tony Robbins' voice: name their actual win, lesson and priority
back to them, use one Robbins frame that fits what they wrote (state-story-strategy,
RPM, identity, the six needs, CANI, the triad), and end with a single specific
challenge tied to the priority they named. Head it "🔥 *Coach feedback*", keep it
under 120 words, high energy, no filler. After the 🔥 noon message, same voice but
lighter — answer what they asked and keep the frame.
"""


def build_system_prompt(
    user_timezone: str = "Asia/Singapore",
    facts_digest: str = "",
    conversation_summary: str = "",
) -> str:
    """Both halves joined, in the order the model sees them. Handy for tests
    and evals; production sends them as two blocks so the first one caches."""
    return (
        build_static_prompt()
        + "\n\n"
        + build_context_block(user_timezone, facts_digest, conversation_summary)
    )


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
    conversation_summary: str = "",
    force_thinking: bool = False,
) -> anthropic.types.Message:
    # Two blocks, and the order matters. Everything before the cache_control
    # breakpoint has to be byte-identical between requests or nothing caches,
    # so the clock, the facts digest and the running summary all go *after*
    # it. With the clock inside the cached block the prefix changed every
    # minute and the ~8k tokens of tools + prompt were re-written on nearly
    # every message instead of being read back at a tenth of the price.
    system_block = [
        {
            "type": "text",
            "text": build_static_prompt(),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": build_context_block(
                user_timezone,
                facts_digest=facts_digest,
                conversation_summary=conversation_summary,
            ),
        },
    ]

    # Adaptive thinking (always on for Sonnet 5); effort controls depth.
    # Hard queries (planning / synthesis / tradeoffs / math) get more.
    kwargs = {}
    if force_thinking or _needs_deep_thinking(messages):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "high"}
        kwargs["max_tokens"] = 8192
    else:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "low"}
        kwargs["max_tokens"] = 4096

    return client.messages.create(
        model=MODEL,
        system=system_block,
        tools=TOOL_DEFINITIONS,
        messages=messages,
        **kwargs,
    )
