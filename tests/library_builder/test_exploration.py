from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.exploration import explore, is_standard_source_layout
from harnessbuddy.library_builder.models import AnalysisResult, BuildSystem, Language

_FAKE_URL = "https://github.com/example/testlib.git"


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


def _ok_result() -> RunResult:
    return RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1)


def _run_explore(workdir: Path, source_path: Path) -> object:
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=_ok_result(),
        ) as mock_run,
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        result = explore(_analysis(source_path), workdir)
    return result, mock_run


# standard layout — source cloned to workdir/src


def test_standard_layout_true_when_source_is_workdir_src(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    (workdir / "src").mkdir(parents=True)
    assert is_standard_source_layout(_analysis(workdir / "src"), workdir) is True


def test_standard_layout_false_for_arbitrary_source(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = tmp_path / "elsewhere"
    source.mkdir(parents=True)
    assert is_standard_source_layout(_analysis(source), workdir) is False


def test_standard_layout_script_written_to_workdir_root(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    result, _ = _run_explore(workdir, source)
    assert (workdir / "build_library.sh").exists()
    assert not (source / "build_library.sh").exists()
    assert result.script_path == workdir / "build_library.sh"


def test_standard_layout_script_uses_relative_paths(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    _run_explore(workdir, source)
    content = (workdir / "build_library.sh").read_text()
    assert "$SCRIPT_DIR/src" in content
    assert "$SCRIPT_DIR/build" in content
    assert "$SCRIPT_DIR/install" in content
    assert str(workdir.resolve()) not in content


def test_standard_layout_runs_with_workdir_as_cwd(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)
    _result, mock_run = _run_explore(workdir, source)
    cwd_arg = mock_run.call_args[0][1]
    assert Path(cwd_arg) == workdir.resolve()


# non-standard layout — source lives outside workdir entirely


def test_non_standard_layout_script_path_unset(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = tmp_path / "elsewhere"
    source.mkdir(parents=True)
    result, _ = _run_explore(workdir, source)
    assert result.script_path is None


def test_non_standard_layout_uses_absolute_source_dir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = tmp_path / "elsewhere"
    source.mkdir(parents=True)
    _run_explore(workdir, source)
    content = (workdir / "build_library.sh").read_text()
    assert str(source.resolve()) in content


def test_non_standard_layout_still_writes_script_to_workdir(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = tmp_path / "elsewhere"
    source.mkdir(parents=True)
    _run_explore(workdir, source)
    assert (workdir / "build_library.sh").exists()
