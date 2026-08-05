from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from harnessbuddy.core.reporting import (
    Phase,
    PhaseReporter,
    build_diagnostic,
    format_diagnostic,
    format_startup_failure,
    summarize_message,
)
from harnessbuddy.core.subprocesses import streaming_context

if TYPE_CHECKING:
    from harnessbuddy.core.reporting import FailureDiagnostic
    from harnessbuddy.core.repos import RepoSource
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.dependency_resolution import DependencyState
    from harnessbuddy.library_builder.environments.base import Environment, EnvironmentExecutor
    from harnessbuddy.library_builder.generation import GenerationInputs
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        BuildSystem,
        HarnessExplorationResult,
    )
    from harnessbuddy.library_builder.stats import RunStatus

_DEFAULT_AGENT = "claude"


def _configure_generate_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "repo_url",
        metavar="REPO_URL",
        help="Repository URL or local path to analyze.",
    )
    p.add_argument(
        "--agent",
        choices=["codex", "claude"],
        default=_DEFAULT_AGENT,
        metavar="codex|claude",
        help=(
            f"Agent backend used to repair a failed build. Default: {_DEFAULT_AGENT}. "
            "Overridden by --no-agents."
        ),
    )
    p.add_argument(
        "--output",
        metavar="DIR",
        help="Parent directory for the generated project. Output is written to DIR/PROJECT_NAME.",
    )
    p.add_argument(
        "--project-name",
        metavar="NAME",
        help="Override the project name inferred from REPO_URL.",
    )
    p.add_argument(
        "--repo-ref",
        metavar="REF",
        help="Branch, tag, or commit to check out. Not valid for a local path.",
    )
    p.add_argument(
        "--environment",
        choices=["local", "oss-fuzz"],
        default="local",
        metavar="local|oss-fuzz",
        help="Target environment to build and validate each stage in. Default: local.",
    )
    p.add_argument(
        "--base-image",
        metavar="IMAGE",
        help="Base image for the generated Dockerfile. Default: the OSS-Fuzz base builder.",
    )
    p.add_argument("--cc", metavar="COMPILER", help="C compiler for local build preparation.")
    p.add_argument("--cxx", metavar="COMPILER", help="C++ compiler for local build preparation.")
    p.add_argument(
        "--library-cflags",
        metavar="FLAGS",
        help="C flags for the library build; defaults to CFLAGS. Use --library-cflags=FLAGS.",
    )
    p.add_argument(
        "--library-cxxflags",
        metavar="FLAGS",
        help="C++ flags for the library build; defaults to CXXFLAGS. Use --library-cxxflags=FLAGS.",
    )
    p.add_argument(
        "--library-configure-arg",
        dest="library_configure_args",
        action="append",
        metavar="ARG",
        help=(
            "Configure option for the library build, baked into build_library.sh: a cmake "
            "-DVAR=VALUE, a meson -Doption=value, an autotools --enable-foo, or a make "
            "VAR=value. Repeat for more than one."
        ),
    )
    p.add_argument(
        "--harness-cflags",
        metavar="FLAGS",
        help="Default C flags in the generated local harness compiler; use --harness-cflags=FLAGS.",
    )
    p.add_argument(
        "--harness-cxxflags",
        metavar="FLAGS",
        help=(
            "Default C++ flags in the generated local harness compiler; "
            "use --harness-cxxflags=FLAGS."
        ),
    )
    p.add_argument(
        "--no-agents",
        action="store_true",
        help="Disable agent repair; a failed build then simply fails the run.",
    )
    p.add_argument(
        "--bypass-scratch-validation",
        action="store_true",
        help=(
            "Turn off default behavior that builds the library in a fresh "
            "environment to confirm that all agent-provided changes are reproducible "
            "and OSS-Fuzz builds work from scratch."
        ),
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-line raw subprocess output while a phase is running. "
            "Phase banners and failure diagnostics are always shown regardless."
        ),
    )


def _configure_extract_features_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "build_path",
        metavar="BUILD_PATH",
        help="Directory containing compile_commands.json to extract features from.",
    )


