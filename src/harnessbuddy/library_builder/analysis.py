from __future__ import annotations

from pathlib import Path

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.models import AnalysisResult, BuildSystem, Language

_C_HEADER_EXTENSIONS: frozenset[str] = frozenset({".h", ".hpp", ".hxx", ".hh"})
_VCS_DIRS: frozenset[str] = frozenset({".git", ".hg", ".svn"})

_BUILD_SYSTEM_CHECKS: list[tuple[BuildSystem, list[str]]] = [
    (BuildSystem.CMAKE, ["CMakeLists.txt"]),
    (BuildSystem.MESON, ["meson.build"]),
    (BuildSystem.AUTOTOOLS, ["configure.ac", "configure.in", "configure"]),
    (BuildSystem.MAKEFILE, ["Makefile", "makefile"]),
    (BuildSystem.NINJA, ["build.ninja"]),
]


class UnsupportedRepositoryError(Exception):
    """Repository has no recognizable C/C++ signals."""


def analyze(source: RepoSource) -> AnalysisResult:
    """Run deterministic static analysis on a repository directory."""
    build_system, build_files = _detect_build_system(source.source_path)
    headers = _detect_headers(source.source_path)
    language = _detect_language(headers)
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
    )


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


def _detect_language(headers: list[Path]) -> Language:
    """Infer likely language from header file extensions."""
    has_c = any(p.suffix == ".h" for p in headers)
    has_cpp = any(p.suffix in {".hpp", ".hxx", ".hh"} for p in headers)
    if has_c and has_cpp:
        return Language.C_AND_CPP
    if has_cpp:
        return Language.CPP
    if has_c:
        return Language.C
    return Language.UNKNOWN
