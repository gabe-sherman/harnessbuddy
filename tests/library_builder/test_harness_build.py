from __future__ import annotations

import logging
import subprocess
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.cli import build_library
from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.environments.local import LocalExecutor
from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation
from harnessbuddy.library_builder.models import (
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
    result: HarnessExplorationResult
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
AGENTIC_LIBS = [lib for lib in LIBS if not lib.builds_static]
_AGENT = "claude"


def _require_cmake() -> None:
    try:
        available = subprocess.run(["cmake", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        available = False
    if not available:
        pytest.skip("cmake not available")


def _build_lib(lib: LibSpec, tmp_path_factory: pytest.TempPathFactory) -> LibBuild:
    _require_cmake()
    src = tmp_path_factory.mktemp(f"{lib.project_name}_src")
    subprocess.run(
        ["git", "clone", "--depth=1", lib.url, str(src)],
        check=True,
        capture_output=True,
    )
    source = RepoSource(source_path=src, clone_url=lib.url, project_name=lib.project_name)
    workdir = tmp_path_factory.mktemp(f"{lib.project_name}_work")
    analysis = analyze(source)
    result = build_library(analysis, workdir, LocalExecutor(), workdir / "oss-fuzz", agent=_AGENT)
    install_dir = workdir / "install"
    result = explore_harness_compilation(install_dir, workdir, analysis.language)
    return LibBuild(spec=lib, result=result, workdir=workdir, source=src)


@pytest.fixture(scope="session")
def real_harness_build(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> LibBuild:
    return _build_lib(request.param, tmp_path_factory)


@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_harness_build", _STATIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestStaticBuilds:
    # Assert no claude code usage here
    @pytest.fixture(autouse=True)
    def _forbid_agent(self) -> types.GeneratorType:
        with patch(
            "harnessbuddy.library_builder.agents.invoke_library_builder_agent",
            side_effect=AssertionError(
                "invoke_library_builder_agent must not be called in static build tests"
            ),
        ):
            yield

    def test_library_builds(self, real_harness_build: LibBuild) -> None:
        result = real_harness_build.result
        assert result.succeeded, (
            f"{real_harness_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_harness_build: LibBuild) -> None:
        lib_dir = real_harness_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_harness_build: LibBuild) -> None:
        include_dir = real_harness_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_harness_build: LibBuild) -> None:
        assert (real_harness_build.workdir / "build_library.sh").exists()

    def test_result_succeeded(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded is True

    def test_result_exit_code_zero(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.exit_code == 0


@pytest.mark.agentic
@pytest.mark.build_matrix
@pytest.mark.parametrize(
    "real_harness_build", AGENTIC_LIBS, indirect=True, ids=lambda lib: lib.project_name
)
class TestDynamicBuilds:
    def test_library_builds(self, real_harness_build: LibBuild) -> None:
        result = real_harness_build.result
        assert result.succeeded, (
            f"{real_harness_build.spec.project_name} build failed:\n{result.stderr}"
        )

    def test_static_library_installed(self, real_harness_build: LibBuild) -> None:
        lib_dir = real_harness_build.workdir / "install" / "lib"
        assert any(lib_dir.glob("*.a")), f"no static library in {lib_dir}"

    def test_headers_installed(self, real_harness_build: LibBuild) -> None:
        include_dir = real_harness_build.workdir / "install" / "include"
        assert any(include_dir.iterdir()), f"no headers in {include_dir}"

    def test_build_library_script_written(self, real_harness_build: LibBuild) -> None:
        assert (real_harness_build.workdir / "build_library.sh").exists()

    def test_result_succeeded(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded is True

    def test_result_exit_code_zero(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.exit_code == 0

    def test_result_command_is_bash_script(self, real_harness_build: LibBuild) -> None:
        cmd = real_harness_build.result.command
        assert cmd[0] == "bash"
        assert Path(cmd[1]).name == "build_library.sh"


@pytest.mark.parametrize(
    "real_harness_build",
    [lib for lib in LIBS if lib.project_name == "zlib"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestZlibBuild:
    def test_builds(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded, (
            f"zlib build failed:\n{real_harness_build.result.stderr}"
        )

    def test_library_path_exists(self, real_harness_build: LibBuild) -> None:
        compile_harnesses_source = real_harness_build.workdir / "compile_harnesses.sh"
        assert compile_harnesses_source.exists()
        with open(compile_harnesses_source) as f:
            contents = f.read()
            assert "libz.a" in contents


@pytest.mark.parametrize(
    "real_harness_build",
    [lib for lib in LIBS if lib.project_name == "libtiff"],
    indirect=True,
    ids=lambda lib: lib.project_name,
)
class TestLibtiffBuild:
    def test_builds(self, real_harness_build: LibBuild) -> None:
        assert real_harness_build.result.succeeded, (
            f"libtiff build failed:\n{real_harness_build.result.stderr}"
        )

    def test_system_package_inclusion(self, real_harness_build: LibBuild) -> None:
        compile_harnesses_source = real_harness_build.workdir / "compile_harnesses.sh"
        assert compile_harnesses_source.exists()
        with open(compile_harnesses_source) as f:
            contents = f.read()
            assert "-llzma" in contents
