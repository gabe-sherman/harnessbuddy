"""Generators for the shell scripts HarnessBuddy validates and then ships.

Every script here is environment-independent: the same text runs as a host subprocess, inside
the OSS-Fuzz base-builder container, and from the generated output directory. Each variable
the two environments disagree about is read with a fallback (`${CC:-clang}`,
`${OUT:-$SCRIPT_DIR/out}`, `${LIB_FUZZING_ENGINE:--fsanitize=fuzzer}`), so an environment that
defines it wins and one that does not still gets a working value.
"""

from __future__ import annotations

import os
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AutotoolsSetup,
    BuildPaths,
    BuildSystem,
    Language,
    LinkConfiguration,
)

# The one name for the harness source directory, in the workspace and in generated output.
HARNESS_SOURCE_DIR = "harness_source"

# libFuzzer's flag, which is what OSS-Fuzz's `compile` exports as LIB_FUZZING_ENGINE. Used as
# a fallback, so the environment's value wins whenever a script runs under `compile`.
_DEFAULT_FUZZING_ENGINE = "-fsanitize=fuzzer"

# Capped rather than -j$(nproc): builds run in containers and CI with memory limits far below
# what a large host's core count implies.
_MAX_BUILD_JOBS = 4

_JOB_COUNT = (
    f'JOBS="$(nproc)"\nif [ "$JOBS" -gt {_MAX_BUILD_JOBS} ]; then JOBS={_MAX_BUILD_JOBS}; fi\n'
)

# Autotools variants that bootstrap by running a script in the source tree, mapped to that
# script's name. Both generate configure; only the filename differs.
_AUTOTOOLS_BOOTSTRAP_SCRIPTS: dict[AutotoolsSetup | None, str] = {
    AutotoolsSetup.AUTOGEN: "autogen.sh",
    AutotoolsSetup.BOOTSTRAP: "bootstrap",
}

_CONFIGURE_ARGS_VARIABLE = "CONFIGURE_ARGS"
_CONFIGURE_ARGS_EXPANSION = f' "${{{_CONFIGURE_ARGS_VARIABLE}[@]}}"'


def build_library_script(  # noqa: PLR0913 -- each argument is a distinct input to the text
    build_system: BuildSystem,
    paths: BuildPaths,
    *,
    autotools_setup: AutotoolsSetup | None = None,
    configure_args: tuple[str, ...] = (),
    cc: str = "clang",
    cxx: str = "clang++",
    cflags: str = "",
    cxxflags: str = "",
) -> str:
    """Generate a build_library.sh script with parameterized paths.

    The compiler settings are baked in as fallbacks rather than read from the environment at
    run time, because the script must reproduce the validated build from a bare shell — which
    is how both the gate and a user following the generated README run it. An environment that
    does define them, such as the base image's sanitizer configuration, still wins.

    Args:
        build_system: detected build system.
        paths: source/build/install path strings for the generated script.
        autotools_setup: autotools bootstrap variant (only used when build_system is AUTOTOOLS).
        configure_args: build-system-level configure options, baked into the script text
            because cmake and meson have no environment equivalent.
        cc: C compiler for the library build.
        cxx: C++ compiler for the library build.
        cflags: C flags for the library build.
        cxxflags: C++ flags for the library build.
    """
    header = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'BUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n'
        "\n"
        f'CC="${{CC:-{_double_quoted_shell_value(cc)}}}"\n'
        f'CXX="${{CXX:-{_double_quoted_shell_value(cxx)}}}"\n'
        f'CFLAGS="${{CFLAGS:-{_double_quoted_shell_value(cflags)}}}"\n'
        f'CXXFLAGS="${{CXXFLAGS:-{_double_quoted_shell_value(cxxflags)}}}"\n'
    )
    header += _JOB_COUNT
    header += _configure_args_block(configure_args)
    header += _skip_if_already_built(paths.install_dir)
    body = _build_body(
        build_system,
        paths.source_dir,
        paths.build_dir,
        paths.install_dir,
        autotools_setup,
        use_configure_args=bool(configure_args),
    )
    return header + body


