# Quickstart: Validating Complete Library Dependency Packaging

## Prerequisites

- `uv sync` from repo root.
- No Docker or network access required for the scenarios below (per Constitution Principle IV,
  tests must run without either by default).

## Scenario 1 — Agent resolves a link without installing anything, package still recorded

Validates Story 1 + the remaining-work item in `research.md`.

1. Write a fixture harness-agent invocation (see existing pattern in
   `tests/test_cli.py::test_generate_harness_missing_package_reaches_output_on_success`) where
   the fake agent:
   - Edits `compile_harnesses.sh` to add a new `-lfoo` to `EXTRA_LINK_FLAGS`.
   - Writes `agent_report.json` with `"missing_libs": ["foo"], "missing_apt_packages":
     ["libfoo-dev"], "missing_brew_packages": ["foo"]`, alongside a **successful** exit (`exit_code=0`)
     and a populated `out/` directory (so `_validate_harness_artifacts` passes).
2. Run `uv run pytest tests/test_cli.py -k <new_test_name> -q`.
3. **Expected**: `rc == 0`, the generated `Dockerfile` contains `libfoo-dev`, and `setup.sh`
   contains `libfoo-dev` (Linux) or `foo` (macOS) — even though `harness_result.succeeded` is
   `True` and no "please install a package" message was ever shown to the user.

## Scenario 2 — Unresolvable dependency still surfaces the right per-platform names

Validates Story 3 (already implemented this session).

1. Run `uv run pytest tests/test_cli.py -k missing_package -q`.
2. **Expected**: all four tests pass, and `setup.sh`'s asserted package name matches
   `sys.platform` (brew name on macOS, apt name on Linux/CI) — confirming apt and brew names are
   resolved independently rather than one being reused for the other.

## Scenario 3 — Dependencies accumulate across stages without loss or duplication

Validates Story 4 (already implemented; regression-tested by
`test_generate_library_and_harness_phase_share_package_without_duplication` in
`tests/test_cli.py`).

1. Run `uv run pytest tests/test_cli.py -k share_package_without_duplication -q`.
2. **Expected**: pass — the shared package (`libzstd-dev`) appears exactly once in the generated
   Dockerfile despite being reported by both the library-build phase and the harness deterministic
   probe.

## Scenario 4 — Real-world repro (manual, optional)

The original bug this feature traces back to. Requires network access and `claude` CLI
credentials, so it is **not** part of the automated suite — run manually to sanity-check an
end-to-end fix:

```bash
uv run harnessbuddy generate https://github.com/curl/curl --agent claude
```

**Expected**: if the harness-repair agent identifies a dependency (e.g. LDAP) not in
`package_names.json`, the final console message and generated `Dockerfile`/`setup.sh` list the
correct platform-specific package name (`libldap2-dev` on apt, `openldap` on brew) — never a
brew-only name landing in the apt install list or vice versa, and never a blank
"missing system libraries:" line.

## Full regression check

Before considering this feature done:

```bash
uv run ruff format && uv run ruff check && uv run ty check
uv run pytest tests/test_cli.py tests/library_builder/ -q
```

All four MUST pass with zero warnings (Constitution Principle I). Note: as of this plan,
`tests/library_builder/test_harness_build.py::TestZlibBuild`/`TestLibtiffBuild` and
`tests/library_builder/test_scripts.py::test_empty_extra_paths_local_script_is_pinned` fail for
an unrelated, already in-progress, uncommitted change to `scripts.py`'s `EXTRA_LINK_FLAGS`
default — not caused by and not in scope for this feature. Confirm those failures are still
isolated to that change (not regressed further) before merging.
