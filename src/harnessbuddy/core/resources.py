"""Locating the data files HarnessBuddy ships: agent skills and the build gate scripts.

These live under `src/harnessbuddy/agents/` and resolve through `importlib.resources`, so a
git checkout and an installed wheel find them the same way. A missing file is an error, not a
degraded mode: without the gate scripts no build can be verified, and without a skill file a
repair agent gets a one-sentence prompt instead of its instructions.
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

_AGENTS_PACKAGE = "harnessbuddy.agents"


def agent_script(name: str) -> Path:
    """Return the filesystem path of agents/scripts/<name>.

    A path rather than a stream: these are shell scripts passed as subprocess arguments, and
    one of them is bind-mounted into a container.
    """
    return _resource_path(f"scripts/{name}")


def skill_instructions(agent_name: str) -> str:
    """Return the text of agents/<agent_name>/SKILL.md — a repair agent's instructions."""
    return _resource_path(f"{agent_name}/SKILL.md").read_text()


def _resource_path(relative_path: str) -> Path:
    resource = files(_AGENTS_PACKAGE).joinpath(relative_path)
    # as_file materializes the resource if the distribution is zipped. HarnessBuddy installs
    # unzipped, so the path stays valid after the context exits.
    with as_file(resource) as path:
        if not path.is_file():
            raise FileNotFoundError(
                f"HarnessBuddy resource {relative_path!r} is missing from the "
                f"{_AGENTS_PACKAGE} package — the installation is incomplete."
            )
        return path
