from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
import shutil

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
    """Clone a remote repository into state_dir and return a RepoSource."""
    name = project_name or name_from_url(url)
    dest = state_dir / name / "src"
    repo_source = RepoSource(source_path=dest, clone_url=url, project_name=name, repo_ref=repo_ref)
    if dest.parent.exists():
        shutil.rmtree(dest.parent) # clear the working state for new runs
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", url, str(dest)], check=True)
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
