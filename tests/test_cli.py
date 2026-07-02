import json
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harnessbuddy.cli import (
    load_project_state,
    main,
    merge_packages_into_state,
    save_project_state,
)
from harnessbuddy.core.agent_stream import AgentStreamResult
from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.models import (
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)

_REPO = "https://github.com/example/repo.git"


@pytest.fixture(autouse=True)
def mock_host_build() -> Generator[MagicMock]:
    """Stub out the actual host build so CLI tests don't invoke cmake/make/etc."""
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ) as m,
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
    ):
        yield m


# generate — success paths


def test_generate_success_local_repo(local_repo_with_origin: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    project_dir = output_dir
    assert project_dir.is_dir()

    oss_fuzz_dir = project_dir / "oss-fuzz"
    assert (oss_fuzz_dir / "Dockerfile").exists()
    assert (oss_fuzz_dir / "build.sh").exists()
    assert (oss_fuzz_dir / "project.yaml").exists()
    local_dir = project_dir / "local"
    assert (local_dir / "setup.sh").exists()
    assert (local_dir / "build_library.sh").exists()


def test_generate_success_prints_summary(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Local build" in out
    assert "OSS-Fuzz" in out


def test_generate_success_project_name_override(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(
        [
            "generate",
            str(local_repo_with_origin),
            "--output",
            str(output_dir),
            "--project-name",
            "custom",
        ]
    )
    assert rc == 0
    assert (Path(".harnessbuddy") / "custom").is_dir()


def test_generate_success_default_output_uses_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repos" / "srcrepo"
    repo.mkdir(parents=True)
    (repo / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\nproject(srcrepo)\n")
    include = repo / "include"
    include.mkdir()
    (include / "srcrepo.h").write_text("#pragma once\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/srcrepo.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    output_dir = tmp_path / "cwd"
    output_dir.mkdir()
    monkeypatch.chdir(output_dir)
    rc = main(["generate", str(repo)])
    assert rc == 0
    assert (output_dir / "output" / "srcrepo").is_dir()


# generate — error paths


def test_generate_nonexistent_path_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(missing), "--output", str(output_dir)])
    assert rc != 0


def test_generate_nonexistent_path_prints_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does_not_exist"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    main(["generate", str(missing), "--output", str(output_dir)])
    err = capsys.readouterr().err
    assert "Repository not found" in err


def test_generate_no_origin_exits_nonzero(local_repo_without_origin: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_without_origin), "--output", str(output_dir)])
    assert rc != 0


def test_generate_no_origin_prints_error(
    local_repo_without_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    main(["generate", str(local_repo_without_origin), "--output", str(output_dir)])
    err = capsys.readouterr().err
    assert "cloneable git origin" in err


def test_generate_no_cpp_signals_exits_nonzero(tmp_path: Path) -> None:
    repo = tmp_path / "norepo"
    repo.mkdir()
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/norepo.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(repo), "--output", str(output_dir)])
    assert rc != 0


def test_generate_no_cpp_signals_prints_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "norepo"
    repo.mkdir()
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/norepo.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    main(["generate", str(repo), "--output", str(output_dir)])
    err = capsys.readouterr().err
    assert "C/C++" in err


def test_generate_output_dir_exists_exits_nonzero(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"

    output_dir.mkdir()
    local_out = output_dir / "local"
    local_out.mkdir(parents=True)
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0


def test_generate_output_dir_exists_prints_warning(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    local_out = output_dir / "local"
    local_out.mkdir(parents=True)
    main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    out = capsys.readouterr().out
    assert "already exists" in out


# host build exploration


def test_generate_prints_host_build_status(
    local_repo_with_origin: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    out = capsys.readouterr().out
    assert "host build" in out.lower()


def test_generate_exploration_runs_bash_script(
    mock_host_build: MagicMock, local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    cmd = mock_host_build.call_args[0][0]
    assert cmd[0] == "bash"
    assert cmd[1].endswith("build_library.sh")


# --no-agents


def test_no_agents_skips_agent_when_build_fails(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    failed_result = RunResult(stdout="build failed", stderr="", exit_code=1, duration_seconds=0.1)
    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=["missing artifacts"],
        ),
        patch("harnessbuddy.library_builder.agents.invoke_library_builder_agent") as mock_agent,
    ):
        main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--no-agents",
            ]
        )
    mock_agent.assert_not_called()


def test_no_agents_skips_harness_agent_when_compilation_fails(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.library_builder.agents.invoke_harness_builder_agent") as mock_agent:
        main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
                "--no-agents",
            ]
        )
    mock_agent.assert_not_called()


# stats.json


def _succeeded_harness_result() -> HarnessExplorationResult:
    return HarnessExplorationResult(
        succeeded=True,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=[],
        stdout="",
        stderr="",
        exit_code=0,
    )


