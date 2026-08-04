from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO


class MergedOutput:
    """Adds `output` to a result type that carries a command's two output streams.

    Whether `stderr` holds anything depends on which runner produced the result:
    `run_command_streaming` merges the child's stderr into stdout and leaves `stderr`
    empty, while `run_command` keeps them apart. Every caller that scans output for
    diagnostics (undefined symbols, missing packages, docker errors) wants both, and
    should not have to know which runner it got.
    """

    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


@dataclass
class RunResult(MergedOutput):
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


# How long to wait for the reader thread to notice the pipe closed after a kill. The
# thread is a daemon, so an unresponsive grandchild that inherited stdout costs us this
# much delay and is then abandoned rather than deadlocking the run.
_DRAIN_GRACE_SECONDS = 5


def _drain(stream: IO[str], lines: list[str], *, quiet: bool) -> None:
    """Read stream to EOF, collecting each line and optionally echoing it live."""
    for line in stream:
        if not quiet:
            print(line, end="", flush=True)
        lines.append(line)


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    """Kill the child and anything it spawned.

    A build runs `bash build_library.sh`, which spawns make, which spawns compilers.
    Killing only the shell leaves that tree running — and holding the stdout pipe open,
    which is what would keep a "timed out" run from ever returning. The child is started
    in its own session (see `run_command_streaming`) precisely so the whole tree can be
    signalled by process group here.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def run_command_streaming(command: list[str], cwd: Path, timeout: int) -> RunResult:
    """Run command, printing each line in real-time while capturing combined output.

    `timeout` bounds the whole call, not just the wait after the child closes stdout: a
    command that hangs while producing no output (waiting on stdin, an infinite configure
    loop) is killed at the deadline and reported as `exit_code=-1`, matching
    `run_command`. Output produced before the deadline is still returned and logged.

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
        start_new_session=True,
    )
    assert proc.stdout is not None
    reader = threading.Thread(
        target=_drain, args=(proc.stdout, lines), kwargs={"quiet": quiet}, daemon=True
    )
    reader.start()
    try:
        exit_code = _await_exit(proc, reader, timeout=timeout, start=start)
    except KeyboardInterrupt:
        # start_new_session detached the child from this process group, so the terminal's
        # Ctrl-C never reached it. Pass it on rather than orphaning a running build.
        _terminate_process_group(proc)
        raise
    _write_log(log_path, "".join(lines))
    return RunResult(
        stdout="".join(lines),
        stderr="",
        exit_code=exit_code,
        duration_seconds=time.monotonic() - start,
    )


def _await_exit(
    proc: subprocess.Popen[str], reader: threading.Thread, *, timeout: int, start: float
) -> int:
    """Wait for proc to finish within the deadline, killing it if it doesn't.

    Returns its exit code, or -1 on timeout.
    """
    reader.join(timeout)
    if not reader.is_alive():
        remaining = max(timeout - (time.monotonic() - start), 0.0)
        with contextlib.suppress(subprocess.TimeoutExpired):
            return proc.wait(timeout=remaining)
    _terminate_process_group(proc)
    reader.join(_DRAIN_GRACE_SECONDS)
    proc.wait()
    return -1


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
