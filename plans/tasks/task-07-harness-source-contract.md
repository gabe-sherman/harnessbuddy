# Task 7: Harness Source Contract

**Status**: pending

## Summary

Implement the multi-harness source contract: filtering, compilation, and output
naming for harness files in `/src/harness_source`.

## Requirements

- Generate `harness_source/default_fuzzer.cc`.
- The default harness must define `LLVMFuzzerTestOneInput`.
- The default harness must not define `main`.
- `compile_harnesses.sh` must loop over direct files in `/src/harness_source`.
- Compile only:
  - `.c`
  - `.cc`
  - `.cpp`
  - `.cxx`
- Ignore:
  - headers
  - docs
  - corpora
  - nested directories
- Emit fuzzer binaries to `$OUT/<basename-without-extension>`.
- Use `$CC` for `.c`.
- Use `$CXX` for C++ sources.
- Link with `$LIB_FUZZING_ENGINE` and deterministic include/link flags.
- For `.c` harness files, use `$CC` for the full compile/link command.

Template shape:

```bash
#!/bin/bash
set -euo pipefail

HARNESS_DIR="/src/harness_source"

for harness in "$HARNESS_DIR"/*; do
  [ -f "$harness" ] || continue

  name="$(basename "$harness")"
  output="${name%.*}"

  case "$harness" in
    *.c)
      "$CC" $CFLAGS $HB_INCLUDE_FLAGS "$harness" \
        $HB_LIBRARY_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"
      ;;
    *.cc|*.cpp|*.cxx)
      "$CXX" $CXXFLAGS $HB_INCLUDE_FLAGS "$harness" \
        $HB_LIBRARY_FLAGS "$LIB_FUZZING_ENGINE" -o "$OUT/$output"
      ;;
  esac
done
```

## Acceptance Criteria

- Tests prove source filtering (only `.c`/`.cc`/`.cpp`/`.cxx` compiled).
- Tests prove output name derivation (basename without extension).
- Tests prove compiler selection by extension.
