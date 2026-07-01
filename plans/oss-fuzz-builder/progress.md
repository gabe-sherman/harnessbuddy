# OSS-Fuzz Builder Progress

## Status: Core pipeline complete — harness compilation exploration implemented

---

## Completed

### Phase 1 — CLI surface and ingestion
- `harnessbuddy generate <REPO_URL>` wired end-to-end
- `ingest_url` / `ingest_local` handle cloning into HarnessBuddy state dir
- Full argument surface: `--agent`, `--output`, `--project-name`, `--repo-ref`,
  `--skip-validation`, `--no-agents`, `--keep-workdir`

### Phase 2 — Static analysis
- `analyze()` detects build system (cmake / meson / autotools / makefile / unknown),
  C/C++ language, headers, and build files from the repo tree
- `AutotoolsSetup` enum distinguishes configure-present, autogen.sh, and autoreconf paths

### Phase 3 — Host-side build exploration
- `explore()` writes `build_library.sh` into the source tree with absolute paths
  for `build/` and `install/` under the workspace, then runs it
- `_validate_install_artifacts` checks that `install/lib/*.a` and `install/include/`
  are populated — build is only marked succeeded when both conditions hold
- `build_library_script()` in `scripts.py` generates per-build-system scripts
  for cmake, meson, autotools (with autogen/autoreconf variants), and makefile
- `host_fallbacks=True` mode adds `CC/CXX/CFLAGS/CXXFLAGS` defaults for host runs;
  oss-fuzz mode omits them (uses the env vars OSS-Fuzz injects at build time)

### Phase 4 — Agent fallback
- `invoke_library_builder_agent()` in `agents.py` spawns `claude --print` or
  `codex exec` when the static build fails
- Prompt is built from `agents/library_builder/SKILL.md` if present; falls back to
  an inline instruction string
- `_BUDGET_PATTERN` detects session/quota limit strings from both Claude and Codex
  and raises `LLMBudgetError` vs generic failure
- `ACTION_REQUIRED` sentinel in agent output raises `BuildFailureError` for
  user-resolvable blockers (e.g. missing system package)
- `system_deps.json` persistence: agent writes `apt_packages` to this file in the
  source dir; `load_system_deps()` reads it back on subsequent runs so packages
  are baked into generated scripts without re-invoking the agent

### Phase 5 — Dual-output generation
- **Local output** (`library_builder/local/generation.py`):
  - `<output>/<project>/output/local/`
  - Files: `setup.sh` (clone + apt install), `build_library.sh`, `build_harness.sh`,
    `harness_src/default_fuzzer.c`
  - `setup.sh` includes `apt-get install` when `analysis.system_packages` is populated
- **OSS-Fuzz output** (`library_builder/oss_fuzz/generation.py`):
  - `<output>/<project>/output/oss-fuzz/`
  - Files: `project.yaml`, `Dockerfile`, `build.sh`, `build_library.sh`,
    `compile_harnesses.sh`, `harness_source/default_fuzzer.cc`, `provenance.json`
  - `Dockerfile` includes `RUN apt-get install` for autotools packages when needed
    plus any `analysis.system_packages`
  - `provenance.json` records full build metadata (build system, files, clone URL,
    host build result) for reproducibility

### Phase 6 — Harness compilation exploration
- `explore_harness_compilation()` in `harness_explorer.py` probes compilation against
  the installed `*.a` files to discover transitive system library dependencies
- Probe uses `--whole-archive` (Linux) / `-all_load` (macOS) to force all symbols in,
  surfacing every undefined transitive dependency without requiring a real harness call
- Linker errors are parsed for undefined symbol names; `symbol_patterns.json` maps
  `-l` flags to lists of regex patterns (anchored, e.g. `^pthread_`, `^sin[fhl]?$`);
  up to 5 retry attempts accumulate discovered `-l` flags
- Three undefined-symbol extraction patterns: Linux (`undefined reference to 'sym'`),
  macOS compact (`Undefined symbol: _sym`), macOS verbose (`"_sym", referenced from:`)
- `_extract_missing_system_libs()` separately detects library-not-found errors:
  macOS (`ld: library 'X' not found`) and Linux (`ld: cannot find -lX`) — stored in
  `HarnessExplorationResult.missing_system_libs`
- Returns `HarnessExplorationResult` with `static_libs`, `include_dir`,
  `transitive_link_flags`, and `missing_system_libs` even on partial failure
- `build_harness.sh` (local) and `compile_harnesses.sh` (OSS-Fuzz) are generated
  with concrete `*.a` file paths and embedded `EXTRA_LINK_FLAGS`; no `build.env`
  sourcing required

### Phase 6b — Package resolution and state persistence
- `package_names.json` maps raw lib names (e.g. `"zstd"`) to apt/brew installable
  package names; `system_libs` list marks libs that need no package (`m`, `pthread`,
  `dl`, `rt`, `resolv`, etc.) and are silently dropped
- `package_names.py` exposes `translate(lib_names) → PackageTranslation` — a pure
  stateless module that deduplicates and drops null brew entries
- Per-project `state.json` at `.harnessbuddy/<project>/state.json` accumulates all
  discovered packages across runs; never removes entries, only unions in new ones
- Two sources merge into state via `merge_packages_into_state()`:
  - **agent** path: packages from `system_deps.json` tagged `source_tag="agent"`
  - **linker** path: packages from `missing_system_libs` translation tagged `"linker"`
- `load_project_state` / `save_project_state` helpers in `cli.py` handle JSON I/O
  with graceful fallback on missing or malformed files
- When harness exploration fails due to missing system libs, the pipeline prints an
  actionable apt/brew install hint and **falls through to generation** (best-effort)
  rather than bailing — output files are produced with the known packages included
