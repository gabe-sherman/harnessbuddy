from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AgentActivityKind = Literal[
    "status", "file_read", "file_edit", "command_run", "tool_result", "raw_fallback"
]


@dataclass
class AgentActivityEvent:
    kind: AgentActivityKind
    text: str


@dataclass
class AgentStreamResult:
    combined_text: str
    exit_code: int
    duration_seconds: float
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


_CLAUDE_RECOGNIZED_TYPES = {"assistant", "user", "system", "result"}
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


def _claude_content_block_event(block: dict[str, Any]) -> AgentActivityEvent | None:
    block_type = block.get("type")
    if block_type == "text":
        return AgentActivityEvent("status", block.get("text", ""))
    if block_type == "tool_use":
        return _claude_tool_event(block.get("name", ""), block.get("input", {}))
    if block_type == "tool_result":
        content = block.get("content")
        text = content if isinstance(content, str) else json.dumps(content)
        return AgentActivityEvent("tool_result", text)
    return None


def _parse_claude_line(line: str) -> list[AgentActivityEvent]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return _raw_fallback(line)
    if not isinstance(data, dict) or data.get("type") not in _CLAUDE_RECOGNIZED_TYPES:
        return _raw_fallback(line)
    event_type = data["type"]
    if event_type not in ("assistant", "user"):
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


_CODEX_RECOGNIZED_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "item.started",
    "item.updated",
    "item.completed",
}


def _codex_item_event(item: dict[str, Any]) -> AgentActivityEvent | None:
    item_type = item.get("type")
    if item_type == "command_execution":
        return AgentActivityEvent("command_run", f"Running: {item.get('command', '?')}")
    if item_type == "file_change":
        return AgentActivityEvent("file_edit", f"Editing {item.get('path', '?')}")
    return None


def _parse_codex_line(line: str) -> list[AgentActivityEvent]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return _raw_fallback(line)
    if not isinstance(data, dict) or data.get("type") not in _CODEX_RECOGNIZED_TYPES:
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


def _claude_result_cost(line: str) -> float | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "result":
        return None
    cost = data.get("total_cost_usd")
    return cost if isinstance(cost, int | float) else None


def _codex_turn_completed_usage(line: str) -> tuple[int | None, int | None]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict) or data.get("type") != "turn.completed":
        return None, None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return usage.get("input_tokens"), usage.get("output_tokens")


def _extract_stats(tool: str, line: str) -> tuple[float | None, int | None, int | None]:
    if tool == "claude":
        return _claude_result_cost(line), None, None
    input_tokens, output_tokens = _codex_turn_completed_usage(line)
    return None, input_tokens, output_tokens


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
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
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
            line_cost, line_input, line_output = _extract_stats(tool, line)
            if line_cost is not None:
                cost_usd = line_cost
            if line_input is not None:
                input_tokens, output_tokens = line_input, line_output
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
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
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
