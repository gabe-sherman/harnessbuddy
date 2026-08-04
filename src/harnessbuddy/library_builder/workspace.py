"""The project workspace: one directory layout, materialized once, verified, then shipped.

`.harnessbuddy/<project>/` is the project, not a scratch area translated into output later.
It holds a real OSS-Fuzz project (`project.yaml`, `Dockerfile`, `build.sh`) plus the build and
harness-compile scripts, and generation copies it verbatim, so what ships is what was
verified, including any repair an agent applied.

Both environments materialize the same layout. Only the oss-fuzz target builds an image from
the Dockerfile here.
"""

from __future__ import annotations

from pathlib import Path

from harnessbuddy.core.files import write_executable
from harnessbuddy.library_builder.build_parameters import BuildParameters
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    AutotoolsSetup,
    Language,
    LinkConfiguration,
)
from harnessbuddy.library_builder.scripts import (
    HARNESS_SOURCE_DIR,
    build_harness_script,
    build_harnesses_script,
    write_default_fuzzer,
)

_AUTOTOOLS_PACKAGES = ("autoconf", "automake", "libtool", "pkg-config")

# ubuntu-24-04, not the Focal-based default tag: Focal's bear (2.4.3) mishandles the
# `bear -- <command>` form exploration.py builds, passing `--` on as if it were the command.
# Ubuntu 24.04 ships bear 3.x, which handles it.
DEFAULT_BASE_IMAGE = "gcr.io/oss-fuzz-base/base-builder:ubuntu-24-04"

# The one place this apt-get invocation text is spelled out. Shared with generation's
# bear-stripping and with inject_apt_packages below.
APT_INSTALL_PREFIX = "RUN apt-get update && apt-get install -y --no-install-recommends"

# build.sh is what OSS-Fuzz's `compile` runs and what check_build.sh runs before asserting,
# so the two cannot disagree about what building this project means. $SCRIPT_DIR rather than
# $SRC: they are the same path in the container, and $SRC does not exist on the host.
_BUILD_SH = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    "\n"
    'echo "=== build_library.sh ==="\n'
    '"$SCRIPT_DIR/build_library.sh"\n'
    'echo "=== compile_harnesses.sh ==="\n'
    '"$SCRIPT_DIR/compile_harnesses.sh"\n'
)


