import subprocess
from pathlib import Path

import pytest

from harnessbuddy.cli import build_parser, main

_REPO = "https://github.com/example/repo.git"


def test_no_subcommand_exits_zero() -> None:
    assert main([]) == 0


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_generate_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["generate", "--help"])
    assert exc_info.value.code == 0


# Integration tests — local fixture repos, no network


def test_generate_success_local_repo(local_repo_with_origin: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    project_dir = output_dir / local_repo_with_origin.name
    assert project_dir.is_dir()
    assert (project_dir / "Dockerfile").exists()
    assert (project_dir / "build.sh").exists()
    assert (project_dir / "project.yaml").exists()


def test_generate_success_prints_summary(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Generated oss-fuzz project" in out
    assert "Project name" in out
    assert "Build system" in out
    assert "Language" in out


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
    # Build a repo whose name won't collide with the cwd output directory.
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


def test_generate_no_origin_exits_nonzero(
    local_repo_without_origin: Path, tmp_path: Path
) -> None:
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
    # pre-create the project directory to trigger OutputDirectoryExistsError
    (output_dir / local_repo_with_origin.name).mkdir()
    rc = main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    assert rc != 0


def test_generate_output_dir_exists_prints_error(
    local_repo_with_origin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / local_repo_with_origin.name).mkdir()
    main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])
    err = capsys.readouterr().err
    assert "already exists" in err


def test_generate_parses_repo_url() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.repo_url == _REPO


# --repo-ref


def test_generate_repo_ref_default_is_none() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.repo_ref is None


def test_generate_parses_repo_ref() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--repo-ref", "v1.3.2"])
    assert args.repo_ref == "v1.3.2"


# --agent


def test_generate_agent_default_is_auto() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.agent == "auto"


def test_generate_parses_agent_codex() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--agent", "codex"])
    assert args.agent == "codex"


def test_generate_parses_agent_claude() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--agent", "claude"])
    assert args.agent == "claude"


def test_generate_invalid_agent_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["generate", _REPO, "--agent", "invalid"])
    assert exc_info.value.code != 0


# --no-agents


def test_generate_no_agents_default_is_false() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.no_agents is False


def test_generate_parses_no_agents() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--no-agents"])
    assert args.no_agents is True


# --output


def test_generate_output_default_is_none() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.output is None


def test_generate_parses_output() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--output", "/tmp/out"])
    assert args.output == "/tmp/out"


# --project-name


def test_generate_project_name_default_is_none() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.project_name is None


def test_generate_parses_project_name() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--project-name", "mylib"])
    assert args.project_name == "mylib"


# --skip-validation


def test_generate_skip_validation_default_is_false() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.skip_validation is False


def test_generate_parses_skip_validation() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--skip-validation"])
    assert args.skip_validation is True


# --allow-host-build


def test_generate_allow_host_build_default_is_false() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.allow_host_build is False


def test_generate_parses_allow_host_build() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--allow-host-build"])
    assert args.allow_host_build is True


# --keep-workdir


def test_generate_keep_workdir_default_is_false() -> None:
    args = build_parser().parse_args(["generate", _REPO])
    assert args.keep_workdir is False


def test_generate_parses_keep_workdir() -> None:
    args = build_parser().parse_args(["generate", _REPO, "--keep-workdir"])
    assert args.keep_workdir is True
