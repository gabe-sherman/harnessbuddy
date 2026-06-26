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


def test_generate_exits_zero() -> None:
    assert main(["generate", _REPO]) == 0


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
