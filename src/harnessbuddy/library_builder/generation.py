from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    AutotoolsSetup,
    BuildExplorationResult,
    BuildSystem,
    GenerationResult,
    Language,
)

_AUTOTOOLS_APT_DEPS = (
    "RUN apt-get update && apt-get install -y --no-install-recommends"
    " autoconf automake libtool pkg-config\n"
)

_BUILD_SH = (
    '#!/bin/bash\nset -euo pipefail\n\n"$SRC/build_library.sh"\n"$SRC/compile_harnesses.sh"\n'
)

_COMPILE_HARNESSES_SH = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    "\n"
    "# shellcheck source=/dev/null\n"
    'source "$SCRIPT_DIR/build.env"\n'
    "\n"
    'HARNESS_DIR="$SCRIPT_DIR/harness_source"\n'
    "\n"
    'for harness in "$HARNESS_DIR"/*; do\n'
    '  [ -f "$harness" ] || continue\n'
    "\n"
    '  name="$(basename "$harness")"\n'
    '  output="${name%.*}"\n'
    "\n"
    '  case "$harness" in\n'
    "    *.c)\n"
    '      "$CC" $CFLAGS $HB_INCLUDE_FLAGS "$harness" \\\n'
    '        $HB_LIBRARY_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n'
    "      ;;\n"
    "    *.cc|*.cpp|*.cxx)\n"
    '      "$CXX" $CXXFLAGS $HB_INCLUDE_FLAGS "$harness" \\\n'
    '        $HB_LIBRARY_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n'
    "      ;;\n"
    "  esac\n"
    "done\n"
)

_DEFAULT_FUZZER_CC = (
    "#include <stddef.h>\n"
    "#include <stdint.h>\n"
    "\n"
    'extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n'
    "    // TODO: Add fuzzing logic using the target library.\n"
    "    return 0;\n"
    "}\n"
)

_HOST_ENV_FALLBACKS = (
    '\nCC="${CC:-cc}"\n'
    'CXX="${CXX:-c++}"\n'
    'CFLAGS="${CFLAGS:-}"\n'
    'CXXFLAGS="${CXXFLAGS:-}"\n'
)


def build_library_script(
    build_system: BuildSystem,
    source_dir: str,
    build_dir: str,
    install_dir: str,
    env_file: str,
    *,
    host_fallbacks: bool = False,
    autotools_setup: AutotoolsSetup | None = None,
) -> str:
    """Generate a build_library.sh script with parameterized paths.

    Args:
        build_system: detected build system.
        source_dir: path string for the source directory.
        build_dir: path string for the build directory (relative or absolute).
        install_dir: path string for the install prefix.
        env_file: path string where build.env will be written.
        host_fallbacks: when True, add CC/CXX/CFLAGS/CXXFLAGS defaults for host builds.
        autotools_setup: autotools bootstrap variant (only used when build_system is AUTOTOOLS).
    """
    header = "#!/bin/bash\nset -euo pipefail\n"
    if host_fallbacks:
        header += _HOST_ENV_FALLBACKS
    else:
        header += '\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    body = _build_body(build_system, source_dir, build_dir, install_dir, autotools_setup)
    footer = (
        f"\ncat > {env_file} <<'EOF'\n"
        f'HB_INCLUDE_FLAGS="-I{install_dir}/include"\n'
        f'HB_LIBRARY_FLAGS="-L{install_dir}/lib"\n'
        "EOF\n"
    )
    return header + body + footer


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
            "\n"
            + setup_step
            + f"mkdir -p {build_dir}\n"
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


class OutputDirectoryExistsError(Exception):
    """Target output directory already exists."""


