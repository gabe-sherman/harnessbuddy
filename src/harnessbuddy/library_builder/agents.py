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
from harnessbuddy.core.resources import skill_instructions

# Imported as modules rather than by name: these are the shared definitions of "the
# artifacts are there", and binding them here would make this module's copy diverge from
# the one every other caller (and every test) sees.
from harnessbuddy.library_builder import exploration, harness_explorer
from harnessbuddy.library_builder.environments import verification
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.exploration import read_agent_report
from harnessbuddy.library_builder.models import (
    AgentStopReason,
    AnalysisResult,
    BuildExplorationResult,
    HarnessExplorationResult,
    HarnessPaths,
)
from harnessbuddy.library_builder.scripts import HARNESS_SOURCE_DIR


def _verification_command(
    environment: Environment,
    *,
    workdir: Path,
    project_name: str,
) -> str:
    """The concrete command (FR-009) that proves a fix works in the selected environment.

    workdir is the workspace, which during exploration is already the real oss-fuzz
    project directory (research.md #1, #7) — there is no separate "eventual output"
    path to reference. Delegates to environments/verification.py so this is the exact
    same command construction the pipeline itself uses to gate pass/fail.
    """
    return " ".join(
        verification.verification_command(
            workdir, environment=environment, project_name=project_name
        )
    )


_LOCAL_PACKAGE_POLICY = (
    "workdir is a directory on this actual host machine. Do not run apt-get/dnf "
    "install yourself here, even for a package you're fully confident about — that would "
    "make an irreversible change to the user's system. Follow the missing-package steps "
    "above: disable the optional feature if possible, otherwise report it in "
    "agent_report.json and stop. This isn't specific to package managers: don't make any "
    "other global change to this host either (editing ~/.bashrc or another shell rc "
    "file, a global `pip install`/`npm install -g`, writing outside workdir) even if it "
    "would fix the build faster — nothing outside workdir is reflected in this "
    "project's output, so the fix wouldn't reproduce on a different machine. If the fix "
    "requires it, put it in a script inside workdir instead."
)

_OSS_FUZZ_PACKAGE_POLICY = (
    "workdir is an OSS-Fuzz project directory containing a Dockerfile. The verification "
    "command rebuilds this Dockerfile from scratch into a fresh, disposable container "
    "every time — it never touches this host machine. Unlike the local environment, you "
    "MAY add packages directly: append to (or add a new) `RUN apt-get install -y "
    "--no-install-recommends ...` line in workdir/Dockerfile, then re-run the verification "
    "command yourself to confirm it works. Still report every package you add via "
    "missing_apt_packages in agent_report.json — HarnessBuddy needs every package "
    "recorded even though you resolved it yourself, so the generated setup.sh installs it "
    "too. Only stop and request human action if you cannot determine a correct apt "
    "package name at all."
)


def _package_policy_note(environment: Environment) -> str:
    """The environment-specific policy on installing missing system packages.

    Both agent skills instruct stopping and reporting missing packages by default — safe
    for Environment.LOCAL, since installing there mutates the user's real machine. In
    Environment.OSS_FUZZ, missing packages are instead a Dockerfile edit into a disposable,
    from-scratch-rebuilt container the agent can verify itself, so it may resolve those
    directly rather than always stopping for a human.
    """
    policy = (
        _OSS_FUZZ_PACKAGE_POLICY if environment is Environment.OSS_FUZZ else _LOCAL_PACKAGE_POLICY
    )
    return f"### Package installation policy\n\n{policy}\n"


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


def _stop_reason(result: AgentStreamResult) -> AgentStopReason | None:
    """The reason the agent stopped without a fix, or None if it did not stop that way.

    The two conditions are detected differently because they are emitted by different
    layers, so they are read from different channels of the stream.

    A budget/rate limit comes from the agent CLI itself, not the model, and surfaces
    anywhere in the transcript (an unparsed stderr line, an error event) -- so it is
    matched against combined_text, and does fail the process, so it stays gated on a
    non-zero exit code. That gate is also what keeps _BUDGET_PATTERN's looser
    alternatives (a bare "429", "rate limit") from matching a build log that merely
    mentions them.

    ACTION REQUIRED is emitted by the *model*, which has no way to set the process exit
    code: `claude --print` exits 0 whenever the CLI ran, however the agent decided to
    stop. Gating it on exit_code made it unreachable in practice, so the marker is
    honored on its own -- which makes it matter *where* the marker is read from. It is
    matched only against model_text, what the model actually wrote. Against
    combined_text, an agent that merely *read* a file quoting the marker failed the run:
    the library-builder SKILL.md documents the marker four times, and an OpenSSL repair
    that verified clean and produced working artifacts was reported as action_required
    purely because the agent had read its own instructions off disk.
    """
    if _BUDGET_PATTERN.search(result.combined_text) and result.exit_code != 0:
        return AgentStopReason.BUDGET_LIMITED
    if _ACTION_REQUIRED in result.model_text:
        return AgentStopReason.ACTION_REQUIRED
    return None