def _configure_args_block(configure_args: tuple[str, ...]) -> str:
    """Bake configure options into a bash array, overridable from the environment.

    An array rather than a string, so an option containing spaces stays one option. An
    EXTRA_CONFIGURE_ARGS in the environment replaces the baked list wholesale.
    """
    if not configure_args:
        return ""
    baked = " ".join(_single_quoted_shell_value(arg) for arg in configure_args)
    return (
        f"\n{_CONFIGURE_ARGS_VARIABLE}=({baked})\n"
        'if [ -n "${EXTRA_CONFIGURE_ARGS:-}" ]; then\n'
        f'  read -r -a {_CONFIGURE_ARGS_VARIABLE} <<<"$EXTRA_CONFIGURE_ARGS"\n'
        "fi\n"
    )


def _skip_if_already_built(install_dir: str) -> str:
    """Exit early when install_dir already has real artifacts (*.a + non-empty include/).

    A repeat invocation that does not first clear install_dir — a harness-discovery retry
    loop, say, unlike explore() — would otherwise redo the full compile for nothing.
    `rm -rf install_dir` forces a real rebuild.
    """
    return (
        "\n"
        f'if compgen -G "{install_dir}/lib/*.a" > /dev/null '
        f'&& [ -d "{install_dir}/include" ] '
        f'&& [ -n "$(ls -A "{install_dir}/include" 2>/dev/null)" ]; then\n'
        f'  echo "Artifacts already present in {install_dir}; skipping build '
        f'(rm -rf {install_dir} to force a rebuild)."\n'
        "  exit 0\n"
        "fi\n"
    )


def _build_body(  # noqa: PLR0913 -- one branch per build system; each needs every path
    build_system: BuildSystem,
    source_dir: str,
    build_dir: str,
    install_dir: str,
    autotools_setup: AutotoolsSetup | None = None,
    *,
    use_configure_args: bool = False,
) -> str:
    extra = _CONFIGURE_ARGS_EXPANSION if use_configure_args else ""
    if build_system == BuildSystem.CMAKE:
        return (
            "\n"
            "# build system: cmake\n"
            "\n"
            f"cmake -B {build_dir} -S {source_dir} \\\n"
            '  -DCMAKE_C_COMPILER="$CC" \\\n'
            '  -DCMAKE_CXX_COMPILER="$CXX" \\\n'
            '  -DCMAKE_C_FLAGS="$CFLAGS" \\\n'
            '  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \\\n'
            f"  -DCMAKE_INSTALL_PREFIX={install_dir} \\\n"
            "  -DBUILD_SHARED_LIBS=OFF \\\n"
            f"  -DBUILD_TESTING=OFF{extra}\n"
            f'cmake --build {build_dir} -- -j"$JOBS"\n'
            f"cmake --install {build_dir}\n"
        )
    if build_system == BuildSystem.MESON:
        return (
            "\n"
            "# build system: meson\n"
            "\n"
            'CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"  meson setup {build_dir} {source_dir} \\\n"
            f"    --prefix={install_dir} --default-library=static{extra}\n"
            f'ninja -C {build_dir} -j"$JOBS"\n'
            f"ninja -C {build_dir} install\n"
        )
    if build_system == BuildSystem.AUTOTOOLS:
        bootstrap_script = _AUTOTOOLS_BOOTSTRAP_SCRIPTS.get(autotools_setup)
        if bootstrap_script is not None:
            # The script sometimes runs configure itself; distclean resets that state.
            setup_step = f"(cd {source_dir} && ./{bootstrap_script} && make distclean || true)\n"
        elif autotools_setup == AutotoolsSetup.AUTORECONF:
            setup_step = f"(cd {source_dir} && autoreconf -fiv)\n"
        else:
            setup_step = ""
        return (
            "\n"
            "# build system: autotools\n"
            "\n" + setup_step + f"mkdir -p {build_dir}\n"
            "(\n"
            f"  cd {build_dir}\n"
            '  CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"    {source_dir}/configure --prefix={install_dir} "
            f"--enable-static --disable-shared{extra}\n"
            '  make -j"$JOBS"\n'
            "  make install\n"
            ")\n"
        )
    if build_system == BuildSystem.MAKEFILE:
        return (
            "\n"
            "# build system: makefile\n"
            "\n"
            f'make -C {source_dir} -j"$JOBS" \\\n'
            '  CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"  PREFIX={install_dir}{extra}\n"
            f"make -C {source_dir} install PREFIX={install_dir}{extra}\n"
        )
    return "\n# build system: unknown\n"


