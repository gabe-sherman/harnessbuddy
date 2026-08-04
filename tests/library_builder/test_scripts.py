from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from harnessbuddy.library_builder.models import (
    AutotoolsSetup,
    BuildPaths,
    BuildSystem,
    LinkConfiguration,
)
from harnessbuddy.library_builder.scripts import (
    HARNESS_SOURCE_DIR,
    build_harness_script,
    build_harnesses_script,
    build_library_script,
)

# The paths workspace materialization uses. Shared by every build_library.sh content test
# below, since the command text they assert on depends on build_system and autotools_setup
# rather than on paths.
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
    (BuildSystem.AUTOTOOLS, AutotoolsSetup.BOOTSTRAP),
    (BuildSystem.MAKEFILE, None),
]


def test_build_library_script_skips_when_artifacts_already_present(tmp_path: Path) -> None:
    """A repeat invocation that does not first clear install_dir — a harness-discovery retry
    loop, unlike explore(), which always wipes it — must not redo the real build. source_dir
    points nowhere, so reaching `make` would fail."""
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
) -> LinkConfiguration:
    return LinkConfiguration(
        static_libs=static_libs if static_libs is not None else [Path("libfoo.a")],
        transitive_link_flags=transitive_link_flags or [],
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


def test_single_harness_compiler_uses_configured_defaults_without_environment_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_cc = tmp_path / "fake-clang"
    _fake_compiler(fake_cc)
    monkeypatch.setenv("CC", str(fake_cc))
    compiler = tmp_path / "compile_harness.sh"
    compiler.write_text(
        build_harness_script(
            _harness(),
            harness_cflags="-fsanitize=fuzzer,address -fprofile-instr-generate",
        )
    )
    compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
    harness = tmp_path / "candidate.c"
    harness.write_text("int LLVMFuzzerTestOneInput(void) { return 0; }\n")
    compiler_args = tmp_path / "compiler-args.txt"
    environment = {**os.environ, "COMPILER_ARGS": str(compiler_args)}
    environment.pop("CC", None)
    environment.pop("CFLAGS", None)

    result = subprocess.run(
        ["bash", str(compiler), str(harness), str(tmp_path / "candidate")],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )

    assert result.returncode == 0
    assert "-fsanitize=fuzzer,address" in compiler_args.read_text().splitlines()


@pytest.mark.skipif(shutil.which("clang") is None, reason="clang is required for libFuzzer linking")
def test_single_harness_compiler_uses_its_baked_flags_in_a_bare_environment(
    tmp_path: Path,
) -> None:
    """The generated compiler has to work from a bare shell with nothing exported, which is how
    the gate and a user of the shipped output both run it."""
    compiler = tmp_path / "compile_harness.sh"
    compiler.write_text(
        build_harness_script(
            _harness(static_libs=[]),
            harness_cflags="-fsanitize=fuzzer -DHARNESSBUDDY_TEST",
        )
    )
    compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
    harness = tmp_path / "candidate.c"
    harness.write_text(
        "#include <stdint.h>\n"
        "#include <stddef.h>\n"
        "#ifndef HARNESSBUDDY_TEST\n"
        "#error generated harness flags were not used\n"
        "#endif\n"
        "int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n"
        "  return (int)(data == 0 && size != 0);\n"
        "}\n"
    )
    environment = {k: v for k, v in os.environ.items() if k not in {"CC", "CFLAGS"}}

    result = subprocess.run(
        ["bash", str(compiler), str(harness), str(tmp_path / "candidate")],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_single_harness_compiler_lets_the_environment_override_its_flags() -> None:
    """The precedence that makes one script text work in both places: the base image's CFLAGS
    carry the sanitizer configuration, so they must win over anything baked in."""
    script = build_harness_script(_harness(), harness_cflags="-fsanitize=fuzzer")
    assert 'CFLAGS="${CFLAGS:--fsanitize=fuzzer}"' in script


def test_batch_compiler_builds_every_supported_harness_into_requested_output_directory(
    tmp_path: Path,
) -> None:
    compiler = tmp_path / "compile_harness.sh"
    compiler.write_text(build_harness_script(_harness()))
    compiler.chmod(compiler.stat().st_mode | stat.S_IXUSR)
    batch = tmp_path / "compile_harnesses.sh"
    batch.write_text(build_harnesses_script())
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
    batch.write_text(build_harnesses_script())
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


def test_extra_paths_are_the_same_in_every_environment() -> None:
    """There is one script text: the environment shows up only in what the fallbacks resolve
    to at run time, never in what is generated."""
    script = build_harness_script(
        _harness(
            extra_include_paths=["/usr/include/foo"],
            extra_library_paths=["/usr/lib/x86_64-linux-gnu"],
        ),
    )
    assert 'EXTRA_LIB_PATHS="-L/usr/lib/x86_64-linux-gnu"\n' in script
    assert '"-I$INSTALL_DIR/include" "-I/usr/include/foo" "$HARNESS_SOURCE"' in script
    assert script.count("$EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS") == 2


def test_whole_archive_uses_the_linux_linker_flags() -> None:
    """Every generated script runs on Linux: on the host HarnessBuddy targets and in the
    base-builder container."""
    script = build_harness_script(_harness(), whole_archive=True)
    assert "-Wl,--whole-archive" in script
    assert "-Wl,--no-whole-archive" in script
    assert "-all_load" not in script


def test_whole_archive_leaves_the_extra_paths_alone() -> None:
    script = build_harness_script(
        _harness(transitive_link_flags=["-lzstd"], extra_library_paths=["/opt/lib"]),
        whole_archive=True,
    )
    assert 'EXTRA_LINK_FLAGS="-lzstd"\n' in script
    assert 'EXTRA_LIB_PATHS="-L/opt/lib"\n' in script


# environment-independence: the fallbacks that let one script text serve both environments


def test_harness_script_falls_back_to_libfuzzer_when_no_engine_is_set() -> None:
    """The in-container probe runs the script directly rather than through `compile`, which is
    what would set $LIB_FUZZING_ENGINE — so the fallback matters there too, not only on the
    host."""
    script = build_harness_script(_harness())
    assert 'LIB_FUZZING_ENGINE="${LIB_FUZZING_ENGINE:--fsanitize=fuzzer}"' in script
    assert script.count('"$LIB_FUZZING_ENGINE"') == 2


def test_batch_script_prefers_the_container_out_directory_when_set() -> None:
    script = build_harnesses_script()
    assert 'OUT_DIR="${2:-${OUT:-$SCRIPT_DIR/out}}"' in script


def test_batch_script_reads_the_one_harness_directory_name() -> None:
    assert f"$SCRIPT_DIR/{HARNESS_SOURCE_DIR}" in build_harnesses_script()


@pytest.mark.parametrize("variable", ["CC", "CXX", "CFLAGS", "CXXFLAGS"])
def test_library_script_lets_the_environment_win_over_its_baked_settings(variable: str) -> None:
    """The baked values reproduce the validated build from a bare shell, and the base image's
    sanitizer configuration still takes precedence in the container."""
    script = build_library_script(BuildSystem.CMAKE, _OSS_FUZZ_PATHS, cflags="-O2")
    assert f'{variable}="${{{variable}:-' in script


def test_library_script_bakes_the_configured_compiler_settings() -> None:
    script = build_library_script(
        BuildSystem.CMAKE, _OSS_FUZZ_PATHS, cc="gcc", cxx="g++", cflags="-O2 -g"
    )
    assert 'CC="${CC:-gcc}"' in script
    assert 'CXX="${CXX:-g++}"' in script
    assert 'CFLAGS="${CFLAGS:--O2 -g}"' in script


# --library-configure-arg — configure options reach the configure step, not CFLAGS


@pytest.mark.parametrize(
    ("build_system", "autotools_setup"),
    [
        (BuildSystem.CMAKE, None),
        (BuildSystem.MESON, None),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.CONFIGURE),
        (BuildSystem.MAKEFILE, None),
    ],
)
def test_configure_args_land_at_the_configure_step(
    build_system: BuildSystem, autotools_setup: AutotoolsSetup | None
) -> None:
    script = build_library_script(
        build_system,
        _OSS_FUZZ_PATHS,
        autotools_setup=autotools_setup,
        configure_args=("-DBUILD_TESTING=OFF",),
    )
    assert "CONFIGURE_ARGS=('-DBUILD_TESTING=OFF')" in script
    # The options reach the build system's configuration, not the compiler's flags:
    # --library-cflags would have made this one a preprocessor define that does nothing.
    body = script[script.index("# build system:") :]
    assert '"${CONFIGURE_ARGS[@]}"' in body
    flag_lines = [line for line in script.splitlines() if line.startswith(("CFLAGS=", "CXXFLAGS="))]
    assert all("-DBUILD_TESTING=OFF" not in line for line in flag_lines)


def test_configure_args_survive_a_value_containing_a_space(tmp_path: Path) -> None:
    """A single space-separated string would word-split this into two options."""
    script = build_library_script(
        BuildSystem.CMAKE, _OSS_FUZZ_PATHS, configure_args=("-DCMAKE_C_FLAGS=-O2 -g",)
    )
    probe = tmp_path / "probe.sh"
    probe.write_text(
        script[: script.index("if compgen")] + 'printf "%s\n" "${CONFIGURE_ARGS[@]}"\n'
    )
    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True, timeout=10)
    assert result.stdout == "-DCMAKE_C_FLAGS=-O2 -g\n"