def _determine_outcome(result: AgentStreamResult) -> str:
    """Classify an agent invocation's outcome for the persisted/printed summary.

    Reads each signal through _stop_reason, so the printed outcome can't disagree with the
    stop reason recorded on the result.
    """
    if _BUDGET_PATTERN.search(result.combined_text):
        return AgentStopReason.BUDGET_LIMITED.value
    if result.exit_code == -1:
        return "timed_out"
    stop_reason = _stop_reason(result)
    if stop_reason is not None:
        return stop_reason.value
    return "succeeded" if result.exit_code == 0 else "failed"


def _report_agent_run(report_path: Path, tool: str, result: AgentStreamResult) -> AgentRunSummary:
    """Write the persisted transcript+summary report and print the summary to the terminal."""
    summary = AgentRunSummary(
        backend=tool,
        outcome=_determine_outcome(result),
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
    build_result: BuildExplorationResult,
    workdir: Path,
    environment: Environment,
) -> str:
    """Construct a Claude prompt for diagnosing and fixing a failed library build."""
    instructions = skill_instructions("library_builder")
    stdout_tail = "\n".join(build_result.stdout.splitlines()[-200:])
    verify_command = _verification_command(
        environment,
        workdir=workdir,
        project_name=analysis.project_name,
    )
    return (
        f"{instructions}\n\n"
        f"## Build failure context\n\n"
        f"- source_dir: {analysis.source_path}\n"
        f"- build_system: {analysis.build_system.value}\n"
        f"- command: {' '.join(build_result.command)}\n"
        f"- exit_code: {build_result.exit_code}\n"
        f"- build_library.sh: {workdir / 'build_library.sh'}\n"
        f"- install_dir: {workdir / 'install'}\n"
        f"- build_dir: {workdir / 'build'}\n\n"
        f"### Build output (last 200 lines)\n\n"
        f"```\n{stdout_tail}\n```\n\n"
        f"### Verification\n\n"
        f"After applying a fix, verify it works by running this exact command:\n\n"
        f"    {verify_command}\n\n"
        f"{_package_policy_note(environment)}"
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


def invoke_library_builder_agent(  # noqa: PLR0913 -- public API; all 6 params are distinct required inputs
    analysis: AnalysisResult,
    build_result: BuildExplorationResult,
    workdir: Path,
    *,
    tool: str = "claude",
    timeout: int = 600,
    environment: Environment = Environment.LOCAL,
) -> BuildExplorationResult:
    """Spawn a Claude Code or Codex subprocess to diagnose and fix a failed build.

    Streams agent output to the terminal. CWD is set to workdir, where build_library.sh
    lives; the agent can still read and modify the repo's build files via source_dir.
    """
    prompt = build_library_prompt(analysis, build_result, workdir, environment)
    if tool == "claude":
        cmd = construct_claude_command(prompt)
    elif tool == "codex":
        cmd = construct_codex_command(prompt)
    else:
        raise ValueError(f"unknown agent tool: {tool!r}")

    result = run_agent_streaming(cmd, workdir, timeout, tool)
    _report_agent_run(workdir / "agent_library_build.log", tool, result)
    report = read_agent_report(workdir)
    stop_reason = _stop_reason(result)

    succeeded = result.exit_code == 0 and stop_reason is None
    stderr = ""
    if succeeded:
        validation_errors = exploration.validate_install_artifacts(workdir / "install")
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
        script_path=(workdir / "build_library.sh") if succeeded else None,
        agent_stop_reason=stop_reason,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        transcript_path=workdir / "agent_library_build.log",
        agent_summary=report.summary if report else None,
        missing_apt_packages=report.missing_apt_packages if report else [],
        extra_include_paths=report.extra_include_paths if report else [],
        extra_library_paths=report.extra_library_paths if report else [],
        environment=environment,
        compile_commands_error=(
            None if succeeded else "not captured: the agent did not produce a passing build"
        ),
    )


