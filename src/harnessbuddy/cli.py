from __future__ import annotations

import argparse
import dataclasses
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
    from harnessbuddy.library_builder.agents import BuildFailureError, LLMBudgetError
    from harnessbuddy.library_builder.build_parameters import BuildParameters
    from harnessbuddy.library_builder.dependency_resolution import DependencySource, DependencyState
    from harnessbuddy.library_builder.environments.base import Environment, EnvironmentExecutor
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
    )
    from harnessbuddy.library_builder.stats import AgentPhaseStats, RunStatus

logger = None


def _configure_generate_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "repo_url",
        metavar="REPO_URL",
        help="Repository URL or local path to analyze.",
    )
    p.add_argument(
        "--agent",
        choices=["codex", "claude"],
        default=None,
        metavar="codex|claude",
        help="Agent backend for fallback. Overridden by --no-agents.",
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
        help="Branch, tag, or commit to check out in the generated Dockerfile.",
    )
    p.add_argument(
        "--environment",
        choices=["local", "oss-fuzz"],
        default="local",
        metavar="local|oss-fuzz",
        help="Target environment to build and validate each stage in. Default: local.",
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
        "--skip-validation",
        action="store_true",
        help=(
            "Don't let a failed library build stop the pipeline before generation. "
            "Both stages still run to produce the artifacts generation needs."
        ),
    )
    p.add_argument(
        "--no-agents",
        action="store_true",
        help="Disable all agent fallback regardless of --agent.",
    )
    p.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep the working directory after the run.",
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
    global logger
    parser = build_parser()
    args = parser.parse_args(argv)
    level = getattr(logging, args.log_level.upper()) if args.log_level else logging.CRITICAL + 1
    # force=True so --log-level reliably takes effect on every invocation, even when the
    # root logger already has handlers configured (e.g. a prior call in the same process,
    # or a test harness's own logging setup) — otherwise basicConfig silently no-ops.
    logging.basicConfig(level=level, force=True)
    logger = logging.getLogger(__name__)
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
    timeout: int = 300,
    quiet: bool = False,
    logs_dir: Path | None = None,
    parameters: BuildParameters | None = None,
) -> BuildExplorationResult:
    """Run the executor's library build, then optionally fall back to an LLM agent.

    Returns the final BuildExplorationResult. result.llm_used is True when the
    agent path was taken. Brackets the deterministic build and (if invoked) the agent
    repair with distinct PhaseReporter banners (FR-001/FR-002); logs_dir, when given,
    is where the deterministic build's full raw output is persisted (FR-004).
    """
    from harnessbuddy.library_builder.build_parameters import BuildParameters

    parameters = parameters or BuildParameters.from_args(object())
    static_log_path = logs_dir / f"{Phase.STATIC_LIBRARY_BUILD.value}.log" if logs_dir else None
    with PhaseReporter(Phase.STATIC_LIBRARY_BUILD) as reporter:
        reporter.set_log_path(static_log_path)
        with (
            parameters.library_environment(),
            streaming_context(quiet=quiet, log_path=static_log_path),
        ):
            result = executor.run_library_build(
                analysis, workspace, timeout=timeout, parameters=parameters
            )
        if result.succeeded:
            reporter.succeed()
        else:
            reporter.fail()

    if not result.succeeded:
        if agent is not None:
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
                agent_reporter.set_log_path(result.transcript_path)
                if result.succeeded:
                    agent_reporter.succeed()
                else:
                    agent_reporter.fail()
            if result.succeeded:
                with parameters.library_environment():
                    result = _sync_artifacts_after_agent_fix(
                        analysis, workspace, executor, result, timeout
                    )
        else:
            print("Library build failed and --agent argument was not provided ...")
    return result