DEFAULT_FUZZER_C = (
    "#include <stddef.h>\n"
    "#include <stdint.h>\n"
    "\n"
    "int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n"
    "    // TODO: Add fuzzing logic using the target library.\n"
    "    return 0;\n"
    "}\n"
)

DEFAULT_FUZZER_CC = (
    "#include <stddef.h>\n"
    "#include <stdint.h>\n"
    "\n"
    'extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n'
    "    // TODO: Add fuzzing logic using the target library.\n"
    "    return 0;\n"
    "}\n"
)


def write_default_fuzzer(harness_dir: Path, language: Language) -> Path:
    """Write a default LLVMFuzzer stub to harness_dir for the given language."""
    ext = "c" if language == Language.C else "cc"
    source = DEFAULT_FUZZER_C if language == Language.C else DEFAULT_FUZZER_CC
    path = harness_dir / f"default_fuzzer.{ext}"
    path.write_text(source)
    return path


def build_harness_script(
    link: LinkConfiguration,
    *,
    whole_archive: bool = False,
    harness_cflags: str | None = None,
    harness_cxxflags: str | None = None,
) -> str:
    """Generate a script that compiles one harness source into one binary.

    Args:
        link: the archives and flags the harness must link against.
        whole_archive: link with --whole-archive, forcing every library symbol in so every
            undefined transitive dependency surfaces. Used by the discovery probe, not by
            the shipped script.
        harness_cflags: C flags baked in as the default, for a run of the shipped script with
            no CFLAGS in the environment. Defaults to libFuzzer's flag.
        harness_cxxflags: the same for C++.
    """
    lib_lines = "".join(f'    "$INSTALL_DIR/lib/{path.name}"\n' for path in link.static_libs)
    extra = " ".join(link.transitive_link_flags)
    extra_line = f'EXTRA_LINK_FLAGS="{extra}"\n' if extra else "EXTRA_LINK_FLAGS=\n"

    extra_lib_paths = " ".join(f"-L{path}" for path in link.extra_library_paths)
    extra_lib_paths_line = (
        f'EXTRA_LIB_PATHS="{extra_lib_paths}"\n' if extra_lib_paths else "EXTRA_LIB_PATHS=\n"
    )
    extra_include_flags = "".join(f' "-I{path}"' for path in link.extra_include_paths)

    if whole_archive:
        static_libs_str = '-Wl,--whole-archive "${STATIC_LIBS[@]-}" -Wl,--no-whole-archive'
    else:
        static_libs_str = '"${STATIC_LIBS[@]-}"'

    cc = os.environ.get("CC", "clang")
    cxx = os.environ.get("CXX", "clang++")
    cflags = harness_cflags or _DEFAULT_FUZZING_ENGINE
    cxxflags = harness_cxxflags or _DEFAULT_FUZZING_ENGINE
    preamble = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'BUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n'
        "\n"
        f'CC="${{CC:-{_double_quoted_shell_value(cc)}}}"\n'
        f'CXX="${{CXX:-{_double_quoted_shell_value(cxx)}}}"\n'
        f'CFLAGS="${{CFLAGS:-{_double_quoted_shell_value(cflags)}}}"\n'
        f'CXXFLAGS="${{CXXFLAGS:-{_double_quoted_shell_value(cxxflags)}}}"\n'
        f'LIB_FUZZING_ENGINE="${{LIB_FUZZING_ENGINE:-{_DEFAULT_FUZZING_ENGINE}}}"\n'
        "\n"
        'INSTALL_DIR="$BUILD_PREFIX/install"\n'
        'HARNESS_SOURCE="${1:?usage: compile_harness.sh SOURCE OUTPUT}"\n'
        'OUTPUT_BINARY="${2:?usage: compile_harness.sh SOURCE OUTPUT}"\n'
        'mkdir -p "$(dirname "$OUTPUT_BINARY")"\n'
        "\n"
    )

    return (
        preamble
        + "STATIC_LIBS=(\n"
        + lib_lines
        + ")\n"
        + "\n"
        + extra_line
        + extra_lib_paths_line
        + "\n"
        + 'case "$HARNESS_SOURCE" in\n'
        + "  *.c)\n"
        + (
            f'    "$CC" $CFLAGS "-I$INSTALL_DIR/include"{extra_include_flags} '
            '"$HARNESS_SOURCE" \\\n'
        )
        + (
            f"        {static_libs_str} $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS"
            ' "$LIB_FUZZING_ENGINE" -o "$OUTPUT_BINARY"\n'
        )
        + "    ;;\n"
        + "  *.cc|*.cpp|*.cxx)\n"
        + (
            f'    "$CXX" $CXXFLAGS "-I$INSTALL_DIR/include"{extra_include_flags} '
            '"$HARNESS_SOURCE" \\\n'
        )
        + (
            f"        {static_libs_str} $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS"
            ' "$LIB_FUZZING_ENGINE" -o "$OUTPUT_BINARY"\n'
        )
        + "    ;;\n"
        + "  *)\n"
        + '    echo "Unsupported harness extension: $HARNESS_SOURCE" >&2\n'
        + "    exit 2\n"
        + "    ;;\n"
        + "esac\n"
    )


