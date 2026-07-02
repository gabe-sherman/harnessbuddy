# Quickstart: Library Feature Extraction for Fuzz Target Generation

Validates User Stories 1–3 end-to-end against a real, already-built C
library: `zlib_feature_test/`, a zlib checkout at the repo root configured
with `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`, so it comes with a working
`compile_commands.json` already in place. Using this fixture directly (all
commands below point at it) means no clone or build step is needed before
exercising `extract-features`/`generate-benchmark` — the same fixture backs
this quickstart and the `tests/feature_extractor/` integration tests
(`research.md` §6).

## Prerequisites

- HarnessBuddy's normal prerequisites (CMake, a C/C++ toolchain).
- LLVM/Clang development packages available on the host — headers plus the
  LibTooling static libraries (`clangTooling`, `clangASTMatchers`,
  `clangBasic`, `clangFrontend`) discoverable via `find_package(Clang)` (new
  for this feature — see `research.md` §1/§2).
- `uv sync` run at the repo root so `pyyaml` (new runtime dependency) is
  installed.
- `zlib_feature_test/` present at the repo root with a `compile_commands.json`
  at its root (untracked — `.gitignore` — the same convention as `output/`
  and `ground_truth_test_output/`). If it's missing, recreate it by cloning
  zlib and configuring it with CMake:
  ```bash
  git clone https://github.com/madler/zlib.git zlib_feature_test
  cmake -S zlib_feature_test -B zlib_feature_test -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  ```

## 1. Extract the JSON feature artifact (User Story 1)

```bash
uv run harnessbuddy extract-features zlib_feature_test
```

**Expected outcome**: `zlib_feature_test/features.json` exists and, when
inspected, contains non-empty `functions`, `typedefs`, and `macros` arrays,
each entry matching `contracts/feature-artifact.schema.json`. Spot-check
known parts of zlib's real public API:
- `deflate` and `inflate` appear as functions with `is_public_api: true`
  and a `params` list matching their real `(z_streamp, int)` signature.
- `z_stream` (typedef) and `z_stream_s` (struct, `kind: "struct"`) both
  appear, with `z_stream`'s `underlying_type` referencing `z_stream_s`.
- `ZEXTERN`/`ZEXPORT` appear among `macros`.

## 2. Convert to an oss-fuzz-gen benchmark YAML (User Story 2)

```bash
uv run harnessbuddy generate-benchmark zlib_feature_test
```

**Expected outcome**: `zlib_feature_test/zlib.yaml` exists, matches
`contracts/benchmark-yaml.schema.json`, and:
- `target_name` is `default_fuzzer`.
- `target_path` is `/src/harness_source/default_fuzzer.c`.
- `functions` contains only entries with `is_public_api: true` from step 1's
  JSON (fewer entries than the full JSON, since zlib's `.c` files also
  define internal/static helpers that must be excluded per FR-012).

Validate against oss-fuzz-gen itself (outside this repo, if available
locally):

```bash
python -c "
from experiment.benchmark import Benchmark
benchmarks = Benchmark.from_yaml('zlib_feature_test/zlib.yaml')
assert benchmarks, 'oss-fuzz-gen could not load the generated benchmark'
print(f'Loaded {len(benchmarks)} function benchmarks')
"
```

## 3. Confirm extraction is independent and non-duplicating (User Story 3)

`zlib_feature_test/` was never produced by `harnessbuddy generate` — it's a
plain CMake build — so simply completing steps 1–2 above already
demonstrates that `extract-features`/`generate-benchmark` don't require
`library_builder` to have run first. The remaining property to confirm is
that re-running never accumulates duplicates (FR-015):

```bash
uv run harnessbuddy extract-features zlib_feature_test
uv run harnessbuddy generate-benchmark zlib_feature_test
ls zlib_feature_test/features.json zlib_feature_test/zlib.yaml   # exactly one of each, freshly overwritten
```

## 4. Confirm the missing-compile_commands.json error path

```bash
mkdir -p /tmp/no-build-here
uv run harnessbuddy extract-features /tmp/no-build-here
```

**Expected outcome**: exit code `1`, with a message naming the missing
`compile_commands.json` and how to produce one (CMake
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` or `bear -- make`) — not a stack trace
or silent empty output.