def _configure_generate_yaml_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "build_path",
        metavar="BUILD_PATH",
        help="Directory containing features.json from a prior extract-features run.",
    )
    p.add_argument(
        "headers",
        metavar="HEADER_NAMES",
        nargs="*",
        help="List of header file names to include in analysis",
    )
    p.add_argument(
        "--target-name",
        metavar="NAME",
        help="Override the default 'default_fuzzer' benchmark target name.",
    )
    p.add_argument(
        "--target-path",
        metavar="PATH",
        help="Override the default /src/harness_source/<target-name>.{c,cc} target path.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harnessbuddy",
        description="Prepare C/C++ libraries for OSS-Fuzz project generation.",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error", "critical"],
        default=None,
        metavar="LEVEL",
        help="Set logging verbosity (debug|info|warning|error|critical). Default: no output.",
    )
    subparsers = parser.add_subparsers(dest="command")
    generate = subparsers.add_parser(
        "generate",
        help="Generate an oss-fuzz project for a C/C++ library.",
        description="Generate an oss-fuzz project directory for a C/C++ library repository.",
    )
    _configure_generate_parser(generate)
    extract_features = subparsers.add_parser(
        "extract-features",
        help="Extract a library's API surface into a JSON feature artifact.",
        description="Extract functions, typedefs, macros, enums, and records from a "
        "compile_commands.json into features.json.",
    )
    _configure_extract_features_parser(extract_features)
    generate_yaml = subparsers.add_parser(
        "generate-yaml",
        help="Convert an extracted feature artifact into an oss-fuzz-gen benchmark YAML.",
        description="Convert features.json into a curated, oss-fuzz-gen-compatible "
        "benchmark YAML file.",
    )
    _configure_generate_yaml_parser(generate_yaml)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    level = getattr(logging, args.log_level.upper()) if args.log_level else logging.CRITICAL + 1
    # force=True because basicConfig silently no-ops when the root logger already has
    # handlers, e.g. a prior call in the same process or a test harness's own setup.
    logging.basicConfig(level=level, force=True)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "generate":
        return _cmd_generate(args)
    if args.command == "extract-features":
        return _cmd_extract_features(args)
    if args.command == "generate-yaml":
        return _cmd_generate_benchmark(args)
    else:
        raise ValueError("Unknown command")


def build_library(  # noqa: PLR0913 -- public API; all params are distinct required/optional inputs
    analysis: AnalysisResult,
    workspace: Path,
    executor: EnvironmentExecutor,
    *,
    agent: str | None = None,
    timeout: int | None = None,
    quiet: bool = False,
    logs_dir: Path | None = None,
    parameters: BuildParameters | None = None,
) -> BuildExplorationResult:
    """Run the executor's library build, then fall back to an LLM agent if it failed.

    result.llm_used is True when the agent path was taken; result.agent_stop_reason says why
    the agent stopped, if it did. The deterministic build and the agent repair get their own
    phase banners. logs_dir, when given, is where the deterministic build's raw output goes.
    """
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.timeouts import DEFAULT_BUILD_TIMEOUT_SECONDS

    parameters = parameters or BuildParameters.defaults()
    timeout = timeout if timeout is not None else DEFAULT_BUILD_TIMEOUT_SECONDS
    log_name = f"{Phase.DETERMINISTIC_LIBRARY_BUILD.value}.log"
    deterministic_log_path = logs_dir / log_name if logs_dir else None
    with PhaseReporter(Phase.DETERMINISTIC_LIBRARY_BUILD) as reporter:
        with (
            parameters.library_environment(),
            streaming_context(quiet=quiet, log_path=deterministic_log_path),
        ):
            result = executor.run_library_build(
                analysis, workspace, timeout=timeout, parameters=parameters
            )
        reporter.succeed() if result.succeeded else reporter.fail()

    if result.succeeded:
        return result
    if agent is None:
        print("Library build failed and agent repair is disabled (--no-agents).")
        return result

    from harnessbuddy.library_builder.agents import invoke_library_builder_agent

    with PhaseReporter(Phase.AGENT_LIBRARY_REPAIR) as agent_reporter:
        with parameters.library_environment():
            result = invoke_library_builder_agent(
                analysis,
                result,
                workspace,
                tool=agent,
                environment=result.environment,
            )
        agent_reporter.succeed() if result.succeeded else agent_reporter.fail()
    return result


