from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.exploration import (
    compile_commands_absent_reason,
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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
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
    assert "$BUILD_PREFIX/build" in content
    assert "$BUILD_PREFIX/install" in content
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
                "extra_include_paths": [1, 2],
                "extra_library_paths": None,
            }
        )
    )
    report = read_agent_report(tmp_path)
    assert report is not None
    assert report.missing_libs == []
    assert report.missing_apt_packages == []
    assert report.extra_include_paths == []
    assert report.extra_library_paths == []


def test_read_agent_report_well_formed_file_returns_all_fields(tmp_path: Path) -> None:
    (tmp_path / "agent_report.json").write_text(
        json.dumps(
            {
                "summary": "Disabled optional SSL support.",
                "missing_libs": ["ldap"],
                "missing_apt_packages": ["libssl-dev"],
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


# the bear wrap — Make and Autotools have no build-system compile_commands.json, so the
# invocation is wrapped. Whether a capture ships is decided later, from the workspace layout
# (workspace.find_compile_commands), not from a field on this result.


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
            "harnessbuddy.library_builder.exploration.validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.exploration.shutil.which",
            return_value=which_bear,
        ),
    ):
        return explore(_analysis(source_path, build_system), workdir, environment=environment)


@pytest.mark.parametrize("build_system", [BuildSystem.MAKEFILE, BuildSystem.AUTOTOOLS])
def test_make_like_build_is_wrapped_with_bear(build_system: BuildSystem, tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        assert command[:2] == ["bear", "--"]
        return _ok_result()

    result = _run_explore_with(workdir, source, build_system=build_system, side_effect=side_effect)
    assert result.succeeded is True


@pytest.mark.parametrize("build_system", [BuildSystem.CMAKE, BuildSystem.MESON])
def test_build_systems_that_emit_their_own_are_not_wrapped(
    build_system: BuildSystem, tmp_path: Path
) -> None:
    """CMake and Meson write compile_commands.json themselves, so bear would only add its
    compiler-probe entries to what they already record."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        assert command == ["bash", "build_library.sh"]
        return _ok_result()

    result = _run_explore_with(workdir, source, build_system=build_system, side_effect=side_effect)
    assert result.succeeded is True


def test_missing_bear_on_the_host_does_not_fail_a_make_build(tmp_path: Path) -> None:
    """A missing bear on the local host must not fail the build: only the capture is skipped."""
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


def test_bear_always_wrapped_in_oss_fuzz_even_without_local_bear(tmp_path: Path) -> None:
    """The oss-fuzz environment skips the shutil.which check, since bear is always present
    in the probe image."""
    workdir = tmp_path / "work"
    source = workdir / "src"
    source.mkdir(parents=True)

    def side_effect(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        assert command[:2] == ["bear", "--"]
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


# compile_commands_absent_reason — the message the run summary prints when nothing was captured


def test_absent_reason_names_bear_for_make_like_without_bear() -> None:
    with patch("harnessbuddy.library_builder.exploration.shutil.which", return_value=None):
        reason = compile_commands_absent_reason(BuildSystem.MAKEFILE)
    assert "bear" in reason


def test_absent_reason_does_not_blame_bear_when_cmake_emits_its_own() -> None:
    with patch("harnessbuddy.library_builder.exploration.shutil.which", return_value=None):
        reason = compile_commands_absent_reason(BuildSystem.CMAKE)
    assert "bear" not in reason
