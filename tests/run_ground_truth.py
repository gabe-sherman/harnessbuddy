from __future__ import annotations

import logging
import shutil
import subprocess as sp
from dataclasses import dataclass
from pathlib import Path

from harnessbuddy.cli import (
    main,
)
from harnessbuddy.library_builder.models import (
    BuildExplorationResult,
    BuildSystem,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibSpec:
    url: str
    project_name: str
    build_system: BuildSystem
    builds_static: bool


@dataclass
class LibBuild:
    spec: LibSpec
    result: BuildExplorationResult
    workdir: Path
    source: Path


LIBS = [
    # cmake
    LibSpec("https://github.com/madler/zlib.git", "zlib", BuildSystem.CMAKE, True),
    LibSpec("https://gitlab.com/libtiff/libtiff.git", "libtiff", BuildSystem.CMAKE, True),
    LibSpec("https://github.com/HDFGroup/hdf5.git", "hdf5", BuildSystem.CMAKE, True),
    LibSpec(
        "https://github.com/c-ares/c-ares.git", "c-ares", BuildSystem.CMAKE, False
    ),  # non-canonical static flag (-DCARES_STATIC)
    LibSpec(
        "https://github.com/curl/curl.git", "curl", BuildSystem.CMAKE, False
    ),  # requires libpsl
    LibSpec("https://github.com/fukuchi/libqrencode.git", "libqrencode", BuildSystem.CMAKE, False),
    # make
    LibSpec("https://github.com/lz4/lz4.git", "lz4", BuildSystem.MAKEFILE, True),
    # autotools
    LibSpec(
        "https://github.com/libimobiledevice/libplist", "libplist", BuildSystem.AUTOTOOLS, True
    ),
    LibSpec("https://github.com/gpac/gpac.git", "gpac", BuildSystem.AUTOTOOLS, True),
    LibSpec("https://github.com/file/file.git", "file", BuildSystem.AUTOTOOLS, True),
    LibSpec("https://github.com/mm2/Little-CMS.git", "lcms", BuildSystem.AUTOTOOLS, True),
    # meson
    LibSpec(
        "https://gitlab.gnome.org/GNOME/tinysparql.git", "tinysparql", BuildSystem.MESON, False
    ),  # requires external deps
    LibSpec(
        "https://github.com/rauc/rauc.git", "rauc", BuildSystem.MESON, False
    ),  # requires dbus-1
]

_AGENT = "claude"


def check_oss_build(project_name: str):
    """Copy a real fuzzing harness stored in ./tests/real_harnesses into the oss-fuzz dir to ensure it can build real harnesses"""
    dst_dir = f"./output/{project_name}/oss-fuzz"
    for f in Path("./tests/real_harnesses").glob(f"{project_name}*"):
        if f.is_file():
            shutil.copy2(f, dst_dir / "harness_src")
    shutil.copytree("src_dir", "dst_dir", dirs_exist_ok=True)

    command = ["docker", "build", f"-t{project_name}:gt-test", "."]
    sp.run(command, cwd=f"./output/{project_name}/oss-fuzz")
    command = ["docker", "run", f"{project_name}:gt-test", "compile"]
    sp.run(command, cwd=f"./output/{project_name}/oss-fuzz")


if __name__ == "__main__":
    """Loop through all libraries and """
    results = []
    for lib in LIBS:
        result = main(["generate", str(lib.url)])
        if not result.succeeded:
            print(f"WARNING: Build for library {lib.project_name} failed")
            continue
        check_oss_build(lib.project_name)
