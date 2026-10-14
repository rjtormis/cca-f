# Wrong ways to end an agentic loop, and the right one.
# Run with: uv run python end_loop_correctly/anti_patterns.py
#
# This file makes NO API calls and needs no API key. It plays back scripted
# responses with the same shape Claude really returns (thinking + text + tool_use
# in one turn, truncated turns, and so on). Every loop-ending strategy runs on the
# same script, so you can see exactly where each wrong one breaks.

from anthropic.types import Message

# ── scripted responses ────────────────────────────────────────────────────


def msg(stop_reason, *content):
    return Message.model_validate(
        {
            "id": "msg_demo",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": list(content),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )


def thinking(text):
    return {"type": "thinking", "thinking": text, "signature": "sig"}


def text(t):
    return {"type": "text", "text": t}


def tool_use(name, tool_input, i):
    return {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": tool_input}


# A normal task: check the calendar, create the event, confirm. 3 turns.
# Claude often writes text *and* calls a tool in the same turn. Several anti-patterns miss this.
NORMAL_TASK = [
    msg(
        "tool_use",
        thinking("I should check Monday's calendar before booking."),
        text("Let me check your calendar first."),
        tool_use("list_calendar_events", {"date": "2026-03-30"}, 1),
    ),
    msg(
        "tool_use",
        text("You're busy 2–3pm, so 10am is free. I'm done checking. Booking it now."),
        tool_use("create_calendar_event", {"title": "Planning", "start": "2026-03-30T10:00", "end": "2026-03-30T11:00"}, 2),
    ),
    msg("end_turn", text("I've scheduled Planning on Monday, March 30 from 10–11am.")),
]

# A longer task: 4 tool turns before the final answer.
LONG_TASK = [
    msg("tool_use", tool_use("list_calendar_events", {"date": f"2026-03-{30 + i}"}, i)) for i in range(4)
] + [msg("end_turn", text("Booked the standups for the next 4 days."))]

# The model ran out of max_tokens while writing a tool call. The input is cut off:
# "end" and "attendees" are missing, so running it would create a broken event.
TRUNCATED_TASK = [
    msg(
        "max_tokens",
        text("Creating the event with all 3 attendees now."),
        tool_use("create_calendar_event", {"title": "Sync", "start": "2026-03-30T10:00"}, 1),
    ),
]

# ── the loop runner shared by every strategy ──────────────────────────────


class Stop(Exception):
    pass


def run_loop(script, keep_going, max_turns=10):
    """Play the script back through a loop that uses `keep_going(response, turn)` to decide.

    Returns (turns_used, tools_run, final_text, note) so the result can be checked.
    """
    tools_run, final_text, note = [], None, ""
    turn = 0
    while True:
        if turn >= len(script):
            note = "asked for another turn after Claude had already finished (an extra API call in real code)"
            break
        if turn >= max_turns:
            note = f"hit the {max_turns}-turn cap"
            break
        response = script[turn]
        turn += 1
        try:
            go_on = keep_going(response, turn)
        except Stop as stop:
            note = str(stop)
            break
        # A naive loop runs every tool_use block it sees, as most hand-written loops do.
        tools_run += [b.name for b in response.content if b.type == "tool_use"]
        texts = [b.text for b in response.content if b.type == "text"]
        final_text = texts[-1] if texts else final_text
        if not go_on:
            break
    return turn, tools_run, final_text, note


# ── anti-patterns ─────────────────────────────────────────────────────────


def stop_when_text_appears(response, turn):
    """❌ "Claude said something, so it must be done." Turns often contain text AND a tool call."""
    return not any(b.type == "text" for b in response.content)


def stop_on_done_phrase(response, turn):
    """❌ Search the text for words like "done". The wording is unpredictable: Claude can say "done" mid-task, or never say it."""
    said = " ".join(b.text for b in response.content if b.type == "text").lower()
    return not any(word in said for word in ("done", "finished", "complete"))


def first_block_is_tool(response, turn):
    """❌ Check only content[0]. The first block is often thinking or text, not the tool_use."""
    return response.content[0].type == "tool_use"


def fixed_iterations(response, turn, limit=3):
    """❌ Use an iteration count as the stop signal. It cuts long tasks off and pretends they succeeded."""
    return turn < limit


def until_end_turn(response, turn):
    """❌ `while stop_reason != "end_turn"`. Keeps going on max_tokens or refusal, so it runs truncated tool calls."""
    return response.stop_reason != "end_turn"


# ── the correct pattern ───────────────────────────────────────────────────


def by_stop_reason(response, turn):
    """✅ Check stop_reason explicitly. Continue only on tool_use (and pause_turn). Stop on end_turn.
    Treat max_tokens and refusal as errors, and never run that turn's tools."""
    match response.stop_reason:
        case "tool_use" | "pause_turn":
            return True
        case "end_turn" | "stop_sequence":
            return False
        case "max_tokens":
            raise Stop("stopped on max_tokens WITHOUT running the truncated tool call (retry with more max_tokens)")
        case "refusal":
            raise Stop("stopped on refusal without running any tools")
        case other:
            raise Stop(f"unexpected stop_reason {other!r}")


# ── scoring ───────────────────────────────────────────────────────────────

STRATEGIES = [
    stop_when_text_appears,
    stop_on_done_phrase,
    first_block_is_tool,
    fixed_iterations,
    until_end_turn,
    by_stop_reason,
]

# What a correct loop should do on each script.
SCENARIOS = [
    ("normal task (3 turns)", NORMAL_TASK, {"turns": 3, "must_run": "create_calendar_event", "truncated_ok": True}),
    ("long task (5 turns)", LONG_TASK, {"turns": 5, "must_run": None, "truncated_ok": True}),
    ("truncated by max_tokens", TRUNCATED_TASK, {"turns": 1, "must_run": None, "truncated_ok": False}),
]


def verdict(turns, tools_run, note, expect):
    if not expect["truncated_ok"] and tools_run:
        return False, f"ran a truncated tool call: {tools_run}"
    if "extra API call" in note:
        return False, note
    if turns != expect["turns"]:
        return False, f"stopped after turn {turns} of {expect['turns']}" + (f": {note}" if note else "")
    if expect["must_run"] and expect["must_run"] not in tools_run:
        return False, f"{expect['must_run']} never ran"
    return True, note or "stopped at the right turn"


if __name__ == "__main__":
    for strategy in STRATEGIES:
        print(f"\n{strategy.__name__}")
        print(f"  {strategy.__doc__.strip().splitlines()[0]}")
        for label, script, expect in SCENARIOS:
            turns, tools_run, _final, note = run_loop(script, strategy)
            ok, why = verdict(turns, tools_run, note, expect)
            print(f"    {'✅' if ok else '❌'} {label:<26} {why}")
