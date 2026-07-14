from __future__ import annotations

import sys
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AutotoolsSetup,
    BuildPaths,
    BuildSystem,
    HarnessExplorationResult,
    Language,
)

_HOST_ENV_FALLBACKS = (
    '\nCC="${CC:-clang}"\nCXX="${CXX:-clang++}"\nCFLAGS="${CFLAGS:-}"\nCXXFLAGS="${CXXFLAGS:-}"\n'
)


def build_library_script(
    build_system: BuildSystem,
    paths: BuildPaths,
    *,
    host_fallbacks: bool = False,
    autotools_setup: AutotoolsSetup | None = None,
) -> str:
    """Generate a build_library.sh script with parameterized paths.

    Args:
        build_system: detected build system.
        paths: source/build/install/env-file path strings for the generated script.
        host_fallbacks: when True, add CC/CXX/CFLAGS/CXXFLAGS defaults for host builds.
        autotools_setup: autotools bootstrap variant (only used when build_system is AUTOTOOLS).
    """
    header = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'BUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n'
    )
    if host_fallbacks:
        header += _HOST_ENV_FALLBACKS
    header += _skip_if_already_built(paths.install_dir)
    body = _build_body(
        build_system, paths.source_dir, paths.build_dir, paths.install_dir, autotools_setup
    )
    return header + body


