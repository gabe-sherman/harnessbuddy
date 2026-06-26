from __future__ import annotations

from pathlib import Path

_STATE_DIR_NAME = ".harnessbuddy"


def default_state_dir() -> Path:
    """Return the repo-local HarnessBuddy state directory path."""
    return Path(_STATE_DIR_NAME)
