from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildPaths,
    GenerationResult,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.scripts import (
    build_harness_script,
    build_library_script,
    write_default_fuzzer,
)

_COMPILE_HARNESSES_SH_STUB = "#!/bin/bash\nset -euo pipefail\n\n# TODO: compile harnesses\n"


def generate_local(
    analysis: AnalysisResult,
    output_path: Path,
    exploration: BuildExplorationResult | None = None,
    harness_exploration: HarnessExplorationResult | None = None,
    brew_packages: list[str] | None = None,
) -> GenerationResult:
    """Generate a local build skeleton for host-native development and testing."""
    output_path.mkdir(parents=True)
    (output_path / "harness_src").mkdir()

    files: list[Path] = [
        _write_setup_sh(output_path, analysis, brew_packages=brew_packages or []),
        _write_build_library_sh(output_path, analysis, exploration),
        _write_compile_harnesses_sh(output_path, harness_exploration),
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


def _write_build_library_sh(
    output_path: Path, analysis: AnalysisResult, exploration: BuildExplorationResult | None
) -> Path:
    """Write build_library.sh, reusing the explored (possibly agent-fixed) script when available.

    The explored script already uses $SCRIPT_DIR-relative paths matching this output
    layout (src/, build/, install/), so copying it verbatim preserves any fixes an
    agent made during exploration. Falls back to the static template when no
    exploration was run or its script isn't safe to copy (e.g. a non-standard source layout).
    """
    path = output_path / "build_library.sh"
    if exploration is not None and exploration.script_path is not None:
        shutil.copy2(exploration.script_path, path)
    else:
        path.write_text(
            build_library_script(
                analysis.build_system,
                BuildPaths(
                    source_dir="$SCRIPT_DIR/src",
                    build_dir="$SCRIPT_DIR/build",
                    install_dir="$SCRIPT_DIR/install",
                ),
                host_fallbacks=True,
                autotools_setup=analysis.autotools_setup,
            )
        )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_compile_harnesses_sh(
    output_path: Path, harness: HarnessExplorationResult | None
) -> Path:
    """Write compile_harnesses.sh, reusing the validated (possibly agent-fixed)
    script when available.

    The validated script already uses $SCRIPT_DIR-relative paths matching this output
    layout (install/, harness_src/, out/), so copying it verbatim preserves any fixes
    made while resolving transitive link flags. Falls back to the static template
    (regenerated from static_libs/transitive_link_flags) when no exploration was run.
    """
    path = output_path / "compile_harnesses.sh"
    if harness is not None and harness.script_path is not None:
        shutil.copy2(harness.script_path, path)
    else:
        content = (
            build_harness_script(harness) if harness is not None else _COMPILE_HARNESSES_SH_STUB
        )
        path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
