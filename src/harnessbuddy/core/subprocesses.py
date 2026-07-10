from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


# (command, cwd, timeout) -> RunResult — the one shape both host-subprocess and
# docker-wrapped stage execution share, so callers (exploration.py, harness_explorer.py)
# can swap how a command runs without changing their retry/parsing logic.
Runner = Callable[[list[str], Path, int], RunResult]


def run_command_streaming(command: list[str], cwd: Path, timeout: int) -> RunResult:
    """Run command, printing each line in real-time while capturing combined output."""
    start = time.monotonic()
    lines: list[str] = []
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
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return RunResult(
            stdout="".join(lines),
            stderr="",
            exit_code=-1,
            duration_seconds=time.monotonic() - start,
        )
    return RunResult(
        stdout="".join(lines),
        stderr="",
        exit_code=proc.returncode,
        duration_seconds=time.monotonic() - start,
    )


def run_command(command: list[str], cwd: Path, timeout: int) -> RunResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        duration = time.monotonic() - start
        return RunResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=duration,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        return RunResult(
            stdout=_decode_output(exc.stdout),
            stderr=_decode_output(exc.stderr),
            exit_code=-1,
            duration_seconds=duration,
        )


def _decode_output(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output
