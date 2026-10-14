# Model-driven decision making (the counterpart to code-driven.py).
# Run with: uv run --env-file .env python decision_making/model-driven.py
#           uv run --env-file .env python decision_making/model-driven.py "your own request"
#
# In code-driven.py, Claude only extracted JSON and a hard-coded if/else chose
# the outcome. Here the roles are swapped. The rules live in the system prompt
# in plain English, and Claude chooses the path by choosing which tool to call:
#
#   list_calendar_events   → gather information first (as often as it likes)
#   create_calendar_event  → CREATED
#   ask_clarification      → NEEDS_CLARIFICATION
#   decline_request        → REJECTED / OUT_OF_SCOPE / NOT_SUPPORTED
#
# The code has no branches for these outcomes. It runs whichever tool Claude
# picks and reports what Claude decided.

import json
import sys
from pathlib import Path

import anthropic

# The file name has a hyphen, so it can't run with `python -m`. Put the project root on the path for `lib`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.formatter import MessageFormatter

fmt = MessageFormatter()
client = anthropic.Anthropic()

# The same rules that code-driven.py enforces with if/else, written as instructions instead.
SYSTEM = """You are a calendar assistant. Decide how to handle each request by calling exactly one of:
create_calendar_event, ask_clarification, or decline_request. You may call list_calendar_events
first to check for conflicts.

Rules:
- You only handle scheduling new calendar events. Decline anything else, including cancellations.
- Events can have at most 10 attendees and last at most 8 hours.
- Don't invent dates or times. If something essential is missing, ask for it.
- Before creating an event, check that day's calendar and avoid conflicts."""

tools = [
    {
        "name": "list_calendar_events",
        "description": "List all calendar events on a given date.",
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "format": "date"}},
            "required": ["date"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Create a calendar event with attendees and optional recurrence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "attendees": {"type": "array", "items": {"type": "string", "format": "email"}},
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
    },
    {
        "name": "ask_clarification",
        "description": "Ask the user for details you need before you can act. Use instead of guessing.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "decline_request",
        "description": "Decline a request that breaks the rules or is outside what you can do.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"enum": ["rejected", "out_of_scope", "not_supported"]},
                "reason": {"type": "string", "description": "Explanation shown to the user."},
            },
            "required": ["category", "reason"],
        },
    },
]

# Tools that end the run. The one Claude calls is its decision.
TERMINAL_TOOLS = {"create_calendar_event", "ask_clarification", "decline_request"}


def run_tool(name, tool_input):
    """Run whatever Claude picked. No rules are checked here."""
    if name == "list_calendar_events":
        return {"events": [{"title": "Existing meeting", "start": "14:00", "end": "15:00"}]}
    if name == "create_calendar_event":
        # No attendee or duration checks: this file trusts Claude to follow the system prompt.
        # In production, keep a guard in code as well (see the README).
        return {"event_id": "evt_123", "status": "created", "title": tool_input["title"]}
    if name == "ask_clarification":
        return {"status": "question sent to user"}
    if name == "decline_request":
        return {"status": "user notified"}
    raise ValueError(f"Unknown tool: {name}")


def to_outcome(name, tool_input):
    """Turn Claude's chosen tool into a label, so the output matches code-driven.py."""
    if name == "create_calendar_event":
        who = ", ".join(tool_input.get("attendees", [])) or "no attendees"
        return "CREATED", f"{tool_input['title']} {tool_input['start']} → {tool_input['end']} with {who}."
    if name == "ask_clarification":
        return "NEEDS_CLARIFICATION", tool_input["question"]
    return tool_input["category"].upper(), tool_input["reason"]


def run(request):
    """Agentic loop: Claude decides and gathers information until it calls a terminal tool."""
    messages = [{"role": "user", "content": request}]
    fmt.print(messages[0])

    while True:
        response = client.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            thinking={"type": "adaptive", "display": "summarized"},
            system=SYSTEM,
            tools=tools,
            messages=messages,
        )
        fmt.print(response)

        if response.stop_reason == "refusal":
            return "REJECTED", "The model declined this request."
        if response.stop_reason != "tool_use":
            # Claude answered in plain text instead of calling a decision tool.
            text = next((b.text for b in response.content if b.type == "text"), "")
            return "NO_DECISION", text or f"stop_reason: {response.stop_reason}"

        decision = None
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = run_tool(block.name, block.input)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
            if block.name in TERMINAL_TOOLS:
                decision = to_outcome(block.name, block.input)
        fmt.print(tool_results)

        if decision:
            return decision

        # Only information-gathering tools were called, so send the results back and let Claude continue.
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


# The same requests as code-driven.py, plus one that needs the model's judgement.
SAMPLE_REQUESTS = [
    "Schedule a 30-minute sync with alice@example.com and bob@example.com on Monday, March 30, 2026 at 10am.",
    "Set up a design review with carol@example.com.",
    "Schedule a one-hour all-hands on Monday, March 30, 2026 at 10am with everyone: "
    + ", ".join(f"user{i}@example.com" for i in range(15)),
    "What's a good recipe for banana bread?",
    # No time given. The hard-coded tree can only ask for one. Claude can check the calendar and pick a free slot.
    "Find a free hour on Monday, March 30, 2026 afternoon for a 1:1 with dan@example.com.",
]

if __name__ == "__main__":
    requests = sys.argv[1:] or SAMPLE_REQUESTS
    for request in requests:
        outcome, message = run(request)
        print(f"\n🧠 Decision (made by Claude): {outcome}\n   {message}\n" + "═" * 80)