def _stats_json_path(output_dir: Path) -> Path:
    return output_dir / "stats.json"


def test_generate_writes_stats_json_clean_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert stats["status"] == "success"
    na_phase = {
        "invoked": False,
        "duration_seconds": "N/A",
        "cost_usd": "N/A",
        "input_tokens": "N/A",
        "output_tokens": "N/A",
        "summary": "N/A",
    }
    assert stats["library_build_agent"] == na_phase
    assert stats["harness_build_agent"] == na_phase


def test_generate_writes_stats_json_library_agent_repaired(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=12.5,
        llm_used=True,
        cost_usd=0.05,
        agent_summary="Added a missing CMake flag.",
    )
    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()),
    ):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert stats["library_build_agent"] == {
        "invoked": True,
        "duration_seconds": 12.5,
        "cost_usd": 0.05,
        "input_tokens": "N/A",
        "output_tokens": "N/A",
        "summary": "Added a missing CMake flag.",
    }
    assert stats["status"] == "success"


def test_generate_writes_stats_json_failed_library_build(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", "build_library.sh"],
        stdout="build failed",
        stderr="",
        exit_code=1,
        duration_seconds=3.0,
    )
    with patch("harnessbuddy.cli.build_library", return_value=fake_build_result):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc != 0
    stats_path = _stats_json_path(output_dir)
    assert stats_path.exists()
    stats = json.loads(stats_path.read_text())
    assert stats["status"] == "failed_library_build"


def test_generate_writes_stats_json_failed_harness_build_emits_stub_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    assert (output_dir / "local").is_dir()
    assert (output_dir / "oss-fuzz").is_dir()
    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["status"] == "failed_harness_build"


def _key_paths(obj: object, prefix: str = "") -> set[str]:
    """Recursively collect dotted key paths from a JSON-decoded object."""
    paths: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths |= _key_paths(value, path)
    return paths


def test_stats_json_same_relative_path_and_shape_across_outcomes(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    success_output = tmp_path / "success_output"
    success_output.mkdir()
    failure_output = tmp_path / "failure_output"
    failure_output.mkdir()

    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc_success = main(
            ["generate", str(local_repo_with_origin), "--output", str(success_output)]
        )
    assert rc_success == 0

    failed_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", "build_library.sh"],
        stdout="build failed",
        stderr="",
        exit_code=1,
        duration_seconds=1.0,
    )
    with patch("harnessbuddy.cli.build_library", return_value=failed_build_result):
        rc_failure = main(
            ["generate", str(local_repo_with_origin), "--output", str(failure_output)]
        )
    assert rc_failure != 0

    success_stats_path = _stats_json_path(success_output)
    failure_stats_path = _stats_json_path(failure_output)
    assert success_stats_path.exists()
    assert failure_stats_path.exists()

    success_keys = _key_paths(json.loads(success_stats_path.read_text()))
    failure_keys = _key_paths(json.loads(failure_stats_path.read_text()))
    assert success_keys == failure_keys


def test_stats_json_overwritten_on_rerun(local_repo_with_origin: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    repaired_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=12.5,
        llm_used=True,
        cost_usd=0.05,
        agent_summary="Added a missing CMake flag.",
    )
    with (
        patch("harnessbuddy.cli.build_library", return_value=repaired_build_result),
        patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()),
    ):
        rc1 = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc1 == 0

    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc2 = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc2 == 0

    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert stats["library_build_agent"]["invoked"] is False


def test_no_stats_json_when_output_directory_never_created(tmp_path: Path) -> None:
    repo = tmp_path / "norepo"
    repo.mkdir()
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/norepo.git"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(repo), "--output", str(output_dir)])
    assert rc != 0
    assert list(output_dir.rglob("stats.json")) == []


# agent_report.json summary flow (Structured Agent Report feature, US1)


def test_generate_agent_report_summary_reaches_stats_on_library_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    failed_result = RunResult(stdout="build failed", stderr="", exit_code=1, duration_seconds=0.1)

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        (workdir / "install" / "lib").mkdir(parents=True, exist_ok=True)
        (workdir / "install" / "include").mkdir(parents=True, exist_ok=True)
        (workdir / "install" / "lib" / "libfoo.a").write_text("stub")
        (workdir / "install" / "include" / "foo.h").write_text("stub")
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "Disabled optional SSL support."})
        )
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
        patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc == 0
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert stats["library_build_agent"]["summary"] == "Disabled optional SSL support."