def build_harness_prompt(
    analysis: AnalysisResult,
    harness: HarnessExplorationResult,
    install_dir: Path,
    workdir: Path,
    environment: Environment,
) -> str:
    """Construct a Claude prompt for diagnosing and fixing a failed harness link probe."""
    instructions = skill_instructions("harness_builder")
    # Some Runners (the oss-fuzz docker-streaming one) merge stderr into stdout and never
    # populate .stderr, so harness.stderr alone can be empty even when the failure's real
    # diagnostic text is sitting in .stdout — concatenate both, matching the same
    # combined-stream handling harness_explorer.py already relies on internally.
    output_tail = "\n".join(harness.output.splitlines()[-200:])
    verify_command = _verification_command(
        environment,
        workdir=workdir,
        project_name=analysis.project_name,
    )
    return (
        f"{instructions}\n\n"
        f"## Harness compilation failure context\n\n"
        f"- source_dir: {analysis.source_path}\n"
        f"- install_dir: {install_dir}\n"
        f"- workdir: {workdir}\n"
        f"- compile_harness.sh: {workdir / 'compile_harness.sh'}\n"
        f"- harness_source: {workdir / HARNESS_SOURCE_DIR}\n"
        f"- static_libs: {', '.join(p.name for p in harness.static_libs) or '(none)'}\n"
        f"- auto_resolved_link_flags: {' '.join(harness.transitive_link_flags) or '(none)'}\n"
        f"- missing_system_libs (linker-reported): "
        f"{', '.join(harness.missing_system_libs) or '(none detected)'}\n"
        f"- exit_code: {harness.exit_code}\n\n"
        f"### Linker/compiler output (last 200 lines)\n\n"
        f"```\n{output_tail}\n```\n\n"
        f"### Verification\n\n"
        f"After applying a fix, verify it works by running this exact command:\n\n"
        f"    {verify_command}\n\n"
        f"{_package_policy_note(environment)}"
    )


def invoke_harness_builder_agent(  # noqa: PLR0913 -- public API; all 6 params are distinct required inputs
    analysis: AnalysisResult,
    harness: HarnessExplorationResult,
    paths: HarnessPaths,
    *,
    tool: str = "claude",
    timeout: int = 600,
    environment: Environment = Environment.LOCAL,
) -> HarnessExplorationResult:
    """Spawn a Claude Code or Codex subprocess to diagnose and fix a failed harness link probe.

    Streams agent output to the terminal. CWD is set to paths.workdir so the agent can read
    and modify compile_harness.sh and harness_source/ directly.
    """
    prompt = build_harness_prompt(analysis, harness, paths.install_dir, paths.workdir, environment)
    if tool == "claude":
        cmd = construct_claude_command(prompt)
    elif tool == "codex":
        cmd = construct_codex_command(prompt)
    else:
        raise ValueError(f"unknown agent tool: {tool!r}")

    result = run_agent_streaming(cmd, paths.workdir, timeout, tool)
    _report_agent_run(paths.workdir / "agent_harness_build.log", tool, result)
    report = read_agent_report(paths.workdir)
    stop_reason = _stop_reason(result)

    succeeded = result.exit_code == 0 and stop_reason is None
    stderr = ""
    missing_system_libs = harness.missing_system_libs
    # The agent edits compile_harness.sh directly, and after a successful fix that script
    # is what ships — so its text, not these lists, is the link configuration of record.
    # They are carried forward only to describe what was tried.
    static_libs = harness.static_libs
    transitive_link_flags = harness.transitive_link_flags
    extra_library_paths = harness.extra_library_paths
    script_path = paths.workdir / "compile_harness.sh"
    if succeeded:
        validation_errors = harness_explorer.validate_harness_artifacts(paths.workdir)
        if validation_errors:
            succeeded = False
            stderr += "\n" + "\n".join(validation_errors)
    if not succeeded:
        # A validation-only failure (agent claimed done but out/ is empty) carries no new
        # linker stderr to re-parse, so preserve the pre-agent list rather than discarding it.
        missing_system_libs = list(
            dict.fromkeys(
                missing_system_libs + harness_explorer.extract_missing_system_libs(stderr)
            )
        )

    # The agent reports the bare library name it couldn't resolve (e.g. "ldap") alongside
    # the actual apt package names below. Add the matching -l flag ourselves so the
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
        agent_stop_reason=stop_reason,
        duration_seconds=result.duration_seconds,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        transcript_path=paths.workdir / "agent_harness_build.log",
        agent_summary=report.summary if report else None,
        missing_apt_packages=report.missing_apt_packages if report else [],
        extra_include_paths=report.extra_include_paths if report else [],
        extra_library_paths=extra_library_paths,
        environment=environment,
    )