def _sync_artifacts_after_agent_fix(
    analysis: AnalysisResult,
    workspace: Path,
    executor: EnvironmentExecutor,
    result: BuildExplorationResult,
    timeout: int,
) -> BuildExplorationResult:
    """Re-run the repair agent's already-fixed build_library.sh once more to populate
    host-side install/ and capture compile_commands.json — an out-of-band agent
    verification (e.g. oss-fuzz's unmounted check_docker_build.sh) may not produce either
    on its own, and the following harness-compilation stage needs real static libraries
    on disk to link against. Best-effort: never regresses result.succeeded, since the
    agent's own verification already proved the fix works.
    """
    sync_result = executor.sync_artifacts_after_agent_fix(analysis, workspace, timeout=timeout)
    if not sync_result.succeeded:
        print(
            "Warning: could not re-populate install/ on the host after the agent's fix; "
            "the harness-compilation stage may fail to find static libraries.",
            file=sys.stderr,
        )
        return result
    return dataclasses.replace(
        result,
        install_dir=sync_result.install_dir,
        compile_commands_path=sync_result.compile_commands_path,
        compile_commands_error=sync_result.compile_commands_error,
    )


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
    """Probe harness compilation, then optionally fall back to an LLM agent if it fails.

    Returns the final HarnessExplorationResult. result.llm_used is True when the
    agent path was taken. library_result's extra_include_paths/extra_library_paths
    (from the library-build agent's AgentReport, if any) are threaded into the probe.
    Brackets the deterministic probe and (if invoked) the agent repair with distinct
    PhaseReporter banners (FR-001/FR-002); logs_dir, when given, is where the
    deterministic probe's full raw output is persisted (FR-004).
    """
    from harnessbuddy.library_builder.build_parameters import BuildParameters

    parameters = parameters or BuildParameters.from_args(object())
    static_log_path = logs_dir / f"{Phase.HARNESS_COMPILE_PROBE.value}.log" if logs_dir else None
    with PhaseReporter(Phase.HARNESS_COMPILE_PROBE) as reporter:
        reporter.set_log_path(static_log_path)
        with (
            parameters.harness_environment(),
            streaming_context(quiet=quiet, log_path=static_log_path),
        ):
            result = executor.run_harness_compile(
                install_dir,
                workspace,
                analysis.language,
                extra_include_paths=library_result.extra_include_paths,
                extra_library_paths=library_result.extra_library_paths,
                parameters=parameters,
            )
        if result.succeeded:
            reporter.succeed()
        else:
            reporter.fail()

    if not result.succeeded and agent is not None:
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
            agent_reporter.set_log_path(result.transcript_path)
            if result.succeeded:
                agent_reporter.succeed()
            else:
                agent_reporter.fail()
    return result


def _ingest_source(args: argparse.Namespace, state_dir: Path) -> RepoSource | str:
    """Clone or resolve the repository, returning its source path or an error message
    on failure (the caller wraps this in the INGESTION phase's diagnostic)."""
    from harnessbuddy.core.repos import (
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
        )
    except RepositoryNotFoundError as exc:
        return f"Repository not found: {exc}"
    except NoCloneableOriginError:
        return (
            "No cloneable git origin found. Provide a URL instead of a local path,"
            " or add a remote origin."
        )


def _confirm_and_clear(path: Path, label: str) -> bool:
    """Remove path if it exists, prompting first when running interactively.

    Returns False if the user declined to overwrite an existing path.
    """
    if not path.exists():
        return True
    if sys.stdin.isatty():
        overwrite = input(f"{label} {path} already exists. Overwrite? (y/n)")
        if overwrite != "y":
            return False
    else:
        print(f"{label} {path} already exists, overwriting ...")
    shutil.rmtree(path)
    return True


def _resolve_output_path(
    args: argparse.Namespace, analysis: AnalysisResult, environment: Environment
) -> Path:
    """Determine the output path for environment, prompting to overwrite an existing directory."""
    from harnessbuddy.library_builder.environments.base import Environment

    base_output = (
        Path(args.output) if args.output else Path.cwd() / "output" / analysis.project_name
    )
    if not _confirm_and_clear(base_output, "Output directory"):
        print("Chose to not overwrite, exiting...")
        exit(0)
    subdir = "oss-fuzz" if environment is Environment.OSS_FUZZ else "local"
    return base_output / subdir


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

    if result.missing_apt_packages or result.missing_brew_packages:
        dependencies = dependency_resolution.from_agent_report(
            [],
            result.missing_apt_packages,
            result.missing_brew_packages,
            source=DependencySource.LIBRARY_AGENT,
        )
        dependency_resolution.merge(state, dependencies)
        dependency_resolution.save_state(state_file, state)

    return result


