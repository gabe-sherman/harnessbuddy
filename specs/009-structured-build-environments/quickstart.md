# Quickstart: Validating Structured Build Environments

Prerequisites: repo checked out, `uv sync` run once. Docker scenarios additionally require
a running Docker daemon.

## 1. Local environment (default — no regression check)

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2
```

Expected: identical behavior to before this feature — host build, host harness probe,
`local/` and `oss-fuzz/` output generated. Final report states `environment: local`.
`stats.json` in the output directory includes `"environment": "local"`.

## 2. Explicit local selection (same as default)

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2 \
  --environment local
```

Expected: identical output to scenario 1 (SC-005 — no regression for users who don't opt
into oss-fuzz).

## 3. oss-fuzz environment, happy path

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2 \
  --environment oss-fuzz
```

Expected:
- Library build stage runs and is validated via `docker run` against
  `gcr.io/oss-fuzz-base/base-builder` (contracts/cli.md) before harness compilation starts.
- Harness compilation stage runs and is validated the same way before generation.
- Final report states `environment: oss-fuzz`; `stats.json` includes `"environment":
  "oss-fuzz"`.
- The generated `oss-fuzz/build_library.sh`/`compile_harnesses.sh` are the exact scripts
  validated in-container (per FR-008 / data-model.md `script_path`), not host-validated
  copies.

## 4. oss-fuzz environment, Docker unavailable

```bash
# with Docker daemon stopped
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2 \
  --environment oss-fuzz
```

Expected: exits 1 with a message naming Docker as unreachable (contracts/cli.md); no agent
invocation even if `--agent claude` was also passed (FR-012).

## 5. Stage failure surfaces at the right stage/environment (SC-002, SC-003)

Pick a library whose harness compilation needs a system library not present in the
oss-fuzz probe image but is present on the local host (or vice versa), and run both:

```bash
uv run harnessbuddy generate <repo> --environment local
uv run harnessbuddy generate <repo> --environment oss-fuzz
```

Expected: the run whose environment lacks the dependency fails specifically at the harness
compilation stage with output attributing the failure to that stage and environment
(Acceptance Scenario US2.3); the other run succeeds. No later stage (generation) runs for
the failing case.

## 6. Agent repair verifies in the selected environment (US3)

```bash
uv run harnessbuddy generate <repo-with-a-known-fixable-build-break> \
  --environment oss-fuzz --agent claude
```

Expected: after the agent edits `build_library.sh`, its own verification step invokes
`agents/scripts/check_docker_build.sh <oss_fuzz_project_dir> <project_name>` (not a host
rebuild) before reporting success — visible in the agent's transcript/log
(`agent_library_build.log`) referencing that script and its arguments.

## 7. Manually exercise the fixed verification scripts (contracts/agent-scripts.md)

```bash
bash agents/scripts/check_local_build.sh .harnessbuddy/<project_name>
bash agents/scripts/check_docker_build.sh output/<project_name>/oss-fuzz <project_name>
```

Expected: both exit 0 against a project that `generate` already validated successfully in
the corresponding environment, and exit non-zero with a clear message if run against a
project whose build is broken (SC-004 — a human can invoke these without hand-editing them
first).
