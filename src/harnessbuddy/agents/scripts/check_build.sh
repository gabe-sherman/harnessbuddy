#!/usr/bin/env bash
# The single definition of "the build passed", for every environment.
#
# Runs the project's own build.sh (build_library.sh then compile_harnesses.sh) from
# nothing and asserts the artifacts that make the output worth shipping. HarnessBuddy's
# pipeline and every repair agent invoke this same script, so a fix an agent verifies is a
# fix the pipeline accepts.
#
# Runs unchanged on the host and inside the OSS-Fuzz base-builder container. Two fallbacks
# are what let one script text serve both: $OUT is honoured when the environment defines it
# and falls back to <workspace>/out otherwise, matching the generated scripts; and the build
# is entered through OSS-Fuzz's own `compile` wherever that exists, falling back to build.sh
# directly on a plain host.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <workspace>" >&2
  exit 1
fi

workspace=$1
cd "$workspace"

OUT="${OUT:-$PWD/out}"
export OUT

rm -rf install build "$OUT"
mkdir -p "$OUT"

# OSS-Fuzz's `compile` runs build.sh, but only after assembling the environment the base
# image half-provides: SANITIZER_FLAGS resolved into CFLAGS/CXXFLAGS, and
# LIB_FUZZING_ENGINE=-fsanitize=fuzzer in place of the deprecated archive path its ENV names.
# Running build.sh directly in that image links against a file compile_libfuzzer has not
# created yet, and once that is worked around it produces an uninstrumented target instead.
# On a host there is no `compile` and nothing to assemble, so build.sh is entered directly.
build_command=(bash build.sh)
if command -v compile > /dev/null 2>&1; then
  build_command=(compile)
fi

echo "=== build.sh ==="
if command -v bear > /dev/null 2>&1; then
  # Captures compile_commands.json for Make/Autotools projects, which have no build-system
  # equivalent. Harmless for the others, which emit their own.
  bear -- "${build_command[@]}"
else
  "${build_command[@]}"
fi

if ! compgen -G "install/lib/*.a" > /dev/null; then
  echo "FAILED: no static libraries (*.a) found in install/lib" >&2
  exit 1
fi

if [[ ! -d install/include ]] || [[ -z "$(ls -A install/include)" ]]; then
  echo "FAILED: install/include is missing or empty" >&2
  exit 1
fi

if [[ ! -d "$OUT" ]] || [[ -z "$(ls -A "$OUT")" ]]; then
  echo "FAILED: $OUT is missing or empty — no harness binary was produced" >&2
  exit 1
fi

echo "OK: build.sh succeeded and install/ plus $OUT are populated"
