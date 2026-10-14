# Ring 1: Single tool, single turn.
# Run with: uv run --env-file .env python -m tutorial.ring1_single_tool

import json

import anthropic

from lib.formatter import MessageFormatter

fmt = MessageFormatter()

# Create a client. It reads ANTHROPIC_API_KEY from the environment.
client = anthropic.Anthropic()

# Define one tool. The input_schema is a JSON Schema object describing
# the arguments Claude should pass when it calls this tool.
tools = [
    {
        "name": "create_calendar_event",
        "description": "Create a calendar event with attendees and optional recurrence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string", "format": "email"},
                },
                "recurrence": {
                    "type": "object",
                    "properties": {
                        "frequency": {"enum": ["daily", "weekly", "monthly"]},
                        "count": {"type": "integer", "minimum": 1},
                    },
                },
            },
            "required": ["title", "start", "end"],
        },
    }
]

user_message = {
    "role": "user",
    "content": "Schedule a 30-minute sync with alice@example.com and bob@example.com on Monday, March 30, 2026 at 10am.",
}
fmt.print(user_message)

# Send the user's request along with the tool definition. Claude decides
# whether to call the tool based on the request and the tool description.
response = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    thinking={"type": "adaptive", "display": "summarized"},  # show Claude's reasoning
    tools=tools,
    tool_choice={"type": "auto", "disable_parallel_tool_use": True},
    messages=[user_message],
)
# When Claude calls a tool, stop_reason is "tool_use" and the content
# contains a tool_use block alongside any thinking/text.
fmt.print(response)

# Find the tool_use block. Scan the content rather than assuming position.
tool_use = next(block for block in response.content if block.type == "tool_use")

# Execute the tool. In a real system this would call your calendar API.
result = {"event_id": "evt_123", "status": "created"}

# Send the result back. The tool_use_id must match the tool_use block's id.
tool_result_message = {
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": tool_use.id,
            "content": json.dumps(result),
        }
    ],
}
fmt.print(tool_result_message)

followup = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    thinking={"type": "adaptive", "display": "summarized"},
    tools=tools,
    tool_choice={"type": "auto", "disable_parallel_tool_use": True},
    messages=[
        user_message,
        {"role": "assistant", "content": response.content},
        tool_result_message,
    ],
)
# With the tool result in hand, Claude answers and stop_reason becomes "end_turn".
fmt.print(followup)
