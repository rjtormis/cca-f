# Code-driven decision tree (Ring 1, without tool use).
# Run with: uv run --env-file .env python decision_making/code-driven.py
#           uv run --env-file .env python decision_making/code-driven.py "your own request"
#
# In Ring 1, Claude decided whether to call create_calendar_event.
# Here Claude has no tools. Its only job is to turn the request into JSON with a
# fixed shape. Every decision after that is a hard-coded if/else in Python: the
# LLM extracts information, the code decides what happens.

import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic

# The file name has a hyphen, so it can't run with `python -m`. Put the project root on the path for `lib`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.formatter import MessageFormatter

fmt = MessageFormatter()
client = anthropic.Anthropic()

MAX_ATTENDEES = 10
MAX_DURATION_HOURS = 8

# Ring 1's tool input_schema became the output schema. Two fields were added for
# the decision tree to branch on: `intent` and `missing_info`. Structured outputs
# require every property to be listed in `required`, so optional values are nullable.
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["schedule_event", "cancel_event", "other"],
            "description": "What the user wants to do.",
        },
        "title": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"], "description": "ISO 8601 date-time, e.g. 2026-03-30T10:00:00"},
        "end": {"type": ["string", "null"], "description": "ISO 8601 date-time"},
        "attendees": {"type": "array", "items": {"type": "string"}},
        "recurrence": {
            "type": ["object", "null"],
            "properties": {
                "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "count": {"type": "integer"},
            },
            "required": ["frequency", "count"],
            "additionalProperties": False,
        },
        "missing_info": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Details needed to act that the user did not give. Empty if nothing is missing.",
        },
    },
    "required": ["intent", "title", "start", "end", "attendees", "recurrence", "missing_info"],
    "additionalProperties": False,
}

SYSTEM = (
    "You extract calendar requests into JSON. Do not invent details: if the user did not "
    "give a date or time, leave it null and list it in missing_info. If only a duration is "
    "given, compute end from start."
)


def create_calendar_event(event):
    # Plain Python function, as in Ring 1. Only the code calls it, never Claude.
    return {"event_id": "evt_123", "status": "created", "title": event["title"]}


def extract(request):
    """Step 1: the LLM turns free text into structured data. It makes no decisions."""
    user_message = {"role": "user", "content": request}
    fmt.print(user_message)

    response = client.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        thinking={"type": "adaptive", "display": "summarized"},
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        messages=[user_message],
    )
    fmt.print(response)
    return response


def decide(response):
    """Step 2: a fixed decision tree. Each branch is a rule written in code."""
    # Branch 1: did the model return usable output at all?
    if response.stop_reason == "refusal":
        return "REJECTED", "The model declined this request."
    if response.stop_reason == "max_tokens":
        return "ERROR", "The response was cut off before the JSON was complete."

    # Structured outputs guarantee the text block is valid JSON that matches the schema.
    data = json.loads(next(b.text for b in response.content if b.type == "text"))

    # Branch 2: route on intent.
    if data["intent"] == "other":
        return "OUT_OF_SCOPE", "I can only help with calendar events."
    if data["intent"] == "cancel_event":
        return "NOT_SUPPORTED", "Cancelling events isn't supported yet."

    # Branch 3: intent is schedule_event. Is anything missing?
    if data["missing_info"] or not (data["start"] and data["end"]):
        missing = ", ".join(data["missing_info"]) or "start/end time"
        return "NEEDS_CLARIFICATION", f"Please tell me: {missing}."

    # Branch 4: business rules the model is never trusted to enforce.
    if len(data["attendees"]) > MAX_ATTENDEES:
        return "REJECTED", f"Too many attendees ({len(data['attendees'])}, max {MAX_ATTENDEES})."

    try:
        start = datetime.fromisoformat(data["start"])
        end = datetime.fromisoformat(data["end"])
    except ValueError:
        return "ERROR", f"Couldn't parse the times: {data['start']} → {data['end']}"
    if end <= start:
        return "REJECTED", "The event ends before it starts."
    if (end - start).total_seconds() > MAX_DURATION_HOURS * 3600:
        return "REJECTED", f"Events longer than {MAX_DURATION_HOURS} hours aren't allowed."

    # Branch 5: every check passed, so the code (not Claude) runs the action.
    result = create_calendar_event(data)
    who = ", ".join(data["attendees"]) or "no attendees"
    return "CREATED", f"{result['title']} on {start:%a %b %d, %Y %H:%M}–{end:%H:%M} with {who} ({result['event_id']})."


# Requests chosen so that each one lands on a different branch of the tree.
SAMPLE_REQUESTS = [
    # Ring 1's original request → CREATED
    "Schedule a 30-minute sync with alice@example.com and bob@example.com on Monday, March 30, 2026 at 10am.",
    # no date or time → NEEDS_CLARIFICATION
    "Set up a design review with carol@example.com.",
    # 15 attendees → REJECTED by the attendee rule
    "Schedule a one-hour all-hands on Monday, March 30, 2026 at 10am with everyone: "
    + ", ".join(f"user{i}@example.com" for i in range(15)),
    # not a calendar request → OUT_OF_SCOPE
    "What's a good recipe for banana bread?",
]

if __name__ == "__main__":
    requests = sys.argv[1:] or SAMPLE_REQUESTS
    for request in requests:
        outcome, message = decide(extract(request))
        print(f"\n🌳 Decision: {outcome}\n   {message}\n" + "═" * 80)
