from __future__ import annotations

import stat
import sys
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    GenerationResult,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.scripts import (
    build_harness_script,
    build_library_script,
    write_default_fuzzer,
)

_BUILD_HARNESS_SH_STUB = "#!/bin/bash\nset -euo pipefail\n\n# TODO: compile harnesses\n"


def generate_local(
    analysis: AnalysisResult,
    output_path: Path,
    exploration: BuildExplorationResult | None = None,  # noqa: ARG001
    harness_exploration: HarnessExplorationResult | None = None,
    brew_packages: list[str] | None = None,
) -> GenerationResult:
    """Generate a local build skeleton for host-native development and testing."""
    output_path.mkdir(parents=True)
    (output_path / "harness_src").mkdir()

    files: list[Path] = [
        _write_setup_sh(output_path, analysis, brew_packages=brew_packages or []),
        _write_build_library_sh(output_path, analysis),
        _write_build_harness_sh(output_path, harness_exploration),
        write_default_fuzzer(output_path / "harness_src", analysis),
    ]

    return GenerationResult(
        project_name=analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _write_setup_sh(
    output_path: Path, analysis: AnalysisResult, *, brew_packages: list[str]
) -> Path:
    path = output_path / "setup.sh"
    lines = [
        "#!/bin/bash\n",
        "set -euo pipefail\n",
        "\n",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n',
        "\n",
        f'git clone {analysis.clone_url} "$SCRIPT_DIR/src"\n',
    ]
    if analysis.repo_ref is not None:
        lines.append(f'git -C "$SCRIPT_DIR/src" checkout {analysis.repo_ref}\n')
    lines.append("\n")
    if sys.platform == "darwin" and brew_packages:
        pkgs = " ".join(brew_packages)
        lines.append(f"brew install {pkgs}\n")
    elif analysis.system_packages:
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


def _write_build_harness_sh(output_path: Path, harness: HarnessExplorationResult | None) -> Path:
    path = output_path / "build_harness.sh"
    content = build_harness_script(harness) if harness is not None else _BUILD_HARNESS_SH_STUB
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