def _harness_failure_diagnostic(
    harness_result: HarnessExplorationResult,
    apt_hint_list: list[str],
    brew_hint_list: list[str],
    log_path: Path | None,
) -> FailureDiagnostic:
    """Build the diagnostic for a failed harness compilation (best-effort continue —
    generation still runs with stub scripts, so this never stops the pipeline)."""
    phase = Phase.AGENT_HARNESS_REPAIR if harness_result.llm_used else Phase.HARNESS_COMPILE_PROBE
    origin = "agent" if harness_result.llm_used else "deterministic"
    step = "LLM repair attempt" if harness_result.llm_used else "harness link probe"
    if apt_hint_list or brew_hint_list:
        libs = ", ".join(harness_result.missing_system_libs)
        apt_hint = " ".join(apt_hint_list) or "(none mapped)"
        brew_hint = " ".join(brew_hint_list) or "(none mapped)"
        message = (
            f"Missing system libraries: {libs}\n"
            f"  apt:  {apt_hint}\n"
            f"  brew: {brew_hint}\n"
            "Install these packages and re-run for a complete harness build."
        )
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
    quiet: bool,
    debug: bool,
    logs_dir: Path | None,
    parameters: BuildParameters,
) -> tuple[HarnessExplorationResult, list[str]]:
    """Probe harness compilation, persist any newly-discovered packages, and report status."""
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder.dependency_resolution import DependencySource

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

    # Covers both libs the linker reported missing (missing_system_libs) and libs it
    # resolved silently because the exploration host already had them
    # (transitive_link_flags).
    linker_deps = dependency_resolution.from_static_probe(
        harness_result.missing_system_libs, harness_result.transitive_link_flags
    )
    # The harness-build agent reports its own apt/brew package names directly (drawn from
    # its general knowledge of the library's packaging), bypassing the translation table.
    harness_agent_deps = dependency_resolution.from_agent_report(
        [],
        harness_result.missing_apt_packages,
        harness_result.missing_brew_packages,
        source=DependencySource.HARNESS_AGENT,
    )
    if linker_deps or harness_agent_deps:
        dependency_resolution.merge(state, linker_deps + harness_agent_deps)
        dependency_resolution.save_state(state_file, state)

    unknown_names = [
        dep.name
        for dep in linker_deps
        if dep.name is not None and dep.apt_package is None and dep.brew_package is None
    ]
    if unknown_names:
        unknown = ", ".join(unknown_names)
        print(
            f"Warning: no known apt/brew package mapping for: {unknown}. "
            "Install these manually before building elsewhere.",
            file=sys.stderr,
        )

    # Apply accumulated apt packages so generators (Dockerfile, setup.sh) see them.
    analysis.system_packages = state.apt_packages
    brew_packages: list[str] = state.brew_packages

    # local/generation.py's setup.sh always renders analysis.system_packages fresh at
    # final generation time, but the oss-fuzz workspace's Dockerfile was already written
    # once, early, by OssFuzzExecutor._materialize_workspace — before this phase's own
    # discoveries existed — and generate_oss_fuzz only ever copies it verbatim. Merge the
    # newly-discovered packages into that file now so they aren't silently dropped.
    if (workspace / "Dockerfile").exists():
        from harnessbuddy.library_builder.oss_fuzz.workspace import inject_apt_packages

        inject_apt_packages(workspace, state.apt_packages)

    if not harness_result.succeeded:
        apt_hint_list = list(
            dict.fromkeys(
                [dep.apt_package for dep in linker_deps if dep.apt_package is not None]
                + harness_result.missing_apt_packages
            )
        )
        brew_hint_list = list(
            dict.fromkeys(
                [dep.brew_package for dep in linker_deps if dep.brew_package is not None]
                + harness_result.missing_brew_packages
            )
        )
        log_path = (
            harness_result.transcript_path
            if harness_result.llm_used
            else (logs_dir / f"{Phase.HARNESS_COMPILE_PROBE.value}.log" if logs_dir else None)
        )
        diagnostic = _harness_failure_diagnostic(
            harness_result, apt_hint_list, brew_hint_list, log_path
        )
        print(
            format_diagnostic(diagnostic, debug=debug, raw_output=harness_result.stdout),
            file=sys.stderr,
        )
        print("Generating output files with best-effort harness info ...", file=sys.stderr)
    else:
        print("Successfully produced harness compilation!")

    return harness_result, brew_packages


