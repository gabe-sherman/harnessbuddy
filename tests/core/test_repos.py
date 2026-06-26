from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import (
    NoCloneableOriginError,
    RepositoryNotFoundError,
    ingest_local,
    name_from_url,
)

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
        ingest_local(missing)


def test_ingest_local_file_path_raises(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("hello")
    with pytest.raises(RepositoryNotFoundError):
        ingest_local(f)


def test_ingest_local_without_origin_raises(local_repo_without_origin: Path) -> None:
    with pytest.raises(NoCloneableOriginError, match="no cloneable git origin"):
        ingest_local(local_repo_without_origin)


# ingest_local — success with origin


def test_ingest_local_with_origin_returns_source(local_repo_with_origin: Path) -> None:
    source = ingest_local(local_repo_with_origin)
    assert source.source_path == local_repo_with_origin
    assert source.clone_url == "https://github.com/example/mylib.git"


def test_ingest_local_project_name_inferred(local_repo_with_origin: Path) -> None:
    source = ingest_local(local_repo_with_origin)
    assert source.project_name == local_repo_with_origin.name


def test_ingest_local_project_name_override(local_repo_with_origin: Path) -> None:
    source = ingest_local(local_repo_with_origin, project_name="custom")
    assert source.project_name == "custom"


def test_ingest_local_repo_ref_propagated(local_repo_with_origin: Path) -> None:
    source = ingest_local(local_repo_with_origin, repo_ref="v1.0.0")
    assert source.repo_ref == "v1.0.0"


def test_ingest_local_repo_ref_default_is_none(local_repo_with_origin: Path) -> None:
    source = ingest_local(local_repo_with_origin)
    assert source.repo_ref is None
