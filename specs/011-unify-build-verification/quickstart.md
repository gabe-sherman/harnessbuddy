# Quickstart: Validating Unified Build Verification

Prerequisites: repo checked out, `uv sync` run once. Docker scenarios additionally require
a running Docker daemon.

## 1. Local environment — no regression

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2 \
  --environment local
```

Expected: same end result as before this feature (`local/`, `oss-fuzz/` output generated,
`stats.json` unchanged in shape). The only observable difference is that the library-build
and harness-compile pass/fail decisions now come from `LocalExecutor` invoking
`agents/scripts/check_local_build.sh` (contracts/verification-scripts.md) — visible with
`--log-level debug`, which logs the subprocess command.

## 2. oss-fuzz environment — workspace looks like a real project mid-run (User Story 2)

```bash
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2 \
  --environment oss-fuzz --keep-workdir
```

While it's running (or after, since `--keep-workdir` preserves the directory), inspect:

```bash
ls .harnessbuddy/zlib/
```

Expected: `Dockerfile`, `build.sh`, `project.yaml`, `build_library.sh`,
`compile_harnesses.sh`, `harness_source/` are all present (contracts/workspace-layout.md) —
not just `build_library.sh`/`compile_harnesses.sh` in isolation as before this feature.

## 3. oss-fuzz environment — final output matches what was validated (FR-005)

Continuing from scenario 2:

```bash
diff .harnessbuddy/zlib/build_library.sh output/zlib/oss-fuzz/build_library.sh
diff .harnessbuddy/zlib/compile_harnesses.sh output/zlib/oss-fuzz/compile_harnesses.sh
```

Expected: no differences — final generation copied the exact validated files
(research.md #6), rather than re-deriving them from templates.

## 4. Shared script produces the same result for HarnessBuddy and a human (SC-001)

```bash
bash agents/scripts/check_docker_build.sh output/zlib/oss-fuzz zlib
```

Expected: exits 0 — the same command HarnessBuddy's own `OssFuzzExecutor` ran as its final
gate (visible in the run's stdout/`stats.json`, per FR-010), reproduced manually with an
identical result.

## 5. Agent repair verifies against a real, existing directory (fixes today's gap)

```bash
uv run harnessbuddy generate <repo-with-a-known-fixable-build-break> \
  --environment oss-fuzz --agent claude
```

Expected: the agent's transcript/log references `check_docker_build.sh
.harnessbuddy/<project> <project>` as its verification command, and that command actually
succeeds or fails against a real directory — not a path that doesn't exist yet (this was
possible to hit before this feature, since `oss_fuzz_project_dir` pointed at the
not-yet-created final output directory during exploration).

## 6. Docker unavailable — unchanged behavior (FR-007)

```bash
# with Docker daemon stopped
uv run harnessbuddy generate https://github.com/madler/zlib.git --repo-ref v1.3.2 \
  --environment oss-fuzz
```

Expected: exits 1, names Docker as unreachable, no agent invocation — identical to spec
009's existing behavior.

## 7. Harness-link discovery stays fast (FR-011)

Pick a library whose harness needs 2+ discovery retries (a transitive `-l` dependency not
resolved on the first attempt). Run with `--log-level debug --environment oss-fuzz` and
confirm the retry loop's log lines show direct `docker run --entrypoint bash <image> -c
"bash compile_harnesses.sh"` calls against the already-built image — not a fresh `docker
build` per attempt — while the run's *final* reported result still comes from one
`check_docker_build.sh` invocation after discovery converges.
