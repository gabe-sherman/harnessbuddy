from __future__ import annotations

import json
from pathlib import Path

from harnessbuddy.library_builder.dependency_resolution import (
    DependencySource,
    DependencyState,
    LibraryDependency,
    from_agent_report,
    from_static_probe,
    load_state,
    merge,
    save_state,
)

# load_state / save_state


def test_load_state_absent_returns_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "state.json")
    assert state == DependencyState()


def test_load_state_ignores_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("not json{{{")
    state = load_state(tmp_path / "state.json")
    assert state == DependencyState()


def test_save_and_load_state_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state = DependencyState()
    merge(
        state,
        [LibraryDependency(source=DependencySource.LINKER, apt_package="libzstd-dev")],
    )
    save_state(state_file, state)
    loaded = load_state(state_file)
    assert loaded.apt_packages == ["libzstd-dev"]
    assert loaded.sources == {"linker": ["libzstd-dev"]}


def test_load_state_reads_pre_refactor_fixture(tmp_path: Path) -> None:
    """A state.json written before this refactor (free-text sources keys) round-trips
    unchanged — FR-005 / quickstart Scenario 2."""
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "apt_packages": ["libssl-dev"],
                "brew_packages": ["openssl"],
                "unknown_libs": [],
                "sources": {"harness_agent": ["libssl-dev"]},
            }
        )
    )
    state = load_state(state_file)
    assert state.apt_packages == ["libssl-dev"]
    assert state.brew_packages == ["openssl"]
    assert state.unknown_libs == []
    assert state.sources == {"harness_agent": ["libssl-dev"]}


# merge


def test_merge_combines_complementary_partial_info() -> None:
    """Two sources report the same library name with complementary partial info."""
    state = DependencyState()
    merge(
        state,
        [
            LibraryDependency(source=DependencySource.LINKER, name="zstd", link_flag="-lzstd"),
            LibraryDependency(
                source=DependencySource.LIBRARY_AGENT,
                name="zstd",
                apt_package="libzstd-dev",
                brew_package="zstd",
            ),
        ],
    )
    assert state.apt_packages == ["libzstd-dev"]
    assert state.brew_packages == ["zstd"]
    # The link_flag-only entry has no package, so it lands in unknown_libs too --
    # merge() doesn't correlate entries across the list, only within one entry's fields.
    assert state.unknown_libs == ["zstd"]


def test_merge_name_only_dependency_lands_in_unknown_libs() -> None:
    state = DependencyState()
    merge(state, [LibraryDependency(source=DependencySource.LINKER, name="nonexistentlib")])
    assert state.unknown_libs == ["nonexistentlib"]
    assert state.apt_packages == []
    assert state.brew_packages == []


def test_merge_twice_is_idempotent() -> None:
    dependencies = [
        LibraryDependency(source=DependencySource.LINKER, name="zstd", apt_package="libzstd-dev")
    ]
    state_once = DependencyState()
    merge(state_once, dependencies)

    state_twice = DependencyState()
    merge(state_twice, dependencies)
    merge(state_twice, dependencies)

    assert state_once == state_twice


def test_merge_unions_across_calls() -> None:
    state = DependencyState()
    merge(
        state, [LibraryDependency(source=DependencySource.LIBRARY_AGENT, apt_package="libssl-dev")]
    )
    merge(
        state,
        [
            LibraryDependency(
                source=DependencySource.LINKER, apt_package="libzstd-dev", brew_package="zstd"
            )
        ],
    )
    assert state.apt_packages == ["libssl-dev", "libzstd-dev"]
    assert state.brew_packages == ["zstd"]


def test_merge_deduplicates_across_calls() -> None:
    state = DependencyState()
    merge(
        state, [LibraryDependency(source=DependencySource.LIBRARY_AGENT, apt_package="libssl-dev")]
    )
    merge(state, [LibraryDependency(source=DependencySource.LINKER, apt_package="libssl-dev")])
    assert state.apt_packages == ["libssl-dev"]