def build_harness(  # noqa: PLR0913 -- public API; all params are distinct required/optional inputs
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    library_result: BuildExplorationResult,
    executor: EnvironmentExecutor,
    *,
    agent: str | None = None,
    quiet: bool = False,
    logs_dir: Path | None = None,
    parameters: BuildParameters | None = None,
) -> HarnessExplorationResult:
    """Probe harness compilation, then fall back to an LLM agent if it failed.

    library_result's extra_include_paths/extra_library_paths, if the library-build agent
    reported any, are threaded into the probe. The deterministic probe and the agent repair
    get their own phase banners. logs_dir, when given, is where the probe's raw output goes.
    """
    from harnessbuddy.library_builder.build_parameters import BuildParameters

    parameters = parameters or BuildParameters.defaults()
    log_name = f"{Phase.HARNESS_COMPILE_PROBE.value}.log"
    deterministic_log_path = logs_dir / log_name if logs_dir else None
    with PhaseReporter(Phase.HARNESS_COMPILE_PROBE) as reporter:
        with (
            parameters.harness_environment(),
            streaming_context(quiet=quiet, log_path=deterministic_log_path),
        ):
            result = executor.run_harness_compile(
                install_dir,
                workspace,
                analysis.language,
                extra_include_paths=library_result.extra_include_paths,
                extra_library_paths=library_result.extra_library_paths,
                parameters=parameters,
                library_llm_used=library_result.llm_used,
            )
        reporter.succeed() if result.succeeded else reporter.fail()

    if result.succeeded or agent is None:
        return result

    from harnessbuddy.library_builder.agents import invoke_harness_builder_agent
    from harnessbuddy.library_builder.models import HarnessPaths

    with PhaseReporter(Phase.AGENT_HARNESS_REPAIR) as agent_reporter:
        with parameters.harness_environment():
            result = invoke_harness_builder_agent(
                analysis,
                result,
                HarnessPaths(install_dir=install_dir, workdir=workspace),
                tool=agent,
                environment=result.environment,
            )
        agent_reporter.succeed() if result.succeeded else agent_reporter.fail()
    return result


def _ingest_source(args: argparse.Namespace, state_dir: Path) -> RepoSource | str:
    """Clone or resolve the repository, returning its source path or an error message
    on failure (the caller wraps this in the INGESTION phase's diagnostic)."""
    from harnessbuddy.core.repos import (
        CloneFailedError,
        LocalRepoRefError,
        NoCloneableOriginError,
        RepositoryNotFoundError,
        ingest_local,
        ingest_url,
    )

    try:
        if _is_url(args.repo_url):
            return ingest_url(
                args.repo_url,
                project_name=args.project_name,
                repo_ref=args.repo_ref,
                state_dir=state_dir,
            )
        return ingest_local(
            Path(args.repo_url),
            project_name=args.project_name,
            repo_ref=args.repo_ref,
            state_dir=state_dir,
        )
    except RepositoryNotFoundError as exc:
        return f"Repository not found: {exc}"
    except (CloneFailedError, LocalRepoRefError) as exc:
        return str(exc)
    except NoCloneableOriginError:
        return (
            "No cloneable git origin found. Provide a URL instead of a local path,"
            " or add a remote origin."
        )


def _resolve_output_path(args: argparse.Namespace, analysis: AnalysisResult) -> Path | None:
    """The output directory for this run, or None if the user declined to overwrite it.

    Resolves and confirms only. Nothing is deleted until generation is ready to write, so a
    run that fails partway leaves the previous, working output intact.
    """
    output_path = Path(args.output) if args.output else Path.cwd() / "output"
    output_path = output_path / analysis.project_name
    if not output_path.exists():
        return output_path
    if sys.stdin.isatty():
        if input(f"Output directory {output_path} already exists. Overwrite? (y/n) ") != "y":
            return None
    else:
        print(f"Output directory {output_path} already exists, overwriting ...")
    return output_path


def _run_library_phase(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    analysis: AnalysisResult,
    workspace: Path,
    agent: str | None,
    state: DependencyState,
    state_file: Path,
    executor: EnvironmentExecutor,
    *,
    quiet: bool,
    logs_dir: Path | None,
    parameters: BuildParameters,
) -> BuildExplorationResult:
    """Build the library, persisting any packages the library-build agent reported missing."""
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder.dependency_resolution import DependencySource

    result = build_library(
        analysis,
        workspace,
        executor,
        agent=agent,
        quiet=quiet,
        logs_dir=logs_dir,
        parameters=parameters,
    )

    if result.missing_apt_packages:
        dependencies = dependency_resolution.from_agent_report(
            [],
            result.missing_apt_packages,
            source=DependencySource.LIBRARY_AGENT,
        )
        dependency_resolution.merge(state, dependencies)
        dependency_resolution.save_state(state_file, state)

    return result


