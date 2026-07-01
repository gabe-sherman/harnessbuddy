from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.harness_explorer import (
    explore_harness_compilation,
    reparse_link_config,
)
from harnessbuddy.library_builder.models import Language

# reparse_link_config


def test_reparse_extracts_added_flag() -> None:
    script = (
        "STATIC_LIBS=(\n"
        '    "$INSTALL_DIR/lib/libcares.a"\n'
        ")\n"
        "\n"
        'EXTRA_LINK_FLAGS="-lresolv"\n'
        "\n"
        'for harness in "$HARNESS_DIR"/*; do\n'
    )
    static_libs, flags = reparse_link_config(script, [Path("libcares.a")], [])
    assert flags == ["-lresolv"]
    assert static_libs == [Path("libcares.a")]


def test_reparse_extracts_reordered_static_libs() -> None:
    script = (
        "STATIC_LIBS=(\n"
        '    "$INSTALL_DIR/lib/libbar.a"\n'
        '    "$INSTALL_DIR/lib/libfoo.a"\n'
        ")\n"
        "\n"
        "EXTRA_LINK_FLAGS=\n"
    )
    static_libs, flags = reparse_link_config(
        script, [Path("libfoo.a"), Path("libbar.a")], ["-lold"]
    )
    assert static_libs == [Path("libbar.a"), Path("libfoo.a")]
    assert flags == []


def test_reparse_strips_brew_prefix_on_darwin() -> None:
    script = 'EXTRA_LINK_FLAGS="-L$(brew --prefix)/lib -lzstd"\n'
    _static_libs, flags = reparse_link_config(script, [], [])
    assert flags == ["-lzstd"]


def test_reparse_falls_back_when_format_not_found() -> None:
    script = "# agent rewrote this script entirely\necho hello\n"
    static_libs, flags = reparse_link_config(script, [Path("libfoo.a")], ["-lold"])
    assert static_libs == [Path("libfoo.a")]
    assert flags == ["-lold"]


def test_reparse_falls_back_when_static_libs_block_empty() -> None:
    script = "STATIC_LIBS=(\n)\n\nEXTRA_LINK_FLAGS=\n"
    static_libs, _flags = reparse_link_config(script, [Path("libfoo.a")], [])
    assert static_libs == [Path("libfoo.a")]


# explore_harness_compilation — script_path on success


def test_script_path_set_on_success(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is True
    assert result.script_path == tmp_path / "build_harness.sh"
    assert result.script_path is not None
    assert result.script_path.exists()


def test_script_path_unset_on_failure(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(
            stdout="", stderr="undefined reference to `foo'", exit_code=1, duration_seconds=0.1
        ),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is False
    assert result.script_path is None
