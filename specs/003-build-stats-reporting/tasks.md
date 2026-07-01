---

description: "Task list for feature implementation"
---

# Tasks: Build Statistics Reporting

**Input**: Design documents from `specs/003-build-stats-reporting/`
(`plan.md`, `research.md`, `data-model.md`, `contracts/stats-json.md`, `quickstart.md`)

**Tests**: Included — Constitution Principle IV requires behavior coverage for new
capability and error paths, and this project's existing tests
(`tests/core/test_agent_stream.py`, `tests/library_builder/test_agents.py`,
`tests/test_cli.py`) already establish the fixture/mocking patterns to extend.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to `spec.md` user stories (US1 = review run outcome/cost at a
  glance, US2 = consistent location/shape across runs). Foundational/Polish tasks carry
  no story label.

## Path Conventions

Single project: `src/harnessbuddy/`, `tests/` at repository root (already in place — no
new top-level directories). No new fixture files needed: the existing
`tests/fixtures/agent_streams/claude_stream_sample.jsonl` already contains a plain-text
`assistant` block *after* all tool-use activity (`"The build now succeeds after
disabling shared libraries."`), `codex_stream_sample.jsonl` already contains one
`agent_message` item (`"hello world"`), and `malformed_stream_sample.jsonl` contains no
text/agent_message content at all — exactly the three cases this feature's new
`final_message` capture needs to test.

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared fields every downstream task reads or writes. Per
Constitution Principle VI, agent output stays normalized into the existing typed result
dataclasses, so these are plain field additions, not a parallel structure.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T001 [P] In `src/harnessbuddy/core/agent_stream.py`, add `final_message: str |
  None = None` to both `AgentStreamResult` and `AgentRunSummary` (after their existing
  `output_tokens` field), per `data-model.md`'s field derivation table.
- [ ] T002 [P] In `src/harnessbuddy/library_builder/models.py`, add `agent_summary: str
  | None = None` to both `BuildExplorationResult` and `HarnessExplorationResult` (after
  their existing `transcript_path` field) — raw, nullable; the `"N/A"`/`"unavailable"`
  translation happens later in `stats.py`, not here.

**Checkpoint**: New fields exist and `uv run ty check` passes with no other code
referencing them yet.

---

## Phase 2: User Story 1 - Review run outcome and cost at a glance (Priority: P1) 🎯 MVP

**Goal**: After any `harnessbuddy generate` run reaches its output directory — whether
it then succeeds, fails during the library build, or fails during the harness build —
`stats.json` reports total run time, each phase's agent invocation (time, cost, and a
plain-language work summary, or `"N/A"` if that agent wasn't invoked), and a final
status of `success`, `failed_library_build`, or `failed_harness_build`.

