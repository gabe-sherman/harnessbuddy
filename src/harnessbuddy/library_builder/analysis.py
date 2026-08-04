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

# Bare Makefile is the last-resort signal: a project shipping meson.build and a convenience
# top-level Makefile is a meson project.
_BUILD_SYSTEM_CHECKS: list[tuple[BuildSystem, list[str]]] = [
    (BuildSystem.CMAKE, ["CMakeLists.txt"]),
    (BuildSystem.AUTOTOOLS, ["configure.ac", "configure.in", "configure"]),
    (BuildSystem.MESON, ["meson.build"]),
    (BuildSystem.MAKEFILE, ["Makefile", "makefile"]),
]

# The outer ceiling has to exceed the one handed to cloc, or cloc's own --timeout never fires
# and reports partial results.
_CLOC_TIMEOUT_SECONDS = 120
_CLOC_KILL_TIMEOUT_SECONDS = _CLOC_TIMEOUT_SECONDS + 30


class UnsupportedRepositoryError(Exception):
    """Repository has no recognizable C/C++ signals."""


def analyze(source: RepoSource) -> AnalysisResult:
    """Run deterministic static analysis on a repository directory."""
    build_system, build_files = _detect_build_system(source.source_path)
    has_headers = _has_c_headers(source.source_path)
    warnings: list[str] = []
    language = _detect_language(source.source_path, warnings)

    if not build_files and not has_headers:
        raise UnsupportedRepositoryError(
            f"No C/C++ signals found in {source.source_path}: "
            "no recognized build system files and no C/C++ headers."
        )

    if build_system == BuildSystem.UNKNOWN:
        warnings.append(f"No recognized build system found in {source.source_path}.")

    if not has_headers:
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
        language=language,
        clone_url=source.clone_url,
        repo_ref=source.repo_ref,
        warnings=warnings,
        autotools_setup=autotools_setup,
    )


def _detect_autotools_setup(root: Path) -> AutotoolsSetup:
    """Detect how to bootstrap autotools for this repository.

    Priority: an existing configure > autogen.sh > bootstrap > autoreconf from configure.ac.
    Only reached once the tree is known to be autotools, so a bare `bootstrap` here is the
    gnulib-style wrapper rather than some other project's setup script.
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


def _has_c_headers(root: Path) -> bool:
    """True when root contains at least one C/C++ header outside a VCS directory.

    Short-circuits on the first hit rather than walking a large repository to the end.
    """
    return any(
        p.is_file()
        and p.suffix in _C_HEADER_EXTENSIONS
        and not any(part in _VCS_DIRS for part in p.relative_to(root).parts)
        for p in root.rglob("*")
    )


def _detect_language(root: Path, warnings: list[str]) -> Language:
    """Determine the dominant C/C++ language by running cloc.

    Falls back to C++ with a warning whenever cloc cannot answer: it is missing, it outran its
    deadline, or it emitted something other than JSON. C++ is the safe guess, since a C library
    compiled by a C++ driver links while the reverse does not.
    """
    try:
        result = subprocess.run(
            ["cloc", "--json", str(root), "--timeout", str(_CLOC_TIMEOUT_SECONDS)],
            capture_output=True,
            text=True,
            timeout=_CLOC_KILL_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            c_lines = data.get("C", {}).get("code", 0)
            cpp_lines = data.get("C++", {}).get("code", 0)
            logger.debug("C LoC: %d, CPP LoC %d", c_lines, cpp_lines)
            if c_lines > 0 or cpp_lines > 0:
                return Language.CPP if cpp_lines > c_lines else Language.C
        reason = f"cloc exited {result.returncode} without usable C/C++ line counts"
    except FileNotFoundError:
        reason = "cloc is not installed"
    except subprocess.TimeoutExpired:
        reason = f"cloc did not finish within {_CLOC_KILL_TIMEOUT_SECONDS}s"
    except json.JSONDecodeError:
        reason = "cloc produced output that is not valid JSON"

    warnings.append(f"Could not determine the source language ({reason}); assuming C++.")
    return Language.CPP
