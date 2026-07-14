from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from harnessbuddy.core.paths import project_state_file

logger = logging.getLogger(__name__)


class RepositoryNotFoundError(Exception):
    """Local path does not exist or is not a directory."""


class NoCloneableOriginError(Exception):
    """Local repository has no cloneable git remote origin."""


@dataclass
class RepoSource:
    source_path: Path
    clone_url: str
    project_name: str
    repo_ref: str | None = None


def name_from_url(url: str) -> str:
    """Infer a project name from a repository URL basename."""
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "project"


def ingest_url(
    url: str,
    *,
    project_name: str | None = None,
    repo_ref: str | None = None,
    state_dir: Path,
) -> RepoSource:
    """Clone a remote repository into state_dir and return a RepoSource.

    Preserves state.json (dependency-resolution state learned across prior runs, e.g.
    apt/brew packages an agent reported missing) across the workspace wipe below —
    without this, every re-run for the same project would silently discard it.
    """
    name = project_name or name_from_url(url)
    project_dir = state_dir / name
    dest = project_dir / "src"
    repo_source = RepoSource(source_path=dest, clone_url=url, project_name=name, repo_ref=repo_ref)

    state_file = project_state_file(state_dir, name)
    preserved_state = state_file.read_bytes() if state_file.exists() else None

    if project_dir.exists():
        shutil.rmtree(project_dir)  # clear stale workspace state for new runs
    project_dir.mkdir(parents=True, exist_ok=True)

    if preserved_state is not None:
        state_file.write_bytes(preserved_state)

    subprocess.run(["git", "clone", "--recursive", url, str(dest)], check=True)
    return repo_source


def ingest_local(
    path: Path,
    *,
    project_name: str | None = None,
    repo_ref: str | None = None,
) -> RepoSource:
    """Validate a local repository path and return a RepoSource.

    Raises RepositoryNotFoundError if the path does not exist or is not a directory.
    Raises NoCloneableOriginError if the repository has no cloneable git remote origin.
    """
    if not path.exists() or not path.is_dir():
        raise RepositoryNotFoundError(f"Local path does not exist or is not a directory: {path}")
    name = project_name or path.name
    origin = _get_git_origin(path)
    if origin is None:
        raise NoCloneableOriginError(
            f"Local repository has no cloneable git origin: {path}. "
            "Generated Dockerfiles require a cloneable URL. "
            "Add a remote with: git remote add origin <url>"
        )
    return RepoSource(source_path=path, clone_url=origin, project_name=name, repo_ref=repo_ref)


def _get_git_origin(path: Path) -> str | None:
    """Return the git remote origin URL, or None if unavailable."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        return url if url else None
    return None
