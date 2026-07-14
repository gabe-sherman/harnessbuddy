from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from harnessbuddy.library_builder.models import BuildPaths, BuildSystem, HarnessExplorationResult
from harnessbuddy.library_builder.scripts import build_harness_script, build_library_script


def test_build_library_script_skips_when_artifacts_already_present(tmp_path: Path) -> None:
    """A repeat invocation that doesn't first clear install_dir (e.g. re-running
    build_library.sh as part of a harness-discovery retry loop, unlike explore()'s own
    authoritative build which always wipes it first) must not redo the real build —
    source_dir points nowhere, so reaching `make` would fail."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()
    (install_dir / "include" / "foo.h").write_text("stub")

    script = build_library_script(
        BuildSystem.MAKEFILE,
        BuildPaths(
            source_dir=str(tmp_path / "does-not-exist"),
            build_dir=str(tmp_path / "build"),
            install_dir=str(install_dir),
        ),
        host_fallbacks=True,
    )
    script_path = tmp_path / "build_library.sh"
    script_path.write_text(script)

    result = subprocess.run(["bash", str(script_path)], capture_output=True, text=True, timeout=10)

    assert result.returncode == 0
    assert "skipping build" in result.stdout


def test_build_library_script_builds_when_artifacts_missing(tmp_path: Path) -> None:
    """Sanity check that the skip-check isn't a no-op that always exits early."""
    script = build_library_script(
        BuildSystem.MAKEFILE,
        BuildPaths(
            source_dir=str(tmp_path / "does-not-exist"),
            build_dir=str(tmp_path / "build"),
            install_dir=str(tmp_path / "install"),
        ),
        host_fallbacks=True,
    )
    script_path = tmp_path / "build_library.sh"
    script_path.write_text(script)

    result = subprocess.run(["bash", str(script_path)], capture_output=True, text=True, timeout=10)

    assert result.returncode != 0
    assert "skipping build" not in result.stdout


def _harness(
    *,
    static_libs: list[Path] | None = None,
    transitive_link_flags: list[str] | None = None,
    extra_include_paths: list[str] | None = None,
    extra_library_paths: list[str] | None = None,
) -> HarnessExplorationResult:
    return HarnessExplorationResult(
        succeeded=True,
        command=[],
        static_libs=static_libs or [Path("libfoo.a")],
        include_dir=Path("/tmp/install/include"),
        transitive_link_flags=transitive_link_flags or [],
        stdout="",
        stderr="",
        exit_code=0,
        extra_include_paths=extra_include_paths or [],
        extra_library_paths=extra_library_paths or [],
    )


# empty extra paths — regression safety net pinning the canonical script shape


def test_empty_extra_paths_local_script_is_pinned() -> None:
    script = build_harness_script(_harness())
    assert script == (
        '#!/bin/bash\nset -euo pipefail\n\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"'
        ' && pwd)"\nBUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n\nCC="${CC:-clang}"\n'
        'CXX="${CXX:-clang++}"\n'
        'CFLAGS="${CFLAGS:-}"\n'
        'CXXFLAGS="${CXXFLAGS:-}"\n\nINSTALL_DIR="$BUILD_PREFIX/install"\n'
        'HARNESS_DIR="$SCRIPT_DIR/harness_src"\nOUT_DIR="$SCRIPT_DIR/out"\nmkdir -p "$OUT_DIR"\n'
        '\nSTATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS=\n'
        'EXTRA_LIB_PATHS=\n\nfor harness in "$HARNESS_DIR"/*; do\n  [ -f "$harness" ] ||'
        ' continue\n  name="$(basename "$harness")"\n  echo "Compiling harness $name"\n'
        '  output="${name%.*}"\n'
        '  case "$harness" in\n    *.c)\n      "$CC" $CFLAGS "-I$INSTALL_DIR/include"'
        ' "$harness" \\\n        "${STATIC_LIBS[@]-}" $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS'
        ' -o "$OUT_DIR/$output"\n      ;;\n    *.cc|*.cpp|*.cxx)\n      "$CXX" $CXXFLAGS'
        ' "-I$INSTALL_DIR/include" "$harness" \\\n        "${STATIC_LIBS[@]-}" $EXTRA_LIB_PATHS'
        ' $EXTRA_LINK_FLAGS -o "$OUT_DIR/$output"\n      ;;\n  esac\ndone\n'
    )