def test_generate_agent_report_summary_reaches_stats_on_library_action_required(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    failed_result = RunResult(stdout="build failed", stderr="", exit_code=1, duration_seconds=0.1)

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        (workdir / "agent_report.json").write_text(
            json.dumps(
                {
                    "summary": "The build requires libssl-dev, which is not installed.",
                    "missing_system_packages": ["libssl-dev"],
                }
            )
        )
        return AgentStreamResult(
            combined_text="ACTION REQUIRED: install libssl-dev",
            exit_code=1,
            duration_seconds=1.0,
        )

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc != 0
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert (
        stats["library_build_agent"]["summary"]
        == "The build requires libssl-dev, which is not installed."
    )


def test_generate_agent_report_summary_reaches_stats_on_harness_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
    )

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "out").mkdir(parents=True, exist_ok=True)
        (workdir / "out" / "probe_harness").write_text("stub binary")
        (workdir / "compile_harnesses.sh").write_text(
            'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS=\n'
        )
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "Linked against the system zlib directly."})
        )
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc == 0
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert stats["harness_build_agent"]["summary"] == "Linked against the system zlib directly."


def test_generate_agent_report_summary_reaches_stats_on_harness_action_required(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
    )

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "agent_report.json").write_text(
            json.dumps(
                {
                    "summary": "Needs libfoo-dev to resolve the undefined symbol.",
                    "missing_system_packages": ["libfoo-dev"],
                }
            )
        )
        return AgentStreamResult(
            combined_text="ACTION REQUIRED: install libfoo-dev",
            exit_code=1,
            duration_seconds=1.0,
        )

    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc != 0
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert (
        stats["harness_build_agent"]["summary"]
        == "Needs libfoo-dev to resolve the undefined symbol."
    )


def test_generate_agent_report_extra_library_path_reaches_both_harness_scripts(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    failed_result = RunResult(stdout="build failed", stderr="", exit_code=1, duration_seconds=0.1)
    extra_lib_path = "/usr/lib/x86_64-linux-gnu"

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        (workdir / "install" / "lib").mkdir(parents=True, exist_ok=True)
        (workdir / "install" / "include").mkdir(parents=True, exist_ok=True)
        (workdir / "install" / "lib" / "libfoo.a").write_text("stub")
        (workdir / "install" / "include" / "foo.h").write_text("stub")
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "done", "extra_library_paths": [extra_lib_path]})
        )
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc == 0
    local_script = (output_dir / "local" / "compile_harnesses.sh").read_text()
    oss_fuzz_script = (output_dir / "oss-fuzz" / "compile_harnesses.sh").read_text()
    assert f"-L{extra_lib_path}" in local_script
    assert f"-L{extra_lib_path}" in oss_fuzz_script


# agent_report.json missing-package flow (Structured Agent Report feature, US3)


def test_generate_library_missing_package_reaches_output_on_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    failed_result = RunResult(stdout="build failed", stderr="", exit_code=1, duration_seconds=0.1)

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        (workdir / "install" / "lib").mkdir(parents=True, exist_ok=True)
        (workdir / "install" / "include").mkdir(parents=True, exist_ok=True)
        (workdir / "install" / "lib" / "libfoo.a").write_text("stub")
        (workdir / "install" / "include" / "foo.h").write_text("stub")
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "done", "missing_system_packages": ["libssl-dev"]})
        )
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
        patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc == 0
    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert "libssl-dev" in dockerfile
    assert "libssl-dev" in setup_sh


def test_generate_library_missing_package_reaches_state_then_next_run_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    failed_result = RunResult(stdout="build failed", stderr="", exit_code=1, duration_seconds=0.1)

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "Needs libssl-dev.", "missing_system_packages": ["libssl-dev"]})
        )
        return AgentStreamResult(
            combined_text="ACTION REQUIRED: install libssl-dev",
            exit_code=1,
            duration_seconds=1.0,
        )

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc != 0

    state_file = Path(".harnessbuddy") / "mylib" / "state.json"
    state = json.loads(state_file.read_text())
    assert "libssl-dev" in state["apt_packages"]

    rc2 = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc2 == 0
    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert "libssl-dev" in dockerfile
    assert "libssl-dev" in setup_sh


def test_generate_harness_missing_package_reaches_output_on_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
    )

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "out").mkdir(parents=True, exist_ok=True)
        (workdir / "out" / "probe_harness").write_text("stub binary")
        (workdir / "compile_harnesses.sh").write_text(
            'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS=\n'
        )
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "done", "missing_system_packages": ["libfoo-dev"]})
        )
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc == 0
    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert "libfoo-dev" in dockerfile
    assert "libfoo-dev" in setup_sh


def test_generate_harness_missing_package_reaches_state_then_next_run_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
    )

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "agent_report.json").write_text(
            json.dumps({"summary": "Needs libfoo-dev.", "missing_system_packages": ["libfoo-dev"]})
        )
        return AgentStreamResult(
            combined_text="ACTION REQUIRED: install libfoo-dev",
            exit_code=1,
            duration_seconds=1.0,
        )

    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc != 0

    state_file = Path(".harnessbuddy") / "mylib" / "state.json"
    state = json.loads(state_file.read_text())
    assert "libfoo-dev" in state["apt_packages"]

    rc2 = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc2 == 0
    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert "libfoo-dev" in dockerfile
    assert "libfoo-dev" in setup_sh