- Hard bail (return 1) is reserved for harness failures with no missing-lib diagnosis
- `analysis.system_packages` (apt names) is populated from state before generation;
  `brew_packages` is passed as a direct kwarg to `generate_local`, keeping
  `AnalysisResult` platform-agnostic
- `setup.sh` uses `sys.platform` at generation time to emit either `brew install`
  (macOS) or `apt-get install` (Linux) — no OS-detection logic in the generated script
- `Dockerfile` already reads `analysis.system_packages`, so linker-discovered packages
  flow into it automatically once state is applied

### Phase 7 — Constitution compliance remediation
- Gap-closing pass against `.specify/memory/constitution.md` v1.1.0
  (`specs/001-library-builder-scaffold/plan.md`'s Constitution Check), tracked
  as beads issue `harnessbuddy-3rj` and children (T001-T017)
- Enabled the `C90` (mccabe, max-complexity 8) and `PLR0913` (max 5 params)
  ruff rules, previously unselected despite being constitution-mandated hard
  limits
- Decomposed `_cmd_generate` (152 lines, complexity 17) in `cli.py` into
  `_ingest_source`, `_resolve_output_paths`, `_run_library_phase`,
  `_run_harness_phase`, and `_generate_outputs` — `_cmd_generate` is now a
  54-line sequential dispatcher; all helpers are under complexity 8 and
  under 60 lines; all 27 pre-existing `test_cli.py` behavior tests passed
  unchanged, confirming behavior was preserved
- Fixed the `ACTION_REQUIRED` escape hatch, which was defined but never
  wired up: `_ACTION_REQUIRED` was `"ACTION_REQUIRED"` (underscore) while
  the SKILL files instruct the agent to print `"ACTION REQUIRED"` (space);
  neither `invoke_library_builder_agent` nor `invoke_harness_builder_agent`
  checked for it or raised `BuildFailureError`. Both now do, and
  `_cmd_generate` catches `BuildFailureError` distinctly from generic build
  failures so a user-resolvable blocker gets an actionable message instead
  of being indistinguishable from any other failure
- Fixed two pre-existing `PLR0913` violations surfaced while re-verifying a
  clean baseline (`invoke_harness_builder_agent`, `build_library_script`)
  by bundling path arguments into new `HarnessPaths`/`BuildPaths`
  dataclasses in `models.py`
- Fixed a test-fixture bug where `_require_cmake()` raised
  `FileNotFoundError` instead of skipping cleanly when `cmake` is entirely
  absent from `PATH`, contradicting `quickstart.md`'s documented behavior
- Full gate suite (`ruff format --check`, `ruff check`, `ty check`,
  `pytest -q`) passes with zero warnings; all six constitution principles
  now pass per the updated Constitution Check table in `plan.md`

### Phase 8 — Agent run introspection

- `specs/002-agent-introspection/` (tracked as beads epic `harnessbuddy-137`,
  T001-T026): replaces raw `--output-format stream-json`/`--json` passthrough
  from the agent fallback with human-readable live narration plus
  duration/cost/token reporting
- New `harnessbuddy/core/agent_stream.py`: `run_agent_streaming()` parses each
  backend's structured event stream line-by-line via `_parse_claude_line`/
  `_parse_codex_line` into `AgentActivityEvent`s (`status`, `file_read`,
  `file_edit`, `command_run`, `tool_result`, `raw_fallback`), printing each as
  it arrives instead of buffering raw JSON; a line that fails to parse or
  match a recognized event shape still surfaces verbatim as `raw_fallback`
  rather than being dropped
- Cost/token stats: Claude's `result` event's `total_cost_usd` populates
  `AgentStreamResult.cost_usd`; Codex has no cost field in its output at all,
  so its `turn.completed.usage` token counts populate `input_tokens`/
  `output_tokens` instead — never a synthesized dollar estimate
- `invoke_library_builder_agent`/`invoke_harness_builder_agent` in
  `agents.py` now call `run_agent_streaming()` instead of
  `run_command_streaming()`, and write a persisted transcript + `=== Agent
  Run Summary ===` trailer (`write_agent_report()`) to
  `agent_library_build.log`/`agent_harness_build.log` in the project
  workspace before raising `BuildFailureError`/`LLMBudgetError`, so
  diagnostics are never lost even when the invocation ultimately fails
- `BuildExplorationResult`/`HarnessExplorationResult` gained `cost_usd`,
  `input_tokens`, `output_tokens`, `transcript_path` fields (plus
  `duration_seconds` on the harness result, previously measured internally
  and silently dropped)
- `write_agent_report`'s parameters were bundled into a new
  `AgentRunSummary` dataclass rather than the flat keyword-argument form
  sketched in `tasks.md`, to satisfy this project's `PLR0913` max-5-argument
  lint rule (same pattern as the `HarnessPaths`/`BuildPaths` bundling from
  Phase 7)
- Full gate suite passes with zero warnings; manually re-verified
  quickstart.md scenarios 3-5 (persisted report file, diagnostics preserved
  on failure, malformed-line handling) end-to-end against a faked subprocess
  replaying the hand-authored fixture JSONL through the real
  `invoke_library_builder_agent` code path. Scenarios 1-2 (live narration,
  cost/token display) need an authenticated `claude`/`codex` CLI hitting a
  real failing build and incurring real API cost — not run this session

---

## Not Yet Started

- Harness builder agent (step 7 in workflow — LLM-generated harness sources)
- OSS-Fuzz Docker validation (`--skip-validation` currently a no-op)
- Seed corpus support
- `--target-headers` option to constrain fuzzing scope
- Self-refining build loop (track success rates, usage costs) — per-invocation
  cost/time/token visibility now exists (Phase 8), but nothing yet acts on it