**Independent Test**: Run `harnessbuddy generate` against a repo that builds and
harness-links cleanly with no agent invocation; `stats.json` reports the total duration,
`"N/A"` for both agents' time/cost/summary, and `status: "success"`.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T003 [P] [US1] In `tests/core/test_agent_stream.py`, add
  `test_run_agent_streaming_final_message_from_last_claude_text_block` (drive
  `run_agent_streaming` over `claude_stream_sample.jsonl` via the existing
  `_FakeProcess`/`Popen`-patch pattern; assert `result.final_message ==
  "The build now succeeds after disabling shared libraries."` — the *last* text block,
  not the earlier `"I'll start by reading the failing build script."` one, proving
  thinking/tool-use lines in between don't count as the closing message),
  `test_run_agent_streaming_final_message_from_codex_agent_message` (same pattern over
  `codex_stream_sample.jsonl`; assert `result.final_message == "hello world"`), and
  `test_run_agent_streaming_final_message_none_without_text_block` (drive it over
  `malformed_stream_sample.jsonl` for both `tool="claude"` and `tool="codex"`; assert
  `result.final_message is None`). Confirm the two populated-message tests FAIL (no
  extraction logic exists yet; `final_message` stays `None`).
- [ ] T004 [P] [US1] Create `tests/library_builder/test_stats.py` with unit tests for
  `harnessbuddy.library_builder.stats` per `data-model.md`/`contracts/stats-json.md`:
  `not_invoked_agent_stats()` returns `invoked=False` with `duration_seconds`,
  `cost_usd`, `summary` all `"N/A"`; `agent_phase_stats_from_build()` given a
  `BuildExplorationResult(llm_used=False, ...)` returns the same not-invoked shape;
  given `llm_used=True` with `cost_usd` and `agent_summary` both set, returns those real
  values; given `llm_used=True, cost_usd=None` (Codex-style), returns `cost_usd ==
  "N/A"`; given `llm_used=True, agent_summary=None` (agent produced no final text),
  returns `summary == "unavailable"`. Mirror all four cases for
  `agent_phase_stats_from_harness()` against `HarnessExplorationResult`. Add
  `agent_phase_stats_from_agent_error()` cases against a hand-built `AgentRunSummary`:
  real cost/summary passed through; `cost_usd=None` → `"N/A"`; `final_message=None` →
  `"unavailable"`. Finally, build one `RunStats` per worked example in
  `contracts/stats-json.md` (clean success, library-agent-repaired, harness-unrecoverable
  with Codex) and assert `write_run_stats(tmp_path / "stats.json", stats)` produces a
  file whose `json.loads(...)` content exactly matches that example's dict, including
  `status` serializing to its lowercase string value. Confirm FAILS (module doesn't
  exist yet).
- [ ] T005 [P] [US1] In `tests/library_builder/test_agents.py`, extend
  `test_action_required_raises_build_failure_error` (and add an analogous
  `test_budget_limited_raises_llm_budget_error` using budget-pattern text like `"reached
  the 5 hour limit"` in the mocked `AgentStreamResult.combined_text`) to assert the
  raised exception's `.summary` is an `AgentRunSummary` whose `duration_seconds`,
  `cost_usd`, and `final_message` match the mocked `AgentStreamResult`'s
  `duration_seconds`, `cost_usd`, and `final_message`. Mirror both for the harness-agent
  path (`test_harness_action_required_raises_build_failure_error` plus a new budget-error
  case). Add `test_library_agent_populates_agent_summary_on_success` and
  `test_harness_agent_populates_agent_summary_on_success`: after a mocked successful
  invocation (`AgentStreamResult(..., final_message="Fixed it.")`), the returned
  `BuildExplorationResult.agent_summary` / `HarnessExplorationResult.agent_summary`
  equals `"Fixed it."`. Confirm all FAIL (no `.summary` attribute, no `.agent_summary`
  population yet).
- [ ] T006 [P] [US1] In `tests/test_cli.py`, add four integration tests driving
  `main(["generate", str(local_repo_with_origin), "--output", str(output_dir)])`
  end-to-end, each asserting on
  `json.loads((output_dir / local_repo_with_origin.name / "output" / "stats.json").read_text())`:
  (1) `test_generate_writes_stats_json_clean_success` — additionally patch
  `harnessbuddy.cli.build_harness` to return a succeeded `HarnessExplorationResult`
  (the fixture's real harness probe fails today since the mocked library build never
  produces real artifacts — patch it explicitly here rather than relying on that
  incidental behavior) → assert `status == "success"` and both `library_build_agent`
  and `harness_build_agent` read `invoked: False` with `"N/A"` everywhere; (2)
  `test_generate_writes_stats_json_library_agent_repaired` — patch
  `harnessbuddy.cli.build_library` to return
  `BuildExplorationResult(llm_used=True, succeeded=True, duration_seconds=12.5,
  cost_usd=0.05, agent_summary="Added a missing CMake flag.", ...)` and patch
  `harnessbuddy.cli.build_harness` to return a succeeded result → assert
  `library_build_agent == {"invoked": True, "duration_seconds": 12.5, "cost_usd": 0.05,
  "summary": "Added a missing CMake flag."}` and `status == "success"`; (3)
  `test_generate_writes_stats_json_failed_library_build` — patch
  `harnessbuddy.cli.build_library` to return `BuildExplorationResult(succeeded=False,
  ...)` → assert `rc != 0`, `stats.json` still exists, `status ==
  "failed_library_build"`; (4)
  `test_generate_writes_stats_json_failed_harness_build_emits_stub_output` — no special
  patching (the fixture's real harness probe already fails non-fatally by default,
  exactly as `test_no_agents_skips_harness_agent_when_compilation_fails` already relies
  on) → assert `rc == 0`, `local/`/`oss-fuzz/` still populated, but `status ==
  "failed_harness_build"`. Confirm all four FAIL (no `stats.json` is written yet).

### Implementation for User Story 1

- [ ] T007 [US1] In `src/harnessbuddy/core/agent_stream.py`, implement
  `_claude_final_text(line: str) -> str | None` (same parse-and-guard shape as
  `_claude_result_cost`: for an `assistant` message, return the `text` of a `type:
  "text"` content block if one is present on that line, else `None`) and
  `_codex_final_text(line: str) -> str | None` (same shape as `_codex_result_cost`: for
  an `item.completed` event whose `item["type"] == "agent_message"`, return
  `item["text"]`, else `None`), plus `_extract_final_text(tool: str, line: str) -> str |
  None` dispatching between them exactly like `_extract_stats` does. In
  `run_agent_streaming`, initialize `final_message: str | None = None` before the loop;
  inside the loop, alongside the existing `_extract_stats(tool, line)` call, call
  `_extract_final_text(tool, line)` and overwrite `final_message` whenever it returns
  non-`None` (last one wins). Pass `final_message=final_message` into the returned
  `AgentStreamResult`. Run T003 and confirm it now passes.
- [ ] T008 [US1] In `src/harnessbuddy/library_builder/agents.py`: change
  `_report_agent_run` to set `final_message=result.final_message` on the
  `AgentRunSummary` it builds and to return that `AgentRunSummary` (was `None`). Add
  `summary: AgentRunSummary` as a required second constructor argument on
  `BuildFailureError` and `LLMBudgetError`, stored as `self.summary`. Change
  `_raise_for_agent_failure` to `_raise_for_agent_failure(exit_code: int,
  combined_output: str, summary: AgentRunSummary) -> None` and pass `summary` into both
  `raise` calls. Update both call sites
  (`invoke_library_builder_agent`/`invoke_harness_builder_agent`) to capture
  `_report_agent_run`'s return value and pass it to `_raise_for_agent_failure`, and set
  `agent_summary=result.final_message` when constructing the returned
  `BuildExplorationResult`/`HarnessExplorationResult`. Run T005 and confirm it passes;
  run `uv run pytest tests/library_builder/test_agents.py -q` and fix any other
  direct `BuildFailureError(...)`/`LLMBudgetError(...)` construction in that test file
  that now needs the new `summary` argument.
- [ ] T009 [US1] Create `src/harnessbuddy/library_builder/stats.py` (matching this
  package's existing `from __future__ import annotations` / absolute-import
  conventions) implementing: `RunStatus` enum (`SUCCESS = "success"`,
  `FAILED_LIBRARY_BUILD = "failed_library_build"`, `FAILED_HARNESS_BUILD =
  "failed_harness_build"`); `AgentPhaseStats` dataclass (`invoked: bool`,
  `duration_seconds: float | str`, `cost_usd: float | str`, `summary: str`, plus
  `to_dict() -> dict[str, object]`); `not_invoked_agent_stats() -> AgentPhaseStats`;
  `agent_phase_stats_from_build(result: BuildExplorationResult) -> AgentPhaseStats` and
  `agent_phase_stats_from_harness(result: HarnessExplorationResult) -> AgentPhaseStats`
  (both: return `not_invoked_agent_stats()` when `not result.llm_used`; otherwise
  `AgentPhaseStats(invoked=True, duration_seconds=result.duration_seconds,
  cost_usd=result.cost_usd if result.cost_usd is not None else "N/A",
  summary=result.agent_summary or "unavailable")`); `agent_phase_stats_from_agent_error(
  summary: AgentRunSummary) -> AgentPhaseStats` (same `"N/A"`/`"unavailable"`
  translation, sourced from the exception's carried summary); `RunStats` dataclass
  (`total_duration_seconds: float`, `library_build_agent: AgentPhaseStats`,
  `harness_build_agent: AgentPhaseStats`, `status: RunStatus`, plus `to_dict() ->
  dict[str, object]` nesting both phases' `to_dict()` and `status.value`); and
  `write_run_stats(path: Path, stats: RunStats) -> None` (`path.write_text(
  json.dumps(stats.to_dict(), indent=2))`). Run T004 and confirm it passes.
- [ ] T010 [US1] In `src/harnessbuddy/cli.py`: import `time`, import `LLMBudgetError`
  alongside the existing `BuildFailureError` import, and import `RunStats`, `RunStatus`,
  `agent_phase_stats_from_build`, `agent_phase_stats_from_harness`,
  `agent_phase_stats_from_agent_error`, `not_invoked_agent_stats`, `write_run_stats`
  from `harnessbuddy.library_builder.stats`. At the top of `_cmd_generate`, record
  `start_time = time.monotonic()`. Immediately after `local_output_path,
  oss_output_path = _resolve_output_paths(args, analysis)`, add `base_output =
  local_output_path.parent` and `base_output.mkdir(parents=True, exist_ok=True)` so an
  output directory exists before either build phase runs (`research.md` §4 — today it
  only exists as a side effect of a successful `_generate_outputs` call). Change the
  library-phase `except BuildFailureError as exc:` to `except (BuildFailureError,
  LLMBudgetError) as exc:`; immediately before its existing `print(...); return 1`,
  write `RunStats(total_duration_seconds=time.monotonic() - start_time,
  library_build_agent=agent_phase_stats_from_agent_error(exc.summary),
  harness_build_agent=not_invoked_agent_stats(),
  status=RunStatus.FAILED_LIBRARY_BUILD)` to `base_output / "stats.json"` via
  `write_run_stats`. Apply the equivalent change to the harness-phase `except
  BuildFailureError as exc:` block (also widened to `(BuildFailureError,
  LLMBudgetError)`), using `library_build_agent=agent_phase_stats_from_build(result)`
  (the library phase already succeeded by this point) and
  `harness_build_agent=agent_phase_stats_from_agent_error(exc.summary)`,
  `status=RunStatus.FAILED_HARNESS_BUILD`. Run T006's two failure-path tests
  (`test_generate_writes_stats_json_failed_library_build` and the
  agent-error variants added in T005) and confirm they pass.
- [ ] T011 [US1] In `src/harnessbuddy/cli.py`'s `_cmd_generate`, right after the call to
  `_generate_outputs(...)` returns its exit code (leave `_generate_outputs` itself
  unchanged — it stays a pure "write the two output trees" function), build
  `RunStats(total_duration_seconds=time.monotonic() - start_time,
  library_build_agent=agent_phase_stats_from_build(result),
  harness_build_agent=agent_phase_stats_from_harness(harness_result),
  status=RunStatus.SUCCESS if harness_result.succeeded else
  RunStatus.FAILED_HARNESS_BUILD)` and write it to `base_output / "stats.json"` via
  `write_run_stats` before returning that exit code. Run T006's remaining two tests
  (`test_generate_writes_stats_json_clean_success`,
  `test_generate_writes_stats_json_library_agent_repaired`,
  `test_generate_writes_stats_json_failed_harness_build_emits_stub_output`) and confirm
  they pass; run `uv run pytest tests/test_cli.py -q` to confirm no regressions in
  existing success/failure tests.

**Checkpoint**: Every run that reaches the output-directory stage produces a
`stats.json` with the correct total duration, per-phase agent stats, and status for all
four outcomes in `contracts/stats-json.md`. This is a demonstrable, independently
valuable MVP on its own.

---

## Phase 3: User Story 2 - Consistent location across runs (Priority: P2)

**Goal**: `stats.json` always lives at the same relative path with the same field
shape, regardless of outcome, so a script running `harnessbuddy generate` across many
libraries never needs outcome-specific handling — and reusing an output directory never
leaves stale or merged data behind.

**Independent Test**: Run two `harnessbuddy generate` invocations with different
outcomes (one clean success, one failed harness build) into separate output
directories; both `stats.json` files sit at the same relative path and have identical
key structure, differing only in values.

No new implementation — this story validates properties of the mechanism Phase 2
already built, per `spec.md`'s own framing of US2 as a consistency guarantee, not new
behavior.

### Tests for User Story 2

- [ ] T012 [P] [US2] In `tests/test_cli.py`, add
  `test_stats_json_same_relative_path_and_shape_across_outcomes`: run one clean-success
  generate (reusing T006's clean-success patches) and one failed-library-build generate
  (reusing T006's failure patches) into two separate `--output` directories; assert both
  produce `stats.json` at `<output_dir>/<project_name>/output/stats.json`, and that
  recursively collecting each JSON's key paths (e.g. via a small recursive helper) yields
  the identical set for both runs, even though the values differ.
- [ ] T013 [P] [US2] In `tests/test_cli.py`, add `test_stats_json_overwritten_on_rerun`
  (FR-010): run `main(["generate", ...])` twice into the same `--output` directory
  (following the existing overwrite flow already exercised by
  `test_generate_output_dir_exists_exits_nonzero`), with the first run patched to a
  library-agent-repaired success (T006's scenario 2 patches) and the second run patched
  to a clean success (T006's scenario 1 patches); assert the final `stats.json`'s
  `library_build_agent.invoked` is `False` (the *second* run's value), not `True` or a
  merge of both runs.
- [ ] T014 [P] [US2] In `tests/test_cli.py`, add
  `test_no_stats_json_when_output_directory_never_created`: run `main(["generate", ...])`
  against the `norepo` fixture pattern already used by
  `test_generate_no_cpp_signals_exits_nonzero` (`UnsupportedRepositoryError`, raised
  before `_resolve_output_paths` is ever called); assert no `stats.json` exists anywhere
  under `--output` afterward.

**Checkpoint**: `stats.json`'s location and shape are proven consistent across outcomes
and reruns, and confirmed absent when no output directory was ever established.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Confirm the full gate suite passes and record this work in the project's
progress log.

- [ ] T015 Run `uv run ruff format`, `uv run ruff check`, `uv run ty check`, and `uv run
  pytest -q`; fix any regression before proceeding.
- [ ] T016 [P] Manually run `quickstart.md` scenarios 1, 4, 6, and 7 (no agent
  credentials needed) against a real repo. Scenarios 2, 3, and 5 need an authenticated
  `claude`/`codex` CLI — run them if credentials are available, otherwise note in the PR
  description that they rely on the automated `test_stats.py`/`test_agents.py`/
  `test_cli.py` coverage instead.
- [ ] T017 [P] Add a short entry to `plans/oss-fuzz-builder/progress.md` recording this
  feature (mirroring the existing "Phase N —" section style).

**Checkpoint**: `ruff format --check`, `ruff check`, `ty check`, and `pytest -q` all
pass with zero warnings; `plan.md`'s Constitution Check remains fully passing.

---

## Dependencies & Execution Order

- **Phase 1 (Foundational)**: No dependencies — start immediately.
- **Phase 2 (US1)**: Depends on Phase 1 (both new dataclass fields must exist before
  tests reference them). Tests (T003-T006) can be written in parallel once Phase 1
  lands; implementation (T007-T011) must follow the order below since each edits
  functions the next one calls:
  - T007 depends on T001 (adds the field T007 populates).
  - T008 depends on T002 (adds the field T008 populates) and T007 (uses
    `result.final_message`, which T007 makes non-trivial).
  - T009 depends on T002 (`agent_phase_stats_from_build`/`_harness` read
    `result.agent_summary`).
  - T010 depends on T008 (`exc.summary`) and T009 (`RunStats`/`RunStatus`/converters).
  - T011 depends on T009 and lands in the same file/function region as T010 (sequential,
    not parallel, despite no data dependency between them).
- **Phase 3 (US2)**: Depends on Phase 2 being fully complete — these tests exercise the
  mechanism Phase 2 built, across multiple runs.
- **Phase 4 (Polish)**: Depends on Phases 2 and 3 both being complete.

### Parallel Opportunities

- T001 and T002 (different files) — fully parallel.
- T003, T004, T005, T006 (different test files) — parallel to write; T006 in particular
  can be drafted independently of T003-T005 since it patches at the `cli.py` boundary
  (`build_library`/`build_harness`), not the lower-level functions those other tests
  cover.
- T012, T013, T014 all land in `tests/test_cli.py` alongside T006 — safe to draft in
  parallel (independent functions) but expect to land as one commit given the file
  overlap, matching the existing `test_cli.py` convention of many independent test
  functions in one file.
- T016 and T017 (independent, unrelated files) — parallel.

---

## Parallel Example: User Story 1 (Tests)

```bash
# Launch all four test-writing tasks for User Story 1 together:
Task: "Add final_message capture tests to tests/core/test_agent_stream.py"
Task: "Create tests/library_builder/test_stats.py"
Task: "Add BuildFailureError/LLMBudgetError.summary tests to tests/library_builder/test_agents.py"
Task: "Add stats.json integration tests to tests/test_cli.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Foundational (two field additions).
2. Complete Phase 2: User Story 1 — every run outcome produces a correct `stats.json`.
3. **STOP and VALIDATE**: run `harnessbuddy generate` against a real repo (clean success,
   and separately with `--agent claude`/`--agent codex` against a known-failing build)
   and inspect `stats.json` by hand against `contracts/stats-json.md`.
4. Ship as MVP — the cross-run consistency checks (US2) are validation, not new
   behavior, and can land in the same change or a fast follow-up.

### Incremental Delivery

1. Foundational → shared fields ready.
2. Add User Story 1 → validate independently → this alone delivers everything the user
   asked for (total time, per-phase agent time/cost/summary, final status, written as
   JSON to the output directory).
3. Add User Story 2 → validate independently → proves the mechanism behaves
   consistently across many runs, which is what makes it usable for scripted/batch
   workflows rather than just a one-off manual check.

### Recommended order

Sequential: Phase 1 → Phase 2 → Phase 3 → Phase 4. Phase 2's implementation tasks
(T007-T011) edit the same handful of functions in sequence
(`run_agent_streaming` → `invoke_*_agent`/`_raise_for_agent_failure` →
`stats.py` → `_cmd_generate`'s two failure sites → `_cmd_generate`'s success site), so
they are not meaningfully parallelizable across contributors despite being
independently testable checkpoints. Phase 3 is pure validation and can start as soon as
Phase 2's checkpoint is green.
