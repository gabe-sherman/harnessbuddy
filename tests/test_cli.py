import pytest

from harnessbuddy.cli import main


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
