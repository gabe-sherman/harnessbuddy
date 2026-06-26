from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.exploration import explore
from harnessbuddy.library_builder.models import AnalysisResult, BuildSystem, Language


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


def _ok() -> RunResult:
    return RunResult(stdout="configured", stderr="", exit_code=0, duration_seconds=0.5)


def _fail() -> RunResult:
    return RunResult(stdout="", stderr="error: missing compiler", exit_code=1, duration_seconds=0.3)


def _timeout() -> RunResult:
    return RunResult(stdout="", stderr="", exit_code=-1, duration_seconds=120.0)


# per build-system command construction


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_cmake_command(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    workdir = tmp_path / "build"
    explore(_make_analysis(BuildSystem.CMAKE, tmp_path), workdir)
    assert mock_run.call_args[0][0] == ["cmake", "-B", str(workdir)]


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_meson_command(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    workdir = tmp_path / "build"
    explore(_make_analysis(BuildSystem.MESON, tmp_path), workdir)
    assert mock_run.call_args[0][0] == ["meson", "setup", str(workdir)]


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_autotools_command(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    explore(_make_analysis(BuildSystem.AUTOTOOLS, tmp_path), tmp_path / "build")
    assert mock_run.call_args[0][0] == ["./configure"]


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_makefile_command(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    explore(_make_analysis(BuildSystem.MAKEFILE, tmp_path), tmp_path / "build")
    assert mock_run.call_args[0][0] == ["make", "-n"]


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_ninja_command(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    explore(_make_analysis(BuildSystem.NINJA, tmp_path), tmp_path / "build")
    assert mock_run.call_args[0][0] == ["ninja", "-n"]


# unknown build system is skipped — no subprocess invoked


def test_unknown_build_system_not_called(tmp_path: Path) -> None:
    with patch("harnessbuddy.library_builder.exploration.run_command") as mock_run:
        explore(_make_analysis(BuildSystem.UNKNOWN, tmp_path), tmp_path / "build")
        mock_run.assert_not_called()


def test_unknown_build_system_result_not_succeeded(tmp_path: Path) -> None:
    result = explore(_make_analysis(BuildSystem.UNKNOWN, tmp_path), tmp_path / "build")
    assert result.succeeded is False


def test_unknown_build_system_empty_command(tmp_path: Path) -> None:
    result = explore(_make_analysis(BuildSystem.UNKNOWN, tmp_path), tmp_path / "build")
    assert result.command == []


# timeout path


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_timeout_treated_as_failure(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _timeout()
    result = explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert result.succeeded is False
    assert result.exit_code == -1


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_custom_timeout_forwarded(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build", timeout=30)
    assert mock_run.call_args[0][2] == 30


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_default_timeout_is_120(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert mock_run.call_args[0][2] == 120


# result fields


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_success_sets_succeeded_true(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    result = explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert result.succeeded is True


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_nonzero_exit_sets_succeeded_false(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _fail()
    result = explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert result.succeeded is False


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_result_captures_stdout(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    result = explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert result.stdout == "configured"


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_result_captures_stderr(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _fail()
    result = explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert result.stderr == "error: missing compiler"


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_result_build_system_matches_input(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    result = explore(_make_analysis(BuildSystem.MESON, tmp_path), tmp_path / "build")
    assert result.build_system == BuildSystem.MESON


@patch("harnessbuddy.library_builder.exploration.run_command")
def test_cwd_is_source_path(mock_run: MagicMock, tmp_path: Path) -> None:
    mock_run.return_value = _ok()
    explore(_make_analysis(BuildSystem.CMAKE, tmp_path), tmp_path / "build")
    assert mock_run.call_args[0][1] == tmp_path
