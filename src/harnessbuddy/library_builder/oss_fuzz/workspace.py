from __future__ import annotations

import stat
from pathlib import Path

from harnessbuddy.library_builder.models import AnalysisResult, AutotoolsSetup, Language

_AUTOTOOLS_PACKAGES = ("autoconf", "automake", "libtool", "pkg-config")

_BUILD_SH = (
    "#!/bin/bash\nset -euo pipefail\n\n"
    'echo "=== build_library.sh ==="\n'
    '"$SRC/build_library.sh"\n'
    'echo "=== compile_harnesses.sh ==="\n'
    '"$SRC/compile_harnesses.sh"\n'
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


def write_build_sh(output_path: Path) -> Path:
    """Write build.sh — the OSS-Fuzz `compile` entrypoint runs this to build the library
    then compile harnesses, in that order, with markers identifying each stage's output
    (FR-008, User Story 3)."""
    path = output_path / "build.sh"
    path.write_text(_BUILD_SH)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


__all__ = ["write_build_sh", "write_dockerfile", "write_project_yaml"]
