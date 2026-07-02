# Quickstart: Validate Harness Linker Dependencies Become Install Commands

Validates the fix end-to-end against the exact scenario that surfaced this
gap: libtiff's harness links against `zstd`, `z`, and `lzma`.

## Prerequisites

- `uv` installed, repo dependencies synced (`uv sync`).
- A machine that already has `zstd`, `zlib`, and `xz`/`lzma` available (so
  the harness link step succeeds locally without hitting the
  missing-locally failure path this feature does not change).

## 1. Reproduce the gap (pre-fix baseline)

```bash
git stash                      # or check out main before the fix lands
uv run harnessbuddy generate https://gitlab.com/libtiff/libtiff.git \
  --output /tmp/harnessbuddy-quickstart
```

Inspect the generated files:

```bash
cat /tmp/harnessbuddy-quickstart/libtiff/local/setup.sh
cat /tmp/harnessbuddy-quickstart/libtiff/oss-fuzz/Dockerfile
grep EXTRA_LINK_FLAGS /tmp/harnessbuddy-quickstart/libtiff/local/compile_harnesses.sh
```

**Expected pre-fix (the bug)**: `compile_harnesses.sh` shows
`EXTRA_LINK_FLAGS="... -lzstd -lz -llzma"`, but `setup.sh` has only the
`# TODO: install build dependencies for this library` placeholder and the
Dockerfile has no `RUN apt-get install` line — confirmed against
`ground_truth_test_output/libtiff/` in this repo.

## 2. Apply the fix and re-run

```bash
git stash pop                  # or check out the branch with the fix
rm -rf /tmp/harnessbuddy-quickstart
uv run harnessbuddy generate https://gitlab.com/libtiff/libtiff.git \
  --output /tmp/harnessbuddy-quickstart
```

## 3. Verify generated output (User Story 1)

```bash
cat /tmp/harnessbuddy-quickstart/libtiff/local/setup.sh
cat /tmp/harnessbuddy-quickstart/libtiff/oss-fuzz/Dockerfile
```

**Expected**:
- `setup.sh` contains `apt-get install -y --no-install-recommends libzstd-dev zlib1g-dev liblzma-dev` (Linux) or `brew install zstd zlib xz` (macOS) — see contracts/generated-install-step.md for exact precedence.
- `Dockerfile` contains `RUN apt-get update && apt-get install -y --no-install-recommends libzstd-dev zlib1g-dev liblzma-dev`.

## 4. Verify the harness builds in a clean environment (SC-002)

```bash
docker build -t harnessbuddy-libtiff-quickstart \
  /tmp/harnessbuddy-quickstart/libtiff/oss-fuzz
```

**Expected**: the image builds successfully, confirming the harness link
step no longer depends on the host machine already having `zstd`/`z`/`lzma`
installed.

## 5. Verify unmapped-dependency reporting (User Story 2)

Run against a library whose harness resolves a link flag with no entry in
`package_names.json` (or temporarily remove an existing mapping entry, e.g.
`zstd`, in a scratch copy of `package_names.json`) and re-run step 2.

**Expected**: console output names the unmapped library explicitly (e.g.
`unknown_libs: zstd`), and the generated `setup.sh`/Dockerfile do not
silently claim the dependency is handled.

## 6. Verify no duplicate packages (User Story 3)

```bash
grep -o 'libzstd-dev\|zlib1g-dev\|liblzma-dev' \
  /tmp/harnessbuddy-quickstart/libtiff/oss-fuzz/Dockerfile | sort | uniq -c
```

**Expected**: every package name appears exactly once in the count, even
though `zlib1g-dev` may be contributed by both the library build phase (if
libtiff's own build depends on zlib) and the harness link phase.

## 7. Automated coverage

```bash
uv run pytest -q tests/test_cli.py tests/library_builder/test_harness_explorer.py \
  tests/library_builder/test_package_names.py
```

**Expected**: all pass, including new cases added for this feature (harness
succeeds with populated `transitive_link_flags` and empty
`missing_system_libs`; agent-repaired harness result also merges correctly).
