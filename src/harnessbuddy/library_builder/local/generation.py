from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path

from harnessbuddy.library_builder.environments.base import Environment
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

_COMPILE_HARNESSES_SH_STUB = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    "\n"
    'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    "\n"
    'CC="${CC:-clang}"\n'
    'CXX="${CXX:-clang++}"\n'
    'CFLAGS="${CFLAGS:--fsanitize=fuzzer}"\n'
    'CXXFLAGS="${CXXFLAGS:--fsanitize=fuzzer}"\n'
    "\n"
    'INSTALL_DIR="$SCRIPT_DIR/install"\n'
    'HARNESS_DIR="$SCRIPT_DIR/harness_src"\n'
    'OUT_DIR="$SCRIPT_DIR/out"\n'
    'mkdir -p "$OUT_DIR"\n'
    "\n"
    "# TODO: add static library paths\n"
    "STATIC_LIBS=()\n"
    "EXTRA_LINK_FLAGS=\n"
    "\n"
    'for harness in "$HARNESS_DIR"/*; do\n'
    '  [ -f "$harness" ] || continue\n'
    '  name="$(basename "$harness")"\n'
    '  output="${name%.*}"\n'
    '  case "$harness" in\n'
    "    *.c)\n"
    '      "$CC" $CFLAGS "-I$INSTALL_DIR/include" "$harness" \\\n'
    '        "${STATIC_LIBS[@]-}" $EXTRA_LINK_FLAGS -o "$OUT_DIR/$output"\n'
    "      ;;\n"
    "    *.cc|*.cpp|*.cxx)\n"
    '      "$CXX" $CXXFLAGS "-I$INSTALL_DIR/include" "$harness" \\\n'
    '        "${STATIC_LIBS[@]-}" $EXTRA_LINK_FLAGS -o "$OUT_DIR/$output"\n'
    "      ;;\n"
    "  esac\n"
    "done\n"
)


def generate_local(
    analysis: AnalysisResult,
    output_path: Path,
    exploration: BuildExplorationResult | None = None,
    harness_exploration: HarnessExplorationResult | None = None,
    brew_packages: list[str] | None = None,
) -> GenerationResult:
    """Generate a local build skeleton for host-native development and testing.

    When exploration ran in the local environment, the workspace it validated already
    contains build_library.sh/compile_harnesses.sh/harness_src/* — this copies those
    already-validated files verbatim (FR-005) instead of re-deriving them. setup.sh has
    no workspace equivalent (exploration already operates on a pre-cloned repository), so
    it is always written fresh.
    """
    output_path.mkdir(parents=True)
    (output_path / "harness_src").mkdir()

    validated_workspace = _validated_local_workspace(exploration)
    harness_src_dir = output_path / "harness_src"
    copied_harness_src = _copy_harness_src(output_path, validated_workspace)

    files: list[Path] = [
        _write_setup_sh(output_path, analysis, brew_packages=brew_packages or []),
        _write_build_library_sh(output_path, analysis, exploration, validated_workspace),
        _write_compile_harnesses_sh(output_path, harness_exploration, validated_workspace),
        *copied_harness_src,
    ]
    if not any(harness_src_dir.glob("default_fuzzer.*")):
        # No validated workspace to copy a discovered default_fuzzer.{c,cc} from (e.g.
        # unknown build system, or exploration never ran) — synthesize a fresh stub.
        files.append(write_default_fuzzer(harness_src_dir, analysis.language))

    return GenerationResult(
        project_name=analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _validated_local_workspace(exploration: BuildExplorationResult | None) -> Path | None:
    """The workspace directory validated during this run, if exploration ran in the
    local environment — the single source of truth for the files it already contains."""
    if (
        exploration is None
        or exploration.script_path is None
        or exploration.environment is not Environment.LOCAL
    ):
        return None
    return exploration.script_path.parent


def _copy_harness_src(output_path: Path, validated_workspace: Path | None) -> list[Path]:
    """Copy harness_src/* from the validated workspace verbatim, including its
    default_fuzzer.{c,cc} — whichever extension harness-link discovery settled on."""
    if validated_workspace is None:
        return []
    src_dir = validated_workspace / "harness_src"
    if not src_dir.exists():
        return []
    copied: list[Path] = []
    for entry in sorted(src_dir.iterdir()):
        dest = output_path / "harness_src" / entry.name
        shutil.copy2(entry, dest)
        copied.append(dest)
    return copied


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
    output_path: Path,
    analysis: AnalysisResult,
    exploration: BuildExplorationResult | None,
    validated_workspace: Path | None,
) -> Path:
    """Write build_library.sh, copying the explored (possibly agent-fixed) script from
    the validated workspace when available. Falls back to the static template only when
    no exploration was run in this environment at all.
    """
    path = output_path / "build_library.sh"
    if validated_workspace is not None and (validated_workspace / "build_library.sh").exists():
        shutil.copy2(validated_workspace / "build_library.sh", path)
    elif (
        exploration is not None
        and exploration.script_path is not None
        and exploration.environment is Environment.LOCAL
    ):
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
    output_path: Path,
    harness: HarnessExplorationResult | None,
    validated_workspace: Path | None,
) -> Path:
    """Write compile_harnesses.sh, copying the validated (possibly agent-fixed, possibly
    still a stub) script from the validated workspace when available. Falls back to the
    regenerated template only when no exploration ran in this environment at all.
    """
    path = output_path / "compile_harnesses.sh"
    if validated_workspace is not None and (validated_workspace / "compile_harnesses.sh").exists():
        shutil.copy2(validated_workspace / "compile_harnesses.sh", path)
    elif (
        harness is not None
        and harness.script_path is not None
        and harness.environment is Environment.LOCAL
    ):
        shutil.copy2(harness.script_path, path)
    else:
        content = (
            build_harness_script(harness) if harness is not None else _COMPILE_HARNESSES_SH_STUB
        )
        path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path
