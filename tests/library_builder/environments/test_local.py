from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.build_parameters import BuildParameters
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.environments.local import LocalExecutor
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildSystem,
    Language,
)

_FAKE_URL = "https://github.com/example/testlib.git"

_EXPLORE_OK = RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1)
_VERIFY_OK = RunResult(stdout="OK: artifacts present", stderr="", exit_code=0, duration_seconds=0.2)


def _analysis(source_path: Path) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=BuildSystem.CMAKE,
        build_files=[],
        headers=[],
        language=Language.C,
        clone_url=_FAKE_URL,
        repo_ref=None,
    )


def test_check_availability_never_raises() -> None:
    LocalExecutor().check_availability()


@contextlib.contextmanager
def _patch_local_boundaries(
    *, explore_result: RunResult, verify_result: RunResult
) -> Iterator[None]:
    """Mock both subprocess boundaries LocalExecutor now shells out to: exploration's
    own build_library.sh run, and the shared check_local_build.sh gate (T006/T007)."""
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=explore_result,
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=verify_result,
        ),
    ):
        yield


def test_run_library_build_tags_environment_local(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    with _patch_local_boundaries(explore_result=_EXPLORE_OK, verify_result=_VERIFY_OK):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)
    assert result.environment is Environment.LOCAL
    assert result.succeeded is True


def test_run_library_build_invokes_check_local_build_sh(tmp_path: Path) -> None:
    """run_library_build gates its pass/fail result via check_local_build.sh (T006, FR-001),
    not by trusting exploration's own subprocess result directly."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=_EXPLORE_OK,
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ) as mock_verify,
    ):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)

    mock_verify.assert_called_once()
    command = mock_verify.call_args[0][0]
    assert command[0] == "bash"
    assert command[1].endswith("check_local_build.sh")
    assert command[2] == str(workdir.resolve())
    assert result.command == command


def test_run_library_build_refreshes_stale_harness_compiler_before_verifying(
    tmp_path: Path,
) -> None:
    """A reused workspace must not validate with obsolete library-only compiler flags."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    (workdir / "compile_harness.sh").write_text('CFLAGS="${CFLAGS:--fsanitize=fuzzer-no-link}"\n')
    (workdir / "compile_harnesses.sh").write_text("#!/bin/bash\n")
    parameters = BuildParameters(
        cc="clang",
        cxx="clang++",
        library_cflags="-fsanitize=fuzzer-no-link,address",
        library_cxxflags="-fsanitize=fuzzer-no-link,address",
        harness_cflags="-fsanitize=fuzzer,address",
        harness_cxxflags="-fsanitize=fuzzer,address",
    )

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=_EXPLORE_OK,
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ),
    ):
        LocalExecutor().run_library_build(_analysis(source), workdir, parameters=parameters)

    compiler = (workdir / "compile_harness.sh").read_text()
    assert 'CFLAGS="-fsanitize=fuzzer,address"' in compiler and "${CFLAGS:-" not in compiler


def test_run_library_build_verification_failure_fails_result(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    verify_failed = RunResult(
        stdout="FAILED: build_library.sh did not succeed",
        stderr="",
        exit_code=1,
        duration_seconds=0.1,
    )
    with _patch_local_boundaries(explore_result=_EXPLORE_OK, verify_result=verify_failed):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)
    assert result.succeeded is False
    assert result.exit_code != 0


def test_run_library_build_skips_verification_when_probe_fails(tmp_path: Path) -> None:
    """A failing probe short-circuits — the shared script is not re-run to reconfirm a
    failure it already reproduced (wasteful, especially for OssFuzzExecutor's Docker
    equivalent, which would otherwise pay for a second full rebuild+recompile)."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    explore_failed = RunResult(
        stdout="CMake Error: something went wrong", stderr="", exit_code=1, duration_seconds=0.1
    )
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=explore_failed,
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming"
        ) as mock_verify,
    ):
        result = LocalExecutor().run_library_build(_analysis(source), workdir)

    mock_verify.assert_not_called()
    assert result.succeeded is False
    assert result.command[1].endswith("check_local_build.sh")
    # The stub compile_harnesses.sh must still be written — a later repair agent's own
    # verification run still needs it to exist.
    assert (workdir / "compile_harnesses.sh").exists()


def test_run_library_build_skips_verification_for_unknown_build_system(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    analysis = _analysis(source)
    analysis.build_system = BuildSystem.UNKNOWN
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming"
    ) as mock_verify:
        result = LocalExecutor().run_library_build(analysis, workdir)
    mock_verify.assert_not_called()
    assert result.succeeded is False


def test_run_harness_compile_tags_environment_local(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with (
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ),
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    assert result.environment is Environment.LOCAL
    assert result.succeeded is True


def test_run_harness_compile_publishes_the_configured_harness_flags(tmp_path: Path) -> None:
    """The published compiler retains the caller's harness-link configuration."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()
    parameters = BuildParameters(
        cc="clang",
        cxx="clang++",
        library_cflags="-fsanitize=fuzzer-no-link,address",
        library_cxxflags="-fsanitize=fuzzer-no-link,address",
        harness_cflags="-fsanitize=fuzzer,address -DHARNESS_FLAG",
        harness_cxxflags="-fsanitize=fuzzer,address -DHARNESS_FLAG",
    )

    with (
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ),
    ):
        LocalExecutor().run_harness_compile(
            install_dir, tmp_path, Language.C, parameters=parameters
        )

    compiler = (tmp_path / "compile_harness.sh").read_text()
    assert 'CFLAGS="-fsanitize=fuzzer,address -DHARNESS_FLAG"' in compiler


def test_run_harness_compile_invokes_check_local_build_sh(tmp_path: Path) -> None:
    """run_harness_compile gates its pass/fail result via check_local_build.sh (T007,
    FR-001), not by trusting the direct probe compile's exit code directly."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with (
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=_VERIFY_OK,
        ) as mock_verify,
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    mock_verify.assert_called_once()
    command = mock_verify.call_args[0][0]
    assert command[1].endswith("check_local_build.sh")
    assert result.command == command


def test_run_harness_compile_skips_verification_without_static_libs(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming"
    ) as mock_verify:
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)
    mock_verify.assert_not_called()
    assert result.succeeded is False


def test_run_harness_compile_skips_verification_when_discovery_fails(tmp_path: Path) -> None:
    """Discovery already exhausted its retry attempts against this install/ output — the
    shared script would only reconfirm the same failure."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with (
        patch(
            "harnessbuddy.library_builder.harness_explorer.run_command",
            return_value=RunResult(
                stdout="", stderr="link failed", exit_code=1, duration_seconds=0.1
            ),
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming"
        ) as mock_verify,
    ):
        result = LocalExecutor().run_harness_compile(install_dir, tmp_path, Language.C)

    mock_verify.assert_not_called()
    assert result.succeeded is False
    assert result.command[1].endswith("check_local_build.sh")
