#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <work_dir>" >&2
  exit 1
fi

work_dir=$1

cd "$work_dir"

echo "=== build_library.sh ==="
if ! bash build_library.sh; then
  echo "FAILED: build_library.sh did not succeed" >&2
  exit 1
fi

echo "=== compile_harnesses.sh ==="
if ! bash compile_harnesses.sh; then
  echo "FAILED: compile_harnesses.sh did not succeed" >&2
  exit 1
fi

if ! compgen -G "install/lib/*.a" > /dev/null; then
  echo "FAILED: no static libraries (*.a) found in install/lib" >&2
  exit 1
fi

if [[ ! -d install/include ]] || [[ -z "$(ls -A install/include)" ]]; then
  echo "FAILED: install/include is missing or empty" >&2
  exit 1
fi

if [[ ! -d out ]] || [[ -z "$(ls -A out)" ]]; then
  echo "FAILED: out/ is missing or empty" >&2
  exit 1
fi

echo "OK: build_library.sh and compile_harnesses.sh succeeded, artifacts present"
