from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
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


def test_run_library_build_writes_stub_compile_harnesses_sh_before_verifying(
    tmp_path: Path,
) -> None:
    """A stub compile_harnesses.sh must exist before check_local_build.sh runs, since
    that script always runs build_library.sh && compile_harnesses.sh together (T006)."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    seen_stub_exists = []

    def _fake_verify(_command: list[str], cwd: Path, _timeout: int) -> RunResult:
        seen_stub_exists.append((Path(cwd) / "compile_harnesses.sh").exists())
        return _VERIFY_OK

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
            side_effect=_fake_verify,
        ),
    ):
        LocalExecutor().run_library_build(_analysis(source), workdir)

    assert seen_stub_exists == [True]
    assert (workdir / "compile_harnesses.sh").exists()


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