def _skip_if_already_built(install_dir: str) -> str:
    """Exit early when install_dir already has real artifacts (*.a + non-empty include/).

    Repeated invocations that don't first clear install_dir (as explore() does for the
    authoritative library-build stage) — e.g. re-running build_library.sh as part of a
    harness-discovery retry loop — would otherwise redo the full compile every time for
    no reason. rm -rf install_dir forces a real rebuild.
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


def _build_body(
    build_system: BuildSystem,
    source_dir: str,
    build_dir: str,
    install_dir: str,
    autotools_setup: AutotoolsSetup | None = None,
) -> str:
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
            "  -DBUILD_SHARED_LIBS=OFF\n"
            f"cmake --build {build_dir} -- -j$(nproc)\n"
            f"cmake --install {build_dir}\n"
        )
    if build_system == BuildSystem.MESON:
        return (
            "\n"
            "# build system: meson\n"
            "\n"
            'CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"  meson setup {build_dir} {source_dir} \\\n"
            f"    --prefix={install_dir} --default-library=static\n"
            f"ninja -C {build_dir}\n"
            f"ninja -C {build_dir} install\n"
        )
    if build_system == BuildSystem.AUTOTOOLS:
        if autotools_setup == AutotoolsSetup.AUTOGEN:
            # sometimes autogen already runs configure, run distclean to reset directory state
            setup_step = f"(cd {source_dir} && ./autogen.sh && make distclean)\n"
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
            f"    {source_dir}/configure --prefix={install_dir} --enable-static --disable-shared\n"
            "  make -j$(nproc)\n"
            "  make install\n"
            ")\n"
        )
    if build_system == BuildSystem.MAKEFILE:
        return (
            "\n"
            "# build system: makefile\n"
            "\n"
            f"make -C {source_dir} -j$(nproc) \\\n"
            '  CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"  PREFIX={install_dir}\n"
            f"make -C {source_dir} install PREFIX={install_dir}\n"
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
    harness: HarnessExplorationResult,
    *,
    whole_archive: bool = False,
    harness_dir_name: str = "harness_src",
    oss_fuzz: bool = False,
) -> str:
    """Generate a harness compilation script.

    Args:
        harness: exploration result providing static libs and link flags.
        whole_archive: when True, link with --whole-archive (Linux, or oss_fuzz — the
            script always runs inside the Linux base-builder container regardless of
            the host OS) or -all_load (macOS, local environment only).
        harness_dir_name: name of the directory containing harness source files.
        oss_fuzz: when True, generate an OSS-Fuzz-compatible script that uses
            CC/CXX/CFLAGS/CXXFLAGS/$OUT/$LIB_FUZZING_ENGINE from the base image
            rather than defining them with local defaults.
    """
    lib_lines = "".join(f'    "$INSTALL_DIR/lib/{p.name}"\n' for p in harness.static_libs)
    extra = " ".join(harness.transitive_link_flags)

    if oss_fuzz:
        extra_line = f'EXTRA_LINK_FLAGS="{extra}"\n' if extra else "EXTRA_LINK_FLAGS=\n"
    else:
        extra_lib_path = "" if sys.platform != "darwin" else "-L$(brew --prefix)/lib "
        if extra:
            extra_line = f'EXTRA_LINK_FLAGS="{extra_lib_path}{extra}"\n'
        else:
            extra_line = "EXTRA_LINK_FLAGS=\n"

    extra_lib_paths = " ".join(f"-L{p}" for p in harness.extra_library_paths)
    extra_lib_paths_line = (
        f'EXTRA_LIB_PATHS="{extra_lib_paths}"\n' if extra_lib_paths else "EXTRA_LIB_PATHS=\n"
    )
    extra_include_flags = "".join(f' "-I{p}"' for p in harness.extra_include_paths)

    if whole_archive:
        if not oss_fuzz and sys.platform == "darwin":
            wa_before, wa_after = "-Wl,-all_load", ""
        else:
            wa_before, wa_after = "-Wl,--whole-archive", "-Wl,--no-whole-archive"
        static_libs_str = f'{wa_before} "${{STATIC_LIBS[@]-}}" {wa_after}'
    else:
        static_libs_str = '"${STATIC_LIBS[@]-}"'

    engine_flag = ' "$LIB_FUZZING_ENGINE"' if oss_fuzz else ""
    out_ref = "$OUT" if oss_fuzz else "$OUT_DIR"

    preamble = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'BUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n'
        "\n"
    )
    if not oss_fuzz:
        preamble += (
            'CC="${CC:-clang}"\n'
            'CXX="${CXX:-clang++}"\n'
            'CFLAGS="${CFLAGS:--fsanitize=fuzzer}"\n'
            'CXXFLAGS="${CXXFLAGS:--fsanitize=fuzzer}"\n'
            "\n"
        )
    preamble += (
        f'INSTALL_DIR="$BUILD_PREFIX/install"\nHARNESS_DIR="$SCRIPT_DIR/{harness_dir_name}"\n'
    )
    if not oss_fuzz:
        preamble += 'OUT_DIR="$SCRIPT_DIR/out"\nmkdir -p "$OUT_DIR"\n'
    preamble += "\n"

    return (
        preamble
        + "STATIC_LIBS=(\n"
        + lib_lines
        + ")\n"
        + "\n"
        + extra_line
        + extra_lib_paths_line
        + "\n"
        + 'for harness in "$HARNESS_DIR"/*; do\n'
        + '  [ -f "$harness" ] || continue\n'
        + '  name="$(basename "$harness")"\n'
        + '  echo "Compiling harness $name"\n'
        + '  output="${name%.*}"\n'
        + '  case "$harness" in\n'
        + "    *.c)\n"
        + f'      "$CC" $CFLAGS "-I$INSTALL_DIR/include"{extra_include_flags} "$harness" \\\n'
        + (
            f"        {static_libs_str} $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS{engine_flag}"
            f' -o "{out_ref}/$output"\n'
        )
        + "      ;;\n"
        + "    *.cc|*.cpp|*.cxx)\n"
        + f'      "$CXX" $CXXFLAGS "-I$INSTALL_DIR/include"{extra_include_flags} "$harness" \\\n'
        + (
            f"        {static_libs_str} $EXTRA_LIB_PATHS $EXTRA_LINK_FLAGS{engine_flag}"
            f' -o "{out_ref}/$output"\n'
        )
        + "      ;;\n"
        + "  esac\n"
        + "done\n"
    )
