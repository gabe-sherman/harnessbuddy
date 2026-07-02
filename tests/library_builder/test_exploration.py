from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.exploration import (
    explore,
    is_standard_source_layout,
    read_agent_report,
)
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
    Language,
)

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


def _run_explore(workdir: Path, source_path: Path) -> tuple[BuildExplorationResult, MagicMock]:
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


# read_agent_report


def test_read_agent_report_absent_file_returns_none(tmp_path: Path) -> None:
    assert read_agent_report(tmp_path) is None


def test_read_agent_report_invalid_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text("not json{{{")
    assert read_agent_report(tmp_path) is None


def test_read_agent_report_top_level_not_object_returns_none(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text(json.dumps(["a", "b"]))
    assert read_agent_report(tmp_path) is None


def test_read_agent_report_summary_not_string_is_treated_as_absent(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text(json.dumps({"summary": 123}))
    report = read_agent_report(tmp_path)
    assert report is not None
    assert report.summary is None


def test_read_agent_report_list_fields_not_list_of_strings_become_empty(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text(
        json.dumps(
            {
                "missing_libs": "ldap",
                "missing_apt_packages": "libssl-dev",
                "missing_brew_packages": None,
                "extra_include_paths": [1, 2],
                "extra_library_paths": None,
            }
        )
    )
    report = read_agent_report(tmp_path)
    assert report is not None
    assert report.missing_libs == []
    assert report.missing_apt_packages == []
    assert report.missing_brew_packages == []
    assert report.extra_include_paths == []
    assert report.extra_library_paths == []


def test_read_agent_report_well_formed_file_returns_all_fields(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text(
        json.dumps(
            {
                "summary": "Disabled optional SSL support.",
                "missing_libs": ["ldap"],
                "missing_apt_packages": ["libssl-dev"],
                "missing_brew_packages": ["openssl"],
                "extra_include_paths": ["/usr/include/foo"],
                "extra_library_paths": ["/usr/lib/x86_64-linux-gnu"],
            }
        )
    )
    report = read_agent_report(tmp_path)
    assert report is not None
    assert report.summary == "Disabled optional SSL support."
    assert report.missing_libs == ["ldap"]
    assert report.missing_apt_packages == ["libssl-dev"]
    assert report.missing_brew_packages == ["openssl"]
    assert report.extra_include_paths == ["/usr/include/foo"]
    assert report.extra_library_paths == ["/usr/lib/x86_64-linux-gnu"]


def test_read_agent_report_deletes_file_after_read_when_well_formed(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text(json.dumps({"summary": "done"}))
    read_agent_report(tmp_path)
    assert not (tmp_path / "agent_report.json").exists()


def test_read_agent_report_deletes_file_after_read_when_malformed(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text("not json{{{")
    read_agent_report(tmp_path)
    assert not (tmp_path / "agent_report.json").exists()
