"""Locating the data files HarnessBuddy ships: agent skills and the build gate scripts.

These live under `src/harnessbuddy/agents/` and are resolved through
`importlib.resources`, so they are found the same way whether HarnessBuddy runs from a
git checkout or from an installed wheel. A missing file is an error, not a degraded mode:
without the gate scripts no build can be verified, and without a skill file a repair agent
would be prompted with a single sentence instead of its instructions.
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

_AGENTS_PACKAGE = "harnessbuddy.agents"


def agent_script(name: str) -> Path:
    """Return the filesystem path of agents/scripts/<name>.

    A real path rather than a stream: these are shell scripts that get invoked as
    subprocess arguments, and one of them is bind-mounted into a container.
    """
    return _resource_path(f"scripts/{name}")


def skill_instructions(agent_name: str) -> str:
    """Return the text of agents/<agent_name>/SKILL.md — a repair agent's instructions."""
    return _resource_path(f"{agent_name}/SKILL.md").read_text()


def _resource_path(relative_path: str) -> Path:
    resource = files(_AGENTS_PACKAGE).joinpath(relative_path)
    # as_file materializes the resource on disk if the distribution is zipped. Everything
    # HarnessBuddy ships is installed unzipped, so the context exits with the path still
    # valid; entering it is what keeps that assumption explicit rather than accidental.
    with as_file(resource) as path:
        if not path.is_file():
            raise FileNotFoundError(
                f"HarnessBuddy resource {relative_path!r} is missing from the "
                f"{_AGENTS_PACKAGE} package — the installation is incomplete."
            )
        return path
