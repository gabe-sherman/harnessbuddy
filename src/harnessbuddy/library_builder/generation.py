from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
    GenerationResult,
    Language,
)

_DOCKERFILE_NO_REF = (
    "FROM gcr.io/oss-fuzz-base/base-builder\n"
    "RUN git clone {clone_url} $SRC/{project_name}\n"
    "COPY harness_source $SRC/harness_source\n"
    "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
    "WORKDIR $SRC/{project_name}\n"
)

_DOCKERFILE_WITH_REF = (
    "FROM gcr.io/oss-fuzz-base/base-builder\n"
    "RUN git clone {clone_url} $SRC/{project_name}\n"
    "RUN git -C $SRC/{project_name} checkout {repo_ref}\n"
    "COPY harness_source $SRC/harness_source\n"
    "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n"
    "WORKDIR $SRC/{project_name}\n"
)

_BUILD_SH = (
    '#!/bin/bash\nset -euo pipefail\n\n"$SRC/build_library.sh"\n"$SRC/compile_harnesses.sh"\n'
)

_BUILD_ENV_FOOTER = (
    "\ncat > ../build.env <<'EOF'\n"
    'HB_INCLUDE_FLAGS="-I../install/include"\n'
    'HB_LIBRARY_FLAGS="-L../install/lib"\n'
    "EOF\n"
)

_BUILD_LIBRARY_SH_CMAKE = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# build system: cmake\n"
    "\n"
    "cmake -B ../build \\\n"
    '  -DCMAKE_C_COMPILER="$CC" \\\n'
    '  -DCMAKE_CXX_COMPILER="$CXX" \\\n'
    '  -DCMAKE_C_FLAGS="$CFLAGS" \\\n'
    '  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \\\n'
    "  -DCMAKE_INSTALL_PREFIX=../install \\\n"
    "  -DBUILD_SHARED_LIBS=OFF\n"
    "cmake --build ../build -- -j$(nproc)\n"
    "cmake --install ../build\n" + _BUILD_ENV_FOOTER
)

_BUILD_LIBRARY_SH_MESON = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# build system: meson\n"
    "\n"
    'CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
    "  meson setup ../build --prefix=../install --default-library=static\n"
    "ninja -C ../build\n"
    "ninja -C ../build install\n" + _BUILD_ENV_FOOTER
)

_BUILD_LIBRARY_SH_AUTOTOOLS = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# build system: autotools\n"
    "\n"
    'CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
    "  ./configure --prefix=../install --enable-static --disable-shared\n"
    "make -j$(nproc)\n"
    "make install\n" + _BUILD_ENV_FOOTER
)

_BUILD_LIBRARY_SH_MAKEFILE = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# build system: makefile\n"
    "\n"
    "make -j$(nproc) \\\n"
    '  CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
    "  PREFIX=../install\n"
    "make install PREFIX=../install\n" + _BUILD_ENV_FOOTER
)

_BUILD_LIBRARY_SH_NINJA = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# build system: ninja\n"
    "\n"
    'CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
    "  ninja -j$(nproc)\n"
    "# best-effort; no standard install convention\n" + _BUILD_ENV_FOOTER
)

_BUILD_LIBRARY_SH_UNKNOWN = (
    "#!/bin/bash\nset -euo pipefail\n\n# build system: unknown\n" + _BUILD_ENV_FOOTER
)

_BUILD_LIBRARY_SH_BY_BUILD_SYSTEM: dict[BuildSystem, str] = {
    BuildSystem.CMAKE: _BUILD_LIBRARY_SH_CMAKE,
    BuildSystem.MESON: _BUILD_LIBRARY_SH_MESON,
    BuildSystem.AUTOTOOLS: _BUILD_LIBRARY_SH_AUTOTOOLS,
    BuildSystem.MAKEFILE: _BUILD_LIBRARY_SH_MAKEFILE,
    BuildSystem.NINJA: _BUILD_LIBRARY_SH_NINJA,
    BuildSystem.UNKNOWN: _BUILD_LIBRARY_SH_UNKNOWN,
}

_COMPILE_HARNESSES_SH = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# shellcheck source=/dev/null\n"
    'source "../build.env"\n'
    "\n"
    'HARNESS_DIR="/src/harness_source"\n'
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
    if analysis.repo_ref is None:
        content = _DOCKERFILE_NO_REF.format(
            clone_url=analysis.clone_url,
            project_name=analysis.project_name,
        )
    else:
        content = _DOCKERFILE_WITH_REF.format(
            clone_url=analysis.clone_url,
            project_name=analysis.project_name,
            repo_ref=analysis.repo_ref,
        )
    path.write_text(content)
    return path


def _write_build_sh(output_path: Path) -> Path:
    path = output_path / "build.sh"
    path.write_text(_BUILD_SH)
    return path


def _write_build_library_sh(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "build_library.sh"
    path.write_text(_BUILD_LIBRARY_SH_BY_BUILD_SYSTEM[analysis.build_system])
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
