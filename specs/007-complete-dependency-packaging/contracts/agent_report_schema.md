# Contract: `agent_report.json` (harness-builder agent)

This is the interface boundary between the `claude`/`codex` subprocess spawned by
`invoke_harness_builder_agent` (`agents.py`) and the rest of HarnessBuddy. The agent writes this
file to its working directory before exiting (on **every** outcome); `read_agent_report`
(`exploration.py`) parses it into an `AgentReport` and deletes it.

This contract is unchanged by this feature at the **schema** level — the fields already exist.
What changes is **when the agent is instructed to populate them**, per `agents/harness_builder/SKILL.md`.

## Schema (unchanged)

```json
{
  "summary": "string, always present",
  "missing_libs": ["bare library name, e.g. \"ldap\" — no \"-l\" prefix"],
  "missing_apt_packages": ["Debian/Ubuntu apt-get package name, e.g. \"libldap2-dev\""],
  "missing_brew_packages": ["Homebrew formula name, e.g. \"openldap\""],
  "extra_include_paths": ["relative path outside install/include"],
  "extra_library_paths": ["relative path outside install/lib"]
}
```

All fields are optional (absent ⇒ empty list / `None`), parsed tolerantly by
`exploration._string_list` — a wrong-typed value (e.g. a string instead of a list) becomes an
empty list rather than an error.

## Behavioral contract change (this feature)

**Before this feature**: `missing_libs`/`missing_apt_packages`/`missing_brew_packages` were
only meaningfully populated when the agent could not resolve a dependency at all (the
"unresolvable failure" path, `SKILL.md` step 5) and exited non-zero.

**After this feature**: the agent MUST populate the same three fields any time it adds a *new*
`-lXXX` flag to `EXTRA_LINK_FLAGS` in `build_harness.sh` — including when it resolves the flag
successfully using a library already present on its own machine (`SKILL.md` step 4). The
distinction between "agent succeeded" and "agent gave up" no longer determines whether these
fields carry data; it only determines `HarnessExplorationResult.succeeded` and whether the user
is shown an "install this before continuing" message (`FR-005`).

## Consumers of this contract

- `invoke_harness_builder_agent` (`agents.py`) — reads `report.missing_libs` to synthesize
  `-l<lib>` flags (already unconditional, no change) and reads
  `report.missing_apt_packages`/`report.missing_brew_packages` onto the returned
  `HarnessExplorationResult` (already unconditional, no change — see `research.md`).
- `_run_harness_phase` (`cli.py`) — merges `harness_result.missing_apt_packages`/
  `missing_brew_packages` into `state.json` via `merge_packages_into_state`, tagged
  `"harness_agent"`, regardless of `harness_result.succeeded`.
- `agents/harness_builder/SKILL.md` — the producer side of this contract; this feature's only
  required change to close Story 1 is here (see `research.md`).

## Compatibility

No breaking change: an agent that only populates these fields on the failure path (old
behavior) continues to work exactly as today, just without the portability improvement Story 1
adds. This is an additive instruction to the agent, not a schema migration.