def _generate_outputs(  # noqa: PLR0913 -- private helper; all 6 params are distinct required inputs
    analysis: AnalysisResult,
    output_path: Path,
    result: BuildExplorationResult,
    harness_result: HarnessExplorationResult,
    brew_packages: list[str],
    environment: Environment,
) -> int:
    """Write the output scaffold matching environment, reporting its output path."""
    from harnessbuddy.library_builder.environments.base import Environment
    from harnessbuddy.library_builder.local.generation import generate_local
    from harnessbuddy.library_builder.models import OutputDirectoryExistsError
    from harnessbuddy.library_builder.oss_fuzz.generation import generate_oss_fuzz

    try:
        if environment is Environment.OSS_FUZZ:
            generation_result = generate_oss_fuzz(analysis, output_path, result)
        else:
            generation_result = generate_local(
                analysis, output_path, result, harness_result, brew_packages=brew_packages
            )
    except OutputDirectoryExistsError as exc:
        print(f"Output directory already exists: {exc}", file=sys.stderr)
        return 1

    print(f"Environment:  {environment.value}")
    print(f"Output:       {generation_result.output_path}")
    verification_command = _command_str(harness_result.command or result.command)
    if verification_command is not None:
        print(f"Verified with: {verification_command}")

    if result.compile_commands_path is not None:
        # Copied alongside output_path's environment subdirs (not into either one) —
        # it's exploration-only workspace data, not part of the shipped project, but
        # still useful to hand to extract-features without digging into
        # .harnessbuddy/<project>/.
        compile_commands_dest = output_path.parent / "compile_commands.json"
        shutil.copy2(result.compile_commands_path, compile_commands_dest)
        print(f"Compile commands: {compile_commands_dest}")

    return 0


def _command_str(command: list[str]) -> str | None:
    """Render a verification command's argv for FR-010 (report/logs record the literal
    command used to confirm success or failure)."""
    return " ".join(command) if command else None


def _write_run_stats(  # noqa: PLR0913 -- private helper; all 8 params are distinct required inputs
    base_output: Path,
    start_time: float,
    library_build_agent: AgentPhaseStats,
    harness_build_agent: AgentPhaseStats,
    status: RunStatus,
    environment: Environment,
    compile_commands_path: Path | None = None,
    verification_command: list[str] | None = None,
    build_parameters: dict[str, str] | None = None,
) -> None:
    """Build and persist stats.json for this run."""
    from harnessbuddy.library_builder.stats import RunStats, write_run_stats

    write_run_stats(
        base_output / "stats.json",
        RunStats(
            total_duration_seconds=time.monotonic() - start_time,
            library_build_agent=library_build_agent,
            harness_build_agent=harness_build_agent,
            status=status,
            environment=environment,
            compile_commands_path=str(compile_commands_path) if compile_commands_path else None,
            verification_command=_command_str(verification_command or []),
            build_parameters=build_parameters,
        ),
    )


def _select_executor(environment: Environment) -> EnvironmentExecutor:
    from harnessbuddy.library_builder.environments.base import Environment
    from harnessbuddy.library_builder.environments.local import LocalExecutor
    from harnessbuddy.library_builder.environments.oss_fuzz import OssFuzzExecutor

    if environment is Environment.OSS_FUZZ:
        return OssFuzzExecutor()
    return LocalExecutor()


def _merge_agent_error_dependencies(
    exc: BuildFailureError | LLMBudgetError,
    state: DependencyState,
    state_file: Path,
    source: DependencySource,
) -> None:
    """Persist any apt/brew packages an agent reported before raising a stop-for-human error."""
    from harnessbuddy.library_builder import dependency_resolution

    if not (exc.report and (exc.report.missing_apt_packages or exc.report.missing_brew_packages)):
        return
    dependencies = dependency_resolution.from_agent_report(
        [], exc.report.missing_apt_packages, exc.report.missing_brew_packages, source=source
    )
    dependency_resolution.merge(state, dependencies)
    dependency_resolution.save_state(state_file, state)


def _agent_stop_diagnostic(
    phase: Phase, exc: BuildFailureError | LLMBudgetError, log_path: Path
) -> FailureDiagnostic:
    """Build the diagnostic for an agent stop-for-human/budget-limited outcome. Does not
    re-print exc.output as raw_output text by default — that's the agent's full
    transcript, which already streamed live to the terminal during the agent run
    (run_agent_streaming); --log-level debug still inlines it via format_diagnostic."""
    from harnessbuddy.library_builder.agents import LLMBudgetError

    message = exc.report.summary if exc.report and exc.report.summary else str(exc)
    if exc.report and (exc.report.missing_apt_packages or exc.report.missing_brew_packages):
        apt_hint = " ".join(exc.report.missing_apt_packages) or "(none mapped)"
        brew_hint = " ".join(exc.report.missing_brew_packages) or "(none mapped)"
        message = (
            f"{message}\n\n"
            f"Missing system packages:\n"
            f"  apt:  {apt_hint}\n"
            f"  brew: {brew_hint}\n"
            "Install these packages and re-run."
        )
    step = (
        "LLM usage/rate limit"
        if isinstance(exc, LLMBudgetError)
        else "LLM repair attempt (action required)"
    )
    return build_diagnostic(phase, step=step, message=message, origin="agent", log_path=log_path)


