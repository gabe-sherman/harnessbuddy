from __future__ import annotations

import stat
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    GenerationResult,
    OutputDirectoryExistsError,
)
from harnessbuddy.library_builder.scripts import build_library_script

_DEFAULT_FUZZER_C = (
    "#include <stddef.h>\n"
    "#include <stdint.h>\n"
    "\n"
    "int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n"
    "    // TODO: Add fuzzing logic using the target library.\n"
    "    return 0;\n"
    "}\n"
)

_BUILD_HARNESS_SH = "#!/bin/bash\nset -euo pipefail\n\n# TODO: compile harnesses\n"


def generate_local(
    analysis: AnalysisResult,
    output_parent: Path,
    exploration: BuildExplorationResult | None = None,  # noqa: ARG001
) -> GenerationResult:
    """Generate a local build skeleton for host-native development and testing."""
    output_path = output_parent / analysis.project_name / "output" / "local"

    if output_path.exists():
        raise OutputDirectoryExistsError(output_path)

    output_path.mkdir(parents=True)
    (output_path / "harness_src").mkdir()

    files: list[Path] = [
        _write_setup_sh(output_path, analysis),
        _write_build_library_sh(output_path, analysis),
        _write_build_harness_sh(output_path),
        _write_default_fuzzer(output_path / "harness_src"),
    ]

    return GenerationResult(
        project_name=analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _write_setup_sh(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "setup.sh"
    lines = [
        "#!/bin/bash\n",
        "set -euo pipefail\n",
        "\n",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n',
        "\n",
        f"git clone {analysis.clone_url} \"$SCRIPT_DIR/src\"\n",
    ]
    if analysis.repo_ref is not None:
        lines.append(f'git -C "$SCRIPT_DIR/src" checkout {analysis.repo_ref}\n')
    lines.append("\n")
    if analysis.system_packages:
        pkgs = " ".join(analysis.system_packages)
        lines.append(f"apt-get install -y --no-install-recommends {pkgs}\n")
    else:
        lines.append("# TODO: install build dependencies for this library\n")
    path.write_text("".join(lines))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_build_library_sh(output_path: Path, analysis: AnalysisResult) -> Path:
    path = output_path / "build_library.sh"
    path.write_text(
        build_library_script(
            analysis.build_system,
            source_dir="$SCRIPT_DIR/src",
            build_dir="$SCRIPT_DIR/build",
            install_dir="$SCRIPT_DIR/install",
            env_file="$SCRIPT_DIR/build.env",
            host_fallbacks=True,
            autotools_setup=analysis.autotools_setup,
        )
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_build_harness_sh(output_path: Path) -> Path:
    path = output_path / "build_harness.sh"
    path.write_text(_BUILD_HARNESS_SH)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_default_fuzzer(harness_dir: Path) -> Path:
    path = harness_dir / "default_fuzzer.c"
    path.write_text(_DEFAULT_FUZZER_C)
    return path
