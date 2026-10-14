# cca-f: Claude Certified Architect (Foundations) study lab

These are my hands-on notes and runnable examples while preparing for the **CCA-F (Claude Certified Architect – Foundations)** certification. Each folder isolates one concept so it can be run, read and compared on its own. Every example prints Claude's thinking, tool calls and tool results, so you can see what happens on each turn.

### What's covered

| Topic | Folder | Concept |
|---|---|---|
| Tool use, step by step | `tutorial/` | Tool schemas → `tool_use` / `tool_result` → agentic loop → parallel calls → `is_error` → Tool Runner |
| Code-driven vs model-driven decisions | `decision_making/` | Fixed `if/else` rules over structured output, compared with Claude choosing the path via tools |
| Ending the agentic loop correctly | `end_loop_correctly/` | `stop_reason` as the loop signal, `max_tokens` / `refusal` handling, iteration caps as safety nets, common anti-patterns |
| Claude Agent SDK | `main.py`, `stop_reason/` | Claude Code's harness with built-in tools, compared with writing the loop yourself |
| Observability | `lib/formatter.py` | One formatter for both the `anthropic` client and the Agent SDK |

> Personal study material, not official Anthropic content. All tools use stub data. The calendar "API" is fake, so nothing real is booked.

### Two ways to build with Claude

| | Library | What you write | Example |
|---|---|---|---|
| **Claude API + tool use** | `anthropic` (`anthropic.Anthropic()`) | Tool schemas, the functions behind them, and the agentic loop | `tutorial/` |
| **Claude Agent SDK** | `claude-agent-sdk` (`query()`, `ClaudeAgentOptions`) | A prompt and options. Claude Code's built-in tools (`Read`, `Edit`, …) and loop do the rest | `main.py` |

## Setup

```bash
uv sync
```

Put your `ANTHROPIC_API_KEY` in `.env`, or export it in your shell.

## Tool-use tutorial (`tutorial/`)