def _build_result_from_agent_error(
    exc: BuildFailureError | LLMBudgetError, analysis: AnalysisResult, environment: Environment
) -> BuildExplorationResult:
    """Synthesize a failed BuildExplorationResult from a stop-for-human/budget-limited
    library-build agent error, so --skip-validation can still continue the pipeline to
    the harness phase and generation instead of aborting with no output at all.
    """
    from harnessbuddy.library_builder.models import BuildExplorationResult

    return BuildExplorationResult(
        build_system=analysis.build_system,
        succeeded=False,
        command=[],
        stdout=exc.output,
        stderr="",
        exit_code=-1,
        duration_seconds=exc.summary.duration_seconds,
        llm_used=True,
        cost_usd=exc.summary.cost_usd,
        input_tokens=exc.summary.input_tokens,
        output_tokens=exc.summary.output_tokens,
        agent_summary=exc.report.summary if exc.report else None,
        missing_apt_packages=exc.report.missing_apt_packages if exc.report else [],
        missing_brew_packages=exc.report.missing_brew_packages if exc.report else [],
        extra_include_paths=exc.report.extra_include_paths if exc.report else [],
        extra_library_paths=exc.report.extra_library_paths if exc.report else [],
        environment=environment,
    )


def _harness_result_from_agent_error(
    exc: BuildFailureError | LLMBudgetError, install_dir: Path, environment: Environment
) -> HarnessExplorationResult:
    """Synthesize a failed HarnessExplorationResult from a stop-for-human/budget-limited
    harness-build agent error, so --skip-validation can still continue to generation
    instead of aborting with no output at all.
    """
    from harnessbuddy.library_builder.models import HarnessExplorationResult

    return HarnessExplorationResult(
        succeeded=False,
        command=[],
        static_libs=sorted((install_dir / "lib").glob("*.a")),
        include_dir=install_dir / "include",
        transitive_link_flags=[],
        stdout=exc.output,
        stderr="",
        exit_code=-1,
        llm_used=True,
        duration_seconds=exc.summary.duration_seconds,
        cost_usd=exc.summary.cost_usd,
        input_tokens=exc.summary.input_tokens,
        output_tokens=exc.summary.output_tokens,
        agent_summary=exc.report.summary if exc.report else None,
        missing_apt_packages=exc.report.missing_apt_packages if exc.report else [],
        missing_brew_packages=exc.report.missing_brew_packages if exc.report else [],
        extra_include_paths=exc.report.extra_include_paths if exc.report else [],
        extra_library_paths=exc.report.extra_library_paths if exc.report else [],
        environment=environment,
    )


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


def _handle_library_build_failure(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    result: BuildExplorationResult,
    environment: Environment,
    *,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
    debug: bool,
    log_path: Path | None,
    parameters: BuildParameters,
) -> int | None:
    """Report a failed library build. Returns an exit code to stop the pipeline, or
    None to continue past it — --skip-validation extends to also skip this per-stage
    environment gate (spec 009 research.md decision #7): both stages still run to
    produce the artifacts generation needs, but a failing stage no longer blocks
    progressing to generation.

    This is the single diagnostic-printing site for a failed library build, whether
    result came from a plain deterministic/agent-attempted build or (via
    _build_result_from_agent_error) a stop-for-human/budget-limited agent error
    converted to a synthetic result under --skip-validation — _handle_library_agent_error
    does not print its own diagnostic in that case, to avoid printing the same failure
    twice (research.md addendum).
    """
    from harnessbuddy.library_builder.stats import (
        RunStatus,
        agent_phase_stats_from_build,
        not_invoked_agent_stats,
    )

    phase = Phase.AGENT_LIBRARY_REPAIR if result.llm_used else Phase.STATIC_LIBRARY_BUILD
    origin = "agent" if result.llm_used else "deterministic"
    step = "LLM repair attempt" if result.llm_used else "static build command"
    message = (
        result.agent_summary
        if (result.llm_used and result.agent_summary)
        else summarize_message(result.stdout)
    )
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
    if not skip_validation:
        _write_run_stats(
            base_output,
            start_time,
            agent_phase_stats_from_build(result),
            not_invoked_agent_stats(),
            RunStatus.FAILED_LIBRARY_BUILD,
            environment,
            result.compile_commands_path,
            result.command,
            parameters.to_dict(),
        )
        return 1
    print(
        "--skip-validation set: continuing to the harness phase and generation "
        "despite the failed library build.",
        file=sys.stderr,
    )
    return None


