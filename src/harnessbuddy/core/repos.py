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


class CloneFailedError(Exception):
    """A git operation against the remote repository failed, carrying git's own stderr."""


class LocalRepoRefError(Exception):
    """--repo-ref was combined with a local path, where it cannot be honoured."""


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


def clean_project_dir(project_dir: Path, keep: set[Path]) -> None:
    """Empty project_dir, preserving the paths in keep and any child that contains one.

    A kept path is not always a direct child, so containment is matched as well as equality:
    deleting the child that holds a nested kept path would take it along too.
    """
    keep_resolved = {p.resolve() for p in keep}
    for child in project_dir.iterdir():
        resolved = child.resolve()
        if any(kept == resolved or kept.is_relative_to(resolved) for kept in keep_resolved):
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _run_git(command: list[str], *, cwd: Path | None = None) -> None:
    """Run a git command, raising CloneFailedError with git's own stderr on failure.

    `check=True` would surface an unreachable host or a bad ref as a CalledProcessError
    traceback; callers need a typed failure to report as an ingestion diagnostic.
    """
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CloneFailedError(
            f"git {' '.join(command[1:])} failed (exit {result.returncode}):\n"
            f"{result.stderr.strip()}"
        )


def ingest_url(
    url: str,
    *,
    project_name: str | None = None,
    repo_ref: str | None = None,
    state_dir: Path,
) -> RepoSource:
    """Clone a remote repository into state_dir and return a RepoSource.

    The repo_ref checkout and the submodule update run on the fresh-clone and already-cloned
    paths alike: `git clean -fdx` wipes untracked submodule content and a checkout can move
    submodule pointers, so without the update a re-run builds against empty or stale trees.

    state.json survives the workspace wipe, so the dependencies learned across prior runs are
    not discarded on every re-run.
    """
    name = (project_name or name_from_url(url)).lower()
    project_dir = state_dir / name
    dest = project_dir / "src"
    repo_source = RepoSource(source_path=dest, clone_url=url, project_name=name, repo_ref=repo_ref)
    state_file = project_state_file(state_dir, name)

    if not dest.exists():
        _run_git(["git", "clone", "--recursive", url, str(dest)])
    else:
        clean_project_dir(project_dir, keep={dest, state_file})
        _run_git(["git", "reset", "--hard"], cwd=dest)
        _run_git(["git", "clean", "-fdx"], cwd=dest)
    if repo_ref:
        _run_git(["git", "checkout", repo_ref], cwd=dest)
    _run_git(["git", "submodule", "update", "--init", "--recursive"], cwd=dest)
    return repo_source


def ingest_local(
    path: Path,
    *,
    project_name: str | None = None,
    repo_ref: str | None = None,
    state_dir: Path,
) -> RepoSource:
    """Validate a local repository path and return a RepoSource.

    Resets the project workspace as ingest_url does, so a previous run's Dockerfile, scripts,
    and agent report cannot be mistaken for this run's. The user's source directory is never
    touched, including when it sits inside the workspace being reset — a deliberate layout,
    since staging a copy at <state_dir>/<project>/src is what earns $SCRIPT_DIR/src-relative
    scripts in the output. Passing it to `keep` is what makes that safe.

    Raises RepositoryNotFoundError if the path does not exist or is not a directory.
    Raises NoCloneableOriginError if the repository has no cloneable git remote origin.
    Raises LocalRepoRefError if repo_ref is set: honouring it would check out a ref in a
    working tree the user owns, and ignoring it would ship a setup.sh and Dockerfile pinning a
    ref that was never built.
    """
    if not path.exists() or not path.is_dir():
        raise RepositoryNotFoundError(f"Local path does not exist or is not a directory: {path}")
    if repo_ref is not None:
        raise LocalRepoRefError(
            f"--repo-ref {repo_ref!r} cannot be applied to the local path {path}: "
            "HarnessBuddy will not check out a ref in a working tree you own, and the "
            "generated output would otherwise claim a ref it never built. "
            f"Check out {repo_ref!r} yourself first, or pass the repository URL instead."
        )
    name = (project_name or path.name).lower()
    origin = _get_git_origin(path)
    if origin is None:
        raise NoCloneableOriginError(
            f"Local repository has no cloneable git origin: {path}. "
            "Generated Dockerfiles require a cloneable URL. "
            "Add a remote with: git remote add origin <url>"
        )
    project_directory = state_dir / name
    if project_directory.is_dir():
        clean_project_dir(
            project_directory, keep={project_state_file(state_dir, name), path.resolve()}
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
