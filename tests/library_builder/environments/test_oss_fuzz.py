from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import (
    Environment,
    EnvironmentUnavailableError,
)
from harnessbuddy.library_builder.environments.oss_fuzz import OssFuzzExecutor
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildSystem,
    Language,
)

_FAKE_URL = "https://github.com/example/testlib.git"


def _analysis(source_path: Path, system_packages: list[str] | None = None) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=BuildSystem.CMAKE,
        build_files=[],
        headers=[],
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
        system_packages=system_packages or [],
    )


# check_availability


def test_check_availability_raises_when_docker_info_fails() -> None:
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(
                stdout="",
                stderr="Cannot connect to the Docker daemon",
                exit_code=1,
                duration_seconds=0.1,
            ),
        ),
        pytest.raises(EnvironmentUnavailableError) as exc_info,
    ):
        OssFuzzExecutor().check_availability()
    assert exc_info.value.environment is Environment.OSS_FUZZ
    assert "Docker daemon not reachable" in str(exc_info.value)


def test_check_availability_ok_when_docker_info_succeeds() -> None:
    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
        return_value=RunResult(
            stdout="Server Version: 24.0", stderr="", exit_code=0, duration_seconds=0.1
        ),
    ):
        OssFuzzExecutor().check_availability()  # must not raise


# run_library_build — probe image build failure classification (T024)


def test_run_library_build_network_failure_raises_environment_unavailable(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
        return_value=RunResult(
            stdout="",
            stderr="Error response from daemon: Get https://gcr.io: no such host",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ), pytest.raises(EnvironmentUnavailableError) as exc_info:
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)
    assert exc_info.value.environment is Environment.OSS_FUZZ


def test_run_library_build_non_network_probe_failure_returns_failed_result(tmp_path: Path) -> None:
    """A genuine probe-image build failure (bad apt package, etc.) is a stage failure
    eligible for agent fallback — not an EnvironmentUnavailableError (T024)."""
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
        return_value=RunResult(
            stdout="",
            stderr="E: Unable to locate package definitely-not-a-real-package",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ):
        result = OssFuzzExecutor().run_library_build(
            _analysis(workdir / "src", ["definitely-not-a-real-package"]), workdir
        )
    assert result.succeeded is False
    assert result.environment is Environment.OSS_FUZZ
    assert "definitely-not-a-real-package" in result.stderr


# run_library_build — happy path tags environment and reuses the probe image


def test_run_library_build_success_tags_environment_oss_fuzz(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        result = OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ


def test_docker_run_invocation_mounts_workdir_and_uses_bash_entrypoint(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ) as mock_streaming,
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        OssFuzzExecutor().run_library_build(_analysis(workdir / "src"), workdir)

    docker_command = mock_streaming.call_args[0][0]
    assert docker_command[0] == "docker"
    assert "--entrypoint" in docker_command
    assert "bash" in docker_command
    assert f"{workdir.resolve()}:{workdir.resolve()}" in docker_command


# run_harness_compile — requires a prior successful run_library_build on the same instance


def test_run_harness_compile_without_prior_library_build_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        OssFuzzExecutor().run_harness_compile(tmp_path / "install", tmp_path, Language.C)


def test_run_harness_compile_reuses_probe_image_from_library_build(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    install_dir = workdir / "install"

    executor = OssFuzzExecutor()
    with (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        executor.run_library_build(_analysis(workdir / "src"), workdir)

    # explore() (called by run_library_build) resets workdir/install — populate the
    # static-lib fixture only after that stage, matching the real pipeline order.
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")

    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = executor.run_harness_compile(install_dir, workdir, Language.C)

    assert result.environment is Environment.OSS_FUZZ
    assert result.succeeded is True


# Docker-gated end-to-end test (T015) — skipped by default per pyproject.toml's docker marker


@pytest.mark.docker
def test_run_library_build_real_docker_end_to_end(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    (source / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\n"
        "project(testlib)\n"
        "add_library(testlib STATIC empty.c)\n"
        "install(TARGETS testlib DESTINATION lib)\n"
    )
    (source / "empty.c").write_text("int testlib_symbol(void) { return 0; }\n")
    include = source / "include"
    include.mkdir()
    (include / "testlib.h").write_text("#pragma once\n")

    result = OssFuzzExecutor().run_library_build(_analysis(source), workdir)
    assert result.succeeded is True
    assert result.environment is Environment.OSS_FUZZ