def _report_library_build_result(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    result: BuildExplorationResult,
    environment: Environment,
    *,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
    debug: bool,
    logs_dir: Path | None,
    parameters: BuildParameters,
) -> int | None:
    """Print the library-build outcome. Returns an exit code to stop the pipeline on
    failure (unless skip_validation), or None to continue to the harness phase."""
    if not result.succeeded:
        log_path = (
            result.transcript_path
            if result.llm_used
            else (logs_dir / f"{Phase.STATIC_LIBRARY_BUILD.value}.log" if logs_dir else None)
        )
        return _handle_library_build_failure(
            result,
            environment,
            skip_validation=skip_validation,
            base_output=base_output,
            start_time=start_time,
            debug=debug,
            log_path=log_path,
            parameters=parameters,
        )
    print("Successfully produced library build!")
    if result.compile_commands_path is not None:
        print(f"Compile commands: {result.compile_commands_path}")
    else:
        print(f"Compile commands: not captured ({result.compile_commands_error})")
    return None


def _final_run_status(
    result: BuildExplorationResult, harness_result: HarnessExplorationResult
) -> RunStatus:
    """The first-failing-stage wins: a library-build failure that was carried past the
    --skip-validation gate is still reported as such, even if the harness stage (run
    against whatever partial install artifacts exist) also fails."""
    from harnessbuddy.library_builder.stats import RunStatus

    if not result.succeeded:
        return RunStatus.FAILED_LIBRARY_BUILD
    if not harness_result.succeeded:
        return RunStatus.FAILED_HARNESS_BUILD
    return RunStatus.SUCCESS


def _print_run_summary(status: RunStatus) -> None:
    """Print a final, unambiguous overall-outcome line.

    Output generation's own PhaseReporter banner reports that phase's narrow job
    (writing scaffold files) succeeding, which it does even when an earlier build
    phase failed and the pipeline only reached generation via best-effort harness
    handling or --skip-validation — without this, that's the last thing printed, and
    reads as an overall success when it wasn't.
    """
    from harnessbuddy.library_builder.stats import RunStatus

    if status is RunStatus.SUCCESS:
        print("Overall: SUCCESS")
        return
    reason = (
        "static library build failed"
        if status is RunStatus.FAILED_LIBRARY_BUILD
        else "harness compile probe failed"
    )
    print(f"Overall: FAILED ({reason} — see diagnostic above)", file=sys.stderr)


def _handle_library_agent_error(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    exc: BuildFailureError | LLMBudgetError,
    analysis: AnalysisResult,
    environment: Environment,
    state: DependencyState,
    state_file: Path,
    workspace: Path,
    *,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
    debug: bool,
    parameters: BuildParameters,
) -> BuildExplorationResult | int:
    """Handle a library-build agent's stop-for-human/budget-limited error: merge any
    reported packages, report the outcome, and either stop the pipeline (returning an
    exit code) or hand back a synthetic failed result to continue with under
    --skip-validation (both stages still run to produce the artifacts generation
    needs, matching the deterministic-failure path in _handle_library_build_failure).

    Only prints its own diagnostic when stopping — when continuing under
    --skip-validation, the synthetic result it returns will flow through
    _report_library_build_result -> _handle_library_build_failure, which is the sole
    diagnostic-printing site for that path (research.md addendum: avoids the
    pre-existing duplicate print of the same agent summary).
    """
    from harnessbuddy.library_builder.dependency_resolution import DependencySource
    from harnessbuddy.library_builder.stats import (
        RunStatus,
        agent_phase_stats_from_agent_error,
        not_invoked_agent_stats,
    )

    _merge_agent_error_dependencies(exc, state, state_file, DependencySource.LIBRARY_AGENT)
    if not skip_validation:
        diagnostic = _agent_stop_diagnostic(
            Phase.AGENT_LIBRARY_REPAIR, exc, workspace / "agent_library_build.log"
        )
        print(format_diagnostic(diagnostic, debug=debug, raw_output=exc.output), file=sys.stderr)
        _write_run_stats(
            base_output,
            start_time,
            agent_phase_stats_from_agent_error(exc.summary, exc.report),
            not_invoked_agent_stats(),
            RunStatus.FAILED_LIBRARY_BUILD,
            environment,
            build_parameters=parameters.to_dict(),
        )
        return 1
    return _build_result_from_agent_error(exc, analysis, environment)


