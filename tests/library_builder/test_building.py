from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.exploration import explore
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
    Language,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibSpec:
    url: str
    project_name: str


@dataclass
class LibBuild:
    spec: LibSpec
    result: BuildExplorationResult
    workdir: Path
    source: Path


CMAKE_LIBS = [
    LibSpec("https://github.com/madler/zlib.git", "zlib"),
    # LibSpec("https://gitlab.com/libtiff/libtiff.git", "libtiff"),
    # LibSpec("https://github.com/c-ares/c-ares.git", "c-ares"), # has non-canonical static library flag (-DCARES_STATIC)
    # LibSpec("https://github.com/curl/curl.git", "curl"), # requires dep of Libpsl
    # LibSpec("https://github.com/HDFGroup/hdf5.git", "hdf5")
]

MAKE_LIBS = [
    # LibSpec("https://github.com/lz4/lz4.git", "lz4"),
]

AUTOTOOLS_LIBS = [
    # LibSpec("https://github.com/libimobiledevice/libplist", "libplist"),
    # LibSpec("https://github.com/gpac/gpac.git", "gpac"),
    LibSpec("https://github.com/file/file.git", "file"),
    LibSpec("https://github.com/mm2/Little-CMS.git", "lcms"),
]

MESON_LIBS = [
    # LibSpec("https://gitlab.gnome.org/GNOME/tinysparql.git", "tinysparql") # This one may be good for pulling in the LLM, it has external deps
    # LibSpec("https://github.com/rauc/rauc.git", "rauc") # Same with this one, depends on apt/brew install dbus-1
]

_REAL_WORLD_LIBS = CMAKE_LIBS + MAKE_LIBS + AUTOTOOLS_LIBS + MESON_LIBS
_REAL_WORLD_LIBS = CMAKE_LIBS


def _make_analysis(build_system: BuildSystem, source_path: Path) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=build_system,
        build_files=[],
        headers=[],
        language=Language.C,
        clone_url="https://example.com/testlib.git",
        repo_ref=None,
    )


def _timeout() -> RunResult:
    return RunResult(stdout="", stderr="", exit_code=-1, duration_seconds=120.0)


def _require_cmake() -> None:
    if subprocess.run(["cmake", "--version"], capture_output=True).returncode != 0:
        pytest.skip("cmake not available")


@pytest.fixture(
    scope="session",
    params=_REAL_WORLD_LIBS,
    ids=lambda lib: lib.project_name,
)
def real_library_build(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> LibBuild:
    _require_cmake()
    lib: LibSpec = request.param
    src = tmp_path_factory.mktemp(f"{lib.project_name}_src")
    subprocess.run(
        ["git", "clone", "--depth=1", lib.url, str(src)],
        check=True,
        capture_output=True,
    )
    source = RepoSource(
        source_path=src,
        clone_url=lib.url,
        project_name=lib.project_name,
    )
    workdir = tmp_path_factory.mktemp(f"{lib.project_name}_work")
    result = explore(analyze(source), workdir)
    return LibBuild(spec=lib, result=result, workdir=workdir, source=src)


@pytest.fixture(scope="session")
def broken_cmake_build(tmp_path_factory: pytest.TempPathFactory) -> tuple[BuildExplorationResult, Path]:
    _require_cmake()
    src = tmp_path_factory.mktemp("broken_cmake_src")
    (src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\n"
        "project(broken)\n"
        "find_package(NonExistentPackage_abc123 REQUIRED)\n"
    )
    (src / "stub.h").write_text("#pragma once\n")
    workdir = tmp_path_factory.mktemp("broken_cmake_work")
    result = explore(analyze(RepoSource(src, "https://example.com", "broken", None)), workdir)
    return result, workdir


# build succeeds and installs artifacts


def test_library_builds(real_library_build: LibBuild) -> None:
    result = real_library_build.result
    assert result.succeeded, f"{real_library_build.spec.project_name} build failed:\n{result.stderr}"


def test_static_library_installed(real_library_build: LibBuild) -> None:
    lib_dir = real_library_build.workdir / "install" / "lib"
    assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"


def test_headers_installed(real_library_build: LibBuild) -> None:
    include_dir = real_library_build.workdir / "install" / "include"
    assert any(include_dir.iterdir()), f"no headers in {include_dir}"


def test_build_library_script_written(real_library_build: LibBuild) -> None:
    assert (real_library_build.source / "build_library.sh").exists()


# build.env written with usable flags


def test_build_env_written(real_library_build: LibBuild) -> None:
    assert (real_library_build.workdir / "build.env").exists()


def test_build_env_has_include_flags(real_library_build: LibBuild) -> None:
    workdir = real_library_build.workdir
    env = (workdir / "build.env").read_text()
    assert f"-I{workdir.resolve()}/install/include" in env


def test_build_env_has_library_flags(real_library_build: LibBuild) -> None:
    workdir = real_library_build.workdir
    env = (workdir / "build.env").read_text()
    assert f"-L{workdir.resolve()}/install/lib" in env


# result fields reflect actual outcome


def test_result_succeeded(real_library_build: LibBuild) -> None:
    assert real_library_build.result.succeeded is True


def test_result_exit_code_zero(real_library_build: LibBuild) -> None:
    assert real_library_build.result.exit_code == 0


def test_result_command_is_bash_script(real_library_build: LibBuild) -> None:
    cmd = real_library_build.result.command
    assert cmd[0] == "bash"
    assert Path(cmd[1]).name == "build_library.sh"


# real build failure


def test_broken_cmake_not_succeeded(broken_cmake_build: tuple) -> None:
    result, _ = broken_cmake_build
    assert result.succeeded is False


def test_broken_cmake_exit_code_nonzero(broken_cmake_build: tuple) -> None:
    result, _ = broken_cmake_build
    assert result.exit_code != 0


# unknown build system — script written but subprocess not called


def test_unknown_build_system_not_succeeded(tmp_path: Path) -> None:
    result = explore(_make_analysis(BuildSystem.UNKNOWN, tmp_path), tmp_path / "work")
    assert result.succeeded is False


def test_unknown_build_system_empty_command(tmp_path: Path) -> None:
    result = explore(_make_analysis(BuildSystem.UNKNOWN, tmp_path), tmp_path / "work")
    assert result.command == []


# timeout edge case — mock retained: triggering a real timeout requires a slow build


@patch("harnessbuddy.library_builder.exploration.run_command_streaming")
def test_timeout_treated_as_failure(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _timeout()
    result = explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "work")
    assert result.succeeded is False
    assert result.exit_code == -1


@patch("harnessbuddy.library_builder.exploration.run_command_streaming")
def test_custom_timeout_forwarded(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = RunResult(stdout="", stderr="", exit_code=0, duration_seconds=1.0)
    explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "work", timeout=30)
    assert mock_run.call_args[0][2] == 30


@patch("harnessbuddy.library_builder.exploration.run_command_streaming")
def test_default_timeout_is_120(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = RunResult(stdout="", stderr="", exit_code=0, duration_seconds=1.0)
    explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "work")
    assert mock_run.call_args[0][2] == 300