# extensibility: quickstart Scenario 3 (US1) -- a hand-built dependency merges correctly
# with no cli.py involvement at all.


def test_merge_supports_a_hypothetical_new_discovery_source() -> None:
    state = DependencyState(apt_packages=["libssl-dev"], sources={"linker": ["libssl-dev"]})
    hypothetical_source = DependencySource("linker")  # stand-in: no new source is added yet
    merge(
        state,
        [
            LibraryDependency(
                source=hypothetical_source, name="curl", apt_package="libcurl4-openssl-dev"
            )
        ],
    )
    assert state.apt_packages == ["libssl-dev", "libcurl4-openssl-dev"]


# source traceability (US2)


def test_sources_keys_match_dependency_source_value_for_each_producer() -> None:
    linker_deps = from_static_probe([], ["-lzstd"])
    state = DependencyState()
    merge(state, linker_deps)
    assert set(state.sources) == {"linker"}

    state = DependencyState()
    merge(
        state,
        from_agent_report([], ["libfoo-dev"], [], source=DependencySource.LIBRARY_AGENT),
    )
    assert set(state.sources) == {"library_agent"}

    state = DependencyState()
    merge(
        state,
        from_agent_report([], ["libfoo-dev"], [], source=DependencySource.HARNESS_AGENT),
    )
    assert set(state.sources) == {"harness_agent"}


# from_static_probe


def test_from_static_probe_resolves_known_library() -> None:
    dependencies = from_static_probe(missing_system_libs=[], transitive_link_flags=["-lzstd"])
    assert dependencies == [
        LibraryDependency(
            source=DependencySource.LINKER,
            name="zstd",
            link_flag="-lzstd",
            apt_package="libzstd-dev",
            brew_package="zstd",
        )
    ]


def test_from_static_probe_places_unmapped_library_for_unknown_handling() -> None:
    dependencies = from_static_probe(
        missing_system_libs=["nonexistentlib"], transitive_link_flags=[]
    )
    assert dependencies == [
        LibraryDependency(source=DependencySource.LINKER, name="nonexistentlib")
    ]


def test_from_static_probe_drops_system_libraries() -> None:
    dependencies = from_static_probe(missing_system_libs=["pthread"], transitive_link_flags=[])
    assert dependencies == []


def test_from_static_probe_unions_missing_libs_and_link_flags() -> None:
    dependencies = from_static_probe(missing_system_libs=["zstd"], transitive_link_flags=["-lz"])
    names = {dep.name for dep in dependencies}
    assert names == {"zstd", "z"}


# from_agent_report


def test_from_agent_report_single_dependency_zips_correctly() -> None:
    dependencies = from_agent_report(
        missing_libs=["ldap"],
        missing_apt_packages=["libldap2-dev"],
        missing_brew_packages=["openldap"],
        source=DependencySource.HARNESS_AGENT,
    )
    assert dependencies == [
        LibraryDependency(
            source=DependencySource.HARNESS_AGENT,
            name="ldap",
            apt_package="libldap2-dev",
            brew_package="openldap",
        )
    ]


def test_from_agent_report_empty_lists_return_empty() -> None:
    assert from_agent_report([], [], [], source=DependencySource.LIBRARY_AGENT) == []


def test_from_agent_report_multi_dependency_correlation_is_positional_only() -> None:
    """Documents research.md's correlation-gap decision: with more than one entry in
    missing_libs, index i's apt/brew package is only *assumed* to describe missing_libs[i] --
    there is no guarantee of correspondence. This is an unchanged, pre-existing limitation,
    not a new claim made by this refactor."""
    dependencies = from_agent_report(
        missing_libs=["ldap", "curl"],
        missing_apt_packages=["libldap2-dev"],
        missing_brew_packages=[],
        source=DependencySource.HARNESS_AGENT,
    )
    assert dependencies[0].name == "ldap"
    assert dependencies[0].apt_package == "libldap2-dev"
    assert dependencies[1].name == "curl"
    assert dependencies[1].apt_package is None
