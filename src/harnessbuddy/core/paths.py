from __future__ import annotations

from pathlib import Path

_STATE_DIR_NAME = ".harnessbuddy"


def default_state_dir() -> Path:
    """Return the repo-local HarnessBuddy state directory path."""
    return Path(_STATE_DIR_NAME)


def project_dir(state_dir: Path, project_name: str) -> Path:
    """Return the project workspace directory (.harnessbuddy/<project>/)."""
    return state_dir / project_name


def project_src_dir(state_dir: Path, project_name: str) -> Path:
    """Return the cloned source directory (.harnessbuddy/<project>/src/)."""
    return state_dir / project_name / "src"


def project_state_file(state_dir: Path, project_name: str) -> Path:
    """Return the per-project persistent state file (.harnessbuddy/<project>/state.json)."""
    return state_dir / project_name / "state.json"
