from __future__ import annotations

import shutil
import stat
from pathlib import Path

from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    GenerationResult,
)
from harnessbuddy.library_builder.oss_fuzz.workspace import APT_INSTALL_PREFIX
from harnessbuddy.library_builder.scripts import write_default_fuzzer


def generate_oss_fuzz(
    analysis: AnalysisResult,
    output_path: Path,
    exploration: BuildExplorationResult | None = None,
) -> GenerationResult:
    """Generate a complete oss-fuzz project skeleton from a static analysis result.

    When exploration ran in the oss-fuzz environment, the workspace it validated
    already contains project.yaml/Dockerfile/build.sh/build_library.sh/
    compile_harness.sh/compile_harnesses.sh/harness_source/* — this copies those already-validated
    files verbatim (FR-005) instead of re-deriving them, so the shipped project is
    exactly what was validated (including any agent-applied fixes). The Dockerfile
    additionally has its exploration-only "bear" apt dependency stripped, since the
    shipped variant must not depend on a HarnessBuddy-only tool (research.md #5).
    """
    output_path.mkdir(parents=True)
    (output_path / "harness_source").mkdir()

    validated_workspace = _validated_oss_fuzz_workspace(exploration)
    harness_source_dir = output_path / "harness_source"
    copied_harness_source = _copy_harness_source(output_path, validated_workspace)

    files: list[Path] = [
        _copy_project_yaml(output_path, validated_workspace),
        _copy_dockerfile(output_path, validated_workspace),
        _copy_build_sh(output_path, validated_workspace),
        _copy_build_library_sh(output_path, validated_workspace),
        _copy_compile_harness_sh(output_path, validated_workspace),
        _copy_compile_harnesses_sh(output_path, validated_workspace),
        *copied_harness_source,
    ]
    if not any(harness_source_dir.glob("default_fuzzer.*")):
        # Validated workspace's harness_source had no discovered default_fuzzer.{c,cc}
        # (e.g. empty harness_source) — synthesize a fresh stub.
        files.append(write_default_fuzzer(harness_source_dir, analysis.language))

    return GenerationResult(
        project_name=analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _validated_oss_fuzz_workspace(exploration: BuildExplorationResult | None) -> Path | None:
    """The workspace directory validated during this run, if exploration ran in the
    oss-fuzz environment — that workspace is itself a real, buildable OSS-Fuzz project
    (User Story 2) and is the single source of truth for the files it already contains.
    """
    if (
        exploration is None
        or exploration.script_path is None
        or exploration.environment is not Environment.OSS_FUZZ
    ):
        return None
    return exploration.script_path.parent


def _require_workspace_file(validated_workspace: Path | None, name: str) -> Path:
    """The validated workspace directory, having confirmed it (and `name` within it)
    exists — every _copy_* helper below needs a fully materialized workspace to copy
    from, since no template-rendering fallback exists anymore (FR-005)."""
    if validated_workspace is None or not (validated_workspace / name).exists():
        raise FileNotFoundError(f"expected to find {name} in workspace {validated_workspace}")
    return validated_workspace / name


def _copy_project_yaml(output_path: Path, validated_workspace: Path | None) -> Path:
    path = output_path / "project.yaml"
    shutil.copy2(_require_workspace_file(validated_workspace, "project.yaml"), path)
    return path


def _copy_dockerfile(output_path: Path, validated_workspace: Path | None) -> Path:
    path = output_path / "Dockerfile"
    shutil.copy2(_require_workspace_file(validated_workspace, "Dockerfile"), path)
    path.write_text(_strip_bear_dependency(path.read_text()))
    return path


def _strip_bear_dependency(dockerfile_content: str) -> str:
    """Drop the "bear" apt package the live workspace Dockerfile depends on for
    compile_commands.json capture during exploration (research.md #5) — bear is never
    needed by the shipped image. Operates on package tokens rather than a fixed string
    replace, since "bear" isn't always followed by a space (e.g. when it's the only or
    last package in the list, immediately followed by a newline)."""
    lines = []
    for line in dockerfile_content.splitlines(keepends=True):
        if not line.startswith(APT_INSTALL_PREFIX):
            lines.append(line)
            continue
        packages = [pkg for pkg in line[len(APT_INSTALL_PREFIX) :].split() if pkg != "bear"]
        if packages:
            lines.append(f"{APT_INSTALL_PREFIX} {' '.join(packages)}\n")
        # else: bear was the only dependency — drop the now-empty install line entirely.
    return "".join(lines)


def _copy_build_sh(output_path: Path, validated_workspace: Path | None) -> Path:
    path = output_path / "build.sh"
    shutil.copy2(_require_workspace_file(validated_workspace, "build.sh"), path)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _copy_build_library_sh(output_path: Path, validated_workspace: Path | None) -> Path:
    """Copy the explored (possibly agent-fixed) build_library.sh from the validated
    workspace verbatim."""
    path = output_path / "build_library.sh"
    shutil.copy2(_require_workspace_file(validated_workspace, "build_library.sh"), path)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _copy_compile_harnesses_sh(output_path: Path, validated_workspace: Path | None) -> Path:
    """Copy the validated (possibly agent-fixed, possibly still a stub)
    compile_harnesses.sh from the validated workspace verbatim."""
    path = output_path / "compile_harnesses.sh"
    shutil.copy2(_require_workspace_file(validated_workspace, "compile_harnesses.sh"), path)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _copy_compile_harness_sh(output_path: Path, validated_workspace: Path | None) -> Path:
    """Copy the validated compiler that accepts one source and one output path."""
    path = output_path / "compile_harness.sh"
    shutil.copy2(_require_workspace_file(validated_workspace, "compile_harness.sh"), path)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _copy_harness_source(output_path: Path, validated_workspace: Path | None) -> list[Path]:
    """Copy harness_source/* from the validated workspace verbatim, including its
    default_fuzzer.{c,cc} — whichever extension harness-link discovery settled on."""
    if validated_workspace is None:
        return []
    src_dir = validated_workspace / "harness_source"
    if not src_dir.exists():
        return []
    copied: list[Path] = []
    for entry in sorted(src_dir.iterdir()):
        dest = output_path / "harness_source" / entry.name
        shutil.copy2(entry, dest)
        copied.append(dest)
    return copied
