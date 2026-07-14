from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from harnessbuddy.cli import build_harness, build_library
from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.environments.base import Environment, EnvironmentUnavailableError
from harnessbuddy.library_builder.environments.local import LocalExecutor
from harnessbuddy.library_builder.environments.oss_fuzz import OssFuzzExecutor
from harnessbuddy.library_builder.models import (
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibSpec:
    url: str
    project_name: str
    build_system: BuildSystem
    builds_static: bool


@dataclass
class LibBuild:
    spec: LibSpec
    environment: Environment
    library_result: BuildExplorationResult
    harness_result: HarnessExplorationResult
    workdir: Path
    source: Path


LIBS = [
    # cmake
    LibSpec("https://github.com/madler/zlib.git", "zlib", BuildSystem.CMAKE, True),
    LibSpec("https://gitlab.com/libtiff/libtiff.git", "libtiff", BuildSystem.CMAKE, True),
    LibSpec("https://github.com/HDFGroup/hdf5.git", "hdf5", BuildSystem.CMAKE, True),
    LibSpec(
        "https://github.com/c-ares/c-ares.git", "c-ares", BuildSystem.CMAKE, False
    ),  # non-canonical static flag (-DCARES_STATIC)
    LibSpec(
        "https://github.com/curl/curl.git", "curl", BuildSystem.CMAKE, False
    ),  # requires libpsl
    LibSpec("https://github.com/fukuchi/libqrencode.git", "libqrencode", BuildSystem.CMAKE, False),
    # make
    LibSpec("https://github.com/lz4/lz4.git", "lz4", BuildSystem.MAKEFILE, True),
    # autotools
    LibSpec(
        "https://github.com/libimobiledevice/libplist", "libplist", BuildSystem.AUTOTOOLS, True
    ),
    LibSpec("https://github.com/gpac/gpac.git", "gpac", BuildSystem.AUTOTOOLS, True),
    LibSpec("https://github.com/file/file.git", "file", BuildSystem.AUTOTOOLS, True),
    LibSpec("https://github.com/mm2/Little-CMS.git", "lcms", BuildSystem.AUTOTOOLS, True),
    # meson
    LibSpec(
        "https://gitlab.gnome.org/GNOME/tinysparql.git", "tinysparql", BuildSystem.MESON, False
    ),  # requires external deps
    LibSpec(
        "https://github.com/rauc/rauc.git", "rauc", BuildSystem.MESON, False
    ),  # requires dbus-1
]

_STATIC_LIBS = [lib for lib in LIBS if lib.builds_static]
_AGENTIC_LIBS = [lib for lib in LIBS if not lib.builds_static]
_AGENT = "claude"

# Smoke subset: a small, fast cross-section of build systems (cmake, make, autotools x3)
# that runs by default. The rest of _STATIC_LIBS is real but slower coverage, opt-in via
# `-m build_matrix` (see pyproject.toml's addopts).
_SMOKE_PROJECT_NAMES = {"zlib", "lcms", "libplist", "file"}
_SMOKE_STATIC_LIBS = [lib for lib in _STATIC_LIBS if lib.project_name in _SMOKE_PROJECT_NAMES]
_EXTENDED_STATIC_LIBS = [
    lib for lib in _STATIC_LIBS if lib.project_name not in _SMOKE_PROJECT_NAMES
]

# Round-robin the smoke libraries across environments so both LocalExecutor and
# OssFuzzExecutor get exercised by default, without doubling the smoke set's real build
# count. Everything not listed here (extended statics, agentic libs) stays LOCAL.
_SMOKE_ENVIRONMENTS: dict[str, Environment] = {
    "zlib": Environment.OSS_FUZZ,
    "lcms": Environment.OSS_FUZZ,
    "file": Environment.LOCAL,
    "libplist": Environment.LOCAL,
}


def _require_cmake() -> None:
    try:
        available = subprocess.run(["cmake", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        available = False
    if not available:
        pytest.skip("cmake not available")


def _select_executor(environment: Environment) -> LocalExecutor | OssFuzzExecutor:
    if environment is Environment.LOCAL:
        _require_cmake()
        return LocalExecutor()
    executor = OssFuzzExecutor()
    try:
        executor.check_availability()
    except EnvironmentUnavailableError as exc:
        pytest.skip(f"oss-fuzz environment unavailable: {exc}")
    return executor


def _build_lib(lib: LibSpec, tmp_path_factory: pytest.TempPathFactory) -> LibBuild:
    """Clone lib and run the library build then the harness-compile probe against it once,
    so library-build tests and harness-probe tests share a single real build instead of
    each re-cloning and re-building the same library. Both stages go through the same
    executor instance, since OssFuzzExecutor.run_harness_compile depends on state (the
    run-scoped image) that only its own prior run_library_build call establishes."""
    environment = _SMOKE_ENVIRONMENTS.get(lib.project_name, Environment.LOCAL)
    executor = _select_executor(environment)
    print(f"RUNNING BUILD LIB FOR {lib.project_name}")
    src = tmp_path_factory.mktemp(f"{lib.project_name}_src")
    subprocess.run(
        ["git", "clone", "--depth=1", lib.url, str(src)],
        check=True,
        capture_output=True,
    )
    source = RepoSource(source_path=src, clone_url=lib.url, project_name=lib.project_name)
    workdir = tmp_path_factory.mktemp(f"{lib.project_name}_work")
    analysis = analyze(source)
    library_result = build_library(analysis, workdir, executor, agent=_AGENT)
    install_dir = workdir / "install"
    harness_result = build_harness(
        analysis, install_dir, workdir, library_result, executor, agent=_AGENT
    )
    return LibBuild(
        spec=lib,
        environment=environment,
        library_result=library_result,
        harness_result=harness_result,
        workdir=workdir,
        source=src,
    )


@pytest.fixture(scope="session")
def real_library_build(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> LibBuild:
    return _build_lib(request.param, tmp_path_factory)


@pytest.fixture(scope="session")
def broken_cmake_build(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[BuildExplorationResult, Path]:
    _require_cmake()
    src = tmp_path_factory.mktemp("broken_cmake_src")
    (src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\n"
        "project(broken)\n"
        "find_package(NonExistentPackage_abc123 REQUIRED)\n"
    )
    (src / "stub.h").write_text("#pragma once\n")
    workdir = tmp_path_factory.mktemp("broken_cmake_work")
    analysis = analyze(RepoSource(src, "https://example.com", "broken", None))
    result = build_library(analysis, workdir, LocalExecutor())
    return result, workdir


# static (deterministic) library builds succeed and install artifacts
#
# NB: class declaration order matters here. real_library_build is session-scoped, so
# whichever test runs first for a given library actually triggers _build_lib. The two
# *LibraryBuildChecks classes (which carry the _forbid_agent guard) must stay declared —
# and therefore collected/run — before the *HarnessBuildChecks classes and the per-library
# classes further down (TestZlibBuild, TestLibtiffBuild), so that guard is active the first
# time each library is actually built.


class _StaticLibraryBuildChecks:
    global _AGENT
    _AGENT = None

    def test_library_builds(self, real_library_build: LibBuild) -> None:
        result = real_library_build.library_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_library_build: LibBuild) -> None:
        lib_dir = real_library_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_library_build: LibBuild) -> None:
        include_dir = real_library_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_library_build: LibBuild) -> None:
        assert (real_library_build.workdir / "build_library.sh").exists()

    def test_result_command_is_bash_script(self, real_library_build: LibBuild) -> None:
        # The reported "reproduce with" command is the shared verification script for
        # the environment the build actually ran in (spec 011), not build_library.sh
        # itself.
        cmd = real_library_build.library_result.command
        assert cmd[0] == "bash"
        expected_script = (
            "check_docker_build.sh"
            if real_library_build.environment is Environment.OSS_FUZZ
            else "check_local_build.sh"
        )
        assert Path(cmd[1]).name == expected_script


@pytest.mark.smoke
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _SMOKE_STATIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestSmokeStaticLibraryBuilds(_StaticLibraryBuildChecks):
    """Runs by default (pytest -q) — a fast cross-section of build systems."""


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _EXTENDED_STATIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestExtendedStaticLibraryBuilds(_StaticLibraryBuildChecks):
    """Opt-in only (`-m build_matrix`) — the rest of the static-build matrix."""


# harness-compile probe succeeds against those same install artifacts


class _StaticHarnessBuildChecks:
    def test_harness_compiles(self, real_library_build: LibBuild) -> None:
        result = real_library_build.harness_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} harness probe failed:\n{result.stderr}"
        )


