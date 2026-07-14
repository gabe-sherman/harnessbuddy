from __future__ import annotations

import stat
from pathlib import Path

from harnessbuddy.library_builder.models import AnalysisResult, AutotoolsSetup, Language

_AUTOTOOLS_PACKAGES = ("autoconf", "automake", "libtool", "pkg-config")

# Shared with oss_fuzz/generation.py (bear-stripping) and inject_apt_packages below —
# the one place this exact apt-get invocation text is spelled out.
APT_INSTALL_PREFIX = "RUN apt-get update && apt-get install -y --no-install-recommends"

_BUILD_SH = (
    "#!/bin/bash\nset -euo pipefail\n\n"
    'echo "=== build_library.sh ==="\n'
    '"$SRC/build_library.sh"\n'
    'echo "=== compile_harnesses.sh ==="\n'
    '"$SRC/compile_harnesses.sh"\n'
)

_COMPILE_HARNESSES_SH_STUB = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    'BUILD_PREFIX="${BUILD_PREFIX:-$SCRIPT_DIR}"\n'
    "\n"
    'INSTALL_DIR="$BUILD_PREFIX/install"\n'
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
    '        "${STATIC_LIBS[@]-}" $EXTRA_LINK_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n'
    "      ;;\n"
    "    *.cc|*.cpp|*.cxx)\n"
    '      "$CXX" $CXXFLAGS "-I$INSTALL_DIR/include" "$harness" \\\n'
    '        "${STATIC_LIBS[@]-}" $EXTRA_LINK_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"\n'
    "      ;;\n"
    "  esac\n"
    "done\n"
)


def write_project_yaml(output_path: Path, analysis: AnalysisResult) -> Path:
    """Write project.yaml — identical content whether written early (workspace
    materialization) or at final generation, since it depends only on analysis."""
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


def write_dockerfile(output_path: Path, analysis: AnalysisResult, *, include_bear: bool) -> Path:
    """Write the OSS-Fuzz project Dockerfile.

    include_bear=True produces the workspace's "live" copy, used to build the run-scoped
    image during exploration so Make/Autotools compile_commands.json capture always has
    bear available (FR-011, research.md #5); include_bear=False produces the copy shipped
    in final oss-fuzz/ output, which must not depend on a HarnessBuddy-only tool.
    """
    path = output_path / "Dockerfile"
    lines = [
        # ubuntu-24-04, not the bare/Focal-based default tag: Focal's only apt-available
        # bear (2.4.3) mishandles the `bear -- <command>` invocation exploration.py's
        # _build_command() constructs (a known argparse REMAINDER quirk in Bear 2.x —
        # `--` ends up passed to subprocess as if it were the command itself, raising
        # "FileNotFoundError: [Errno 2] No such file or directory: '--'"). Ubuntu 24.04's
        # bear (3.x, the modern rewrite) handles `--` correctly.
        "FROM gcr.io/oss-fuzz-base/base-builder:ubuntu-24-04\n",
        f"ENV FUZZING_LANGUAGE={analysis.language.value}\n",
    ]

    apt_packages: list[str] = []
    if include_bear:
        apt_packages.append("bear")
    if analysis.autotools_setup in {AutotoolsSetup.AUTOGEN, AutotoolsSetup.AUTORECONF}:
        apt_packages.extend(_AUTOTOOLS_PACKAGES)
    apt_packages.extend(analysis.system_packages)
    if apt_packages:
        lines.append(f"{APT_INSTALL_PREFIX} {' '.join(apt_packages)}\n")

    lines.append(f"RUN git clone --recursive {analysis.clone_url} $SRC/src\n")
    if analysis.repo_ref is not None:
        lines.append(f"RUN git -C $SRC/src checkout {analysis.repo_ref}\n")
    lines += [
        "COPY harness_source $SRC/harness_source\n",
        "COPY build.sh build_library.sh compile_harnesses.sh $SRC/\n",
        "WORKDIR $SRC/src\n",
    ]
    path.write_text("".join(lines))
    return path


def inject_apt_packages(output_path: Path, packages: list[str]) -> Path:
    """Merge newly-discovered apt packages into the workspace's existing Dockerfile,
    in place, preserving everything already there (including any agent edits) rather
    than re-rendering from write_dockerfile. Needed because the workspace's Dockerfile
    is written once, early, by _materialize_workspace — before the harness phase's
    linker-dependency discovery (or its own repair agent) can know what else is
    required (research.md #5), and generate_oss_fuzz only ever copies this file
    verbatim into final output, never regenerates it.
    """
    path = output_path / "Dockerfile"
    if not packages:
        return path
    lines = path.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(APT_INSTALL_PREFIX):
            existing = line[len(APT_INSTALL_PREFIX) :].split()
            merged = list(dict.fromkeys(existing + packages))
            lines[i] = f"{APT_INSTALL_PREFIX} {' '.join(merged)}\n"
            break
    else:
        insert_at = next(
            (i + 1 for i, line in enumerate(lines) if line.startswith("ENV FUZZING_LANGUAGE=")),
            len(lines),
        )
        lines.insert(insert_at, f"{APT_INSTALL_PREFIX} {' '.join(dict.fromkeys(packages))}\n")
    path.write_text("".join(lines))
    return path


def write_build_sh(output_path: Path) -> Path:
    """Write build.sh — the OSS-Fuzz `compile` entrypoint runs this to build the library
    then compile harnesses, in that order, with markers identifying each stage's output
    (FR-008, User Story 3)."""
    path = output_path / "build.sh"
    path.write_text(_BUILD_SH)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def write_compile_harnesses_stub(output_path: Path) -> Path:
    """Seed compile_harnesses.sh with a stub that compiles whatever's in
    harness_source/ (research.md #3) — written early during workspace materialization
    so check_docker_build.sh's /out non-empty check has something to find even before
    harness-link discovery ever runs. generate_oss_fuzz later copies whatever ends up
    here (this stub, or an agent's fix) verbatim, never re-deriving it."""
    path = output_path / "compile_harnesses.sh"
    path.write_text(_COMPILE_HARNESSES_SH_STUB)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


__all__ = [
    "inject_apt_packages",
    "write_build_sh",
    "write_compile_harnesses_stub",
    "write_dockerfile",
    "write_project_yaml",
]