def _handle_harness_agent_error(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    exc: BuildFailureError | LLMBudgetError,
    analysis: AnalysisResult,
    environment: Environment,
    state: DependencyState,
    state_file: Path,
    install_dir: Path,
    result: BuildExplorationResult,
    workspace: Path,
    *,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
    debug: bool,
    parameters: BuildParameters,
) -> tuple[HarnessExplorationResult, list[str]] | int:
    """Handle a harness-build agent's stop-for-human/budget-limited error: merge any
    reported packages, report the outcome, and either stop the pipeline (returning an
    exit code) or hand back a synthetic failed result plus brew packages to continue
    with under --skip-validation, so the library build's own artifacts still reach
    generation. Unlike the library-side equivalent, this always prints its own
    diagnostic — there is no downstream harness-failure-report call site that would
    otherwise duplicate it (research.md addendum).
    """
    from harnessbuddy.library_builder.dependency_resolution import DependencySource
    from harnessbuddy.library_builder.stats import (
        RunStatus,
        agent_phase_stats_from_agent_error,
        agent_phase_stats_from_build,
    )

    _merge_agent_error_dependencies(exc, state, state_file, DependencySource.HARNESS_AGENT)
    diagnostic = _agent_stop_diagnostic(
        Phase.AGENT_HARNESS_REPAIR, exc, workspace / "agent_harness_build.log"
    )
    print(format_diagnostic(diagnostic, debug=debug, raw_output=exc.output), file=sys.stderr)
    if not skip_validation:
        _write_run_stats(
            base_output,
            start_time,
            agent_phase_stats_from_build(result),
            agent_phase_stats_from_agent_error(exc.summary, exc.report),
            RunStatus.FAILED_HARNESS_BUILD,
            environment,
            result.compile_commands_path,
            result.command,
            parameters.to_dict(),
        )
        return 1
    print(
        "--skip-validation set: continuing to generation despite the harness build "
        "agent stopping for human action.",
        file=sys.stderr,
    )
    analysis.system_packages = state.apt_packages
    harness_result = _harness_result_from_agent_error(exc, install_dir, environment)
    return harness_result, state.brew_packages


