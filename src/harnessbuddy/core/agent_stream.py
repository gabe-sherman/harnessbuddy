from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AgentActivityKind = Literal[
    "model_text", "status", "file_read", "file_edit", "command_run", "tool_result", "raw_fallback"
]


@dataclass
class AgentActivityEvent:
    kind: AgentActivityKind
    text: str


@dataclass
class AgentStreamResult:
    """One agent invocation's output, split into two channels.

    `combined_text` is the whole rendered transcript — every event, including tool
    results (file contents the agent read, output of commands it ran) and our own
    narration lines. It's what gets persisted for a human to read.

    `model_text` is only what the *model itself* wrote as its response: Claude `text`
    content blocks and Codex `agent_message` items. Match on this, not on
    `combined_text`, when looking for a marker the model was instructed to *print* —
    e.g. `ACTION REQUIRED`. Searching the full transcript makes any file quoting the
    marker (its own SKILL.md, notably) trip it, failing a build that succeeded.

    Thinking/reasoning blocks are deliberation, not output, so they are deliberately
    excluded from `model_text` too: an agent weighing whether to print a marker must
    not be read as having printed it.
    """

    combined_text: str
    exit_code: int
    duration_seconds: float
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_text: str = ""


_CLAUDE_READ_TOOLS = {"Read"}
_CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_CLAUDE_COMMAND_TOOLS = {"Bash"}


def _raw_fallback(line: str) -> list[AgentActivityEvent]:
    return [AgentActivityEvent("raw_fallback", line.rstrip("\n"))]


def _claude_tool_event(name: str, tool_input: dict[str, Any]) -> AgentActivityEvent:
    if name in _CLAUDE_READ_TOOLS:
        return AgentActivityEvent("file_read", f"Reading {tool_input.get('file_path', '?')}")
    if name in _CLAUDE_EDIT_TOOLS:
        return AgentActivityEvent("file_edit", f"Editing {tool_input.get('file_path', '?')}")
    if name in _CLAUDE_COMMAND_TOOLS:
        return AgentActivityEvent("command_run", f"Running: {tool_input.get('command', '?')}")
    return AgentActivityEvent("status", f"Using tool {name}")


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return json.dumps(content)


def _claude_content_block_event(block: dict[str, Any]) -> AgentActivityEvent | None:
    block_type = block.get("type")
    if block_type == "text":
        return AgentActivityEvent("model_text", block.get("text", ""))
    if block_type == "thinking":
        thinking = block.get("thinking") or ""
        return AgentActivityEvent("status", f"Thinking: {thinking}") if thinking else None
    if block_type == "tool_use":
        return _claude_tool_event(block.get("name", ""), block.get("input", {}))
    if block_type == "tool_result":
        return AgentActivityEvent("tool_result", _tool_result_text(block.get("content")))
    return None


def _parse_claude_line(line: str) -> list[AgentActivityEvent]:
    """Parse one line of `claude --output-format stream-json` output.

    Anthropic adds new top-level event types over time (e.g. `rate_limit_event`) and
    this function does not attempt to enumerate them all. A well-formed JSON object
    with a `type` string is treated as a legitimate event we simply don't render
    narration for (silent, not a fallback) — `raw_fallback` is reserved for input that
    doesn't even look like a structured event, so a truly unexpected/garbled line is
    still visible for diagnosis without every future event type flooding the terminal
    with raw JSON.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return _raw_fallback(line)
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        return _raw_fallback(line)
    if data["type"] not in ("assistant", "user"):
        return []
    content = data.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return []
    events: list[AgentActivityEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        event = _claude_content_block_event(block)
        if event is not None:
            events.append(event)
    return events


def _codex_item_event(item: dict[str, Any]) -> AgentActivityEvent | None:
    item_type = item.get("type")
    if item_type == "command_execution":
        return AgentActivityEvent("command_run", f"Running: {item.get('command', '?')}")
    if item_type == "file_change":
        return AgentActivityEvent("file_edit", f"Editing {item.get('path', '?')}")
    if item_type == "agent_message":
        # Codex's equivalent of a Claude plain-text assistant content block — the
        # model's actual response text, not an internal reasoning trace.
        return AgentActivityEvent("model_text", item.get("text", ""))
    if item_type == "reasoning":
        # Codex's equivalent of Claude's "thinking" content block.
        text = item.get("text") or ""
        return AgentActivityEvent("status", f"Thinking: {text}") if text else None
    if item_type == "error":
        return AgentActivityEvent("status", f"Warning: {item.get('message', '?')}")
    return None


def _parse_codex_line(line: str) -> list[AgentActivityEvent]:
    """Parse one line of `codex exec --json` output.

    Same silent-skip-vs-raw_fallback distinction as `_parse_claude_line`: a
    well-formed JSON object with a `type` string is a legitimate event we may not
    render narration for; `raw_fallback` is reserved for lines that don't look like a
    structured event at all.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return _raw_fallback(line)
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        return _raw_fallback(line)
    if data["type"] not in ("item.started", "item.updated", "item.completed"):
        return []
    item = data.get("item")
    if not isinstance(item, dict):
        return []
    event = _codex_item_event(item)
    return [event] if event is not None else []


