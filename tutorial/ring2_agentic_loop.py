# Ring 2: The agentic loop.
# Run with: uv run --env-file .env python -m tutorial.ring2_agentic_loop

import json

import anthropic

from lib.formatter import MessageFormatter

fmt = MessageFormatter()
client = anthropic.Anthropic()

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


def run_tool(name, tool_input):
    if name == "create_calendar_event":
        return {"event_id": "evt_123", "status": "created", "title": tool_input["title"]}
    return {"error": f"Unknown tool: {name}"}


# Keep the full conversation history in a list so each turn sees prior context.
messages = [
    {
        "role": "user",
        "content": "Schedule a weekly team standup every Monday at 9am for the next 4 weeks, starting Monday, March 30, 2026. Invite the whole team: alice@example.com, bob@example.com, carol@example.com.",
    }
]
fmt.print(messages[0])

response = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    thinking={"type": "adaptive", "display": "summarized"},
    tools=tools,
    tool_choice={"type": "auto", "disable_parallel_tool_use": True},
    messages=messages,
)
fmt.print(response)

# Loop until Claude stops asking for tools. Each iteration runs the requested
# tool, appends the result to history, and asks Claude to continue.
while response.stop_reason == "tool_use":
    tool_use = next(block for block in response.content if block.type == "tool_use")
    result = run_tool(tool_use.name, tool_use.input)

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

    messages.append({"role": "assistant", "content": response.content})
    messages.append(tool_result_message)

    response = client.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        thinking={"type": "adaptive", "display": "summarized"},
        tools=tools,
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        messages=messages,
    )
    fmt.print(response)