This folder recreates [Tutorial: Build a tool-using agent](https://platform.claude.com/docs/en/agents-and-tools/tool-use/build-a-tool-using-agent) with the `anthropic` client. It builds a calendar agent in five steps. Each step is a standalone file:

| File | What it adds |
|---|---|
| `ring1_single_tool.py` | One tool, one round trip: `tool_use` → you run it → `tool_result` → final answer |
| `ring2_agentic_loop.py` | A `while response.stop_reason == "tool_use"` loop with a running message history |
| `ring3_parallel_tools.py` | A second tool. All `tool_use` blocks in a response are handled and the results go back in **one** user message |
| `ring4_error_handling.py` | Failing tools send back `tool_result` with `is_error: True`, so Claude can recover instead of the program crashing |
| `ring5_tool_runner.py` | Replaces the hand-written loop with `@beta_tool` and `client.beta.messages.tool_runner()` |

Run them as modules from the project root so that `lib` can be imported:

```bash
uv run --env-file .env python -m tutorial.ring1_single_tool
uv run --env-file .env python -m tutorial.ring2_agentic_loop
uv run --env-file .env python -m tutorial.ring3_parallel_tools
uv run --env-file .env python -m tutorial.ring4_error_handling
uv run --env-file .env python -m tutorial.ring5_tool_runner
```

The code matches the tutorial except for these changes:
- Each step is printed with `fmt.print(...)`, so you see the prompt, thinking, every tool call and result, and `stop_reason` and token counts for each turn.
- `thinking={"type": "adaptive", "display": "summarized"}` is set so that the thinking text is shown. Without it, `claude-opus-5-5` returns empty thinking blocks.
- `max_tokens` is 16000 instead of 1024. Thinking is always on with `claude-opus-5-5`, so 1024 can cut a turn short.
- Ring 5 iterates over the runner instead of calling `.until_done()`, so every turn is printed and not just the last one.

## Code-driven decision tree (`decision_making/code-driven.py`)

This is Ring 1 rewritten without tool use. Claude no longer decides what to do. It only turns the request into JSON with a fixed shape (via `output_config.format`). A hard-coded Python decision tree then picks the outcome and runs the action:

```
Claude's response
├─ stop_reason refusal / max_tokens       → REJECTED / ERROR
├─ intent == "other"                      → OUT_OF_SCOPE
├─ intent == "cancel_event"               → NOT_SUPPORTED
├─ missing_info or no start/end           → NEEDS_CLARIFICATION
├─ > 10 attendees                         → REJECTED
├─ end ≤ start, or longer than 8 hours    → REJECTED
└─ otherwise → create_calendar_event()    → CREATED
```

```bash
uv run --env-file .env python decision_making/code-driven.py                      # 4 sample requests, one per branch
uv run --env-file .env python decision_making/code-driven.py "Lunch with dan@example.com Friday at noon"
```

Compared with tool use (`tutorial/`):

| | Tool use (Ring 1) | Code-driven |
|---|---|---|
| Who decides to create the event | Claude, by calling the tool | Your `if/else` |
| Business rules (attendee limit, durations) | Inside the tool. Claude reacts to the errors | Checked before anything runs |
| Paths the program can take | Open-ended | Fixed and predictable, so each branch can be tested |
| What Claude returns | `tool_use` blocks | One JSON object matching your schema |

Run the file as a script, not with `-m`, because the hyphen in `code-driven` isn't allowed in a module name. The file adds the project root to `sys.path` itself so that `lib` can be imported.

## Model-driven decisions (`decision_making/model-driven.py`)

This is the same calendar assistant with the roles swapped. The rules are written as plain English in the system prompt, and **Claude picks the outcome by choosing which tool to call**. The code has no branches for outcomes. It runs whichever tool Claude picks:

```
Claude reads the request + rules
├─ list_calendar_events    → gather info, then keep going (as many times as it wants)
├─ create_calendar_event   → CREATED
├─ ask_clarification       → NEEDS_CLARIFICATION
└─ decline_request         → REJECTED / OUT_OF_SCOPE / NOT_SUPPORTED
```

```bash
uv run --env-file .env python decision_making/model-driven.py      # same 4 samples as code-driven, plus one more
uv run --env-file .env python decision_making/model-driven.py "Find 30 minutes Monday March 30 2026 for a call with eve@example.com"
```

The fifth sample, *"Find a free hour on Monday afternoon…"*, shows where the model-driven approach does better. The hard-coded tree can only reply with NEEDS_CLARIFICATION because no time was given. Claude checks the calendar, sees the 2–3pm meeting, and books 3–4pm.

### Code-driven vs model-driven

| | Code-driven | Model-driven |
|---|---|---|
| Who picks the path | Your `if/else` | Claude, by choosing a tool |
| Where the rules live | Python code | System prompt, in plain English |
| Rule enforcement | Guaranteed. Code always runs it | Very likely but not guaranteed. Claude follows instructions |
| Handles requests you didn't plan for | No, only the branches you wrote | Yes, e.g. finding a free slot or suggesting alternatives |
| Predictable and testable | Fully. The same JSON always takes the same branch | Less so. Test with sample prompts (evals) |
| Cost and latency | 1 API call | 1 or more calls (it may gather info first) |

In practice the two are usually combined. Let the model decide the flow, and keep hard rules (attendee limits, permissions, money) enforced in code inside the tool. `model-driven.py` deliberately leaves out that code guard so the model's own decisions are visible. Ring 4 in `tutorial/` shows how to add the guard.

## Ending the loop correctly (`end_loop_correctly/`)

An agentic loop should be driven by **`response.stop_reason`**, the API's own signal for why Claude stopped. `end_turn` isn't something you call. It's the value the API returns when Claude has finished.

| `stop_reason` | Meaning | What the loop does |
|---|---|---|
| `tool_use` | Claude wants tools run | Run **every** `tool_use` block, send back all results, continue |
| `pause_turn` | A server tool (web search, code execution) paused a long turn | Append the response as-is and continue. Don't add a "continue" message |
| `end_turn` / `stop_sequence` | Claude is finished | **Stop. This is the only successful exit** |
| `max_tokens` | Cut off mid-response | **Don't** run its tools, because their input may be truncated. Retry with more `max_tokens` or fail |
| `refusal` | Claude declined | Stop. Don't run any tools from that turn |

The iteration counter (`MAX_ITERATIONS`) is a **safety net** against runaway loops. It is not how the loop normally ends. If it's hit, that's an error, not a normal finish.

### Anti-patterns (`anti_patterns.py`, offline, no API key needed)

```bash
uv run python end_loop_correctly/anti_patterns.py
```

This plays back scripted responses with the same shape Claude really returns through six loop-ending strategies. Only `stop_reason` passes all three scenarios:

| Strategy | Why it breaks |
|---|---|
| Stop when a text block appears | Claude often writes text **and** calls a tool in the same turn, so the loop stops after turn 1 |
| Search the text for "done" / "finished" | The wording is unpredictable. "I'm done checking, booking now…" stops too early, and a final answer without those words loops forever |
| Check only `content[0].type == "tool_use"` | The first block is often `thinking` or `text` |
| A fixed iteration count as the stop signal | Cuts off longer tasks and reports them as if they succeeded |
| `while stop_reason != "end_turn"` | Keeps going on `max_tokens` and runs a **truncated** tool call |
| ✅ `match response.stop_reason` | Handles every case explicitly |

### Correct loop (`correct_loop.py`, live)

```bash
uv run --env-file .env python end_loop_correctly/correct_loop.py         # normal run: tool_use → tool_use → end_turn
uv run --env-file .env python end_loop_correctly/correct_loop.py 150     # tiny max_tokens, to trigger the truncation branch
```

With `150`, Claude's `create_calendar_event` call gets cut off. The loop skips it, retries the turn with double the `max_tokens`, and then runs the complete call.

## Agent SDK example (`main.py`)

```bash
uv run --env-file .env main.py
```

`main.py` asks Claude to find and fix crash bugs in `buggy.py` using Claude Code's built-in `Read`, `Edit` and `Glob` tools. If `buggy.py` has already been fixed, Claude reports that there is nothing to change.

## Message formatter (`lib/formatter.py`)

The formatter works with both libraries. It shows each message as its own labelled section:

| Section | Agent SDK (`claude_agent_sdk`) | Claude API (`anthropic`) |
|---|---|---|
| 👤 **User** | `UserMessage` with plain text | `{"role": "user", "content": "..."}` |
| 💭 **Thinking** | `ThinkingBlock` | `thinking` block |
| 💬 **Claude** | `TextBlock` | `text` block |
| 🔧 **Tool call** | `ToolUseBlock` / `ServerToolUseBlock` | `tool_use` / `server_tool_use` block, with the tool name, id and input |
| ✅ **Tool result** / ❌ **Tool error** | `ToolResultBlock` | `tool_result` dicts you send back, red when `is_error: True` |
| `── stop_reason · tokens` | (none) | Footer on each `Message` / `BetaMessage` |
| ⚙️ **System** | `SystemMessage`: session init (model, cwd, tools, MCP servers), task progress, hooks | (none) |
| ⏳ **Rate limit** | `RateLimitEvent` | (none) |
| 🏁 **Result** | `ResultMessage`: status, turns, duration, cost, tokens, session id and the final answer | (none) |

Tool results are labelled with the name of the tool that produced them. Messages from subagents are tagged `[subagent <id>]`.

### Basic usage: Claude API (`anthropic`)

Pass `fmt.print()` anything you send or receive: the user message, each response, and the tool results you build.

```python
import anthropic
from lib.formatter import MessageFormatter

fmt = MessageFormatter()
client = anthropic.Anthropic()

fmt.print(messages[0])                      # the user prompt
response = client.messages.create(...)
fmt.print(response)                         # thinking, text, tool calls, stop_reason
...
fmt.print(tool_results)                     # list of tool_result dicts (or the full user message)
```

With the Tool Runner, iterate over the runner to see every turn:

```python
runner = client.beta.messages.tool_runner(...)
for message in runner:
    fmt.print(message)
    fmt.print(runner.generate_tool_call_response())  # cached, so the tools only run once
```

### Basic usage: Agent SDK (`claude_agent_sdk`)

```python
from claude_agent_sdk import ClaudeAgentOptions, query
from lib.formatter import MessageFormatter

formatter = MessageFormatter()

async for message in query(prompt="...", options=ClaudeAgentOptions(...)):
    formatter.print(message)
```

If you don't need to configure it, use the module-level shortcuts:

```python
from lib import print_message, format_message

print_message(message)        # print it
text = format_message(message)  # or get the string, e.g. to log it
```

The formatter also works with `ClaudeSDKClient`:

```python
async with ClaudeSDKClient(options) as client:
    await client.query("...")
    async for message in client.receive_response():
        formatter.print(message)
```

### Showing thinking

Newer models don't return thinking text by default. You only get a signature. To see the reasoning, ask for summarized thinking. The setting is the same in both libraries:

```python
client.messages.create(..., thinking={"type": "adaptive", "display": "summarized"})   # anthropic
ClaudeAgentOptions(thinking={"type": "adaptive", "display": "summarized"})             # Agent SDK
```

If a thinking block comes back empty, the formatter shows a hint to turn on this setting.

### Options

```python
MessageFormatter(
    color=None,             # None = auto (TTY only; respects NO_COLOR / FORCE_COLOR)
    show_thinking=True,     # show thinking blocks
    show_system=True,       # show system/init/task events
    show_usage=True,        # show cost and token counts in the result
    max_result_chars=2000,  # truncate long tool results (None = never truncate)
    width=None,             # wrap width; defaults to terminal width (max 120)
    stream=None,            # output stream; defaults to sys.stdout
)
```

Some examples:

```python
MessageFormatter(show_system=False)                   # hide the init and system noise
MessageFormatter(max_result_chars=None)               # show full tool output
MessageFormatter(color=False, stream=open("run.log", "w"))  # write plain text to a file
```

`StreamEvent` messages, which are partial token deltas, are skipped. The complete `AssistantMessage` that follows them is shown instead.
