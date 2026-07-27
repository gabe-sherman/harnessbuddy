import json
import logging
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harnessbuddy.cli import build_parser, main
from harnessbuddy.core.agent_stream import AgentStreamResult
from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import Environment
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
        # LocalExecutor now also gates on the shared check_local_build.sh script
        # (T006/T007) — stub that boundary too so tests don't invoke a real build.
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
            return_value=RunResult(stdout="OK", stderr="", exit_code=0, duration_seconds=0.1),
        ),
    ):
        yield m


# generate — success paths


def test_generate_success_local_repo(local_repo_with_origin: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    project_dir = output_dir
    assert project_dir.is_dir()

    assert not (project_dir / "oss-fuzz").exists()
    local_dir = project_dir / "local"
    assert (local_dir / "setup.sh").exists()
    assert (local_dir / "build_library.sh").exists()


def test_generate_success_prints_summary(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Environment:  local" in out
    assert f"Output:       {output_dir / 'local'}" in out


def test_generate_records_explicit_local_build_parameters(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--cc",
                "clang-19",
                "--cxx",
                "clang++-19",
                "--library-cflags=-fsanitize=fuzzer-no-link,address",
                "--library-cxxflags=-fsanitize=fuzzer-no-link,address",
                "--harness-cflags=-fsanitize=fuzzer,address",
                "--harness-cxxflags=-fsanitize=fuzzer,address",
            ]
        )

    assert rc == 0
    assert json.loads((output_dir / "stats.json").read_text())["build_parameters"] == {
        "cc": "clang-19",
        "cxx": "clang++-19",
        "library_cflags": "-fsanitize=fuzzer-no-link,address",
        "library_cxxflags": "-fsanitize=fuzzer-no-link,address",
        "harness_cflags": "-fsanitize=fuzzer,address",
        "harness_cxxflags": "-fsanitize=fuzzer,address",
    }


def test_generate_success_project_name_override(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
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


def test_generate_success_mixed_case_project_name_is_lowercased_consistently(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    """Docker rejects uppercase image tags; the project name is lowercased at ingestion
    (core/repos.py) so every later phase (workspace/state/logs directories, all derived
    from AnalysisResult.project_name) agrees with where the repo was actually cloned —
    a regression test for a bug where lowercasing only in analyze() left the ingested
    source directory and the later workspace path pointing at different, mismatched
    casings of the same name."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--project-name",
                "MyLib",
            ]
        )
    assert rc == 0
    # Path.exists() alone isn't reliable here: some host filesystems (e.g. a
    # case-insensitive macOS bind mount under Docker Desktop) resolve "MyLib" and
    # "mylib" to the same entry regardless of casing. Read the actual directory entry
    # name back instead, which stays case-preserving even there.
    entry_names = {entry.name for entry in Path(".harnessbuddy").iterdir()}
    assert "mylib" in entry_names
    assert "MyLib" not in entry_names


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
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
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


# generate — pre-existing output directory (non-interactive overwrite, not an error)


def test_generate_output_dir_exists_overwrites_and_succeeds(
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
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
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


def _oss_fuzz_workspace(project_name: str = "mylib") -> Path:
    """A minimal stand-in for the workspace OssFuzzExecutor._materialize_workspace and
    explore() leave behind, for tests that mock harnessbuddy.cli.build_library directly
    (so no real workspace is ever written to disk) but still exercise the oss-fuzz
    generation path — generate_oss_fuzz requires every one of these files to exist in
    the exploration result's script_path's directory (no template-rendering fallback).

    Written into the real .harnessbuddy/<project_name>/ state directory (matching
    project_dir()), not tmp_path — that's where the real pipeline's own `workspace`
    variable points, and generate_oss_fuzz's Dockerfile-merge step (inject_apt_packages)
    writes there too, so a script_path pointing anywhere else would silently diverge
    from what generate_oss_fuzz actually copies.
    """
    import shutil

    from harnessbuddy.core.paths import default_state_dir, project_dir

    workspace = project_dir(default_state_dir(), project_name)
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    (workspace / "project.yaml").write_text("homepage: workspace-validated\n")
    (workspace / "Dockerfile").write_text(
        "FROM gcr.io/oss-fuzz-base/base-builder:ubuntu-24-04\n"
        "ENV FUZZING_LANGUAGE=c\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends bear\n"
    )
    (workspace / "build.sh").write_text("#!/bin/bash\n# validated build.sh\n")
    (workspace / "build_library.sh").write_text("#!/bin/bash\n# validated build\n")
    (workspace / "compile_harness.sh").write_text("#!/bin/bash\n# validated compiler\n")
    (workspace / "compile_harnesses.sh").write_text("#!/bin/bash\n# validated harness\n")
    (workspace / "harness_source").mkdir()
    (workspace / "harness_source" / "default_fuzzer.c").write_text("// stub\n")
    return workspace


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


# --skip-validation — extends to skip the per-stage environment gate (harnessbuddy-6gn)


def test_skip_validation_continues_past_failed_library_build(
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
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--skip-validation",
            ]
        )
    # Both stages still ran and generation still happened — a failed library build no
    # longer stops the pipeline before generation (spec 009 research.md decision #7).
    assert rc == 0
    assert (output_dir / "local").is_dir()
    assert not (output_dir / "oss-fuzz").exists()
    stats = json.loads(_stats_json_path(output_dir).read_text())
    assert stats["status"] == "failed_library_build"


def test_skip_validation_continues_past_failed_library_build_prints_overall_failed(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output generation always writes files (best-effort), so its own phase banner
    reports [SUCCEEDED] even though the underlying build failed -- without an explicit
    overall-outcome line, that banner is the last thing printed and reads as a full
    success. Confirm the run is never left looking like an unqualified success."""
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
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--skip-validation",
            ]
        )
    assert rc == 0
    err = capsys.readouterr().err
    assert "Overall: FAILED" in err
    assert "static library build" in err


def test_generate_success_prints_overall_success(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Overall: SUCCESS" in out


def test_generate_writes_stats_json_failed_harness_build_prints_overall_failed(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Overall: FAILED" in err
    assert "harness compile probe" in err


def test_without_skip_validation_still_stops_on_failed_library_build(
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
    assert not (output_dir / "local").exists()


def test_generate_writes_stats_json_failed_harness_build_emits_stub_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    assert (output_dir / "local").is_dir()
    assert not (output_dir / "oss-fuzz").exists()
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
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
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
                    "missing_apt_packages": ["libssl-dev"],
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
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
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
        (workdir / "out" / "default_fuzzer").write_text("stub binary")
        (workdir / "compile_harness.sh").write_text(
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
                    "missing_apt_packages": ["libfoo-dev"],
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


def test_generate_agent_report_extra_library_path_reaches_local_harness_script(
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
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
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
    local_script = (output_dir / "local" / "compile_harness.sh").read_text()
    assert f"-L{extra_lib_path}" in local_script


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
            json.dumps(
                {
                    "summary": "done",
                    "missing_apt_packages": ["libssl-dev"],
                    "missing_brew_packages": ["openssl"],
                }
            )
        )
        return AgentStreamResult(combined_text="done", exit_code=0, duration_seconds=1.0)

    with (
        patch(
            "harnessbuddy.library_builder.exploration.run_command_streaming",
            return_value=failed_result,
        ),
        patch(
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
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
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert ("openssl" if sys.platform == "darwin" else "libssl-dev") in setup_sh


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
            json.dumps(
                {
                    "summary": "Needs libssl-dev.",
                    "missing_apt_packages": ["libssl-dev"],
                    "missing_brew_packages": ["openssl"],
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
            "harnessbuddy.library_builder.environments.verification.run_command_streaming",
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
    assert "openssl" in state["brew_packages"]

    rc2 = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc2 == 0
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert ("openssl" if sys.platform == "darwin" else "libssl-dev") in setup_sh


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
        (workdir / "out" / "default_fuzzer").write_text("stub binary")
        (workdir / "compile_harness.sh").write_text(
            'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS=\n'
        )
        (workdir / "agent_report.json").write_text(
            json.dumps(
                {
                    "summary": "done",
                    "missing_apt_packages": ["libfoo-dev"],
                    "missing_brew_packages": ["foo"],
                }
            )
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
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert ("foo" if sys.platform == "darwin" else "libfoo-dev") in setup_sh


def test_generate_harness_agent_resolved_link_still_reports_package_on_success(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An agent that resolves a link failure using a library already on its own machine
    (nothing to install there) must still report that library's packages, so the
    generated output stays portable to environments that don't already have it — closing
    the remaining gap identified in specs/007-complete-dependency-packaging's research.md.
    """
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
        (workdir / "out" / "default_fuzzer").write_text("stub binary")
        (workdir / "compile_harness.sh").write_text(
            'STATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS="-lfoo"\n'
        )
        (workdir / "agent_report.json").write_text(
            json.dumps(
                {
                    "summary": "Added -lfoo; already present on this machine.",
                    "missing_libs": ["foo"],
                    "missing_apt_packages": ["libfoo-dev"],
                    "missing_brew_packages": ["foo"],
                }
            )
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
    assert "ACTION REQUIRED" not in capsys.readouterr().err
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    local_compile_harnesses = (output_dir / "local" / "compile_harness.sh").read_text()
    assert ("foo" if sys.platform == "darwin" else "libfoo-dev") in setup_sh
    assert "-lfoo" in local_compile_harnesses


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
            json.dumps(
                {
                    "summary": "Needs libfoo-dev.",
                    "missing_apt_packages": ["libfoo-dev"],
                    "missing_brew_packages": ["foo"],
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

    state_file = Path(".harnessbuddy") / "mylib" / "state.json"
    state = json.loads(state_file.read_text())
    assert "libfoo-dev" in state["apt_packages"]
    assert "foo" in state["brew_packages"]

    rc2 = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc2 == 0
    setup_sh = (output_dir / "local" / "setup.sh").read_text()
    assert ("foo" if sys.platform == "darwin" else "libfoo-dev") in setup_sh


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
        (workdir / "out" / "default_fuzzer").write_text("stub binary")
        (workdir / "compile_harness.sh").write_text(
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
    workspace = _oss_fuzz_workspace()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        llm_used=True,
        missing_apt_packages=["libzstd-dev"],
        environment=Environment.OSS_FUZZ,
        script_path=workspace / "build_library.sh",
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
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            return_value=RunResult(
                stdout="Server Version: 24.0", stderr="", exit_code=0, duration_seconds=0.1
            ),
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
            ]
        )
    assert rc == 0

    dockerfile = (output_dir / "oss-fuzz" / "Dockerfile").read_text()
    assert dockerfile.count("libzstd-dev") == 1


# phase banners and failure diagnostics (spec 012)


def test_generate_success_prints_phase_banners_in_order(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    # build_harness is mocked wholesale here (matching this file's usual pattern), so its
    # own Harness compile probe banner never runs — only the phases whose PhaseReporter
    # actually executes in this test are asserted.
    expected_labels = [
        "Repository ingestion",
        "Static analysis",
        "Static library build",
        "Output generation",
    ]
    positions = [out.index(label) for label in expected_labels]
    assert positions == sorted(positions)
    # No agent-assisted phase ran, since nothing failed and no --agent was passed.
    assert "Agent-assisted" not in out


def test_generate_quiet_still_prints_phase_banners(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir), "--quiet"])
    assert rc == 0
    out = capsys.readouterr().out
    for label in ("Repository ingestion", "Static library build", "Output generation"):
        assert label in out


def test_generate_quiet_flag_parses() -> None:
    parser = build_parser()
    args = parser.parse_args(["generate", _REPO, "--quiet"])
    assert args.quiet is True


def test_generate_default_quiet_is_false() -> None:
    parser = build_parser()
    args = parser.parse_args(["generate", _REPO])
    assert args.quiet is False


def test_generate_failed_library_build_prints_diagnostic(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", "build_library.sh"],
        stdout="configure error: missing libfoo",
        stderr="",
        exit_code=1,
        duration_seconds=3.0,
    )
    with patch("harnessbuddy.cli.build_library", return_value=fake_build_result):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "FAILURE" in err
    assert "Static library build" in err
    assert "configure error: missing libfoo" in err
    assert "deterministic" in err.lower() or "build step failed" in err.lower()


def test_generate_failed_library_build_debug_mode_includes_raw_output(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", "build_library.sh"],
        stdout="THE_FULL_RAW_BUILD_TRANSCRIPT",
        stderr="",
        exit_code=1,
        duration_seconds=3.0,
    )
    with patch("harnessbuddy.cli.build_library", return_value=fake_build_result):
        rc = main(
            [
                "--log-level",
                "debug",
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
            ]
        )
    assert rc != 0
    err = capsys.readouterr().err
    assert "THE_FULL_RAW_BUILD_TRANSCRIPT" in err


def test_generate_failed_library_build_no_debug_omits_raw_output_repeat(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", "build_library.sh"],
        stdout="THE_FULL_RAW_BUILD_TRANSCRIPT",
        stderr="",
        exit_code=1,
        duration_seconds=3.0,
    )
    with patch("harnessbuddy.cli.build_library", return_value=fake_build_result):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "--- Full raw output" not in err


def test_generate_agent_repaired_but_still_failed_library_build_diagnostic_is_agent_origin(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=False,
        command=["bash", "build_library.sh"],
        stdout="agent attempt output",
        stderr="",
        exit_code=1,
        duration_seconds=3.0,
        llm_used=True,
        agent_summary="Tried adding a CMake flag but the build still failed.",
    )
    with patch("harnessbuddy.cli.build_library", return_value=fake_build_result):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "Agent-assisted library repair" in err
    assert "Tried adding a CMake flag but the build still failed." in err
    # Exactly one diagnostic block for this failure — not the pre-existing duplicate
    # print of the same agent summary (research.md addendum).
    assert err.count("Tried adding a CMake flag but the build still failed.") == 1


def test_check_environment_availability_failure_uses_startup_failure_format(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    with patch(
        "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
        return_value=RunResult(
            stdout="",
            stderr="Cannot connect to the Docker daemon",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
            ]
        )
    assert rc == 1
    err = capsys.readouterr().err
    assert "STARTUP FAILURE" in err
    assert "unavailable" in err.lower()


def test_log_level_debug_sets_internal_logging_level() -> None:
    main(["--log-level", "debug"])
    assert logging.getLogger("harnessbuddy.cli").getEffectiveLevel() == logging.DEBUG


# extract-features


def test_extract_features_missing_compile_commands_exits_with_actionable_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["extract-features", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "compile_commands.json" in err
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" in err


# generate-yaml


def test_generate_benchmark_missing_features_json_exits_with_actionable_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["generate-yaml", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "features.json" in err
    assert "extract-features" in err


def test_generate_local_copies_compile_commands_alongside_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    workspace_compile_commands = tmp_path / "workspace_compile_commands.json"
    workspace_compile_commands.write_text("[]")
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        compile_commands_path=workspace_compile_commands,
    )
    with (
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()),
    ):
        rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    dest = output_dir / "compile_commands.json"
    assert dest.exists()
    assert dest.read_text() == "[]"
    # Alongside the environment subdirs, not copied into either one.
    assert not (output_dir / "local" / "compile_commands.json").exists()


def test_generate_never_creates_feature_extractor_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    assert not list(output_dir.rglob("features.json"))
    # local is the default --environment, which never writes project.yaml (an oss-fuzz-only
    # artifact) — no .yaml file (e.g. a stray generate-yaml artifact) should appear at all.
    assert not list(output_dir.rglob("*.yaml"))


# --environment flag (spec 009)


def test_environment_flag_defaults_to_local() -> None:
    parser = build_parser()
    args = parser.parse_args(["generate", _REPO])
    assert args.environment == "local"


def test_environment_flag_accepts_local() -> None:
    parser = build_parser()
    args = parser.parse_args(["generate", _REPO, "--environment", "local"])
    assert args.environment == "local"


def test_environment_flag_accepts_oss_fuzz() -> None:
    parser = build_parser()
    args = parser.parse_args(["generate", _REPO, "--environment", "oss-fuzz"])
    assert args.environment == "oss-fuzz"


def test_environment_flag_rejects_invalid_value(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["generate", _REPO, "--environment", "bogus"])
    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_generate_default_and_explicit_local_report_environment_local(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    explicit_dir = tmp_path / "explicit"
    explicit_dir.mkdir()

    with patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()):
        rc_default = main(["generate", str(local_repo_with_origin), "--output", str(default_dir)])
        rc_explicit = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(explicit_dir),
                "--environment",
                "local",
            ]
        )
    assert rc_default == 0
    assert rc_explicit == 0
    default_stats = json.loads((default_dir / "stats.json").read_text())
    explicit_stats = json.loads((explicit_dir / "stats.json").read_text())
    assert default_stats["environment"] == "local"
    assert explicit_stats["environment"] == "local"
    # Identical modulo the total_duration_seconds timing field.
    del default_stats["total_duration_seconds"]
    del explicit_stats["total_duration_seconds"]
    assert default_stats == explicit_stats


def _mock_oss_fuzz_docker(*, docker_info_ok: bool = True, probe_build_ok: bool = True) -> tuple:
    """Return (run_command_patch, run_command_streaming_patch) contexts for OssFuzzExecutor."""
    docker_info_result = RunResult(
        stdout="Server Version: 24.0" if docker_info_ok else "",
        stderr="" if docker_info_ok else "Cannot connect to the Docker daemon",
        exit_code=0 if docker_info_ok else 1,
        duration_seconds=0.1,
    )
    probe_build_result = RunResult(
        stdout="",
        stderr="" if probe_build_ok else "E: Unable to locate package bogus-package",
        exit_code=0 if probe_build_ok else 1,
        duration_seconds=0.1,
    )

    call_count = {"n": 0}

    def _run_command(*_args: object, **_kwargs: object) -> RunResult:
        call_count["n"] += 1
        return docker_info_result if call_count["n"] == 1 else probe_build_result

    return (
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command",
            side_effect=_run_command,
        ),
        patch(
            "harnessbuddy.library_builder.environments.oss_fuzz.run_command_streaming",
            return_value=RunResult(stdout="build ok", stderr="", exit_code=0, duration_seconds=0.1),
        ),
    )


def test_generate_oss_fuzz_success_reports_environment_oss_fuzz(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    run_command_patch, run_streaming_patch = _mock_oss_fuzz_docker()
    with (
        run_command_patch,
        run_streaming_patch,
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.harness_explorer.explore_harness_compilation",
            return_value=_succeeded_harness_result(),
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
            ]
        )
    assert rc == 0
    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["environment"] == "oss-fuzz"


def test_generate_oss_fuzz_only_writes_oss_fuzz_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    run_command_patch, run_streaming_patch = _mock_oss_fuzz_docker()
    with (
        run_command_patch,
        run_streaming_patch,
        patch(
            "harnessbuddy.library_builder.exploration._validate_install_artifacts",
            return_value=[],
        ),
        patch(
            "harnessbuddy.library_builder.harness_explorer.explore_harness_compilation",
            return_value=_succeeded_harness_result(),
        ),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
            ]
        )
    assert rc == 0
    assert not (output_dir / "local").exists()
    oss_fuzz_dir = output_dir / "oss-fuzz"
    assert (oss_fuzz_dir / "Dockerfile").exists()
    assert (oss_fuzz_dir / "build.sh").exists()
    assert (oss_fuzz_dir / "project.yaml").exists()


def test_generate_oss_fuzz_copies_compile_commands_alongside_output(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    workspace = _oss_fuzz_workspace()
    workspace_compile_commands = tmp_path / "workspace_compile_commands.json"
    workspace_compile_commands.write_text("[]")
    fake_build_result = BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=True,
        command=["bash", "build_library.sh"],
        stdout="",
        stderr="",
        exit_code=0,
        duration_seconds=1.0,
        environment=Environment.OSS_FUZZ,
        script_path=workspace / "build_library.sh",
        compile_commands_path=workspace_compile_commands,
    )
    run_command_patch, run_streaming_patch = _mock_oss_fuzz_docker()
    with (
        run_command_patch,
        run_streaming_patch,
        patch("harnessbuddy.cli.build_library", return_value=fake_build_result),
        patch("harnessbuddy.cli.build_harness", return_value=_succeeded_harness_result()),
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
            ]
        )
    assert rc == 0
    dest = output_dir / "compile_commands.json"
    assert dest.exists()
    assert dest.read_text() == "[]"
    # Alongside the environment subdirs, not copied into either one — matches the
    # upstream OSS-Fuzz project convention that the shipped project looks like a plain
    # git clone plus build scripts.
    assert not (output_dir / "oss-fuzz" / "compile_commands.json").exists()


def test_generate_oss_fuzz_docker_unavailable_exits_without_agent(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
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
        patch("harnessbuddy.library_builder.agents.invoke_library_builder_agent") as mock_agent,
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
                "--agent",
                "claude",
            ]
        )
    assert rc == 1
    mock_agent.assert_not_called()
    err = capsys.readouterr().err
    assert "unavailable" in err.lower()
    assert not output_dir.exists() or not (output_dir / "stats.json").exists()


def test_generate_oss_fuzz_library_failure_stops_before_harness_phase(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    run_command_patch, run_streaming_patch = _mock_oss_fuzz_docker(probe_build_ok=False)
    with (
        run_command_patch,
        run_streaming_patch,
        patch(
            "harnessbuddy.library_builder.harness_explorer.explore_harness_compilation"
        ) as mock_harness,
    ):
        rc = main(
            [
                "generate",
                str(local_repo_with_origin),
                "--output",
                str(output_dir),
                "--environment",
                "oss-fuzz",
            ]
        )
    assert rc == 1
    mock_harness.assert_not_called()
    assert not (output_dir / "local").exists()
    assert not (output_dir / "oss-fuzz").exists()