def test_configure_args_can_be_overridden_from_the_environment(tmp_path: Path) -> None:
    script = build_library_script(
        BuildSystem.CMAKE, _OSS_FUZZ_PATHS, configure_args=("-DBUILD_TESTING=OFF",)
    )
    probe = tmp_path / "probe.sh"
    probe.write_text(
        script[: script.index("if compgen")] + 'printf "%s\n" "${CONFIGURE_ARGS[@]}"\n'
    )
    result = subprocess.run(
        ["bash", str(probe)],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "EXTRA_CONFIGURE_ARGS": "-DFOO=bar -DBAZ=qux"},
    )
    assert result.stdout == "-DFOO=bar\n-DBAZ=qux\n"


def test_no_configure_args_leaves_the_array_out_entirely() -> None:
    script = build_library_script(BuildSystem.CMAKE, _OSS_FUZZ_PATHS)
    assert "CONFIGURE_ARGS" not in script


# job cap — every build system, not just cmake


@pytest.mark.parametrize(
    ("build_system", "autotools_setup"),
    [
        (BuildSystem.CMAKE, None),
        (BuildSystem.MESON, None),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.CONFIGURE),
        (BuildSystem.MAKEFILE, None),
    ],
)
def test_every_build_system_caps_parallelism(
    build_system: BuildSystem, autotools_setup: AutotoolsSetup | None
) -> None:
    """Builds run in containers and CI with memory limits far below what -j$(nproc) implies on
    a large host, so the cap applies to every build system rather than only to cmake."""
    script = build_library_script(build_system, _OSS_FUZZ_PATHS, autotools_setup=autotools_setup)
    assert 'if [ "$JOBS" -gt 4 ]; then JOBS=4; fi' in script
    assert '-j"$JOBS"' in script
    assert "-j$(nproc)" not in script