def _harness_failure_diagnostic(
    harness_result: HarnessExplorationResult,
    apt_hint_list: list[str],
    log_path: Path | None,
) -> FailureDiagnostic:
    """Build the diagnostic for a failed harness compilation, which stops the run: the probe
    is the only evidence that compile_harness.sh's link line works."""
    phase = Phase.AGENT_HARNESS_REPAIR if harness_result.llm_used else Phase.HARNESS_COMPILE_PROBE
    origin = "agent" if harness_result.llm_used else "deterministic"
    step = _agent_step(harness_result) if harness_result.llm_used else "harness link probe"
    if apt_hint_list:
        libs = ", ".join(harness_result.missing_system_libs)
        message = (
            f"Missing system libraries: {libs}\n"
            f"  apt: {' '.join(apt_hint_list)}\n"
            "Install these packages and re-run for a complete harness build."
        )
    elif harness_result.validation_errors:
        message = _rejected_repair_message(harness_result)
    elif harness_result.llm_used and harness_result.agent_summary:
        message = harness_result.agent_summary
    else:
        message = summarize_message(harness_result.stderr or harness_result.stdout)
    return build_diagnostic(
        phase,
        step=step,
        message=message,
        origin=origin,
        log_path=log_path,
        exit_code=harness_result.exit_code,
    )


def _rejected_repair_message(result: BuildExplorationResult | HarnessExplorationResult) -> str:
    """The message for a repair the agent reported as done and HarnessBuddy rejected.

    The agent's summary describes the failure it set out to fix, not why the run stopped, so
    it appears as context under the artifact check that rejected it.
    """
    detail = "\n".join(f"  {error.strip()}" for error in result.validation_errors)
    message = (
        f"The agent reported success, but the artifacts it claims to have built "
        f"are not there:\n{detail}"
    )
    if result.agent_summary:
        message += f"\n  Agent's own summary: {result.agent_summary}"
    return message


def _agent_step(result: BuildExplorationResult | HarnessExplorationResult) -> str:
    """How to describe an agent attempt in a diagnostic, including why it stopped."""
    from harnessbuddy.library_builder.models import AgentStopReason

    if result.agent_stop_reason is AgentStopReason.BUDGET_LIMITED:
        return "LLM repair attempt (usage/rate limit reached)"
    if result.agent_stop_reason is AgentStopReason.ACTION_REQUIRED:
        return "LLM repair attempt (action required)"
    return "LLM repair attempt"


def _run_harness_phase(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    library_result: BuildExplorationResult,
    agent: str | None,
    state: DependencyState,
    state_file: Path,
    executor: EnvironmentExecutor,
    *,
    environment: Environment,
    quiet: bool,
    debug: bool,
    logs_dir: Path | None,
    parameters: BuildParameters,
) -> HarnessExplorationResult:
    """Probe harness compilation, persist any newly-discovered packages, and report status."""
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder import workspace as workspace_layout
    from harnessbuddy.library_builder.dependency_resolution import DependencySource
    from harnessbuddy.library_builder.environments.base import Environment

    harness_result = build_harness(
        analysis,
        install_dir,
        workspace,
        library_result,
        executor,
        agent=agent,
        quiet=quiet,
        logs_dir=logs_dir,
        parameters=parameters,
    )

    # Covers both the libs the linker reported missing and the ones it resolved silently
    # because the exploration host already had them.
    linker_deps = dependency_resolution.from_deterministic_probe(
        harness_result.missing_system_libs, harness_result.transitive_link_flags
    )
    # The harness-build agent names apt packages itself, bypassing the translation table.
    harness_agent_deps = dependency_resolution.from_agent_report(
        [],
        harness_result.missing_apt_packages,
        source=DependencySource.HARNESS_AGENT,
    )
    if linker_deps or harness_agent_deps:
        dependency_resolution.merge(state, linker_deps + harness_agent_deps)
        dependency_resolution.save_state(state_file, state)

    unknown_names = [
        dep.name for dep in linker_deps if dep.name is not None and dep.apt_package is None
    ]
    if unknown_names:
        print(
            f"Warning: no known apt package mapping for: {', '.join(unknown_names)}. "
            "Install these manually before building elsewhere.",
            file=sys.stderr,
        )

    if environment is Environment.OSS_FUZZ:
        # The Dockerfile was written before this phase's discoveries existed, and generation
        # copies it rather than re-rendering it, so merge them in now.
        workspace_layout.inject_apt_packages(workspace, state.apt_packages)

    if harness_result.succeeded:
        print("Successfully produced harness compilation!")
        return harness_result

    apt_hint_list = list(
        dict.fromkeys(
            [dep.apt_package for dep in linker_deps if dep.apt_package is not None]
            + harness_result.missing_apt_packages
        )
    )
    log_path = (
        harness_result.transcript_path
        if harness_result.llm_used
        else (logs_dir / f"{Phase.HARNESS_COMPILE_PROBE.value}.log" if logs_dir else None)
    )
    diagnostic = _harness_failure_diagnostic(harness_result, apt_hint_list, log_path)
    print(
        format_diagnostic(diagnostic, debug=debug, raw_output=harness_result.stdout),
        file=sys.stderr,
    )
    print(
        f"No output directory was generated. The library build artifacts are still in "
        f"{workspace / 'install'} if you want to debug the link line there.",
        file=sys.stderr,
    )
    return harness_result


