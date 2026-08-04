"""Single dependency-resolution/merge point for library_builder.

Consolidates HarnessBuddy's dependency-resolution logic — previously scattered across
`cli.py`'s five near-identical merge blocks and inlined package-translation code in
`_run_harness_phase` — into one shared type (`LibraryDependency`), a closed
`DependencySource` enum, and one `merge()` function. See
specs/008-consolidate-dependency-resolution/ for the full design and rationale.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class DependencySource(StrEnum):
    LINKER = "linker"
    LIBRARY_AGENT = "library_agent"
    HARNESS_AGENT = "harness_agent"


@dataclass(frozen=True)
class LibraryDependency:
    """One dependency, possibly only partially resolved. Only `source` is required."""

    source: DependencySource
    name: str | None = None
    apt_package: str | None = None


@dataclass
class DependencyState:
    """Persisted dependency-resolution state — the on-disk shape of state.json."""

    version: int = 1
    apt_packages: list[str] = field(default_factory=list)
    sources: dict[str, list[str]] = field(default_factory=dict)


def load_state(path: Path) -> DependencyState:
    """Load state.json for a project; return an empty DependencyState if absent/malformed."""
    if not path.exists():
        return DependencyState()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return DependencyState()

    state = DependencyState()
    if isinstance(data.get("apt_packages"), list):
        state.apt_packages = [str(p) for p in data["apt_packages"]]
    if isinstance(data.get("sources"), dict):
        state.sources = {
            k: [str(p) for p in v] for k, v in data["sources"].items() if isinstance(v, list)
        }
    return state


def save_state(path: Path, state: DependencyState) -> None:
    """Write state.json for a project."""
    path.write_text(
        json.dumps(
            {
                "version": state.version,
                "apt_packages": state.apt_packages,
                "sources": state.sources,
            },
            indent=2,
        )
    )


def merge(state: DependencyState, dependencies: list[LibraryDependency]) -> None:
    """Union dependencies into state in-place, deduplicating while preserving order.

    The only function permitted to mutate DependencyState.apt_packages/sources.
    Idempotent: merging the same dependencies list twice produces the same state as
    merging it once.
    """
    new_apt: list[str] = []
    new_apt_by_source: dict[str, list[str]] = {}

    for dep in dependencies:
        if dep.apt_package is not None:
            new_apt.append(dep.apt_package)
            new_apt_by_source.setdefault(dep.source.value, []).append(dep.apt_package)

    state.apt_packages = list(dict.fromkeys(state.apt_packages + new_apt))
    for source_tag, apt_names in new_apt_by_source.items():
        existing = state.sources.get(source_tag, [])
        state.sources[source_tag] = list(dict.fromkeys(existing + apt_names))


def from_static_probe(
    missing_system_libs: list[str], transitive_link_flags: list[str]
) -> list[LibraryDependency]:
    """Translate the deterministic harness-link probe's outputs into LibraryDependency entries.

    Unions missing_system_libs (linker-reported missing) with the bare names embedded in
    transitive_link_flags (linker-resolved silently because the exploration host already had
    them — spec 005), then translates each name independently through
    package_names.translate() so system libraries (dropped silently by translate(), never
    added to unknown_libs) are excluded entirely rather than misclassified as unknown.
    """
    from harnessbuddy.library_builder.harness_explorer import lib_names_from_link_flags
    from harnessbuddy.library_builder.package_names import translate

    names_from_flags = lib_names_from_link_flags(transitive_link_flags)
    names = list(dict.fromkeys(missing_system_libs + names_from_flags))

    dependencies: list[LibraryDependency] = []
    for name in names:
        translation = translate([name])
        if not (translation.apt_packages or translation.unknown_libs):
            continue  # system library — translate() drops it silently, as today
        dependencies.append(
            LibraryDependency(
                source=DependencySource.LINKER,
                name=name,
                apt_package=translation.apt_packages[0] if translation.apt_packages else None,
            )
        )
    return dependencies


def from_agent_report(
    missing_libs: list[str],
    missing_apt_packages: list[str],
    *,
    source: DependencySource,
) -> list[LibraryDependency]:
    """Wrap an agent's self-reported dependency lists into LibraryDependency entries.

    Zips the two lists positionally — index i's name and apt package are assumed to
    describe the same dependency. This is a real, pre-existing, documented limitation when
    an agent reports more than one distinct dependency in a single run (nothing guarantees
    missing_apt_packages[0] corresponds to missing_libs[0] rather than missing_libs[1]); it is
    not a new guarantee introduced by this refactor (research.md's correlation-gap decision).
    """
    return [
        LibraryDependency(source=source, name=name, apt_package=apt)
        for name, apt in itertools.zip_longest(missing_libs, missing_apt_packages)
    ]
