import json
from uuid import UUID

import structlog

from app.core.claude_client import TOOL_DEFINITIONS, create_message
from app.core.date_corrector import correct_weekdays
from app.core.memory import load_conversation_history, save_message
from app.db.database import async_session
from app.db.repositories import UserRepository
from app.services import (
    briefing,
    calendar_service,
    expenses,
    gmail_service,
    health,
    notes,
    reminders,
    research,
    tasks,
    voice,
    ziwei,
)

logger = structlog.get_logger()

MAX_TOOL_ITERATIONS = 5


async def process_message(message: dict) -> str:
    """Process an incoming WhatsApp message through Claude with tool use."""
    sender = message["from"]

    # Get or create user
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create(sender)
    user_id = user.id

    # Handle voice messages
    if message["type"] == "audio":
        voice_result = await voice.transcribe_voice_message(message["audio_id"])
        if voice_result["success"]:
            user_text = voice_result["text"]
            logger.info("Transcribed voice message", text=user_text[:100])
        else:
            return f"Could not transcribe voice message: {voice_result['error']}"
    elif message["type"] == "text":
        user_text = message["text"]
    elif message["type"] == "image":
        user_text = message.get("caption", "User sent an image")
    else:
        return "I can process text and voice messages. Please send me a text or voice note!"

    # Load conversation history
    history = await load_conversation_history(user_id)

    # Add current message
    history.append({"role": "user", "content": user_text})

    # Save user message
    await save_message(user_id, "user", user_text)

    # Claude tool-use loop
    response_text = await _run_claude_loop(user_id, history, user.timezone)

    # Fix any mis-computed weekdays before sending to user
    response_text = correct_weekdays(response_text, user.timezone)

    # Save assistant response
    await save_message(user_id, "assistant", response_text)

    return response_text


async def _run_claude_loop(user_id: UUID, messages: list[dict], timezone: str) -> str:
    """Run the Claude tool-use loop until we get a text response."""
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = create_message(messages, user_timezone=timezone)

        # Check if Claude wants to use tools
        if response.stop_reason == "tool_use":
            # Process all tool calls in this response
            tool_results = []
            assistant_content = response.content

            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    tool_id = block.id

                    logger.info("Executing tool", tool=tool_name, input=json.dumps(tool_input)[:200])

                    result = await _execute_tool(user_id, tool_name, tool_input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result),
                    })

            # Add assistant message with tool calls and results
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        else:
            # Extract text response
            text_parts = [block.text for block in response.content if hasattr(block, "text")]
            return "\n".join(text_parts) if text_parts else "I'm not sure how to help with that."

    return "I ran into a complex situation and couldn't complete your request. Could you try rephrasing?"


async def _execute_tool(user_id: UUID, tool_name: str, tool_input: dict) -> dict:
    """Route a tool call to the appropriate service."""
    try:
        # Calendar
        if tool_name == "get_events":
            return await calendar_service.get_events(user_id, **tool_input)
        elif tool_name == "create_event":
            return await calendar_service.create_event(user_id, **tool_input)
        elif tool_name == "update_event":
            return await calendar_service.update_event(user_id, **tool_input)
        elif tool_name == "delete_event":
            return await calendar_service.delete_event(user_id, **tool_input)

        # Gmail
        elif tool_name == "search_emails":
            return await gmail_service.search_emails(user_id, **tool_input)
        elif tool_name == "read_email":
            return await gmail_service.read_email(user_id, **tool_input)
        elif tool_name == "draft_reply":
            return await gmail_service.draft_reply(user_id, **tool_input)
        elif tool_name == "get_unread_count":
            return await gmail_service.get_unread_count(user_id)

        # Tasks
        elif tool_name == "list_tasks":
            return await tasks.list_tasks(user_id, **tool_input)
        elif tool_name == "create_task":
            return await tasks.create_task(user_id, **tool_input)
        elif tool_name == "update_task":
            return await tasks.update_task(user_id, **tool_input)
        elif tool_name == "delete_task":
            return await tasks.delete_task(user_id, **tool_input)

        # Reminders
        elif tool_name == "set_reminder":
            return await reminders.set_reminder(user_id, **tool_input)
        elif tool_name == "list_reminders":
            return await reminders.list_reminders(user_id)
        elif tool_name == "cancel_reminder":
            return await reminders.cancel_reminder(user_id, **tool_input)

        # Notes
        elif tool_name == "save_note":
            return await notes.save_note(user_id, **tool_input)
        elif tool_name == "search_notes":
            return await notes.search_notes(user_id, **tool_input)
        elif tool_name == "list_notes":
            return await notes.list_notes(user_id, **tool_input)
        elif tool_name == "delete_note":
            return await notes.delete_note(user_id, **tool_input)

        # Expenses
        elif tool_name == "log_expense":
            return await expenses.log_expense(user_id, **tool_input)
        elif tool_name == "get_expense_summary":
            return await expenses.get_expense_summary(user_id, **tool_input)
        elif tool_name == "list_expenses":
            return await expenses.list_expenses(user_id, **tool_input)

        # Health
        elif tool_name == "log_health_metric":
            return await health.log_health_metric(user_id, **tool_input)
        elif tool_name == "get_health_summary":
            return await health.get_health_summary(user_id, **tool_input)
        elif tool_name == "list_health_metrics":
            return await health.list_health_metrics(user_id, **tool_input)

        # Research
        elif tool_name == "web_search":
            return await research.web_search(**tool_input)

        # Briefing
        elif tool_name == "get_daily_briefing":
            return await briefing.get_daily_briefing(user_id)

        # Ziwei Doushu (紫微斗数)
        elif tool_name == "get_ziwei_fortune":
            return await ziwei.get_ziwei_fortune(user_id, **tool_input)

        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.exception("Tool execution failed", tool=tool_name, error=str(e))
        return {"success": False, "error": f"Tool '{tool_name}' failed: {str(e)}"}
