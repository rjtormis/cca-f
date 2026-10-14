# Ending an agentic loop correctly, against the real API.
# Run with: uv run --env-file .env python end_loop_correctly/correct_loop.py
#           uv run --env-file .env python end_loop_correctly/correct_loop.py 150   ← tiny max_tokens, to see the max_tokens branch
#
# The rules (see anti_patterns.py for what goes wrong without them):
#   1. stop_reason drives the loop: continue on "tool_use" / "pause_turn", finish on "end_turn".
#   2. "max_tokens" and "refusal" are not success. Never run tools from those turns.
#   3. MAX_ITERATIONS is a safety net for runaway loops. Hitting it is an error, not a normal finish.
#   4. Look for tool_use blocks anywhere in content, not just content[0], and run all of them.

import json
import sys
from pathlib import Path

import anthropic

# Put the project root on the path so `lib` can be imported when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.formatter import MessageFormatter

fmt = MessageFormatter()
client = anthropic.Anthropic()

MAX_ITERATIONS = 10  # safety net: a real task here needs about 3
MAX_TOKENS_CEILING = 32000  # don't retry truncated turns past this

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
        "description": "Create a calendar event with attendees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "attendees": {"type": "array", "items": {"type": "string", "format": "email"}},
            },
            "required": ["title", "start", "end"],
        },
    },
]


def run_tool(name, tool_input):
    if name == "list_calendar_events":
        return {"events": [{"title": "Existing meeting", "start": "14:00", "end": "15:00"}]}
    if name == "create_calendar_event":
        return {"event_id": "evt_123", "status": "created", "title": tool_input["title"]}
    raise ValueError(f"Unknown tool: {name}")


class LoopError(Exception):
    """The loop ended without Claude finishing the task."""


def run_agent(request, max_tokens=16000):
    messages = [{"role": "user", "content": request}]
    fmt.print(messages[0])

    for iteration in range(1, MAX_ITERATIONS + 1):
        response = client.messages.create(
            model="claude-opus-5-5",
            max_tokens=max_tokens,
            thinking={"type": "adaptive", "display": "summarized"},
            tools=tools,
            messages=messages,
        )
        fmt.print(response)
        print(f"   ↳ iteration {iteration}/{MAX_ITERATIONS}: stop_reason={response.stop_reason!r}")

        match response.stop_reason:
            case "end_turn" | "stop_sequence":
                # ✅ Success: Claude decided it is finished. This is the ONLY normal exit.
                return "\n".join(b.text for b in response.content if b.type == "text")

            case "tool_use":
                # Run EVERY tool_use block. The first block may be thinking or text, and there may be several calls.
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    try:
                        result = json.dumps(run_tool(block.name, block.input))
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                    except Exception as exc:
                        tool_results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True}
                        )
                fmt.print(tool_results)
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            case "pause_turn":
                # Server-side tools (web search, code execution) paused a long turn. Send it back as-is
                # to resume. Don't add a "continue" user message.
                messages.append({"role": "assistant", "content": response.content})

            case "max_tokens":
                # ⚠️ Cut off mid-response. Any tool_use input in this turn may be incomplete, so DON'T run it
                # and DON'T append it. Retry the same turn with more room.
                if max_tokens >= MAX_TOKENS_CEILING:
                    raise LoopError(f"Still truncated at max_tokens={max_tokens}.")
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                print(f"   ↳ truncated: retrying this turn with max_tokens={max_tokens}")

            case "refusal":
                # Claude declined. A refusal can cut a tool call off, so never run tools from this turn.
                raise LoopError("Claude declined the request.")

            case other:
                # A stop_reason this loop doesn't know. Fail loudly rather than guess.
                raise LoopError(f"Unhandled stop_reason: {other!r}")

    # 🛑 Only reached if the loop never returned: the safety net was hit while Claude was
    # still asking for tools, so this is NOT success.
    raise LoopError(f"Gave up after {MAX_ITERATIONS} iterations without end_turn.")


if __name__ == "__main__":
    max_tokens = int(sys.argv[1]) if len(sys.argv) > 1 else 16000
    try:
        answer = run_agent(
            "Check what I have on Monday, March 30, 2026, then schedule a one-hour planning session "
            "with alice@example.com that day that avoids any conflicts.",
            max_tokens=max_tokens,
        )
        print(f"\n✅ Finished on end_turn\n   {answer}")
    except LoopError as err:
        print(f"\n🛑 Loop ended without finishing: {err}")