@pytest.mark.smoke
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _SMOKE_STATIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestSmokeStaticHarnessBuilds(_StaticHarnessBuildChecks):
    """Runs by default (pytest -q)."""


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _EXTENDED_STATIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestExtendedStaticHarnessBuilds(_StaticHarnessBuildChecks):
    """Opt-in only (`-m build_matrix`)."""


# agentic (non-deterministic) builds — the repair agent may be invoked to fix them


@pytest.mark.agentic
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _AGENTIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestAgenticLibraryBuilds:
    def test_library_builds(self, real_library_build: LibBuild) -> None:
        result = real_library_build.library_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_library_build: LibBuild) -> None:
        lib_dir = real_library_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_library_build: LibBuild) -> None:
        include_dir = real_library_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_library_build: LibBuild) -> None:
        assert (real_library_build.workdir / "build_library.sh").exists()


@pytest.mark.agentic
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build", _AGENTIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestAgenticHarnessBuilds:
    def test_harness_compiles(self, real_library_build: LibBuild) -> None:
        result = real_library_build.harness_result
        assert result.succeeded, (
            f"{real_library_build.spec.project_name} harness probe failed:\n{result.stderr}"
        )


@pytest.mark.agentic
@pytest.mark.parametrize(
    "real_library_build",
    [lib for lib in LIBS if lib.project_name == "curl"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestCurlBuild:
    def test_llm_used_to_fix_missing_libpsl(self, real_library_build: LibBuild) -> None:
        assert real_library_build.library_result.llm_used, (
            "build succeeded with no LLM usage, this indicates something weird is happening"
        )


@pytest.mark.smoke
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build",
    [lib for lib in LIBS if lib.project_name == "zlib"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestZlibBuild:
    def test_library_path_in_compile_harnesses_script(self, real_library_build: LibBuild) -> None:
        compile_harnesses_source = real_library_build.workdir / "compile_harnesses.sh"
        assert compile_harnesses_source.exists()
        assert "libz.a" in compile_harnesses_source.read_text()


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_library_build",
    [lib for lib in LIBS if lib.project_name == "libtiff"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestLibtiffBuild:
    def test_system_package_inclusion(self, real_library_build: LibBuild) -> None:
        compile_harnesses_source = real_library_build.workdir / "compile_harnesses.sh"
        assert compile_harnesses_source.exists()
        assert "-llzma" in compile_harnesses_source.read_text()


# real build failure


def test_broken_cmake_not_succeeded(broken_cmake_build: tuple) -> None:
    result, _ = broken_cmake_build
    assert result.succeeded is False


def test_broken_cmake_exit_code_nonzero(broken_cmake_build: tuple) -> None:
    result, _ = broken_cmake_build
    assert result.exit_code != 0