def generate(
    analysis: AnalysisResult,
    output_parent: Path,
    exploration: BuildExplorationResult | None = None,
) -> GenerationResult:
    """Generate a complete oss-fuzz project skeleton from a static analysis result."""
    output_path = output_parent / analysis.project_name
    if output_path.exists():
        raise OutputDirectoryExistsError(
            f"Output directory already exists: {output_path}. "
            "Remove it or choose a different --output directory."
        )
    output_path.mkdir(parents=True)
    (output_path / "harness_source").mkdir()

    files: list[Path] = [
        _write_project_yaml(output_path, analysis),
        _write_dockerfile(output_path, analysis),
        _write_build_sh(output_path),
        _write_build_library_sh(output_path, analysis),
        _write_compile_harnesses_sh(output_path),
        _write_default_fuzzer(output_path / "harness_source"),
        _write_provenance_json(output_path, analysis, exploration),
    ]

    return GenerationResult(
        project_name=analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _write_project_yaml(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "project.yaml"
    language = "c" if analysis.language == Language.C else "c++"
    path.write_text(f"homepage: {analysis.clone_url}\nlanguage: {language}\n")
    return path


def _write_dockerfile(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "Dockerfile"
    lines = ["FROM gcr.io/oss-fuzz-base/base-builder\n"]
    if analysis.autotools_setup in {AutotoolsSetup.AUTOGEN, AutotoolsSetup.AUTORECONF}:
        lines.append(_AUTOTOOLS_APT_DEPS)
    lines.append(f"RUN git clone {analysis.clone_url} $SRC/{analysis.project_name}\n")
    if analysis.repo_ref is not None:
        lines.append(
            f"RUN git -C $SRC/{analysis.project_name} checkout {analysis.repo_ref}\n"
        )
    lines += [
        "COPY harness_source $SRC/harness_source\n",
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n",
        f"WORKDIR $SRC/{analysis.project_name}\n",
    ]
    path.write_text("".join(lines))
    return path


def _write_build_sh(output_path: Path) -> Path:
    path = output_path / "build.sh"
    path.write_text(_BUILD_SH)
    return path


def _write_build_library_sh(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "build_library.sh"
    path.write_text(
        build_library_script(
            analysis.build_system,
            source_dir=f"$SCRIPT_DIR/{analysis.project_name}",
            build_dir="$SCRIPT_DIR/build",
            install_dir="$SCRIPT_DIR/install",
            env_file="$SCRIPT_DIR/build.env",
            autotools_setup=analysis.autotools_setup,
        )
    )
    return path


def _write_compile_harnesses_sh(output_path: Path) -> Path:
    path = output_path / "compile_harnesses.sh"
    path.write_text(_COMPILE_HARNESSES_SH)
    return path


def _write_default_fuzzer(harness_dir: Path) -> Path:
    path = harness_dir / "default_fuzzer.cc"
    path.write_text(_DEFAULT_FUZZER_CC)
    return path


def _write_provenance_json(
    output_path: Path,
    analysis: AnalysisResult,
    exploration: BuildExplorationResult | None = None,
) -> Path:
    path = output_path / "provenance.json"
    provenance: dict[str, object] = {
        "project_name": analysis.project_name,
        "build_system": analysis.build_system.value,
        "build_files": sorted(
            str(p.relative_to(analysis.source_path)) for p in analysis.build_files
        ),
        "headers": sorted(str(p.relative_to(analysis.source_path)) for p in analysis.headers),
        "language": analysis.language.value,
        "clone_url": analysis.clone_url,
        "repo_ref": analysis.repo_ref,
        "output_path": str(output_path),
        "warnings": analysis.warnings,
    }
    if analysis.autotools_setup is not None:
        provenance["autotools_setup"] = analysis.autotools_setup.value
    if exploration is not None:
        provenance["host_build_exploration"] = {
            "build_system": exploration.build_system.value,
            "succeeded": exploration.succeeded,
            "command": exploration.command,
            "stdout": exploration.stdout,
            "stderr": exploration.stderr,
            "exit_code": exploration.exit_code,
            "duration_seconds": exploration.duration_seconds,
        }
    path.write_text(json.dumps(provenance, indent=2) + "\n")
    return path
