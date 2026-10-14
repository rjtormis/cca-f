# Ring 5: The Tool Runner SDK abstraction.
# Run with: uv run --env-file .env python -m tutorial.ring5_tool_runner

import json

import anthropic
from anthropic import beta_tool

from lib.formatter import MessageFormatter

fmt = MessageFormatter()
client = anthropic.Anthropic()


@beta_tool
def create_calendar_event(
    title: str,
    start: str,
    end: str,
    attendees: list[str] | None = None,
    recurrence: dict | None = None,
) -> str:
    """Create a calendar event with attendees and optional recurrence.

    Args:
        title: Event title.
        start: Start time in ISO 8601 format.
        end: End time in ISO 8601 format.
        attendees: Email addresses to invite.
        recurrence: Dict with 'frequency' (daily, weekly, monthly) and 'count'.
    """
    if attendees and len(attendees) > 10:
        raise ValueError("Too many attendees (max 10)")
    return json.dumps({"event_id": "evt_123", "status": "created", "title": title})


@beta_tool
def list_calendar_events(date: str) -> str:
    """List all calendar events on a given date.

    Args:
        date: Date in YYYY-MM-DD format.
    """
    return json.dumps({"events": [{"title": "Existing meeting", "start": "14:00", "end": "15:00"}]})


user_message = {
    "role": "user",
    "content": "Check what I have on Monday, March 30, 2026, then schedule a one-hour planning session that day that avoids any conflicts.",
}
fmt.print(user_message)

runner = client.beta.messages.tool_runner(
    model="claude-opus-5-5",
    max_tokens=16000,
    thinking={"type": "adaptive", "display": "summarized"},
    tools=[create_calendar_event, list_calendar_events],
    messages=[user_message],
)

# The tutorial calls runner.until_done() to get only the final message.
# Iterating instead yields every assistant turn, so each step can be printed.
for message in runner:
    fmt.print(message)
    # The runner caches this response, so calling it here does not run the tools twice.
    fmt.print(runner.generate_tool_call_response())
