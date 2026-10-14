"""Pretty terminal output for Claude conversations.

Works with both:
- the Claude Agent SDK: every message yielded by `query()` /
  `ClaudeSDKClient.receive_response()` (thinking, text, tool calls, tool
  results, system events, rate limits, final result summary);
- the Anthropic client (`anthropic` package): `Message` / `BetaMessage`
  responses, message params you send (`{"role": "user", "content": [...]}`)
  and plain lists of content blocks such as the `tool_result` dicts you build
  in a manual agentic loop.
"""

import json
import os
import shutil
import sys
import textwrap
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import (
    RateLimitEvent,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
)

_RESET = "\033[0m"
_STYLES = {
    "dim": "\033[2m",
    "bold": "\033[1m",
    "italic": "\033[3m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "gray": "\033[90m",
}


def _get(obj: Any, key: str) -> Any:
    """Read a field from a dict or an object (pydantic model / dataclass)."""
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class MessageFormatter:
    """Formats SDK messages into readable, labelled terminal sections.

    Args:
        color: Use ANSI colors. Defaults to auto-detect (respects NO_COLOR / FORCE_COLOR).
        show_thinking: Render ThinkingBlocks.
        show_system: Render SystemMessage events (init, task progress, hooks, ...).
        show_usage: Include token usage / cost in the final result summary.
        max_result_chars: Truncate tool results longer than this (None = no limit).
        width: Wrap width for prose; defaults to the terminal width.
    """

    def __init__(
        self,
        *,
        color: bool | None = None,
        show_thinking: bool = True,
        show_system: bool = True,
        show_usage: bool = True,
        max_result_chars: int | None = 2000,
        width: int | None = None,
        stream=None,
    ):
        self.stream = stream or sys.stdout
        self.color = _supports_color(self.stream) if color is None else color
        self.show_thinking = show_thinking
        self.show_system = show_system
        self.show_usage = show_usage
        self.max_result_chars = max_result_chars
        self.width = width or min(shutil.get_terminal_size((100, 24)).columns, 120)
        # tool_use_id -> tool name, so results can be labelled with the tool that produced them
        self._tool_names: dict[str, str] = {}

    # ── public API ────────────────────────────────────────────────────────

    def format(self, message: Any) -> str:
        """Return the formatted string for one message ("" if nothing to show)."""
        if isinstance(message, AssistantMessage):
            return self._join(self._block(b, message.parent_tool_use_id) for b in message.content)
        if isinstance(message, UserMessage):
            return self._user(message)
        if isinstance(message, ResultMessage):
            return self._result(message)
        if isinstance(message, SystemMessage):
            return self._system(message) if self.show_system else ""
        if isinstance(message, RateLimitEvent):
            return self._rate_limit(message)
        if isinstance(message, StreamEvent):
            return ""  # partial deltas; the full AssistantMessage follows
        if message is None:
            return ""
        # anthropic client: a Message / BetaMessage returned by messages.create() or the tool runner
        if _get(message, "type") == "message":
            return self._api_message(message)
        # anthropic client: a message param you send, e.g. {"role": "user", "content": [tool_result, ...]}
        if isinstance(message, dict) and "role" in message:
            return self._api_param(message)
        # a bare list of content blocks, e.g. the tool_results list built in a manual loop
        if isinstance(message, list):
            return self._join(self._block(b, None) for b in message)
        return self._header("?", type(message).__name__, "gray") + "\n" + self._indent(repr(message))

    def print(self, message: Any) -> None:
        out = self.format(message)
        if out:
            print(out, file=self.stream, flush=True)

    # ── content blocks ────────────────────────────────────────────────────

    def _block(self, block: Any, parent: str | None) -> str:
        sub = self._sub_label(parent)
        if isinstance(block, ThinkingBlock):
            return self._thinking(block.thinking, sub)
        if isinstance(block, TextBlock):
            return self._text(block.text, sub)
        if isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
            return self._tool_use(block.id, block.name, block.input, isinstance(block, ServerToolUseBlock), sub)
        if isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
            return self._tool_result(block.tool_use_id, block.content, getattr(block, "is_error", False), sub)
        # anthropic client blocks (pydantic models) and request params (dicts) carry a "type" field
        if (kind := _get(block, "type")) is not None:
            return self._api_block(block, kind, sub)
        return self._header("?", type(block).__name__, "gray") + "\n" + self._indent(repr(block))

    def _api_block(self, block: Any, kind: str, sub: str) -> str:
        if kind == "thinking":
            return self._thinking(_get(block, "thinking") or "", sub)
        if kind == "redacted_thinking":
            return self._thinking("(redacted)", sub) if self.show_thinking else ""
        if kind == "text":
            return self._text(_get(block, "text") or "", sub)
        if kind in ("tool_use", "server_tool_use"):
            return self._tool_use(
                _get(block, "id"), _get(block, "name"), _get(block, "input"), kind == "server_tool_use", sub
            )
        if kind.endswith("tool_result"):  # tool_result, web_search_tool_result, ...
            return self._tool_result(_get(block, "tool_use_id"), _get(block, "content"), _get(block, "is_error"), sub)
        body = block if isinstance(block, dict) else block.model_dump() if hasattr(block, "model_dump") else repr(block)
        return self._header("•", kind, "gray") + "\n" + self._c(self._indent(self._pretty(body)), "gray")

    def _thinking(self, text: str, sub: str) -> str:
        if not self.show_thinking:
            return ""
        body = text.strip() or "(thinking omitted — set thinking display to 'summarized')"
        return self._header("💭", f"Thinking{sub}", "magenta") + "\n" + self._c(self._wrap(body), "dim", "italic")

    def _text(self, text: str, sub: str) -> str:
        text = text.strip()
        try:
            # Structured-output replies are JSON. Indent them instead of wrapping mid-token.
            body = self._indent(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
        except ValueError:
            body = self._wrap(text)
        return self._header("💬", f"Claude{sub}", "cyan") + "\n" + body

    def _tool_use(self, tool_id: str, name: str, tool_input: Any, server: bool, sub: str) -> str:
        self._tool_names[tool_id] = name
        kind = "Server tool" if server else "Tool call"
        head = self._header("🔧", f"{kind}{sub}: {self._c(name, 'bold')}", "yellow")
        id_line = self._c(f"   id: {tool_id}", "gray")
        return f"{head}\n{id_line}\n{self._indent(self._pretty(tool_input))}"

    def _tool_result(self, tool_use_id: str, content: Any, is_error: bool | None, sub: str) -> str:
        name = self._tool_names.get(tool_use_id, "unknown tool")
        icon, label, color = ("❌", "Tool error", "red") if is_error else ("✅", "Tool result", "green")
        head = self._header(icon, f"{label}{sub}: {self._c(name, 'bold')}", color)
        body = self._truncate(self._content_to_text(content))
        return f"{head}\n{self._indent(body or '(empty)')}"

    def _user(self, message: UserMessage) -> str:
        sub = self._sub_label(message.parent_tool_use_id)
        if isinstance(message.content, str):
            return self._header("👤", f"User{sub}", "blue") + "\n" + self._wrap(message.content.strip())
        return self._join(self._block(b, message.parent_tool_use_id) for b in message.content)

    # ── anthropic client (Messages API) ───────────────────────────────────

    def _api_message(self, message: Any) -> str:
        out = self._join(self._block(b, None) for b in message.content)
        if not self.show_usage:
            return out
        stop_color = "yellow" if message.stop_reason == "tool_use" else "green"
        if message.stop_reason in ("max_tokens", "refusal"):
            stop_color = "red"
        parts = [f"stop_reason: {self._c(str(message.stop_reason), 'bold', stop_color)}"]
        if u := message.usage:
            parts.append(f"tokens: in {u.input_tokens}  out {u.output_tokens}")
            if u.cache_read_input_tokens:
                parts.append(f"cache read {u.cache_read_input_tokens}")
        return out + "\n" + self._c("   ── ", "gray") + self._c("  ·  ", "gray").join(parts)

    def _api_param(self, message: dict) -> str:
        content = message["content"]
        if isinstance(content, str):
            if message["role"] == "assistant":
                return self._text(content, "")
            return self._header("👤", "User", "blue") + "\n" + self._wrap(content.strip())
        if message["role"] == "user":
            # user text blocks are the prompt; everything else (tool results) renders as blocks
            return self._join(
                self._header("👤", "User", "blue") + "\n" + self._wrap(_get(b, "text").strip())
                if _get(b, "type") == "text"
                else self._block(b, None)
                for b in content
            )
        return self._join(self._block(b, None) for b in content)

    # ── other message types ───────────────────────────────────────────────

    def _system(self, message: SystemMessage) -> str:
        data = message.data or {}
        if message.subtype == "init":
            details = [
                f"model: {data.get('model', '?')}",
                f"cwd: {data.get('cwd', '?')}",
                f"permission mode: {data.get('permissionMode', '?')}",
                f"tools: {', '.join(data.get('tools', [])) or '-'}",
            ]
            if servers := data.get("mcp_servers"):
                details.append("mcp: " + ", ".join(f"{s['name']} ({s['status']})" for s in servers))
            body = "\n".join(details)
        else:
            fields = {k: v for k, v in vars(message).items() if k not in ("subtype", "data", "uuid", "session_id")}
            body = self._pretty(fields or data)
        return self._header("⚙️ ", f"System: {message.subtype}", "gray") + "\n" + self._c(self._indent(body), "gray")

    def _rate_limit(self, event: RateLimitEvent) -> str:
        info = event.rate_limit_info
        util = f"{info.utilization:.0%}" if info.utilization is not None else "?"
        return self._header("⏳", f"Rate limit: {info.status} ({util} used)", "yellow")

    def _result(self, message: ResultMessage) -> str:
        ok = not message.is_error
        icon, color = ("🏁", "green") if ok else ("💥", "red")
        lines = [
            f"status: {message.subtype}" + (f" ({message.stop_reason})" if message.stop_reason else ""),
            f"turns: {message.num_turns}   duration: {message.duration_ms / 1000:.1f}s "
            f"(api {message.duration_api_ms / 1000:.1f}s)",
        ]
        if self.show_usage:
            if message.total_cost_usd is not None:
                lines.append(f"cost: ${message.total_cost_usd:.4f}")
            if u := message.usage:
                lines.append(
                    f"tokens: in {u.get('input_tokens', 0)}  out {u.get('output_tokens', 0)}  "
                    f"cache read {u.get('cache_read_input_tokens', 0)}  "
                    f"cache write {u.get('cache_creation_input_tokens', 0)}"
                )
        lines.append(f"session: {message.session_id}")
        out = self._rule(color) + "\n" + self._header(icon, "Result", color) + "\n" + self._indent("\n".join(lines))
        if message.result:
            out += "\n\n" + self._wrap(message.result.strip())
        return out + "\n" + self._rule(color)

    # ── helpers ───────────────────────────────────────────────────────────

    def _c(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(_STYLES[s] for s in styles) + text + _RESET

    def _header(self, icon: str, title: str, color: str) -> str:
        return "\n" + self._c(f"{icon} {title}", "bold", color)

    def _rule(self, color: str) -> str:
        return "\n" + self._c("─" * self.width, color)

    def _sub_label(self, parent: str | None) -> str:
        return f" [subagent {parent[-8:]}]" if parent else ""

    def _indent(self, text: str) -> str:
        return textwrap.indent(text, "   ")

    def _wrap(self, text: str) -> str:
        # Wrap long prose lines but keep existing line breaks (code, lists).
        width = max(self.width - 3, 20)
        lines = []
        for line in text.splitlines() or [""]:
            lines.extend(textwrap.wrap(line, width, replace_whitespace=False) or [""])
        return self._indent("\n".join(lines))

    def _pretty(self, value: Any) -> str:
        if isinstance(value, dict) and all(isinstance(v, (str, int, float, bool, type(None))) for v in value.values()):
            # Flat dicts read best as key: value lines; multi-line strings stay intact.
            parts = []
            for k, v in value.items():
                if isinstance(v, str) and "\n" in v:
                    parts.append(f"{k}:\n{self._indent(self._truncate(v))}")
                else:
                    parts.append(f"{k}: {v}")
            return "\n".join(parts) or "{}"
        return json.dumps(value, indent=2, default=str, ensure_ascii=False)

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, dict) and item.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(item, indent=2, default=str))
            return "\n".join(parts).strip()
        return json.dumps(content, indent=2, default=str)

    def _truncate(self, text: str) -> str:
        limit = self.max_result_chars
        if limit is None or len(text) <= limit:
            return text
        return text[:limit] + self._c(f"\n… ({len(text) - limit} more chars truncated)", "gray")

    @staticmethod
    def _join(parts) -> str:
        return "\n".join(p for p in parts if p)


_default: MessageFormatter | None = None


def _get_default() -> MessageFormatter:
    global _default
    if _default is None:
        _default = MessageFormatter()
    return _default


def format_message(message: Any) -> str:
    """Format a message with a shared default MessageFormatter."""
    return _get_default().format(message)


def print_message(message: Any) -> None:
    """Print a message with a shared default MessageFormatter."""
    _get_default().print(message)
