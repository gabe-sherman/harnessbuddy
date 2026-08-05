#!/usr/bin/env bash
# Run check_build.sh inside the workspace's own OSS-Fuzz image.
#
# Rebuilds the image from the workspace Dockerfile first, so an edit to it takes effect;
# Docker's layer cache makes that cheap when nothing changed. The workspace is then
# bind-mounted at /src, the path OSS-Fuzz tooling expects, so install/ and
# compile_commands.json land on the host for the next stage to use. The harness binaries need
# a mount of their own, since they go to the image's own $OUT=/out, outside /src — so
# <workspace>/out is bound there too, as OSS-Fuzz's helper.py does. Without it they would be
# discarded with the container, and a repair that linked something would look like one that
# only said so.
#
# This decides only where the gate runs. The assertions live in check_build.sh alone.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <workspace> <project_name> [--keep-artifacts]" >&2
  exit 1
fi

workspace=$1
project_name=$2
# Passed straight through to check_build.sh inside the container, which validates it.
check_build_options=("${@:3}")
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tag="harnessbuddy-dev/${project_name}:latest"

cd "$workspace"

if ! docker build -t "$tag" .; then
  echo "FAILED: docker build failed for $workspace" >&2
  exit 1
fi

# Created before the mount so it belongs to the invoking user; Docker would otherwise create
# it root-owned, which the chown below cannot reach from the host side.
mkdir -p out

mounts=(
  -v "$PWD:/src"
  -v "$PWD/out:/out"
  -v "$script_dir/check_build.sh:/usr/local/bin/check_build.sh:ro"
)

# The workspace mount covers /src whole, shadowing the source tree the image cloned there, so
# the container reads <workspace>/src from the host. An oss-fuzz run over a local path leaves
# that a symlink out of the workspace, which dangles in the container unless its target is
# mounted too — at its own path, which is what the symlink resolves to.
if [[ -L src ]]; then
  source_target="$(readlink -f src)"
  if [[ -n $source_target ]]; then
    mounts+=(-v "$source_target:$source_target")
  fi
fi

status=0
docker run --rm "${mounts[@]}" -w /src --entrypoint bash \
  "$tag" /usr/local/bin/check_build.sh /src "${check_build_options[@]}" || status=$?

# The container runs as root, so install/, build/, and out/ come back root-owned and the next
# run cannot delete them. -h stops at the symlink above rather than following it into the
# source tree, which belongs to the user. /out is named explicitly rather than left to
# the walk of /src: it is the same host directory as /src/out, but naming it does not depend
# on chown descending into a nested bind mount.
docker run --rm "${mounts[@]}" --entrypoint chown \
  "$tag" -Rh "$(id -u):$(id -g)" /src /out > /dev/null 2>&1 || true

if [[ $status -ne 0 ]]; then
  echo "FAILED: check_build.sh did not succeed inside the container" >&2
  exit 1
fi

echo "OK: check_build.sh succeeded inside the container"
