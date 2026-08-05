"""The shared build gate: what it runs the build with, and what it does with the capture.

The gate is the last step on every lane -- deterministic or agent-repaired -- so it is where
compile_commands.json is asked for and where a container-built one is translated back to host
paths. Whether it first rebuilds the library from nothing depends on the lane; that decision is
covered here too.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments import gate
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import HarnessExplorationResult

_PASSED = RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=0.1)


def _probe_succeeded(workdir: Path) -> HarnessExplorationResult:
    """A harness probe the gate will act on: succeeded, with something to link against."""
    return HarnessExplorationResult(
        succeeded=True,
        command=["bash", "compile_harness.sh"],
        static_libs=[Path("libmylib.a")],
        include_dir=workdir / "install" / "include",
        transitive_link_flags=[],
        stdout="",
        stderr="",
        exit_code=0,
    )


def _run_gate(  # type: ignore[no-untyped-def]
    workdir: Path,
    *,
    environment: Environment,
    side_effect=None,
    library_llm_used: bool = False,
    bypass_scratch_validation: bool = False,
):
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        side_effect=side_effect,
        return_value=None if side_effect else _PASSED,
    ):
        return gate.apply_to_harness_result(
            _probe_succeeded(workdir),
            workdir,
            environment=environment,
            project_name="mylib",
            library_llm_used=library_llm_used,
            bypass_scratch_validation=bypass_scratch_validation,
        )


def test_gate_asks_cmake_to_export_compile_commands(tmp_path: Path) -> None:
    """Set here rather than baked into build_library.sh: CMake reads it from the environment, so
    the shipped script stays free of capture-only flags."""
    seen: dict[str, str | None] = {}

    def side_effect(_command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        seen["value"] = os.environ.get("CMAKE_EXPORT_COMPILE_COMMANDS")
        return _PASSED

    _run_gate(tmp_path, environment=Environment.LOCAL, side_effect=side_effect)
    assert seen["value"] == "ON"


def test_gate_does_not_leak_the_capture_setting_afterwards(tmp_path: Path) -> None:
    _run_gate(tmp_path, environment=Environment.LOCAL)
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" not in os.environ


def _write_container_capture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "directory": "/src/build",
                    "file": "/src/src/foo.c",
                    "command": "cc -I/src/install/include -c /src/src/foo.c",
                }
            ]
        )
    )


def test_gate_rewrites_container_paths_to_the_host_workspace(tmp_path: Path) -> None:
    """A container build bakes /src into the capture, and the host-side feature extractor
    fatal-errors trying to chdir there. Done for every lane, since the gate runs on every lane."""
    captured = tmp_path / "build" / "compile_commands.json"
    _write_container_capture(captured)

    _run_gate(tmp_path, environment=Environment.OSS_FUZZ)

    host = str(tmp_path.resolve())
    entry = json.loads(captured.read_text())[0]
    assert entry["directory"] == f"{host}/build"
    assert entry["file"] == f"{host}/src/foo.c"
    assert entry["command"] == f"cc -I{host}/install/include -c {host}/src/foo.c"


def test_gate_rewrites_a_capture_in_the_workspace_root_too(tmp_path: Path) -> None:
    """Where bear leaves it, for a Make or Autotools project."""
    captured = tmp_path / "compile_commands.json"
    _write_container_capture(captured)

    _run_gate(tmp_path, environment=Environment.OSS_FUZZ)

    assert json.loads(captured.read_text())[0]["file"] == f"{tmp_path.resolve()}/src/foo.c"


def test_gate_leaves_a_host_built_capture_alone(tmp_path: Path) -> None:
    """Nothing to translate on the local host, and a path that merely looks like /src on a
    developer's machine is a real path there."""
    captured = tmp_path / "build" / "compile_commands.json"
    _write_container_capture(captured)
    before = captured.read_text()

    _run_gate(tmp_path, environment=Environment.LOCAL)

    assert captured.read_text() == before


def test_gate_skipped_for_a_failed_probe_does_not_touch_a_capture(tmp_path: Path) -> None:
    """The gate does not run, so there is no new build to describe and nothing to rewrite."""
    captured = tmp_path / "build" / "compile_commands.json"
    _write_container_capture(captured)
    before = captured.read_text()

    failed = HarnessExplorationResult(
        succeeded=False,
        command=[],
        static_libs=[],
        include_dir=None,
        transitive_link_flags=[],
        stdout="",
        stderr="link failed",
        exit_code=1,
    )
    with patch(
        "harnessbuddy.library_builder.environments.verification.run_command_streaming",
        return_value=_PASSED,
    ) as mock_run:
        gate.apply_to_harness_result(
            failed, tmp_path, environment=Environment.OSS_FUZZ, project_name="mylib"
        )

    mock_run.assert_not_called()
    assert captured.read_text() == before


def test_deterministic_lane_does_not_pay_for_a_second_cold_build(tmp_path: Path) -> None:
    """explore() already deleted build/ and install/ before building, so a wipe here would
    repeat that build to reach the same state -- the run's largest avoidable cost."""
    result = _run_gate(tmp_path, environment=Environment.LOCAL, library_llm_used=False)

    assert "--keep-artifacts" in result.command


def test_agent_lane_still_rebuilds_from_nothing(tmp_path: Path) -> None:
    """An agent changed the build since the last cold build, so its fix has to survive one."""
    result = _run_gate(tmp_path, environment=Environment.LOCAL, library_llm_used=True)

    assert "--keep-artifacts" not in result.command


def test_bypass_drops_the_rebuild_on_the_agent_lane_too(tmp_path: Path) -> None:
    result = _run_gate(
        tmp_path,
        environment=Environment.LOCAL,
        library_llm_used=True,
        bypass_scratch_validation=True,
    )

    assert "--keep-artifacts" in result.command


def test_the_decision_is_stamped_on_the_result_for_the_repair_agent(tmp_path: Path) -> None:
    """The agent is told to run the gate, so it needs the same answer this gate used. Reading it
    off the result is what keeps the two from drifting apart."""
    kept = _run_gate(tmp_path, environment=Environment.LOCAL, library_llm_used=False)
    wiped = _run_gate(tmp_path, environment=Environment.LOCAL, library_llm_used=True)

    assert kept.gate_keeps_artifacts is True
    assert wiped.gate_keeps_artifacts is False


def test_the_container_gate_carries_the_decision_too(tmp_path: Path) -> None:
    """The flag has to reach check_build.sh inside the container, not stop at the wrapper."""
    kept = _run_gate(tmp_path, environment=Environment.OSS_FUZZ, library_llm_used=False)
    wiped = _run_gate(tmp_path, environment=Environment.OSS_FUZZ, library_llm_used=True)

    assert kept.command[-1] == "--keep-artifacts"
    assert "--keep-artifacts" not in wiped.command


def test_a_reported_command_for_a_failed_probe_matches_the_lane(tmp_path: Path) -> None:
    """The gate is skipped, but the command it reports is what a reader will run to reproduce,
    so it must describe the same build the lane would have gated."""
    failed = HarnessExplorationResult(
        succeeded=False,
        command=[],
        static_libs=[Path("libmylib.a")],
        include_dir=tmp_path / "install" / "include",
        transitive_link_flags=[],
        stdout="",
        stderr="undefined reference",
        exit_code=1,
    )
    result = gate.apply_to_harness_result(
        failed,
        tmp_path,
        environment=Environment.LOCAL,
        project_name="mylib",
        library_llm_used=False,
    )

    assert "--keep-artifacts" in result.command