def _run_library_phase_or_agent_error(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    analysis: AnalysisResult,
    workspace: Path,
    agent: str | None,
    state: DependencyState,
    state_file: Path,
    executor: EnvironmentExecutor,
    *,
    environment: Environment,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
    quiet: bool,
    debug: bool,
    logs_dir: Path | None,
    parameters: BuildParameters,
) -> BuildExplorationResult | int:
    """Run the library-build phase, converting a stop-for-human/budget-limited agent
    error into either an exit code (pipeline stops) or a synthetic failed result to
    continue with (--skip-validation)."""
    from harnessbuddy.library_builder.agents import BuildFailureError, LLMBudgetError

    try:
        return _run_library_phase(
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
    except (BuildFailureError, LLMBudgetError) as exc:
        return _handle_library_agent_error(
            exc,
            analysis,
            environment,
            state,
            state_file,
            workspace,
            skip_validation=skip_validation,
            base_output=base_output,
            start_time=start_time,
            debug=debug,
            parameters=parameters,
        )


def _run_harness_phase_or_agent_error(  # noqa: PLR0913 -- private helper; all params are distinct required inputs
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    result: BuildExplorationResult,
    agent: str | None,
    state: DependencyState,
    state_file: Path,
    executor: EnvironmentExecutor,
    *,
    environment: Environment,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
    quiet: bool,
    debug: bool,
    logs_dir: Path | None,
    parameters: BuildParameters,
) -> tuple[HarnessExplorationResult, list[str]] | int:
    """Run the harness-build phase, converting a stop-for-human/budget-limited agent
    error into either an exit code (pipeline stops) or a synthetic failed result plus
    brew packages to continue with (--skip-validation)."""
    from harnessbuddy.library_builder.agents import BuildFailureError, LLMBudgetError

    try:
        return _run_harness_phase(
            analysis,
            install_dir,
            workspace,
            result,
            agent,
            state,
            state_file,
            executor,
            quiet=quiet,
            debug=debug,
            logs_dir=logs_dir,
            parameters=parameters,
        )
    except (BuildFailureError, LLMBudgetError) as exc:
        return _handle_harness_agent_error(
            exc,
            analysis,
            environment,
            state,
            state_file,
            install_dir,
            result,
            workspace,
            skip_validation=skip_validation,
            base_output=base_output,
            start_time=start_time,
            debug=debug,
            parameters=parameters,
        )


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
    from harnessbuddy.library_builder.stats import (
        agent_phase_stats_from_build,
        agent_phase_stats_from_harness,
    )

    start_time = time.monotonic()
    state_dir = default_state_dir()
    environment = Environment(args.environment)
    executor = _select_executor(environment)
    quiet = args.quiet
    debug = args.log_level == "debug"
    parameters = BuildParameters.from_args(args)

    availability_rc = _check_environment_availability(executor, environment)
    if availability_rc is not None:
        return availability_rc

    with PhaseReporter(Phase.INGESTION) as reporter:
        source = _ingest_source(args, state_dir)
        if isinstance(source, str):
            reporter.fail()
            diagnostic = build_diagnostic(
                Phase.INGESTION,
                step="repository resolution",
                message=source,
                origin="deterministic",
            )
            print(format_diagnostic(diagnostic, debug=debug), file=sys.stderr)
            return 1
        reporter.succeed()

    with PhaseReporter(Phase.STATIC_ANALYSIS) as reporter:
        try:
            analysis = analyze(source)
        except UnsupportedRepositoryError:
            reporter.fail()
            diagnostic = build_diagnostic(
                Phase.STATIC_ANALYSIS,
                step="build-system detection",
                message="No C/C++ build signals found in this repository.",
                origin="deterministic",
            )
            print(format_diagnostic(diagnostic, debug=debug), file=sys.stderr)
            return 1
        reporter.succeed()

    output_path = _resolve_output_path(args, analysis, environment)
    base_output = output_path.parent
    base_output.mkdir(parents=True, exist_ok=True)

    workspace = project_dir(state_dir, analysis.project_name)
    logs_dir = project_logs_dir(state_dir, analysis.project_name)

    state_file = project_state_file(state_dir, analysis.project_name)
    state = dependency_resolution.load_state(state_file)
    agent = None if args.no_agents else args.agent
    outcome = _run_library_phase_or_agent_error(
        analysis,
        workspace,
        agent,
        state,
        state_file,
        executor,
        environment=environment,
        skip_validation=args.skip_validation,
        base_output=base_output,
        start_time=start_time,
        quiet=quiet,
        debug=debug,
        logs_dir=logs_dir,
        parameters=parameters,
    )
    if isinstance(outcome, int):
        return outcome
    result = outcome
    rc = _report_library_build_result(
        result,
        environment,
        skip_validation=args.skip_validation,
        base_output=base_output,
        start_time=start_time,
        debug=debug,
        logs_dir=logs_dir,
        parameters=parameters,
    )
    if rc is not None:
        return rc

    install_dir = workspace / "install"
    outcome = _run_harness_phase_or_agent_error(
        analysis,
        install_dir,
        workspace,
        result,
        agent,
        state,
        state_file,
        executor,
        environment=environment,
        skip_validation=args.skip_validation,
        base_output=base_output,
        start_time=start_time,
        quiet=quiet,
        debug=debug,
        logs_dir=logs_dir,
        parameters=parameters,
    )
    if isinstance(outcome, int):
        return outcome
    harness_result, brew_packages = outcome

    with PhaseReporter(Phase.OUTPUT_GENERATION) as reporter:
        with parameters.harness_environment():
            rc = _generate_outputs(
                analysis,
                output_path,
                result,
                harness_result,
                brew_packages,
                environment,
            )
        if rc == 0:
            reporter.succeed()
        else:
            reporter.fail()
    final_status = _final_run_status(result, harness_result)
    _print_run_summary(final_status)
    _write_run_stats(
        base_output,
        start_time,
        agent_phase_stats_from_build(result),
        agent_phase_stats_from_harness(harness_result),
        final_status,
        environment,
        result.compile_commands_path,
        harness_result.command or result.command,
        parameters.to_dict(),
    )
    return rc


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