def _verify_shipped_dockerfile(workspace: Path, project_name: str, *, quiet: bool) -> bool:
    """Build the workspace Dockerfile with nothing mounted and run OSS-Fuzz's `compile`.

    The last step before generation for an oss-fuzz target. Everything before it ran with the
    workspace mounted, which is what makes the artifacts reachable — and also what lets a
    broken clone or apt layer in the Dockerfile pass unnoticed.
    """
    from harnessbuddy.library_builder.environments import verification

    with PhaseReporter(Phase.DOCKERFILE_VERIFICATION) as reporter:
        with streaming_context(quiet=quiet):
            result = verification.run_from_scratch_docker_verification(
                workspace, project_name=project_name
            )
        reporter.succeed() if result.passed else reporter.fail()
    return result.passed


def _generate_outputs(workspace: Path, output_path: Path, inputs: GenerationInputs) -> int:
    """Write the output directory, reporting what landed where."""
    from harnessbuddy.library_builder.generation import MissingInstallTreeError, generate

    if output_path.exists():
        # Deferred until now, not done when the path was confirmed, so a run that fails
        # partway leaves the previous output in place.
        shutil.rmtree(output_path)
    try:
        result = generate(workspace, output_path, inputs)
    except MissingInstallTreeError as exc:
        # The half-written directory goes: publishing is all-or-nothing, and a partial one
        # reads as a usable project to anything that only checks that it exists.
        shutil.rmtree(output_path, ignore_errors=True)
        print(f"Output generation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Environment:  {inputs.environment.value}")
    print(f"Output:       {result.output_path}")
    verification_command = _command_str(inputs.harness.command or inputs.build.command)
    if verification_command is not None:
        print(f"Verified with: {verification_command}")
    if (output_path / "compile_commands.json").exists():
        print(f"Compile commands: {output_path / 'compile_commands.json'}")
    return 0


def _command_str(command: list[str]) -> str | None:
    """Render a verification command's argv, so the report records the literal command that
    confirmed success or failure."""
    return " ".join(command) if command else None


def _write_run_stats(  # noqa: PLR0913 -- private helper; every param is a distinct record field
    stats_path: Path,
    workdir: Path,
    start_time: float,
    build_result: BuildExplorationResult,
    harness_result: HarnessExplorationResult | None,
    status: RunStatus,
    environment: Environment,
    parameters: BuildParameters,
    *,
    bypass_scratch_validation: bool = False,
) -> None:
    """Build and persist stats.json for this run, successful or not.

    The compile-commands path is resolved from workdir rather than read off a result, so it
    records the capture the gate's build actually left behind -- including on the lane where a
    repair agent produced that build.
    """
    from harnessbuddy.library_builder.stats import (
        RunStats,
        agent_phase_stats,
        not_invoked_agent_stats,
        write_run_stats,
    )
    from harnessbuddy.library_builder.workspace import find_compile_commands

    stats_path.parent.mkdir(parents=True, exist_ok=True)
    command = (harness_result.command if harness_result else None) or build_result.command
    compile_commands = find_compile_commands(workdir)
    write_run_stats(
        stats_path,
        RunStats(
            total_duration_seconds=time.monotonic() - start_time,
            library_build_agent=agent_phase_stats(build_result),
            harness_build_agent=(
                agent_phase_stats(harness_result)
                if harness_result is not None
                else not_invoked_agent_stats()
            ),
            status=status,
            environment=environment,
            compile_commands_path=str(compile_commands) if compile_commands else None,
            verification_command=_command_str(command),
            build_parameters=parameters.to_dict(),
            scratch_validation_bypassed=bypass_scratch_validation,
        ),
    )


