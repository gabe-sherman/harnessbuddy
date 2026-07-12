from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import Environment
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


def _analysis(source_path: Path, build_system: BuildSystem = BuildSystem.CMAKE) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=build_system,
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


# compile_commands.json capture dispatch (T007)


def _run_explore_with(  # noqa: PLR0913 -- test helper; all 6 params are distinct fixture knobs
    workdir: Path,
    source_path: Path,
    *,
    build_system: BuildSystem,
    side_effect,
    which_bear: str | None = "/usr/bin/bear",
    environment: Environment = Environment.LOCAL,
) -> BuildExplorationResult:
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            side_effect=side_effect,
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.exploration.shutil.which",
            return_value=which_bear,
        ),
    ):
        return explore(_analysis(source_path, build_system), workdir, environment=environment)


def test_cmake_capture_reconfigures_and_copies_file(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], cwd: Path, _timeout: int) -> RunResult:
        if command[0] == "cmake":
            (cwd / "build" / "compile_commands.json").write_text("[]")
        return _ok_result()

    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.CMAKE, side_effect=side_effect
    )
    assert result.succeeded is True
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"
    assert result.compile_commands_error is None
    assert (workdir / "compile_commands.json").read_text() == "[]"


def test_cmake_capture_records_error_when_reconfigure_produces_no_file(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.CMAKE, side_effect=lambda *_a: _ok_result()
    )
    assert result.succeeded is True
    assert result.compile_commands_path is None
    assert result.compile_commands_error is not None
    assert not (workdir / "compile_commands.json").exists()


def test_meson_capture_copies_file_ninja_already_wrote(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(_command: list[str], cwd: Path, _timeout: int) -> RunResult:
        (cwd / "build" / "compile_commands.json").write_text("[]")
        return _ok_result()

    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.MESON, side_effect=side_effect
    )
    assert result.succeeded is True
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"
    assert result.compile_commands_error is None


def test_meson_capture_records_error_when_file_absent(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.MESON, side_effect=lambda *_a: _ok_result()
    )
    assert result.succeeded is True
    assert result.compile_commands_path is None
    assert result.compile_commands_error is not None


def test_make_capture_wraps_with_bear_and_captures_direct_output(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], cwd: Path, _timeout: int) -> RunResult:
        assert command[:2] == ["bear", "--"]
        (cwd / "compile_commands.json").write_text("[]")
        return _ok_result()

    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.MAKEFILE, side_effect=side_effect
    )
    assert result.succeeded is True
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"
    assert result.compile_commands_error is None


def test_autotools_capture_wraps_with_bear(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], cwd: Path, _timeout: int) -> RunResult:
        assert command[:2] == ["bear", "--"]
        (cwd / "compile_commands.json").write_text("[]")
        return _ok_result()

    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.AUTOTOOLS, side_effect=side_effect
    )
    assert result.succeeded is True
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"


def test_make_capture_best_effort_when_bear_missing_on_host(tmp_path: Path) -> None:
    """bear missing on the local host must not fail the build (FR-008) — only the
    capture is skipped, with an actionable message recorded."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        assert command == ["bash", "build_library.sh"]
        return _ok_result()

    result = _run_explore_with(
        workdir,
        source,
        build_system=BuildSystem.MAKEFILE,
        side_effect=side_effect,
        which_bear=None,
    )
    assert result.succeeded is True
    assert result.compile_commands_path is None
    assert result.compile_commands_error is not None
    assert "bear" in result.compile_commands_error


def test_make_capture_bear_always_wrapped_in_oss_fuzz_even_without_local_bear(
    tmp_path: Path,
) -> None:
    """The oss-fuzz environment never runs the shutil.which check — bear is a hard
    requirement there (FR-011), so capture is attempted unconditionally."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], cwd: Path, _timeout: int) -> RunResult:
        assert command[:2] == ["bear", "--"]
        (cwd / "compile_commands.json").write_text("[]")
        return _ok_result()

    result = _run_explore_with(
        workdir,
        source,
        build_system=BuildSystem.MAKEFILE,
        side_effect=side_effect,
        which_bear=None,
        environment=Environment.OSS_FUZZ,
    )
    assert result.succeeded is True
    assert result.compile_commands_path == workdir.resolve() / "compile_commands.json"


def test_compile_commands_not_set_when_build_fails(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    failing_result = RunResult(stdout="", stderr="boom", exit_code=1, duration_seconds=0.1)
    result = _run_explore_with(
        workdir, source, build_system=BuildSystem.CMAKE, side_effect=lambda *_a: failing_result
    )
    assert result.succeeded is False
    assert result.compile_commands_path is None
    assert result.compile_commands_error is None
