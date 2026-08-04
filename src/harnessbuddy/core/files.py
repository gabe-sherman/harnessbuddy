"""Writing files that are meant to be run."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def write_executable(path: Path, text: str) -> Path:
    """Write text to path and make it executable, returning path.

    Every generated shell script goes through here: HarnessBuddy runs these scripts
    itself, hands them to a repair agent to run, and ships them for a user to run, so a
    script that is written but not executable is a defect in all three roles.
    """
    path.write_text(text)
    return make_executable(path)


def copy_executable(source: Path, destination: Path) -> Path:
    """Copy source to destination and make it executable, returning destination.

    Used when a validated script is published verbatim rather than regenerated, so that
    any repair an agent applied survives into the output.
    """
    shutil.copy2(source, destination)
    return make_executable(destination)


def make_executable(path: Path) -> Path:
    """Add the executable bits to an existing file, returning path."""
    path.chmod(path.stat().st_mode | _EXECUTABLE_BITS)
    return path
