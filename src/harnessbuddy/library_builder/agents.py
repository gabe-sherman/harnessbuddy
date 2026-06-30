from __future__ import annotations

import re
from pathlib import Path

from harnessbuddy.core.subprocesses import run_command_streaming
from harnessbuddy.library_builder.exploration import _validate_install_artifacts
from harnessbuddy.library_builder.harness_explorer import (
    _extract_missing_system_libs,
    _validate_harness_artifacts,
)
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    HarnessExplorationResult,
)

_SKILL_PATH: Path = (
    Path(__file__).parent.parent.parent.parent / "agents" / "library_builder" / "SKILL.md"
)

_INLINE_INSTRUCTIONS: str = (
    "Fix the failed C/C++ static library build. "
    "Modify build_library.sh in the source directory so that "
    "install/lib/*.a and install/include/* are populated after running it."
)

_HARNESS_SKILL_PATH: Path = (
    Path(__file__).parent.parent.parent.parent / "agents" / "harness_builder" / "SKILL.md"
)

_HARNESS_INLINE_INSTRUCTIONS: str = (
    "Fix the failed harness link probe. Modify build_harness.sh in the work directory "
    "so that compiling and linking the probe harness against the installed static "
    "libraries succeeds and produces a binary in out/."
)

_ACTION_REQUIRED = "ACTION_REQUIRED"

_BUDGET_PATTERN = re.compile(
    "|".join(
        (
            # Claude: 5-hour session limit
            r"reached the 5 hour limit",
            r"session time limit",
            # Codex/OpenAI: quota and rate limit errors
            r"usage limit (?:reached|exceeded)",
            r"reached (?:your|the).{0,80}usage limit",
            r"rate limit (?:reached|exceeded)",
            r"quota (?:exceeded|reached)",
            r"exceeded your current quota",
            r"too many requests",
            r"\b429\b",
            r"try again (?:after|in) \d+",
        )
    ),
    re.IGNORECASE | re.DOTALL,
)


class BuildFailureError(Exception):
    """Agent output contained ACTION_REQUIRED, signaling a user-resolvable roadblock."""

    def __init__(self, output: str) -> None:
        super().__init__(
            f"Agent requires user action. Review the output below, resolve the issue, "
            f"then retry.\n\n{output}"
        )
        self.output = output


class LLMBudgetError(Exception):
    """Agent exited because it hit a usage limit (Claude 5-hour limit or Codex quota)."""

    def __init__(self, output: str) -> None:
        super().__init__(
            f"Agent hit a usage or rate limit. Review the output below and retry later.\n\n{output}"
        )
        self.output = output


def build_library_prompt(
    analysis: AnalysisResult,
    exploration: BuildExplorationResult,
    workdir: Path,
) -> str:
    """Construct a Claude prompt for diagnosing and fixing a failed library build."""
    instructions = _SKILL_PATH.read_text() if _SKILL_PATH.exists() else _INLINE_INSTRUCTIONS
    stdout_tail = "\n".join(exploration.stdout.splitlines()[-200:])
    return (
        f"{instructions}\n\n"
        f"## Build failure context\n\n"
        f"- source_dir: {analysis.source_path}\n"
        f"- build_system: {analysis.build_system.value}\n"
        f"- command: {' '.join(exploration.command)}\n"
        f"- exit_code: {exploration.exit_code}\n"
        f"- install_dir: {workdir / 'install'}\n"
        f"- build_dir: {workdir / 'build'}\n\n"
        f"### Build output (last 200 lines)\n\n"
        f"```\n{stdout_tail}\n```\n"
    )


