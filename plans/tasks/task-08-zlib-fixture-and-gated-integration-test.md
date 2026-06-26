# Task 8: zlib Fixture and Gated Integration Test

**Status**: pending

## Summary

Add the first real integration target using zlib to validate the full
generate → validate pipeline against a known, pinned repository.

## Requirements

- Add a local pretend zlib harness fixture:
  `tests/fixtures/harnesses/zlib/zlib_crc_fuzzer.cc`
- The harness should:
  - `#include <zlib.h>`
  - define `LLVMFuzzerTestOneInput`
  - call a real zlib symbol such as `crc32`
- Add a gated integration test using:
  - repository: `https://github.com/madler/zlib.git`
  - tag: `v1.3.2`
- The generated zlib project must use `--repo-ref v1.3.2`.
- The integration test must:
  1. Generate the oss-fuzz project.
  2. Copy the zlib harness fixture into `harness_source/`.
  3. Run oss-fuzz validation when explicitly enabled.
- Skip Docker and network work unless `HARNESSBUDDY_RUN_DOCKER=1`.

## Acceptance Criteria

- Normal tests do not require Docker or network access.
- The gated test validates the generated zlib project when `HARNESSBUDDY_RUN_DOCKER=1`.
