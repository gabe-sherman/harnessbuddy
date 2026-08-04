#!/usr/bin/env bash
# Run check_build.sh inside the workspace's own OSS-Fuzz image.
#
# Rebuilds the image from the workspace Dockerfile first, so a Dockerfile edit (an added
# apt package, say) takes effect — Docker's layer cache makes that cheap when nothing
# changed. The workspace is then bind-mounted at /src, the path OSS-Fuzz tooling expects,
# so everything the build produces (install/, out/, compile_commands.json) lands on the
# host where the next pipeline stage and the generated output can use it.
#
# This is only where the gate runs, never what it checks: the assertions live in
# check_build.sh alone.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <workspace> <project_name>" >&2
  exit 1
fi

workspace=$1
project_name=$2
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tag="harnessbuddy-dev/${project_name}:latest"

cd "$workspace"

if ! docker build -t "$tag" .; then
  echo "FAILED: docker build failed for $workspace" >&2
  exit 1
fi

mounts=(
  -v "$PWD:/src"
  -v "$script_dir/check_build.sh:/usr/local/bin/check_build.sh:ro"
)

# The workspace mount covers /src whole, including the source tree the image cloned there,
# so the container reads <workspace>/src from the host instead. When that is a symlink out
# of the workspace — an oss-fuzz run over a local path, where HarnessBuddy links the user's
# tree rather than copies it — the link target must be mounted too, or it dangles in the
# container and the build stops on a missing source directory. Bound at its own path, which
# is what the symlink resolves to.
if [[ -L src ]]; then
  source_target="$(readlink -f src)"
  if [[ -n $source_target ]]; then
    mounts+=(-v "$source_target:$source_target")
  fi
fi

status=0
docker run --rm "${mounts[@]}" -w /src --entrypoint bash \
  "$tag" /usr/local/bin/check_build.sh /src || status=$?

# The container runs as root, so install/, build/, and out/ come back root-owned and the
# next run cannot delete them. -h keeps this to the symlink above rather than the source
# tree it points at, which belongs to the user.
docker run --rm "${mounts[@]}" --entrypoint chown \
  "$tag" -Rh "$(id -u):$(id -g)" /src > /dev/null 2>&1 || true

if [[ $status -ne 0 ]]; then
  echo "FAILED: check_build.sh did not succeed inside the container" >&2
  exit 1
fi

echo "OK: check_build.sh succeeded inside the container"