# build_library.sh content for the workspace's SCRIPT_DIR/BUILD_PREFIX layout: the build
# command per build system, the autotools setup variants, and the guarantee that capture-only
# instrumentation never leaks into the shipped script.


@pytest.mark.parametrize(
    ("build_system", "autotools_setup", "expected_cmd"),
    [
        (BuildSystem.CMAKE, None, "cmake -B $BUILD_PREFIX/build"),
        (BuildSystem.MESON, None, "meson setup"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.AUTORECONF, "$SCRIPT_DIR/src/configure"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.CONFIGURE, "$SCRIPT_DIR/src/configure"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.AUTOGEN, "$SCRIPT_DIR/src/configure"),
        (BuildSystem.AUTOTOOLS, AutotoolsSetup.BOOTSTRAP, "$SCRIPT_DIR/src/configure"),
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
    assert "./bootstrap" not in script


def test_build_library_script_autotools_autogen_runs_autogen() -> None:
    script = build_library_script(
        BuildSystem.AUTOTOOLS, _OSS_FUZZ_PATHS, autotools_setup=AutotoolsSetup.AUTOGEN
    )
    assert "./autogen.sh" in script


def test_build_library_script_autotools_bootstrap_runs_bootstrap() -> None:
    script = build_library_script(
        BuildSystem.AUTOTOOLS, _OSS_FUZZ_PATHS, autotools_setup=AutotoolsSetup.BOOTSTRAP
    )
    assert "./bootstrap" in script
    assert "./autogen.sh" not in script


def test_build_library_script_autotools_autoreconf_runs_autoreconf() -> None:
    script = build_library_script(
        BuildSystem.AUTOTOOLS, _OSS_FUZZ_PATHS, autotools_setup=AutotoolsSetup.AUTORECONF
    )
    assert "autoreconf -fiv" in script


@pytest.mark.parametrize(("build_system", "autotools_setup"), _ALL_BUILD_SYSTEM_VARIANTS)
def test_build_library_script_has_no_capture_instrumentation(
    build_system: BuildSystem, autotools_setup: AutotoolsSetup | None
) -> None:
    """build_library_script's output must never carry CMake/bear capture-only flags: capture is
    applied by explore(), never baked into the script, so the shipped one is unaffected for
    every build system."""
    script = build_library_script(build_system, _OSS_FUZZ_PATHS, autotools_setup=autotools_setup)
    assert "CMAKE_EXPORT_COMPILE_COMMANDS" not in script
    assert "bear" not in script