def materialize(
    workdir: Path,
    analysis: AnalysisResult,
    *,
    parameters: BuildParameters,
    base_image: str | None = None,
) -> None:
    """Write the full project layout into workdir, except build_library.sh.

    Every file here is fixed or derives only from static analysis, so this runs before any
    build attempt. That is what leaves a repair agent something runnable even when no build
    system was identified: the gate it is told to run needs compile_harnesses.sh and a harness
    source to exist regardless.

    build_library.sh is written by exploration.write_build_library_script, which owns the
    source-layout decision and rewrites it on each attempt.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    write_project_yaml(workdir, analysis)
    write_dockerfile(workdir, analysis, include_bear=True, base_image=base_image)
    write_build_sh(workdir)
    write_harness_scripts(workdir, analysis.language, parameters=parameters)


def write_harness_scripts(
    workdir: Path, language: Language, *, parameters: BuildParameters
) -> Path:
    """Write compile_harness.sh, compile_harnesses.sh, and a default harness source.

    The stub gives the gate's non-empty-$OUT assertion something to find before harness-link
    discovery has run. Discovery later rewrites compile_harness.sh with each flag it resolves.
    """
    harness_source_dir = workdir / HARNESS_SOURCE_DIR
    harness_source_dir.mkdir(parents=True, exist_ok=True)
    write_default_fuzzer(harness_source_dir, language)
    write_executable(
        workdir / "compile_harness.sh",
        build_harness_script(
            LinkConfiguration(),
            harness_cflags=parameters.harness_cflags,
            harness_cxxflags=parameters.harness_cxxflags,
        ),
    )
    return write_executable(workdir / "compile_harnesses.sh", build_harnesses_script())


def write_project_yaml(output_path: Path, analysis: AnalysisResult) -> Path:
    """Write project.yaml — depends only on analysis, so it never needs rewriting."""
    path = output_path / "project.yaml"
    path.write_text(
        f"homepage: {analysis.clone_url}\n"
        f"language: {analysis.language.value}\n"
        "sanitizers:\n"
        "  - address\n"
        "  - undefined\n"
        f"main_repo: {analysis.clone_url}\n"
    )
    return path


def write_dockerfile(
    output_path: Path,
    analysis: AnalysisResult,
    *,
    include_bear: bool,
    base_image: str | None = None,
    system_packages: list[str] | None = None,
) -> Path:
    """Write the OSS-Fuzz project Dockerfile.

    include_bear=True produces the workspace's live copy, so the image the exploration probe
    runs in always has bear for Make/Autotools compile_commands.json capture.
    include_bear=False produces the shipped copy, which must not need a HarnessBuddy-only tool.
    """
    path = output_path / "Dockerfile"
    lines = [
        f"FROM {base_image or DEFAULT_BASE_IMAGE}\n",
        f"ENV FUZZING_LANGUAGE={analysis.language.value}\n",
    ]

    apt_packages: list[str] = []
    if include_bear:
        apt_packages.append("bear")
    if analysis.autotools_setup in {
        AutotoolsSetup.AUTOGEN,
        AutotoolsSetup.BOOTSTRAP,
        AutotoolsSetup.AUTORECONF,
    }:
        apt_packages.extend(_AUTOTOOLS_PACKAGES)
    apt_packages.extend(system_packages or [])
    if apt_packages:
        lines.append(f"{APT_INSTALL_PREFIX} {' '.join(dict.fromkeys(apt_packages))}\n")

    lines.append(f"RUN git clone --recursive {analysis.clone_url} $SRC/src\n")
    if analysis.repo_ref is not None:
        lines.append(f"RUN git -C $SRC/src checkout {analysis.repo_ref}\n")
    lines += [
        f"COPY {HARNESS_SOURCE_DIR} $SRC/{HARNESS_SOURCE_DIR}\n",
        "COPY build.sh build_library.sh compile_harness.sh compile_harnesses.sh $SRC/\n",
        "WORKDIR $SRC/src\n",
    ]
    path.write_text("".join(lines))
    return path


def inject_apt_packages(output_path: Path, packages: list[str]) -> Path:
    """Merge newly-discovered apt packages into an existing Dockerfile, in place.

    Merges rather than re-renders, so agent edits survive: this file is written before the
    harness phase can know what else is needed, and generation only ever copies it.
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
    """Write build.sh — the library build then harness compilation, with a marker per stage.

    OSS-Fuzz's `compile` runs it, check_build.sh runs it and then asserts, and a host user can
    run it directly.
    """
    return write_executable(output_path / "build.sh", _BUILD_SH)


def strip_bear_dependency(dockerfile_content: str) -> str:
    """Drop the "bear" apt package, which only the workspace image needs.

    Splits the package list into tokens rather than replacing a fixed string, since "bear" is
    not always followed by a space — it may be last in the list.
    """
    lines = []
    for line in dockerfile_content.splitlines(keepends=True):
        if not line.startswith(APT_INSTALL_PREFIX):
            lines.append(line)
            continue
        packages = [pkg for pkg in line[len(APT_INSTALL_PREFIX) :].split() if pkg != "bear"]
        if packages:
            lines.append(f"{APT_INSTALL_PREFIX} {' '.join(packages)}\n")
        # else: bear was the only package — drop the now-empty install line entirely.
    return "".join(lines)


__all__ = [
    "APT_INSTALL_PREFIX",
    "DEFAULT_BASE_IMAGE",
    "inject_apt_packages",
    "materialize",
    "strip_bear_dependency",
    "write_build_sh",
    "write_dockerfile",
    "write_harness_scripts",
    "write_project_yaml",
]
