from __future__ import annotations

import re
from pathlib import Path

from harnessbuddy.core.agent_stream import (
    AgentRunSummary,
    AgentStreamResult,
    format_agent_summary,
    run_agent_streaming,
    write_agent_report,
)
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.exploration import (
    _validate_install_artifacts,
    is_standard_source_layout,
    read_agent_report,
)
from harnessbuddy.library_builder.harness_explorer import (
    _extract_missing_system_libs,
    _validate_harness_artifacts,
    reparse_lib_paths,
    reparse_link_config,
)
from harnessbuddy.library_builder.models import (
    AgentReport,
    AnalysisResult,
    BuildExplorationResult,
    HarnessExplorationResult,
    HarnessPaths,
)

_SKILL_PATH: Path = (
    Path(__file__).parent.parent.parent.parent / "agents" / "library_builder" / "SKILL.md"
)

_INLINE_INSTRUCTIONS: str = (
    "Fix the failed C/C++ static library build. "
    "Modify build_library.sh in the work directory so that "
    "install/lib/*.a and install/include/* are populated after running it."
)

_HARNESS_SKILL_PATH: Path = (
    Path(__file__).parent.parent.parent.parent / "agents" / "harness_builder" / "SKILL.md"
)

_HARNESS_INLINE_INSTRUCTIONS: str = (
    "Fix the failed harness link probe. Modify compile_harnesses.sh in the work directory "
    "so that compiling and linking the probe harness against the installed static "
    "libraries succeeds and produces a binary in out/."
)

_AGENTS_SCRIPTS_DIR: Path = Path(__file__).parent.parent.parent.parent / "agents" / "scripts"


def _verification_command(
    environment: Environment,
    *,
    workdir: Path,
    project_name: str,
    oss_fuzz_project_dir: Path | None,
) -> str:
    """The concrete command (FR-009) that proves a fix works in the selected environment."""
    if environment is Environment.OSS_FUZZ:
        project_dir = oss_fuzz_project_dir if oss_fuzz_project_dir is not None else workdir
        script = _AGENTS_SCRIPTS_DIR / "check_docker_build.sh"
        return f"bash {script} {project_dir} {project_name}"
    script = _AGENTS_SCRIPTS_DIR / "check_local_build.sh"
    return f"bash {script} {workdir}"

_ACTION_REQUIRED = "ACTION REQUIRED"

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

    def __init__(self, output: str, summary: AgentRunSummary, report: AgentReport | None) -> None:
        super().__init__(
            f"Agent requires user action. Review the output below, resolve the issue, "
            f"then retry.\n\n{output}"
        )
        self.output = output
        self.summary = summary
        self.report = report


class LLMBudgetError(Exception):
    """Agent exited because it hit a usage limit (Claude 5-hour limit or Codex quota)."""

    def __init__(self, output: str, summary: AgentRunSummary, report: AgentReport | None) -> None:
        super().__init__(
            f"Agent hit a usage or rate limit. Review the output below and retry later.\n\n{output}"
        )
        self.output = output
        self.summary = summary
        self.report = report


def _raise_for_agent_failure(
    exit_code: int, combined_output: str, summary: AgentRunSummary, report: AgentReport | None
) -> None:
    """Raise LLMBudgetError or BuildFailureError if agent output signals either condition."""
    if exit_code == 0:
        return
    if _BUDGET_PATTERN.search(combined_output):
        raise LLMBudgetError(combined_output, summary, report)
    if _ACTION_REQUIRED in combined_output:
        raise BuildFailureError(combined_output, summary, report)


def _determine_outcome(exit_code: int, combined_text: str) -> str:
    """Classify an agent invocation's outcome for the persisted/printed summary."""
    if _BUDGET_PATTERN.search(combined_text):
        return "budget_limited"
    if exit_code == -1:
        return "timed_out"
    return "succeeded" if exit_code == 0 else "failed"


