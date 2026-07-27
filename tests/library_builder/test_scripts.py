from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from harnessbuddy.library_builder.models import (
    AutotoolsSetup,
    BuildPaths,
    BuildSystem,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.scripts import (
    build_harness_script,
    build_harnesses_script,
    build_library_script,
)

# The paths generate_oss_fuzz's workspace materialization uses (oss_fuzz/generation.py),
# shared by every build_library.sh content test below since the command text these
# tests assert on is driven entirely by build_system/autotools_setup, not by paths.
_OSS_FUZZ_PATHS = BuildPaths(
    source_dir="$SCRIPT_DIR/src",
    build_dir="$BUILD_PREFIX/build",
    install_dir="$BUILD_PREFIX/install",
)

_ALL_BUILD_SYSTEM_VARIANTS = [
    (BuildSystem.CMAKE, None),
    (BuildSystem.MESON, None),
    (BuildSystem.AUTOTOOLS, AutotoolsSetup.AUTORECONF),
    (BuildSystem.AUTOTOOLS, AutotoolsSetup.CONFIGURE),
    (BuildSystem.AUTOTOOLS, AutotoolsSetup.AUTOGEN),
    (BuildSystem.MAKEFILE, None),
]


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


def _fake_compiler(path: Path) -> None:
    path.write_text(
        '#!/bin/bash\nset -euo pipefail\nprintf "%s\\n" "$@" > "$COMPILER_ARGS"\ntouch "${!#}"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_single_harness_compiler_accepts_explicit_source_and_output_paths(tmp_path: Path) -> None:
    compiler = tmp_path / "compile_harness.sh"
    compiler.write_text(build_harness_script(_harness(transitive_link_flags=["-lfoo"])))
    compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
    fake_cc = tmp_path / "fake-clang"
    _fake_compiler(fake_cc)
    harness = tmp_path / "candidate.c"
    harness.write_text("int LLVMFuzzerTestOneInput(void) { return 0; }\n")
    output = tmp_path / "nested" / "candidate"
    compiler_args = tmp_path / "compiler-args.txt"

    result = subprocess.run(
        ["bash", str(compiler), str(harness), str(output)],
        capture_output=True,
        text=True,
        env={**os.environ, "CC": str(fake_cc), "CFLAGS": "", "COMPILER_ARGS": str(compiler_args)},
        timeout=10,
    )

    assert result.returncode == 0
    assert output.exists()
    assert str(harness) in compiler_args.read_text().splitlines()
    assert "-lfoo" in compiler_args.read_text().splitlines()


def test_batch_compiler_builds_every_supported_harness_into_requested_output_directory(
    tmp_path: Path,
) -> None:
    compiler = tmp_path / "compile_harness.sh"
    compiler.write_text(build_harness_script(_harness()))
    compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
    batch = tmp_path / "compile_harnesses.sh"
    batch.write_text(build_harnesses_script(harness_dir_name="harness_source", oss_fuzz=False))
    batch.chmod(batch.stat().st_mode | stat.S_IXUSR)
    fake_compiler = tmp_path / "fake-clang"
    _fake_compiler(fake_compiler)
    harness_dir = tmp_path / "agent-output"
    harness_dir.mkdir()
    (harness_dir / "first.c").write_text("// C\n")
    (harness_dir / "second.cc").write_text("// C++\n")
    (harness_dir / "ignore.txt").write_text("not a harness\n")
    output_dir = tmp_path / "binaries"

    result = subprocess.run(
        ["bash", str(batch), str(harness_dir), str(output_dir)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CC": str(fake_compiler),
            "CXX": str(fake_compiler),
            "CFLAGS": "",
            "CXXFLAGS": "",
            "COMPILER_ARGS": str(tmp_path / "compiler-args.txt"),
        },
        timeout=10,
    )

    assert result.returncode == 0
    assert {path.name for path in output_dir.iterdir()} == {"first", "second"}


def test_batch_compiler_rejects_harnesses_that_would_overwrite_each_other(tmp_path: Path) -> None:
    batch = tmp_path / "compile_harnesses.sh"
    batch.write_text(build_harnesses_script(harness_dir_name="harness_source", oss_fuzz=False))
    batch.chmod(batch.stat().st_mode | stat.S_IXUSR)
    harness_dir = tmp_path / "agent-output"
    harness_dir.mkdir()
    (harness_dir / "same.c").write_text("// C\n")
    (harness_dir / "same.cc").write_text("// C++\n")

    result = subprocess.run(
        ["bash", str(batch), str(harness_dir), str(tmp_path / "binaries")],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2


# extra_include_paths — -I flags in both compile branches


def test_extra_include_paths_appear_in_both_compile_branches() -> None:
    script = build_harness_script(
        _harness(extra_include_paths=["/usr/include/foo", "/opt/include"])
    )
    c_line = (
        '"$CC" $CFLAGS "-I$INSTALL_DIR/include" '
        '"-I/usr/include/foo" "-I/opt/include" "$HARNESS_SOURCE" \\'
    )
    cxx_line = (
        '"$CXX" $CXXFLAGS "-I$INSTALL_DIR/include" "-I/usr/include/foo" "-I/opt/include" '
        '"$HARNESS_SOURCE" \\'
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
    assert '"-I$INSTALL_DIR/include" "-I/usr/include/foo" "$HARNESS_SOURCE"' in script
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


# build_library.sh content for the oss-fuzz workspace's SCRIPT_DIR/BUILD_PREFIX layout —
# build command per build system, autotools setup variants, and the guarantee that
# capture-only instrumentation (spec 010 US2) never leaks into the shipped script.


@pytest.mark.parametrize(
    ("build_system", "autotools_setup", "expected_cmd"),
    [
        (BuildSystem.CMAKE, None, "cmake -B $BUILD_PREFIX/build"),
        (BuildSystem.MESON, None, "meson setup"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.AUTORECONF, "$SCRIPT_DIR/src/configure"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.CONFIGURE, "$SCRIPT_DIR/src/configure"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.AUTOGEN, "$SCRIPT_DIR/src/configure"),
        (BuildSystem.MAKEFILE, None, "make -C $SCRIPT_DIR/src"),
    ],
)
def test_build_library_script_oss_fuzz_build_command(
    build_system: BuildSystem, autotools_setup: AutotoolsSetup | None, expected_cmd: str
) -> None:
    script = build_library_script(build_system, _OSS_FUZZ_PATHS, autotools_setup=autotools_setup)
    assert expected_cmd in script


def test_build_library_script_autotools_configure_has_no_setup_step() -> None:
    script = build_library_script(
        BuildSystem.AUTOTOOLS, _OSS_FUZZ_PATHS, autotools_setup=AutotoolsSetup.CONFIGURE
    )
    assert "autoreconf" not in script
    assert "autogen.sh" not in script


def test_build_library_script_autotools_autogen_runs_autogen() -> None:
    script = build_library_script(
        BuildSystem.AUTOTOOLS, _OSS_FUZZ_PATHS, autotools_setup=AutotoolsSetup.AUTOGEN
    )
    assert "./autogen.sh" in script


def test_build_library_script_autotools_autoreconf_runs_autoreconf() -> None:
    script = build_library_script(
        BuildSystem.AUTOTOOLS, _OSS_FUZZ_PATHS, autotools_setup=AutotoolsSetup.AUTORECONF
    )
    assert "autoreconf -fiv" in script


@pytest.mark.parametrize(("build_system", "autotools_setup"), _ALL_BUILD_SYSTEM_VARIANTS)
def test_build_library_script_has_no_capture_instrumentation(
    build_system: BuildSystem, autotools_setup: AutotoolsSetup | None
) -> None:
    """build_library_script's output must never carry CMake/bear capture-only flags —
    capture is applied at the orchestration level (explore()), never baked into the
    template itself (spec 010 User Story 2), so the shipped oss-fuzz script is
    structurally unaffected regardless of build system."""
    script = build_library_script(build_system, _OSS_FUZZ_PATHS, autotools_setup=autotools_setup)
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" not in script
    assert "bear" not in script