def test_generate_harness_linked_flags_only_reaches_output_on_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_harness_result = HarnessExplorationResult(
        succeeded=True,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=["-lzstd", "-lz"],
        stdout="",
        stderr="",
        exit_code=0,
    )

    with patch("harnessbuddy.cli.build_harness", return_value=fake_harness_result):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0

    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    assert "libzstd-dev" in dockerfile
    assert "zlib1g-dev" in dockerfile

    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    if sys.platform == "darwin":
        assert "zstd" in setup_sh
        assert "zlib" in setup_sh
    else:
        assert "libzstd-dev" in setup_sh
        assert "zlib1g-dev" in setup_sh


def test_generate_agent_repaired_harness_linked_flags_reaches_output_on_success(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
    )

    def fake_run_agent_streaming(
        _cmd: list[str], cwd: Path, _timeout: int, _tool: str
    ) -> AgentStreamResult:
        workdir = Path(cwd)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "out").mkdir(parents=True, exist_ok=True)
        (workdir / "out" / "probe_harness").write_text("stub binary")
        (workdir / "compile_harnesses.sh").write_text(
            'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS="-llzma"\n'
        )
        (workdir / "agent_report.json").write_text(json.dumps({"summary": "done"}))
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch(
            "harnessbuddy.library_builder.agents.run_agent_streaming",
            side_effect=fake_run_agent_streaming,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--agent",
                "claude",
            ]
        )
    assert rc == 0

    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    assert "liblzma-dev" in dockerfile

    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    if sys.platform == "darwin":
        assert "xz" in setup_sh
    else:
        assert "liblzma-dev" in setup_sh


def test_generate_harness_unknown_linked_lib_warns_on_success(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_harness_result = HarnessExplorationResult(
        succeeded=True,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=["-lnonexistentlib"],
        stdout="",
        stderr="",
        exit_code=0,
    )

    with patch("harnessbuddy.cli.build_harness", return_value=fake_harness_result):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    assert "nonexistentlib" in capsys.readouterr().err


def test_generate_library_and_harness_phase_share_package_without_duplication(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        llm_used=True,
        missing_system_packages=["libzstd-dev"],
    )
    fake_harness_result = HarnessExplorationResult(
        succeeded=True,
        command=[],
        static_libs=[],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=["-lzstd"],
        stdout="",
        stderr="",
        exit_code=0,
    )

    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch("harnessbuddy.cli.build_harness", return_value=fake_harness_result),
    ):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0

    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    assert dockerfile.count("libzstd-dev") == 1


# load_project_state / save_project_state / merge_packages_into_state


def test_load_project_state_absent_returns_empty(tmp_path: Path) -> None:
    state = load_project_state(tmp_path / "state.json")
    assert state["apt_packages"] == []
    assert state["brew_packages"] == []
    assert state["unknown_libs"] == []
    assert state["sources"] == {}


def test_save_and_load_project_state_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = load_project_state(state_file)
    merge_packages_into_state(
        state,
        apt_packages=["libzstd-dev"],
        brew_packages=["zstd"],
        unknown_libs=[],
        source_tag="linker",
    )
    save_project_state(state_file, state)
    loaded = load_project_state(state_file)
    assert loaded["apt_packages"] == ["libzstd-dev"]
    assert loaded["brew_packages"] == ["zstd"]


def test_merge_packages_unions_across_calls(tmp_path: Path) -> None:
    state = load_project_state(tmp_path / "state.json")
    merge_packages_into_state(
        state, apt_packages=["libssl-dev"], brew_packages=[], unknown_libs=[], source_tag="agent"
    )
    merge_packages_into_state(
        state,
        apt_packages=["libzstd-dev"],
        brew_packages=["zstd"],
        unknown_libs=[],
        source_tag="linker",
    )
    assert state["apt_packages"] == ["libssl-dev", "libzstd-dev"]
    assert state["brew_packages"] == ["zstd"]


def test_merge_packages_deduplicates(tmp_path: Path) -> None:
    state = load_project_state(tmp_path / "state.json")
    merge_packages_into_state(
        state, apt_packages=["libssl-dev"], brew_packages=[], unknown_libs=[], source_tag="agent"
    )
    merge_packages_into_state(
        state, apt_packages=["libssl-dev"], brew_packages=[], unknown_libs=[], source_tag="linker"
    )
    assert state["apt_packages"] == ["libssl-dev"]


def test_load_project_state_ignores_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("not json{{{")
    state = load_project_state(tmp_path / "state.json")
    assert state["apt_packages"] == []