def _report_agent_run(report_path: Path, tool: str, result: AgentStreamResult) -> AgentRunSummary:
    """Write the persisted transcript+summary report and print the summary to the terminal."""
    summary = AgentRunSummary(
        backend=tool,
        outcome=_determine_outcome(result.exit_code, result.combined_text),
        duration_seconds=result.duration_seconds,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    write_agent_report(report_path, result.combined_text, summary)
    print(format_agent_summary(summary))
    return summary


def build_library_prompt(
    analysis: AnalysisResult,
    exploration: BuildExplorationResult,
    workdir: Path,
    environment: Environment,
    *,
    oss_fuzz_project_dir: Path | None = None,
) -> str:
    """Construct a Claude prompt for diagnosing and fixing a failed library build."""
    instructions = _SKILL_PATH.read_text() if _SKILL_PATH.exists() else _INLINE_INSTRUCTIONS
    stdout_tail = "\n".join(exploration.stdout.splitlines()[-200:])
    verify_command = _verification_command(
        environment,
        workdir=workdir,
        project_name=analysis.project_name,
        oss_fuzz_project_dir=oss_fuzz_project_dir,
    )
    return (
        f"{instructions}\n\n"
        f"## Build failure context\n\n"
        f"- source_dir: {analysis.source_path}\n"
        f"- build_system: {analysis.build_system.value}\n"
        f"- command: {' '.join(exploration.command)}\n"
        f"- exit_code: {exploration.exit_code}\n"
        f"- build_library.sh: {workdir / 'build_library.sh'}\n"
        f"- install_dir: {workdir / 'install'}\n"
        f"- build_dir: {workdir / 'build'}\n\n"
        f"### Build output (last 200 lines)\n\n"
        f"```\n{stdout_tail}\n```\n\n"
        f"### Verification\n\n"
        f"After applying a fix, verify it works by running this exact command:\n\n"
        f"    {verify_command}\n"
    )


def construct_claude_command(prompt: str) -> list[str]:
    cmd = [
        "claude",
        "--print",
        "--permission-mode",
        "auto",
        "--output-format=stream-json",
        "--verbose",
        prompt,
    ]
    return cmd


def construct_codex_command(prompt: str) -> list[str]:
    cmd = ["codex", "exec", "--sandbox", "workspace-write", "--json", prompt]
    return cmd


def invoke_library_builder_agent(  # noqa: PLR0913 -- public API; all params are distinct required inputs
    analysis: AnalysisResult,
    exploration: BuildExplorationResult,
    workdir: Path,
    *,
    tool: str = "claude",
    timeout: int = 600,
    environment: Environment = Environment.LOCAL,
    oss_fuzz_project_dir: Path | None = None,
) -> BuildExplorationResult:
    """Spawn a Claude Code or Codex subprocess to diagnose and fix a failed build.

    Streams agent output to the terminal. CWD is set to workdir, where build_library.sh
    lives; the agent can still read and modify the repo's build files via source_dir.
    """
    prompt = build_library_prompt(
        analysis, exploration, workdir, environment, oss_fuzz_project_dir=oss_fuzz_project_dir
    )
    if tool == "claude":
        cmd = construct_claude_command(prompt)
    elif tool == "codex":
        cmd = construct_codex_command(prompt)
    else:
        raise ValueError(f"unknown agent tool: {tool!r}")

    result = run_agent_streaming(cmd, workdir, timeout, tool)
    summary = _report_agent_run(workdir / "agent_library_build.log", tool, result)
    report = read_agent_report(workdir)
    _raise_for_agent_failure(result.exit_code, result.combined_text, summary, report)

    succeeded = result.exit_code == 0
    stderr = ""
    if succeeded:
        validation_errors = _validate_install_artifacts(workdir / "install")
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)

    return BuildExplorationResult(
        build_system=analysis.build_system,
        succeeded=succeeded,
        command=cmd,
        stdout=result.combined_text,
        stderr=stderr,
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        llm_used=True,
        script_path=(
            workdir / "build_library.sh" if is_standard_source_layout(analysis, workdir) else None
        ),
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        transcript_path=workdir / "agent_library_build.log",
        agent_summary=report.summary if report else None,
        missing_apt_packages=report.missing_apt_packages if report else [],
        missing_brew_packages=report.missing_brew_packages if report else [],
        extra_include_paths=report.extra_include_paths if report else [],
        extra_library_paths=report.extra_library_paths if report else [],
        environment=environment,
    )


def build_harness_prompt(  # noqa: PLR0913 -- public API; all params are distinct required inputs
    analysis: AnalysisResult,
    harness: HarnessExplorationResult,
    install_dir: Path,
    workdir: Path,
    environment: Environment,
    *,
    oss_fuzz_project_dir: Path | None = None,
) -> str:
    """Construct a Claude prompt for diagnosing and fixing a failed harness link probe."""
    instructions = (
        _HARNESS_SKILL_PATH.read_text()
        if _HARNESS_SKILL_PATH.exists()
        else _HARNESS_INLINE_INSTRUCTIONS
    )
    stderr_tail = "\n".join(harness.stderr.splitlines()[-200:])
    harness_dir_name = "harness_source" if environment is Environment.OSS_FUZZ else "harness_src"
    verify_command = _verification_command(
        environment,
        workdir=workdir,
        project_name=analysis.project_name,
        oss_fuzz_project_dir=oss_fuzz_project_dir,
    )
    return (
        f"{instructions}\n\n"
        f"## Harness compilation failure context\n\n"
        f"- source_dir: {analysis.source_path}\n"
        f"- install_dir: {install_dir}\n"
        f"- workdir: {workdir}\n"
        f"- compile_harnesses.sh: {workdir / 'compile_harnesses.sh'}\n"
        f"- harness_src: {workdir / harness_dir_name}\n"
        f"- static_libs: {', '.join(p.name for p in harness.static_libs) or '(none)'}\n"
        f"- auto_resolved_link_flags: {' '.join(harness.transitive_link_flags) or '(none)'}\n"
        f"- missing_system_libs (linker-reported): "
        f"{', '.join(harness.missing_system_libs) or '(none detected)'}\n"
        f"- exit_code: {harness.exit_code}\n\n"
        f"### Linker/compiler output (last 200 lines of stderr)\n\n"
        f"```\n{stderr_tail}\n```\n\n"
        f"### Verification\n\n"
        f"After applying a fix, verify it works by running this exact command:\n\n"
        f"    {verify_command}\n"
    )


