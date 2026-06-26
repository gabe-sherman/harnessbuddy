from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
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

_BUILD_LIBRARY_SH = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# build system: {build_system}\n"
    'mkdir -p "$WORK/harnessbuddy"\n'
    "cat > \"$WORK/harnessbuddy/build.env\" <<'EOF'\n"
    'HB_INCLUDE_FLAGS=""\n'
    'HB_LIBRARY_FLAGS=""\n'
    "EOF\n"
)

_COMPILE_HARNESSES_SH = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    "# shellcheck source=/dev/null\n"
    'source "$WORK/harnessbuddy/build.env"\n'
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
    path.write_text(_BUILD_LIBRARY_SH.format(build_system=analysis.build_system.value))
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