def _double_quoted_shell_value(value: str) -> str:
    """Escape a configured default that is embedded inside a shell double-quoted value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def _single_quoted_shell_value(value: str) -> str:
    """Wrap a configure option in single quotes so spaces and $ survive verbatim."""
    return "'" + value.replace("'", "'\\''") + "'"


def build_harnesses_script() -> str:
    """Generate a deterministic batch wrapper around ``compile_harness.sh``.

    Takes optional source and output directories and nothing else: every compiler and linker
    decision stays in the single-harness script. $OUT is honoured when the environment defines
    it and falls back to $SCRIPT_DIR/out, so one script serves both environments.
    """
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'HARNESS_DIR="${{1:-$SCRIPT_DIR/{HARNESS_SOURCE_DIR}}}"\n'
        'OUT_DIR="${2:-${OUT:-$SCRIPT_DIR/out}}"\n'
        'mkdir -p "$OUT_DIR"\n'
        "shopt -s nullglob\n"
        "harnesses=(\n"
        '  "$HARNESS_DIR"/*.c "$HARNESS_DIR"/*.cc\n'
        '  "$HARNESS_DIR"/*.cpp "$HARNESS_DIR"/*.cxx\n'
        ")\n"
        "outputs=()\n"
        'for harness in "${harnesses[@]}"; do\n'
        '  name="$(basename "${harness%.*}")"\n'
        '  for output in "${outputs[@]}"; do\n'
        '    if [ "$output" = "$name" ]; then\n'
        '      echo "Duplicate harness output name: $name" >&2\n'
        "      exit 2\n"
        "    fi\n"
        "  done\n"
        '  outputs+=("$name")\n'
        "done\n"
        'for harness in "${harnesses[@]}"; do\n'
        '  name="$(basename "${harness%.*}")"\n'
        '  "$SCRIPT_DIR/compile_harness.sh" "$harness" "$OUT_DIR/$name"\n'
        "done\n"
    )
