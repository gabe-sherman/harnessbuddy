"""Writing files that are meant to be run."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def write_executable(path: Path, text: str) -> Path:
    """Write text to path and make it executable, returning path.

    Every generated shell script goes through here. HarnessBuddy runs them, repair agents run
    them, and users run them, so one written without the executable bit is broken three ways.
    """
    path.write_text(text)
    return make_executable(path)


def copy_executable(source: Path, destination: Path) -> Path:
    """Copy source to destination and make it executable, returning destination.

    For publishing a validated script verbatim rather than regenerating it, so any repair an
    agent applied survives into the output.
    """
    shutil.copy2(source, destination)
    return make_executable(destination)


def make_executable(path: Path) -> Path:
    """Add the executable bits to an existing file, returning path."""
    path.chmod(path.stat().st_mode | _EXECUTABLE_BITS)
    return path
