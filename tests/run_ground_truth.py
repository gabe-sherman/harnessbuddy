"""Ground-truth sanity check: run the CLI against real-world libraries, then verify each
generated oss-fuzz project builds and compiles a real, hand-written fuzzing harness.

Not a pytest test — a manual dev script, run with:

    uv run python tests/run_ground_truth.py

Drop curated harness sources into tests/real_harnesses/<project_name>_*.{c,cc,cpp}; only
libraries with a matching harness get the Docker build/compile check. Requires Docker.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from harnessbuddy.cli import main
from harnessbuddy.library_builder.models import BuildSystem

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path("ground_truth_test_output")
_REAL_HARNESSES_DIR = Path(__file__).parent / "real_harnesses"


@dataclass(frozen=True)
class LibSpec:
    url: str
    project_name: str
    build_system: BuildSystem
    builds_deterministically: bool | None


LIBS = [
    # LibSpec("https://github.com/madler/zlib.git", "zlib", BuildSystem.CMAKE, True),
    # LibSpec(
    #     "https://github.com/fukuchi/libqrencode.git", "libqrencode", BuildSystem.CMAKE, False
    # ),
    # LibSpec("https://gitlab.com/libtiff/libtiff.git", "libtiff", BuildSystem.CMAKE, True),
    LibSpec("https://github.com/curl/curl.git", "curl", BuildSystem.CMAKE, False),
    # LibSpec("https://github.com/lvgl/lvgl.git", "lvgl", BuildSystem.CMAKE, None),
    # LibSpec("https://github.com/Mbed-TLS/mbedtls.git", "mbedtls", BuildSystem.CMAKE, None),
    LibSpec("https://github.com/ImageMagick/ImageMagick", "imagemagick", BuildSystem.CMAKE, None),
    LibSpec("https://github.com/htop-dev/htop.git", "htop", BuildSystem.AUTOTOOLS, None),
    LibSpec("https://github.com/libusb/libusb", "libusb", BuildSystem.AUTOTOOLS, None),
    LibSpec(
        "https://github.com/openvenues/libpostal.git", "libpostal", BuildSystem.AUTOTOOLS, None
    ),
]

_AGENT = "claude"


def _generate(lib: LibSpec) -> bool:
    """Run `harnessbuddy generate` for lib into output/<project_name>, returning success."""
    project_output = _OUTPUT_DIR / lib.project_name
    if project_output.exists():
        shutil.rmtree(project_output)
    rc = main(["generate", lib.url, "--agent", _AGENT, "--output", str(project_output)])
    return rc == 0


def _docker_build_and_compile(project_name: str) -> bool:
    """Drop real harness sources into the generated oss-fuzz project and verify they
    build and compile inside the OSS-Fuzz Docker image.

    Returns True when there's no ground-truth harness to check (nothing to fail) or when
    the Docker build and compile both succeed.
    """
    harnesses = list(_REAL_HARNESSES_DIR.glob(f"{project_name}*"))
    if not harnesses:
        logger.warning("no ground-truth harness for %s, skipping docker check", project_name)
        return True
    oss_fuzz_dir = _OUTPUT_DIR / project_name / "oss-fuzz"
    harness_dir = oss_fuzz_dir / "harness_source"
    ext = "c"
    for harness in harnesses:
        ext = harness.suffix
        shutil.copy2(harness, harness_dir / harness.name)

    tag = f"{project_name}:gt-test"
    build = subprocess.run(["docker", "build", "-t", tag, "."], cwd=oss_fuzz_dir, check=False)
    if build.returncode != 0:
        logger.error("docker build failed for %s", project_name)
        return False

    # Compile the harness and fuzz it for 2 seconds
    result = subprocess.run(
        [
            "docker",
            "run",
            "-e",
            f"FUZZING_LANGUAGE={ext}",
            "--rm",
            "--entrypoint",
            "bash",
            tag,
            "-c",
            f"compile && /out/{project_name} -max_total_time=2",
        ],
        cwd=oss_fuzz_dir,
        check=False,
    )
    if result.returncode != 0:
        logger.error("harness compile failed for %s", project_name)
        return False

    return True


def run_ground_truth() -> None:
    """Generate and Docker-verify every library in LIBS, printing a pass/fail summary."""
    failures = []
    for lib in LIBS:
        print(f"=== {lib.project_name} ===")
        if not _generate(lib):
            print(f"WARNING: generate failed for {lib.project_name}")
            failures.append(lib.project_name)
            continue
        if not _docker_build_and_compile(lib.project_name):
            failures.append(lib.project_name)

    if failures:
        print(f"FAILED: {', '.join(failures)}")
    else:
        print("All libraries passed the ground-truth check.")


if __name__ == "__main__":
    run_ground_truth()