def test_empty_extra_paths_oss_fuzz_script_is_pinned() -> None:
    script = build_harness_script(_harness(), oss_fuzz=True)
    assert script == (
        '#!/bin/bash\nset -euo pipefail\n\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")"'
        ' && pwd)"\nBUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n\n'
        'INSTALL_DIR="$BUILD_PREFIX/install"\nHARNESS_DIR="$SCRIPT_DIR/harness_src"\n'
        '\nSTATIC_LIBS=(\n    "$INSTALL_DIR/lib/libfoo.a"\n)\n\nEXTRA_LINK_FLAGS=\n'
        'EXTRA_LIB_PATHS=\n\nfor harness in "$HARNESS_DIR"/*; do\n  [ -f "$harness" ] ||'
        ' continue\n  name="$(basename "$harness")"\n  echo "Compiling harness $name"\n'
        '  output="${name%.*}"\n'
        '  case "$harness" in\n    *.c)\n      "$CC" $CFLAGS "-I$INSTALL_DIR/include"'
        ' "$harness" \\\n        "${STATIC_LIBS[@]-}" $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS'
        ' "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n      ;;\n    *.cc|*.cpp|*.cxx)\n'
        '      "$CXX" $CXXFLAGS "-I$INSTALL_DIR/include" "$harness" \\\n'
        '        "${STATIC_LIBS[@]-}" $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS "$LIB_FUZZING_ENGINE"'
        ' -o "$OUT/$output"\n      ;;\n  esac\ndone\n'
    )


# extra_include_paths — -I flags in both compile branches


def test_extra_include_paths_appear_in_both_compile_branches() -> None:
    script = build_harness_script(
        _harness(extra_include_paths=["/usr/include/foo", "/opt/include"])
    )
    c_line = (
        '"$CC" $CFLAGS "-I$INSTALL_DIR/include" "-I/usr/include/foo" "-I/opt/include" "$harness" \\'
    )
    cxx_line = (
        '"$CXX" $CXXFLAGS "-I$INSTALL_DIR/include" "-I/usr/include/foo" "-I/opt/include" '
        '"$harness" \\'
    )
    assert c_line in script
    assert cxx_line in script


# extra_library_paths — EXTRA_LIB_PATHS variable, defined and referenced


def test_extra_library_paths_define_and_reference_variable() -> None:
    script = build_harness_script(
        _harness(extra_library_paths=["/usr/lib/x86_64-linux-gnu", "/opt/lib"])
    )
    assert 'EXTRA_LIB_PATHS="-L/usr/lib/x86_64-linux-gnu -L/opt/lib"\n' in script
    assert script.count("$EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS") == 2


def test_extra_library_paths_ordered_before_extra_link_flags_var() -> None:
    script = build_harness_script(
        _harness(transitive_link_flags=["-lbar"], extra_library_paths=["/opt/lib"])
    )
    assert "$EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS" in script


def test_oss_fuzz_variant_with_both_extra_paths_produces_same_flags() -> None:
    script = build_harness_script(
        _harness(
            extra_include_paths=["/usr/include/foo"],
            extra_library_paths=["/usr/lib/x86_64-linux-gnu"],
        ),
        oss_fuzz=True,
    )
    assert 'EXTRA_LIB_PATHS="-L/usr/lib/x86_64-linux-gnu"\n' in script
    assert '"-I$INSTALL_DIR/include" "-I/usr/include/foo" "$harness"' in script
    assert script.count("$EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS") == 2


def test_macos_whole_archive_with_extra_library_paths_does_not_corrupt_brew_prefix() -> None:
    with patch("harnessbuddy.library_builder.scripts.sys.platform", "darwin"):
        script = build_harness_script(
            _harness(transitive_link_flags=["-lzstd"], extra_library_paths=["/opt/homebrew/lib"]),
            whole_archive=True,
        )
    assert 'EXTRA_LINK_FLAGS="-L$(brew --prefix)/lib -lzstd"\n' in script
    assert 'EXTRA_LIB_PATHS="-L/opt/homebrew/lib"\n' in script
    assert "-Wl,-all_load" in script


def test_oss_fuzz_whole_archive_uses_linux_flags_even_on_macos_host() -> None:
    """The generated script always runs inside the Linux base-builder container, so
    oss_fuzz=True must use --whole-archive regardless of the host OS running
    HarnessBuddy itself."""
    with patch("harnessbuddy.library_builder.scripts.sys.platform", "darwin"):
        script = build_harness_script(_harness(), whole_archive=True, oss_fuzz=True)
    assert "-Wl,--whole-archive" in script
    assert "-Wl,--no-whole-archive" in script
    assert "-all_load" not in script


def test_linux_host_whole_archive_uses_linux_flags_for_local_environment() -> None:
    with patch("harnessbuddy.library_builder.scripts.sys.platform", "linux"):
        script = build_harness_script(_harness(), whole_archive=True)
    assert "-Wl,--whole-archive" in script
    assert "-Wl,--no-whole-archive" in script
    assert "-all_load" not in script
