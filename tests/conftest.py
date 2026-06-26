from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def local_repo_with_origin(tmp_path: Path) -> Path:
    """Minimal git repository with a remote origin URL."""
    repo = tmp_path / "mylib"
    repo.mkdir()
    include = repo / "include"
    include.mkdir()
    (include / "mylib.h").write_text("#pragma once\nvoid mylib_hello(void);\n")
    (repo / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\nproject(mylib)\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/mylib.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


@pytest.fixture
def local_repo_without_origin(tmp_path: Path) -> Path:
    """Minimal git repository with no remote origin."""
    repo = tmp_path / "noorigin"
    repo.mkdir()
    (repo / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\nproject(noorigin)\n"
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    return repo
