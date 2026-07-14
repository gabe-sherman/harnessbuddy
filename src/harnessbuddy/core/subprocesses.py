from __future__ import annotations

import contextlib
import contextvars
import subprocess
import time
from collections.abc import Callable, Iterator
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

_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("harnessbuddy_quiet", default=False)
_log_path: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "harnessbuddy_log_path", default=None
)


@contextlib.contextmanager
def streaming_context(*, quiet: bool = False, log_path: Path | None = None) -> Iterator[None]:
    """Scope `run_command_streaming`'s live-printing and log-file destination for every
    call made while this context is active (FR-004/FR-011).

    Set once by a phase boundary in cli.py (via `PhaseReporter`) and read implicitly by
    `run_command_streaming` wherever it is actually invoked (exploration.py,
    harness_explorer.py, environments/*) — those modules don't need to thread a
    quiet/log_path parameter through their own signatures for this to work.
    """
    quiet_token = _quiet.set(quiet)
    log_path_token = _log_path.set(log_path)
    try:
        yield
    finally:
        _quiet.reset(quiet_token)
        _log_path.reset(log_path_token)


def _write_log(log_path: Path | None, text: str) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(text)


def run_command_streaming(command: list[str], cwd: Path, timeout: int) -> RunResult:
    """Run command, printing each line in real-time while capturing combined output.

    Live per-line printing is suppressed when the active `streaming_context` has
    quiet=True; the full combined output is always written to that context's
    log_path regardless (FR-004/FR-011), whether this call succeeds, fails, or
    times out.
    """
    quiet = _quiet.get()
    log_path = _log_path.get()
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
            if not quiet:
                print(line, end="", flush=True)
            lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        _write_log(log_path, "".join(lines))
        return RunResult(
            stdout="".join(lines),
            stderr="",
            exit_code=-1,
            duration_seconds=time.monotonic() - start,
        )
    _write_log(log_path, "".join(lines))
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
