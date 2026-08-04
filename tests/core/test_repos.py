from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from harnessbuddy.core.repos import (
    LocalRepoRefError,
    NoCloneableOriginError,
    RepositoryNotFoundError,
    ingest_local,
    name_from_url,
)


def _ingest(path: Path, tmp_path: Path, **kwargs: object):  # type: ignore[no-untyped-def]
    """ingest_local against a throwaway state directory."""
    return ingest_local(path, state_dir=tmp_path / "state", **kwargs)  # type: ignore[arg-type]


# name_from_url


def test_name_from_url_https_with_git_suffix() -> None:
    assert name_from_url("https://github.com/example/mylib.git") == "mylib"


def test_name_from_url_https_without_git_suffix() -> None:
    assert name_from_url("https://github.com/example/mylib") == "mylib"


def test_name_from_url_ssh_format() -> None:
    assert name_from_url("git@github.com:example/mylib.git") == "mylib"


def test_name_from_url_trailing_slash() -> None:
    assert name_from_url("https://github.com/example/mylib/") == "mylib"


# ingest_local — error cases


def test_ingest_local_missing_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    with pytest.raises(RepositoryNotFoundError, match="does not exist"):
        _ingest(missing, tmp_path)


def test_ingest_local_file_path_raises(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with pytest.raises(RepositoryNotFoundError):
        _ingest(f, tmp_path)


def test_ingest_local_without_origin_raises(
    local_repo_without_origin: Path, tmp_path: Path
) -> None:
    with pytest.raises(NoCloneableOriginError, match="no cloneable git origin"):
        _ingest(local_repo_without_origin, tmp_path)


# ingest_local — success with origin


def test_ingest_local_with_origin_returns_source(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    source = _ingest(local_repo_with_origin, tmp_path)
    assert source.source_path == local_repo_with_origin
    assert source.clone_url == "https://github.com/example/mylib.git"


def test_ingest_local_project_name_inferred(local_repo_with_origin: Path, tmp_path: Path) -> None:
    source = _ingest(local_repo_with_origin, tmp_path)
    assert source.project_name == local_repo_with_origin.name


def test_ingest_local_project_name_override(local_repo_with_origin: Path, tmp_path: Path) -> None:
    source = _ingest(local_repo_with_origin, tmp_path, project_name="custom")
    assert source.project_name == "custom"


def test_ingest_local_project_name_override_is_lowercased(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    """Docker rejects uppercase repository/tag names (e.g. the harnessbuddy-dev/<project>
    image tag and OSS-Fuzz's own project-name convention), and every later stage
    (workspace/state/logs directories) derives its path from this same name — so it must
    be normalized once, here, rather than downstream, to avoid a casing mismatch between
    where the repo was actually cloned and where later phases look for it."""
    source = _ingest(local_repo_with_origin, tmp_path, project_name="MyLib")
    assert source.project_name == "mylib"


def test_ingest_local_inferred_project_name_is_lowercased(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "MyLib"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/MyLib.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    source = _ingest(repo, tmp_path)
    assert source.project_name == "mylib"


def test_ingest_local_rejects_a_repo_ref(local_repo_with_origin: Path, tmp_path: Path) -> None:
    """Checking out a ref would mutate a working tree the user owns, and ignoring it would
    ship a setup.sh and Dockerfile pinning a ref that was never built."""
    with pytest.raises(LocalRepoRefError, match=r"v1\.0\.0"):
        _ingest(local_repo_with_origin, tmp_path, repo_ref="v1.0.0")


def test_ingest_local_repo_ref_default_is_none(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    source = _ingest(local_repo_with_origin, tmp_path)
    assert source.repo_ref is None


# workspace reset — a previous run's files must not survive into this one


def test_ingest_local_clears_stale_workspace_files(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    """Without this, a previous run's Dockerfile and scripts survive and get mistaken for
    something this run produced."""
    state_dir = tmp_path / "state"
    workspace = state_dir / local_repo_with_origin.name.lower()
    workspace.mkdir(parents=True)
    (workspace / "Dockerfile").write_text("FROM stale\n")
    (workspace / "harness_source").mkdir()

    _ingest(local_repo_with_origin, tmp_path)

    assert not (workspace / "Dockerfile").exists()
    assert not (workspace / "harness_source").exists()


def test_ingest_local_preserves_learned_dependency_state(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    """state.json holds packages learned across earlier runs; wiping it would make every
    re-run rediscover them."""
    state_dir = tmp_path / "state"
    workspace = state_dir / local_repo_with_origin.name.lower()
    workspace.mkdir(parents=True)
    (workspace / "state.json").write_text('{"apt_packages": ["libzstd-dev"]}')

    _ingest(local_repo_with_origin, tmp_path)

    assert (workspace / "state.json").read_text() == '{"apt_packages": ["libzstd-dev"]}'


def test_ingest_local_never_touches_the_users_source_tree(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    before = sorted(p.name for p in local_repo_with_origin.iterdir())
    _ingest(local_repo_with_origin, tmp_path)
    assert sorted(p.name for p in local_repo_with_origin.iterdir()) == before


def test_ingest_local_keeps_a_source_tree_staged_inside_the_workspace(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    """Staging a copy of the source at <state_dir>/<project>/src is a deliberate layout — it is
    what satisfies is_standard_source_layout, so the published scripts come out
    $SCRIPT_DIR/src-relative instead of carrying host-only absolute paths. The workspace reset
    must not take that source with it: it is the very tree the run is about to build, and
    wiping it turns the run into 'No C/C++ build signals found in this repository'."""
    state_dir = tmp_path / "state"
    staged = state_dir / "prepared" / "src"
    staged.parent.mkdir(parents=True)
    shutil.copytree(local_repo_with_origin, staged, symlinks=True)
    (staged.parent / "Dockerfile").write_text("FROM stale\n")

    source = ingest_local(staged, project_name="prepared", state_dir=state_dir)

    assert source.source_path == staged
    assert (staged / "CMakeLists.txt").is_file()
    assert (staged / "include" / "mylib.h").is_file()
    # The rest of the stale workspace still goes.
    assert not (staged.parent / "Dockerfile").exists()
