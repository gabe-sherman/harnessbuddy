import json
import subprocess
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harnessbuddy.cli import (
    load_project_state,
    load_system_deps,
    main,
    merge_packages_into_state,
    save_project_state,
)
from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildSystem,
    Language,
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
    project_dir = output_dir / local_repo_with_origin.name
    assert project_dir.is_dir()
    oss_fuzz_dir = project_dir / "output" / "oss-fuzz"
    assert (oss_fuzz_dir / "Dockerfile").exists()
    assert (oss_fuzz_dir / "build.sh").exists()
    assert (oss_fuzz_dir / "project.yaml").exists()
    local_dir = project_dir / "output" / "local"
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
    assert (output_dir / "custom").is_dir()


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
    assert (output_dir / "srcrepo").is_dir()


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
    local_out = output_dir / local_repo_with_origin.name / "output" / "local"
    local_out.mkdir(parents=True)
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc != 0


def test_generate_output_dir_exists_prints_error(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    local_out = output_dir / local_repo_with_origin.name / "output" / "local"
    local_out.mkdir(parents=True)
    main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    err = capsys.readouterr().err
    assert "already exists" in err


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


# load_system_deps


def _bare_analysis(source_path: Path) -> AnalysisResult:
    return AnalysisResult(
        project_name="testlib",
        source_path=source_path,
        build_system=BuildSystem.CMAKE,
        build_files=[source_path / "CMakeLists.txt"],
        headers=[source_path / "include" / "foo.h"],
        language=Language.C,
        clone_url="https://github.com/example/testlib.git",
        repo_ref=None,
    )


def test_load_system_deps_absent_leaves_packages_empty(tmp_path: Path) -> None:
    analysis = _bare_analysis(tmp_path)
    load_system_deps(analysis)
    assert analysis.system_packages == []


def test_load_system_deps_loads_packages(tmp_path: Path) -> None:
    (tmp_path / "system_deps.json").write_text(
        json.dumps({"apt_packages": ["libssl-dev", "libz-dev"]})
    )
    analysis = _bare_analysis(tmp_path)
    load_system_deps(analysis)
    assert analysis.system_packages == ["libssl-dev", "libz-dev"]


def test_load_system_deps_ignores_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "system_deps.json").write_text("not json{{{")
    analysis = _bare_analysis(tmp_path)
    load_system_deps(analysis)
    assert analysis.system_packages == []


def test_load_system_deps_ignores_missing_apt_packages_key(tmp_path: Path) -> None:
    (tmp_path / "system_deps.json").write_text(json.dumps({"other_key": ["foo"]}))
    analysis = _bare_analysis(tmp_path)
    load_system_deps(analysis)
    assert analysis.system_packages == []


def test_load_system_deps_handles_non_list_value(tmp_path: Path) -> None:
    (tmp_path / "system_deps.json").write_text(json.dumps({"apt_packages": "libssl-dev"}))
    analysis = _bare_analysis(tmp_path)
    load_system_deps(analysis)
    assert analysis.system_packages == []


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


def test_generate_system_deps_loaded_from_source_path(
    local_repo_with_origin: Path, tmp_path: Path
) -> None:
    (local_repo_with_origin / "system_deps.json").write_text(
        json.dumps({"apt_packages": ["libssl-dev"]})
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    dockerfile = (output_dir / "mylib" / "output" / "oss-fuzz" / "Dockerfile").read_text()
    assert "libssl-dev" in dockerfile
    setup_sh = (output_dir / "mylib" / "output" / "local" / "setup.sh").read_text()
    assert "libssl-dev" in setup_sh