def _finish_generate_run(  # noqa: PLR0913 -- private helper; every param is a distinct record field
    rc: int,
    stats_path: Path,
    workdir: Path,
    output_path: Path,
    start_time: float,
    build_result: BuildExplorationResult,
    harness_result: HarnessExplorationResult,
    environment: Environment,
    parameters: BuildParameters,
    *,
    bypass_scratch_validation: bool = False,
) -> int:
    """Record the run's outcome and report it, returning the exit code to hand back.

    The output copy of stats.json is only made when generation succeeded: a failed one removes
    the directory it was writing, so there is nothing left to put it in.
    """
    from harnessbuddy.library_builder.stats import RunStatus

    status = RunStatus.SUCCESS if rc == 0 else RunStatus.FAILED_OUTPUT_GENERATION
    _write_run_stats(
        stats_path,
        workdir,
        start_time,
        build_result,
        harness_result,
        status,
        environment,
        parameters,
        bypass_scratch_validation=bypass_scratch_validation,
    )
    if rc == 0:
        shutil.copy2(stats_path, output_path / "stats.json")
    _print_run_summary(status)
    return rc


def _select_executor(
    environment: Environment, base_image: str | None, *, bypass_scratch_validation: bool = False
) -> EnvironmentExecutor:
    from harnessbuddy.library_builder.environments.base import Environment
    from harnessbuddy.library_builder.environments.local import LocalExecutor
    from harnessbuddy.library_builder.environments.oss_fuzz import OssFuzzExecutor

    if environment is Environment.OSS_FUZZ:
        return OssFuzzExecutor(
            base_image=base_image, bypass_scratch_validation=bypass_scratch_validation
        )
    return LocalExecutor(base_image=base_image, bypass_scratch_validation=bypass_scratch_validation)


def _check_environment_availability(
    executor: EnvironmentExecutor, environment: Environment
) -> int | None:
    """Return an exit code if the environment is unavailable, else None to proceed."""
    from harnessbuddy.library_builder.environments.base import EnvironmentUnavailableError

    try:
        executor.check_availability()
    except EnvironmentUnavailableError as exc:
        print(
            format_startup_failure(f"Environment '{environment.value}' is unavailable: {exc}"),
            file=sys.stderr,
        )
        return 1
    return None


def _report_library_build_failure(
    result: BuildExplorationResult, *, debug: bool, log_path: Path | None
) -> None:
    """Print the diagnostic for a failed library build — deterministic or post-agent.

    One printer for one control path: a failed library build always stops the run, whether no
    agent was armed, the repair did not hold, or the agent stopped for a person to act.
    """
    phase = Phase.AGENT_LIBRARY_REPAIR if result.llm_used else Phase.DETERMINISTIC_LIBRARY_BUILD
    origin = "agent" if result.llm_used else "deterministic"
    step = _agent_step(result) if result.llm_used else "deterministic build command"
    if result.validation_errors:
        message = _rejected_repair_message(result)
    elif result.llm_used and result.agent_summary:
        message = result.agent_summary
    else:
        message = summarize_message(result.stdout)
    diagnostic = build_diagnostic(
        phase,
        step=step,
        message=message,
        origin=origin,
        log_path=log_path,
        exit_code=result.exit_code,
    )
    print(format_diagnostic(diagnostic, debug=debug, raw_output=result.stdout), file=sys.stderr)
    if result.command:
        print(f"Reproduce with: {_command_str(result.command)}", file=sys.stderr)


def _report_library_build_success() -> None:
    print("Successfully produced library build!")


def _report_compile_commands(workdir: Path, build_system: BuildSystem) -> None:
    """Report the capture, after the gate rather than after the library phase.

    Reported here because the gate rebuilds from nothing and is the last thing to write the
    file. Reported at the end of the library phase instead, it could only ever describe a build
    the gate was about to redo -- and on the repair lane it had nothing to describe at all.
    """
    from harnessbuddy.library_builder.exploration import compile_commands_absent_reason
    from harnessbuddy.library_builder.workspace import find_compile_commands

    captured = find_compile_commands(workdir)
    if captured is not None:
        print(f"Compile commands: {captured}")
    else:
        print(f"Compile commands: not captured ({compile_commands_absent_reason(build_system)})")


