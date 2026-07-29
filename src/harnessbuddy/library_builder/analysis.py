from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    AutotoolsSetup,
    BuildSystem,
    Language,
)

logger = logging.getLogger(__name__)
_C_HEADER_EXTENSIONS: frozenset[str] = frozenset({".h", ".hpp", ".hxx", ".hh"})
_VCS_DIRS: frozenset[str] = frozenset({".git", ".hg", ".svn"})

_BUILD_SYSTEM_CHECKS: list[tuple[BuildSystem, list[str]]] = [
    (BuildSystem.CMAKE, ["CMakeLists.txt"]),
    (BuildSystem.AUTOTOOLS, ["configure.ac", "configure.in", "configure"]),
    (BuildSystem.MAKEFILE, ["Makefile", "makefile"]),
    (BuildSystem.MESON, ["meson.build"]),
]


class UnsupportedRepositoryError(Exception):
    """Repository has no recognizable C/C++ signals."""


def analyze(source: RepoSource) -> AnalysisResult:
    """Run deterministic static analysis on a repository directory."""
    build_system, build_files = _detect_build_system(source.source_path)
    headers = _detect_headers(source.source_path)
    language = _detect_language(source.source_path)
    warnings: list[str] = []

    if not build_files and not headers:
        raise UnsupportedRepositoryError(
            f"No C/C++ signals found in {source.source_path}: "
            "no recognized build system files and no C/C++ headers."
        )

    if build_system == BuildSystem.UNKNOWN:
        warnings.append(f"No recognized build system found in {source.source_path}.")

    if not headers:
        warnings.append(f"No C/C++ header files found in {source.source_path}.")

    autotools_setup = (
        _detect_autotools_setup(source.source_path)
        if build_system == BuildSystem.AUTOTOOLS
        else None
    )

    return AnalysisResult(
        project_name=source.project_name,
        source_path=source.source_path,
        build_system=build_system,
        build_files=build_files,
        headers=headers,
        language=language,
        clone_url=source.clone_url,
        repo_ref=source.repo_ref,
        warnings=warnings,
        autotools_setup=autotools_setup,
    )


def _detect_autotools_setup(root: Path) -> AutotoolsSetup:
    """Detect how to bootstrap autotools for this repository.

    Priority: configure script present > autogen.sh > bootstrap > autoreconf from
    configure.ac. Only reached once configure.ac/configure.in/configure has already
    established that this is an autotools tree, so a bare `bootstrap` here is the
    gnulib-style autotools wrapper rather than some other project's setup script.
    """
    if (root / "configure").exists():
        return AutotoolsSetup.CONFIGURE
    if (root / "autogen.sh").exists():
        return AutotoolsSetup.AUTOGEN
    if (root / "bootstrap").exists():
        return AutotoolsSetup.BOOTSTRAP
    return AutotoolsSetup.AUTORECONF


def _detect_build_system(root: Path) -> tuple[BuildSystem, list[Path]]:
    """Detect the primary build system in priority order."""
    for build_system, filenames in _BUILD_SYSTEM_CHECKS:
        found = [root / name for name in filenames if (root / name).exists()]
        if found:
            return build_system, found
    return BuildSystem.UNKNOWN, []


def _detect_headers(root: Path) -> list[Path]:
    """Return sorted C/C++ header files under root, excluding VCS directories."""
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in _C_HEADER_EXTENSIONS
        and not any(part in _VCS_DIRS for part in p.relative_to(root).parts)
    )


def _detect_language(root: Path) -> Language:
    """Determine the dominant C/C++ language by running cloc, falling back to headers."""
    result = subprocess.run(
        ["cloc", "--json", str(root) , "--timeout",  "120"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        c_lines = data.get("C", {}).get("code", 0)
        cpp_lines = data.get("C++", {}).get("code", 0)
        logger.debug("C LoC: %d, CPP LoC %d", c_lines, cpp_lines)
        if c_lines > 0 or cpp_lines > 0:
            return Language.CPP if cpp_lines > c_lines else Language.C

    return Language.UNKNOWN
