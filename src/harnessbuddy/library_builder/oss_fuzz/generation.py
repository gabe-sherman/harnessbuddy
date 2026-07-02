from __future__ import annotations

import shutil
import stat
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    AutotoolsSetup,
    BuildExplorationResult,
    BuildPaths,
    GenerationResult,
    HarnessExplorationResult,
    Language,
)
from harnessbuddy.library_builder.scripts import (
    build_harness_script,
    build_library_script,
    write_default_fuzzer,
)

_AUTOTOOLS_PACKAGES = ("autoconf", "automake", "libtool", "pkg-config")

_BUILD_SH = (
    '#!/bin/bash\nset -euo pipefail\n\n"$SRC/build_library.sh"\n"$SRC/compile_harnesses.sh"\n'
)

_COMPILE_HARNESSES_SH_STUB = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    "\n"
    'INSTALL_DIR="$SCRIPT_DIR/install"\n'
    'HARNESS_DIR="$SCRIPT_DIR/harness_source"\n'
    "\n"
    "# TODO: add static library paths\n"
    "STATIC_LIBS=()\n"
    "EXTRA_LINK_FLAGS=\n"
    "\n"
    'for harness in "$HARNESS_DIR"/*; do\n'
    '  [ -f "$harness" ] || continue\n'
    '  name="$(basename "$harness")"\n'
    '  output="${name%.*}"\n'
    '  case "$harness" in\n'
    "    *.c)\n"
    '      "$CC" $CFLAGS "-I$INSTALL_DIR/include" "$harness" \\\n'
    '        "${STATIC_LIBS[@]}" $EXTRA_LINK_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n'
    "      ;;\n"
    "    *.cc|*.cpp|*.cxx)\n"
    '      "$CXX" $CXXFLAGS "-I$INSTALL_DIR/include" "$harness" \\\n'
    '        "${STATIC_LIBS[@]}" $EXTRA_LINK_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n'
    "      ;;\n"
    "  esac\n"
    "done\n"
)


def generate_oss_fuzz(
    analysis: AnalysisResult,
    output_path: Path,
    exploration: BuildExplorationResult | None = None,
    harness_exploration: HarnessExplorationResult | None = None,
) -> GenerationResult:
    """Generate a complete oss-fuzz project skeleton from a static analysis result."""
    output_path.mkdir(parents=True)
    (output_path / "harness_source").mkdir()

    files: list[Path] = [
        _write_project_yaml(output_path, analysis),
        _write_dockerfile(output_path, analysis),
        _write_build_sh(output_path),
        _write_build_library_sh(output_path, analysis, exploration),
        _write_compile_harnesses_sh(output_path, harness_exploration),
        write_default_fuzzer(output_path / "harness_source", analysis),
    ]

    return GenerationResult(
        project_name=analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _write_project_yaml(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "project.yaml"
    language = "c" if analysis.language == Language.C else "c++"
    path.write_text(
        f"homepage: {analysis.clone_url}\n"
        f"language: {language}\n"
        "sanitizers:\n"
        "  - address\n"
        "  - undefined\n"
        f"main_repo: {analysis.clone_url}\n"
    )
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
        lines.append(f"RUN apt-get update && apt-get install -y --no-install-recommends {pkgs}\n")

    lines.append(f"RUN git clone {analysis.clone_url} $SRC/src\n")
    if analysis.repo_ref is not None:
        lines.append(f"RUN git -C $SRC/src checkout {analysis.repo_ref}\n")
    lines += [
        "COPY harness_source $SRC/harness_source\n",
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n",
        "WORKDIR $SRC/src\n",
    ]
    path.write_text("".join(lines))
    return path


def _write_build_sh(output_path: Path) -> Path:
    path = output_path / "build.sh"
    path.write_text(_BUILD_SH)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_build_library_sh(
    output_path: Path, analysis: AnalysisResult, exploration: BuildExplorationResult | None
) -> Path:
    """Write build_library.sh, reusing the explored (possibly agent-fixed) script when available.

    The Dockerfile clones the repo to $SRC/src, matching the $SCRIPT_DIR/src the explored
    script uses, so copying it verbatim preserves any fixes an agent made during exploration.
    Falls back to the static template when no exploration was run or its script isn't safe
    to copy (e.g. a non-standard source layout).
    """
    path = output_path / "build_library.sh"
    if exploration is not None and exploration.script_path is not None:
        shutil.copy2(exploration.script_path, path)
    else:
        path.write_text(
            build_library_script(
                analysis.build_system,
                BuildPaths(
                    source_dir="$SCRIPT_DIR/src",
                    build_dir="$SCRIPT_DIR/build",
                    install_dir="$SCRIPT_DIR/install",
                ),
                autotools_setup=analysis.autotools_setup,
            )
        )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_compile_harnesses_sh(
    output_path: Path, harness: HarnessExplorationResult | None
) -> Path:
    path = output_path / "compile_harnesses.sh"
    content = (
        build_harness_script(harness, harness_dir_name="harness_source", oss_fuzz=True)
        if harness is not None
        else _COMPILE_HARNESSES_SH_STUB
    )
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
