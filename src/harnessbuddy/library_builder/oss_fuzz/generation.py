from __future__ import annotations

import json
import stat
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    AutotoolsSetup,
    BuildExplorationResult,
    GenerationResult,
    Language,
    OutputDirectoryExistsError,
)
from harnessbuddy.library_builder.scripts import build_library_script

_AUTOTOOLS_PACKAGES = ("autoconf", "automake", "libtool", "pkg-config")

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


def generate_oss_fuzz(
    analysis: AnalysisResult,
    output_parent: Path,
    exploration: BuildExplorationResult | None = None,
) -> GenerationResult:
    """Generate a complete oss-fuzz project skeleton from a static analysis result."""
    output_path = output_parent / analysis.project_name / "output" / "oss-fuzz"

    if output_path.exists():
        raise OutputDirectoryExistsError(output_path)

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
    path.write_text(f"homepage: {analysis.clone_url}\nlanguage: {language}\nsanitizers:\n  - address\n  - undefined\nmain_repo: {analysis.clone_url}\n")
    return path


def _write_dockerfile(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "Dockerfile"
    lines = ["FROM gcr.io/oss-fuzz-base/base-builder\n"]

    apt_packages: list[str] = []
    if analysis.autotools_setup in {AutotoolsSetup.AUTOGEN, AutotoolsSetup.AUTORECONF}:
        apt_packages.extend(_AUTOTOOLS_PACKAGES)
    apt_packages.extend(analysis.system_packages)
    if apt_packages:
        pkgs = " ".join(apt_packages)
        lines.append(
            f"RUN apt-get update && apt-get install -y --no-install-recommends {pkgs}\n"
        )

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
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
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
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_compile_harnesses_sh(output_path: Path) -> Path:
    path = output_path / "compile_harnesses.sh"
    path.write_text(_COMPILE_HARNESSES_SH)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
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
    if analysis.system_packages:
        provenance["system_packages"] = analysis.system_packages
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