def _print_run_summary(status: RunStatus) -> None:
    """Print a final, unambiguous overall-outcome line.

    Without it, the last thing printed is the output-generation banner, which only reports
    that writing the files succeeded.
    """
    from harnessbuddy.library_builder.stats import RunStatus

    if status is RunStatus.SUCCESS:
        print("Overall: SUCCESS")
        return
    reason = {
        RunStatus.FAILED_LIBRARY_BUILD: "library build failed",
        RunStatus.FAILED_HARNESS_BUILD: "harness compile probe failed",
        RunStatus.FAILED_DOCKERFILE_VERIFICATION: (
            "the generated Dockerfile did not build from scratch"
        ),
        RunStatus.FAILED_OUTPUT_GENERATION: "the output directory could not be published",
    }[status]
    print(f"Overall: FAILED ({reason} — see diagnostic above)", file=sys.stderr)


def _print_run_configuration(
    environment: Environment, agent: str | None, parameters: BuildParameters
) -> None:
    """Announce what this run will do before it starts doing it.

    The agent backend is named because a default run may call a paid network service, and the
    configure options because they change what gets built and are otherwise only visible
    inside the generated script.
    """
    print(f"Environment:  {environment.value}")
    print(f"Agent:        {agent or 'none (--no-agents)'}")
    if parameters.library_configure_args:
        print(f"Configure:    {' '.join(parameters.library_configure_args)}")


def _print_analysis(analysis: AnalysisResult) -> None:
    print(f"Build system: {analysis.build_system.value}")
    print(f"Language:     {analysis.language.value}")
    for warning in analysis.warnings:
        print(f"Warning: {warning}", file=sys.stderr)