def invoke_library_builder_agent(
    analysis: AnalysisResult,
    exploration: BuildExplorationResult,
    workdir: Path,
    *,
    tool: str = "claude",
    timeout: int = 600,
) -> BuildExplorationResult:
    """Spawn a Claude Code or Codex subprocess to diagnose and fix a failed build.

    Streams agent output to the terminal. CWD is set to analysis.source_path so
    the agent can read and modify the repo's build files directly.
    """
    prompt = build_library_prompt(analysis, exploration, workdir)
    if tool == "claude":
        cmd = ["claude", "--print", "--permission-mode", "auto", prompt]
    elif tool == "codex":
        cmd = ["codex", "exec", "--sandbox", "workspace-write", prompt]
    else:
        raise ValueError(f"unknown agent tool: {tool!r}")

    run_result = run_command_streaming(cmd, analysis.source_path, timeout)

    if run_result.exit_code != 0:
        combined = run_result.stdout + run_result.stderr
        if _BUDGET_PATTERN.search(combined):
            raise LLMBudgetError(combined)

    succeeded = run_result.exit_code == 0
    stderr = run_result.stderr
    if succeeded:
        validation_errors = _validate_install_artifacts(workdir / "install")
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)

    return BuildExplorationResult(
        build_system=analysis.build_system,
        succeeded=succeeded,
        command=cmd,
        stdout=run_result.stdout,
        stderr=stderr,
        exit_code=run_result.exit_code,
        duration_seconds=run_result.duration_seconds,
        llm_used=True,
    )


def build_harness_prompt(
    analysis: AnalysisResult,
    harness: HarnessExplorationResult,
    install_dir: Path,
    workdir: Path,
) -> str:
    """Construct a Claude prompt for diagnosing and fixing a failed harness link probe."""
    instructions = (
        _HARNESS_SKILL_PATH.read_text()
        if _HARNESS_SKILL_PATH.exists()
        else _HARNESS_INLINE_INSTRUCTIONS
    )
    stderr_tail = "\n".join(harness.stderr.splitlines()[-200:])
    return (
        f"{instructions}\n\n"
        f"## Harness compilation failure context\n\n"
        f"- source_dir: {analysis.source_path}\n"
        f"- install_dir: {install_dir}\n"
        f"- workdir: {workdir}\n"
        f"- build_harness.sh: {workdir / 'build_harness.sh'}\n"
        f"- harness_src: {workdir / 'harness_src'}\n"
        f"- static_libs: {', '.join(p.name for p in harness.static_libs) or '(none)'}\n"
        f"- auto_resolved_link_flags: {' '.join(harness.transitive_link_flags) or '(none)'}\n"
        f"- missing_system_libs (linker-reported): "
        f"{', '.join(harness.missing_system_libs) or '(none detected)'}\n"
        f"- exit_code: {harness.exit_code}\n\n"
        f"### Linker/compiler output (last 200 lines of stderr)\n\n"
        f"```\n{stderr_tail}\n```\n"
    )


def invoke_harness_builder_agent(
    analysis: AnalysisResult,
    harness: HarnessExplorationResult,
    install_dir: Path,
    workdir: Path,
    *,
    tool: str = "claude",
    timeout: int = 600,
) -> HarnessExplorationResult:
    """Spawn a Claude Code or Codex subprocess to diagnose and fix a failed harness link probe.

    Streams agent output to the terminal. CWD is set to workdir so the agent can read
    and modify build_harness.sh and harness_src/ directly.
    """
    prompt = build_harness_prompt(analysis, harness, install_dir, workdir)
    if tool == "claude":
        cmd = ["claude", "--print", "--permission-mode", "auto", prompt]
    elif tool == "codex":
        cmd = ["codex", "exec", "--sandbox", "workspace-write", prompt]
    else:
        raise ValueError(f"unknown agent tool: {tool!r}")

    run_result = run_command_streaming(cmd, workdir, timeout)

    if run_result.exit_code != 0:
        combined = run_result.stdout + run_result.stderr
        if _BUDGET_PATTERN.search(combined):
            raise LLMBudgetError(combined)

    succeeded = run_result.exit_code == 0
    stderr = run_result.stderr
    missing_system_libs = harness.missing_system_libs
    if succeeded:
        validation_errors = _validate_harness_artifacts(workdir)
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)
    if not succeeded:
        missing_system_libs = _extract_missing_system_libs(stderr)

    return HarnessExplorationResult(
        succeeded=succeeded,
        static_libs=harness.static_libs,
        include_dir=harness.include_dir,
        transitive_link_flags=harness.transitive_link_flags,
        stdout=run_result.stdout,
        stderr=stderr,
        exit_code=run_result.exit_code,
        missing_system_libs=missing_system_libs if not succeeded else [],
        llm_used=True,
    )