_LINE_PARSERS: dict[str, Callable[[str], list[AgentActivityEvent]]] = {
    "claude": _parse_claude_line,
    "codex": _parse_codex_line,
}


def _claude_result_cost(line: str) -> tuple[float | None, int | None, int | None]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(data, dict) or data.get("type") != "result":
        return None, None, None
    cost = data.get("total_cost_usd")
    input_tokens = data.get("usage").get("input_tokens")
    output_tokens = data.get("usage").get("output_tokens")
    return (
        cost if isinstance(cost, float) else None,
        input_tokens if isinstance(input_tokens, int) else None,
        output_tokens if isinstance(output_tokens, int) else None,
    )


def _codex_result_cost(line: str) -> tuple[None, int | None, int | None]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(data, dict) or data.get("type") != "turn.completed":
        return None, None, None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    # usd usage not available
    return None, usage.get("input_tokens"), usage.get("output_tokens")


def _extract_stats(tool: str, line: str) -> tuple[float | None, int | None, int | None]:
    if tool == "claude":
        return _claude_result_cost(line)
    return _codex_result_cost(line)


@dataclass
class _StreamAccumulator:
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _apply_line_stats(acc: _StreamAccumulator, tool: str, line: str) -> None:
    """Update the running stats accumulator from one stdout line, last-value-wins."""
    cost, input_tokens, output_tokens = _extract_stats(tool, line)
    if cost is not None:
        acc.cost_usd = cost
    if input_tokens is not None:
        acc.input_tokens = input_tokens
    if output_tokens is not None:
        acc.output_tokens = output_tokens


def run_agent_streaming(
    command: list[str], cwd: Path, timeout: int, tool: str
) -> AgentStreamResult:
    """Run an agent CLI, rendering its structured event stream as readable lines.

    Mirrors the Popen/line-iteration/TimeoutExpired structure of
    run_command_streaming, but parses each line as a backend-specific structured
    event instead of treating it as opaque text.
    """
    if tool not in _LINE_PARSERS:
        raise ValueError(f"unknown agent tool: {tool!r}")
    parse_line = _LINE_PARSERS[tool]

    start = time.monotonic()
    texts: list[str] = []
    model_texts: list[str] = []
    acc = _StreamAccumulator()
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            for event in parse_line(line):
                print(event.text, flush=True)
                texts.append(event.text)
                if event.kind == "model_text":
                    model_texts.append(event.text)
            _apply_line_stats(acc, tool, line)
        proc.wait(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        exit_code = -1

    return AgentStreamResult(
        combined_text="\n".join(texts),
        exit_code=exit_code,
        duration_seconds=time.monotonic() - start,
        cost_usd=acc.cost_usd,
        input_tokens=acc.input_tokens,
        output_tokens=acc.output_tokens,
        model_text="\n".join(model_texts),
    )


@dataclass
class AgentRunSummary:
    backend: str
    outcome: str
    duration_seconds: float
    cost_usd: float | None
    input_tokens: int | None = None
    output_tokens: int | None = None


def format_agent_summary(summary: AgentRunSummary) -> str:
    """Render the fixed-format '=== Agent Run Summary ===' trailer block."""
    if summary.cost_usd is not None:
        stats_line = f"cost: ${summary.cost_usd:.4f}"
    elif summary.input_tokens is not None and summary.output_tokens is not None:
        stats_line = f"tokens: input={summary.input_tokens} output={summary.output_tokens}"
    else:
        stats_line = "cost: unavailable"

    return (
        "=== Agent Run Summary ===\n"
        f"backend: {summary.backend}\n"
        f"outcome: {summary.outcome}\n"
        f"duration: {summary.duration_seconds:.1f}s\n"
        f"{stats_line}\n"
    )


def write_agent_report(path: Path, combined_text: str, summary: AgentRunSummary) -> None:
    """Persist an invocation's transcript and time/cost summary to a report file."""
    path.write_text(f"{combined_text}\n{format_agent_summary(summary)}")