def _cmd_generate(args: argparse.Namespace) -> int:
    from harnessbuddy.core.paths import (
        default_state_dir,
        project_dir,
        project_logs_dir,
        project_state_file,
    )
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.environments.base import Environment
    from harnessbuddy.library_builder.generation import GenerationInputs
    from harnessbuddy.library_builder.stats import RunStatus

    start_time = time.monotonic()
    state_dir = default_state_dir()
    environment = Environment(args.environment)
    bypass_scratch_validation = args.bypass_scratch_validation
    executor = _select_executor(
        environment, args.base_image, bypass_scratch_validation=bypass_scratch_validation
    )
    quiet = args.quiet
    debug = args.log_level == "debug"
    parameters = BuildParameters.from_args(args)
    agent = None if args.no_agents else args.agent

    _print_run_configuration(environment, agent, parameters)
    availability_rc = _check_environment_availability(executor, environment)
    if availability_rc is not None:
        return availability_rc

    with PhaseReporter(Phase.INGESTION) as reporter:
        source = _ingest_source(args, state_dir)
        if isinstance(source, str):
            reporter.fail()
            print(
                format_diagnostic(
                    build_diagnostic(
                        Phase.INGESTION,
                        step="repository resolution",
                        message=source,
                        origin="deterministic",
                    ),
                    debug=debug,
                ),
                file=sys.stderr,
            )
            return 1
        reporter.succeed()

    with PhaseReporter(Phase.STATIC_ANALYSIS) as reporter:
        try:
            analysis = analyze(source)
        except UnsupportedRepositoryError:
            reporter.fail()
            print(
                format_diagnostic(
                    build_diagnostic(
                        Phase.STATIC_ANALYSIS,
                        step="build-system detection",
                        message="No C/C++ build signals found in this repository.",
                        origin="deterministic",
                    ),
                    debug=debug,
                ),
                file=sys.stderr,
            )
            return 1
        reporter.succeed()
    _print_analysis(analysis)

    output_path = _resolve_output_path(args, analysis)
    if output_path is None:
        print("Chose not to overwrite the existing output directory; nothing was written.")
        return 1

    workspace = project_dir(state_dir, analysis.project_name)
    logs_dir = project_logs_dir(state_dir, analysis.project_name)
    stats_path = workspace / "stats.json"
    state_file = project_state_file(state_dir, analysis.project_name)
    state = dependency_resolution.load_state(state_file)

    build_result = _run_library_phase(
        analysis,
        workspace,
        agent,
        state,
        state_file,
        executor,
        quiet=quiet,
        logs_dir=logs_dir,
        parameters=parameters,
    )
    if not build_result.succeeded:
        _report_library_build_failure(
            build_result,
            debug=debug,
            log_path=(
                build_result.transcript_path
                if build_result.llm_used
                else logs_dir / f"{Phase.DETERMINISTIC_LIBRARY_BUILD.value}.log"
            ),
        )
        _write_run_stats(
            stats_path,
            workspace,
            start_time,
            build_result,
            None,
            RunStatus.FAILED_LIBRARY_BUILD,
            environment,
            parameters,
            bypass_scratch_validation=bypass_scratch_validation,
        )
        _print_run_summary(RunStatus.FAILED_LIBRARY_BUILD)
        return 1
    _report_library_build_success()

    harness_result = _run_harness_phase(
        analysis,
        workspace / "install",
        workspace,
        build_result,
        agent,
        state,
        state_file,
        executor,
        environment=environment,
        quiet=quiet,
        debug=debug,
        logs_dir=logs_dir,
        parameters=parameters,
    )
    if not harness_result.succeeded:
        _write_run_stats(
            stats_path,
            workspace,
            start_time,
            build_result,
            harness_result,
            RunStatus.FAILED_HARNESS_BUILD,
            environment,
            parameters,
            bypass_scratch_validation=bypass_scratch_validation,
        )
        _print_run_summary(RunStatus.FAILED_HARNESS_BUILD)
        return 1
    _report_compile_commands(workspace, analysis.build_system)

    if (
        environment is Environment.OSS_FUZZ
        and not bypass_scratch_validation
        and not _verify_shipped_dockerfile(workspace, analysis.project_name, quiet=quiet)
    ):
        print(
            "The generated Dockerfile did not build and compile from scratch, so the "
            "oss-fuzz project would not work where it matters. Nothing was generated.",
            file=sys.stderr,
        )
        _write_run_stats(
            stats_path,
            workspace,
            start_time,
            build_result,
            harness_result,
            RunStatus.FAILED_DOCKERFILE_VERIFICATION,
            environment,
            parameters,
            bypass_scratch_validation=bypass_scratch_validation,
        )
        _print_run_summary(RunStatus.FAILED_DOCKERFILE_VERIFICATION)
        return 1

    with PhaseReporter(Phase.OUTPUT_GENERATION) as reporter:
        with parameters.harness_environment():
            rc = _generate_outputs(
                workspace,
                output_path,
                GenerationInputs(
                    analysis=analysis,
                    build=build_result,
                    harness=harness_result,
                    system_packages=state.apt_packages,
                    environment=environment,
                    agent_backend=(
                        agent if (build_result.llm_used or harness_result.llm_used) else None
                    ),
                    scratch_validation_bypassed=bypass_scratch_validation,
                ),
            )
        reporter.succeed() if rc == 0 else reporter.fail()

    return _finish_generate_run(
        rc,
        stats_path,
        workspace,
        output_path,
        start_time,
        build_result,
        harness_result,
        environment,
        parameters,
        bypass_scratch_validation=bypass_scratch_validation,
    )


def _cmd_extract_features(args: argparse.Namespace) -> int:
    from harnessbuddy.feature_extractor.extraction import (
        FeatureArtifactError,
        MissingCompileCommandsError,
        extract_features,
    )
    from harnessbuddy.feature_extractor.native_build import NativeBuildError

    build_path = Path(args.build_path)
    try:
        result = extract_features(build_path)
    except (MissingCompileCommandsError, NativeBuildError, FeatureArtifactError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"Extracted {len(result.functions)} functions, {len(result.typedefs)} typedefs, "
        f"{len(result.macros)} macros, {len(result.enums)} enums, "
        f"{len(result.records)} records -> {build_path / 'features.json'}"
    )
    return 0


def _cmd_generate_benchmark(args: argparse.Namespace) -> int:
    from harnessbuddy.feature_extractor.benchmark_yaml import generate_benchmark
    from harnessbuddy.feature_extractor.extraction import (
        FeatureArtifactError,
        MissingFeatureArtifactError,
    )

    build_path = Path(args.build_path)
    try:
        benchmark = generate_benchmark(
            build_path,
            headers=args.headers,
            target_name=args.target_name,
            target_path=args.target_path,
        )
    except (MissingFeatureArtifactError, FeatureArtifactError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"Generated benchmark for {len(benchmark.functions)} public functions -> "
        f"{build_path / f'{benchmark.project}.yaml'}"
    )
    return 0


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "git://", "ssh://", "git@"))