def invoke_harness_builder_agent(  # noqa: PLR0913 -- public API; all params are distinct required inputs
    analysis: AnalysisResult,
    harness: HarnessExplorationResult,
    paths: HarnessPaths,
    *,
    tool: str = "claude",
    timeout: int = 600,
    environment: Environment = Environment.LOCAL,
    oss_fuzz_project_dir: Path | None = None,
) -> HarnessExplorationResult:
    """Spawn a Claude Code or Codex subprocess to diagnose and fix a failed harness link probe.

    Streams agent output to the terminal. CWD is set to paths.workdir so the agent can read
    and modify compile_harnesses.sh and harness_src/ directly.
    """
    prompt = build_harness_prompt(
        analysis,
        harness,
        paths.install_dir,
        paths.workdir,
        environment,
        oss_fuzz_project_dir=oss_fuzz_project_dir,
    )
    if tool == "claude":
        cmd = construct_claude_command(prompt)
    elif tool == "codex":
        cmd = construct_codex_command(prompt)
    else:
        raise ValueError(f"unknown agent tool: {tool!r}")

    result = run_agent_streaming(cmd, paths.workdir, timeout, tool)
    summary = _report_agent_run(paths.workdir / "agent_harness_build.log", tool, result)
    report = read_agent_report(paths.workdir)
    _raise_for_agent_failure(result.exit_code, result.combined_text, summary, report)

    succeeded = result.exit_code == 0
    stderr = ""
    missing_system_libs = harness.missing_system_libs
    static_libs = harness.static_libs
    transitive_link_flags = harness.transitive_link_flags
    extra_library_paths = harness.extra_library_paths
    script_path = paths.workdir / "compile_harnesses.sh"
    if succeeded:
        validation_errors = _validate_harness_artifacts(paths.workdir)
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)
        else:
            # The agent edits STATIC_LIBS/EXTRA_LINK_FLAGS/EXTRA_LIB_PATHS in the script
            # directly rather than through us, so re-derive them instead of trusting the
            # pre-fix values.
            script_text = script_path.read_text()
            static_libs, transitive_link_flags = reparse_link_config(
                script_text, static_libs, transitive_link_flags
            )
            extra_library_paths = reparse_lib_paths(script_text, extra_library_paths)
    if not succeeded:
        # A validation-only failure (agent claimed done but out/ is empty) carries no new
        # linker stderr to re-parse, so preserve the pre-agent list rather than discarding it.
        missing_system_libs = list(
            dict.fromkeys(missing_system_libs + _extract_missing_system_libs(stderr))
        )

    # The agent reports the bare library name it couldn't resolve (e.g. "ldap") alongside
    # the actual apt/brew package names below. Add the matching -l flag ourselves so the
    # generated script attempts the link once the package is installed.
    report_missing_libs = report.missing_libs if report else []
    if report_missing_libs:
        missing_system_libs = list(dict.fromkeys(missing_system_libs + report_missing_libs))
        new_flags = [f"-l{lib}" for lib in report_missing_libs]
        transitive_link_flags = list(dict.fromkeys(transitive_link_flags + new_flags))

    report_library_paths = report.extra_library_paths if report else []
    extra_library_paths = list(dict.fromkeys(extra_library_paths + report_library_paths))

    return HarnessExplorationResult(
        succeeded=succeeded,
        command=cmd,
        static_libs=static_libs,
        include_dir=harness.include_dir,
        transitive_link_flags=transitive_link_flags,
        stdout=result.combined_text,
        stderr=stderr,
        exit_code=result.exit_code,
        missing_system_libs=missing_system_libs if not succeeded else [],
        llm_used=True,
        script_path=script_path if succeeded else None,
        duration_seconds=result.duration_seconds,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        transcript_path=paths.workdir / "agent_harness_build.log",
        agent_summary=report.summary if report else None,
        missing_apt_packages=report.missing_apt_packages if report else [],
        missing_brew_packages=report.missing_brew_packages if report else [],
        extra_include_paths=report.extra_include_paths if report else [],
        extra_library_paths=extra_library_paths,
        environment=environment,
    )
