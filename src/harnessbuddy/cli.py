from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harnessbuddy.core.repos import RepoSource
    from harnessbuddy.library_builder.agents import BuildFailureError, LLMBudgetError
    from harnessbuddy.library_builder.dependency_resolution import DependencySource, DependencyState
    from harnessbuddy.library_builder.environments.base import Environment, EnvironmentExecutor
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
    )
    from harnessbuddy.library_builder.stats import AgentPhaseStats, RunStatus


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


def _configure_extract_features_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "build_path",
        metavar="BUILD_PATH",
        help="Directory containing compile_commands.json to extract features from.",
    )


def _configure_generate_benchmark_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "build_path",
        metavar="BUILD_PATH",
        help="Directory containing features.json from a prior extract-features run.",
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
    generate_benchmark = subparsers.add_parser(
        "generate-yaml",
        help="Convert an extracted feature artifact into an oss-fuzz-gen benchmark YAML.",
        description="Convert features.json into a curated, oss-fuzz-gen-compatible "
        "benchmark YAML file.",
    )
    _configure_generate_benchmark_parser(generate_benchmark)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    level = getattr(logging, args.log_level.upper()) if args.log_level else logging.CRITICAL + 1
    logging.basicConfig(level=level)
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
    return 0


def build_library(
    analysis: AnalysisResult,
    workspace: Path,
    executor: EnvironmentExecutor,
    *,
    agent: str | None = None,
    timeout: int = 300,
) -> BuildExplorationResult:
    """Run the executor's library build, then optionally fall back to an LLM agent.

    Returns the final BuildExplorationResult. result.llm_used is True when the
    agent path was taken.
    """
    result = executor.run_library_build(analysis, workspace, timeout=timeout)
    if not result.succeeded:
        if agent is not None:
            from harnessbuddy.library_builder.agents import invoke_library_builder_agent

            print("Deterministic library build failed, invoking library build agent")
            print("=" * 25 + "Begin Agent Output" + "=" * 25)
            result = invoke_library_builder_agent(
                analysis,
                result,
                workspace,
                tool=agent,
                environment=result.environment,
            )
        else:
            print("Library build failed and --agent argument was not provided ...")
    return result


def build_harness(  # noqa: PLR0913 -- public API; all 6 params are distinct required inputs
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    library_result: BuildExplorationResult,
    executor: EnvironmentExecutor,
    *,
    agent: str | None = None,
) -> HarnessExplorationResult:
    """Probe harness compilation, then optionally fall back to an LLM agent if it fails.

    Returns the final HarnessExplorationResult. result.llm_used is True when the
    agent path was taken. library_result's extra_include_paths/extra_library_paths
    (from the library-build agent's AgentReport, if any) are threaded into the probe.
    """
    result = executor.run_harness_compile(
        install_dir,
        workspace,
        analysis.language,
        extra_include_paths=library_result.extra_include_paths,
        extra_library_paths=library_result.extra_library_paths,
    )
    if not result.succeeded and agent is not None:
        from harnessbuddy.library_builder.agents import invoke_harness_builder_agent
        from harnessbuddy.library_builder.models import HarnessPaths

        result = invoke_harness_builder_agent(
            analysis,
            result,
            HarnessPaths(install_dir=install_dir, workdir=workspace),
            tool=agent,
            environment=result.environment,
        )
    return result


def _ingest_source(args: argparse.Namespace, state_dir: Path) -> RepoSource | int:
    """Clone or resolve the repository, returning its source path or an exit code on failure."""
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
        print(f"Repository not found: {exc}", file=sys.stderr)
        return 1
    except NoCloneableOriginError:
        print(
            "No cloneable git origin found. Provide a URL instead of a local path,"
            " or add a remote origin.",
            file=sys.stderr,
        )
        return 1


def _resolve_output_paths(args: argparse.Namespace, analysis: AnalysisResult) -> tuple[Path, Path]:
    """Determine local/oss-fuzz output paths, prompting to overwrite an existing directory."""
    base_output = (
        Path(args.output) if args.output else Path.cwd() / "output" / analysis.project_name
    )
    if base_output.exists():
        if sys.stdin.isatty():
            overwrite = input(f"Output directory {base_output} already exists. Overwrite? (y/n)")
            if overwrite != "y":
                print("Chose to not overwrite, exiting...")
                exit(0)
        else:
            print(f"Output directory {base_output} already exists, overwriting ...")
        shutil.rmtree(base_output)
    return base_output / "local", base_output / "oss-fuzz"


def _run_library_phase(  # noqa: PLR0913 -- private helper; all 6 params are distinct required inputs
    analysis: AnalysisResult,
    workspace: Path,
    agent: str | None,
    state: DependencyState,
    state_file: Path,
    executor: EnvironmentExecutor,
) -> BuildExplorationResult:
    """Build the library, persisting any packages the library-build agent reported missing."""
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder.dependency_resolution import DependencySource

    print(f"Running build in {workspace} ...")
    if agent:
        print(f"Agent fallback enabled ({agent}).")

    result = build_library(analysis, workspace, executor, agent=agent)

    if result.llm_used:
        print("Agent finished.")

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


def _print_harness_failure_message(
    harness_result: HarnessExplorationResult, apt_hint_list: list[str], brew_hint_list: list[str]
) -> None:
    """Print the best-effort-continue warning for a failed harness compilation."""
    if apt_hint_list or brew_hint_list:
        libs = ", ".join(harness_result.missing_system_libs)
        apt_hint = " ".join(apt_hint_list) or "(none mapped)"
        brew_hint = " ".join(brew_hint_list) or "(none mapped)"
        print(
            f"Harness compilation incomplete — missing system libraries: {libs}\n"
            f"  apt:  {apt_hint}\n"
            f"  brew: {brew_hint}\n"
            f"Install these packages and re-run for a complete harness build.\n"
            f"Generating output files with best-effort harness info ...",
            file=sys.stderr,
        )
    else:
        print(
            f"Harness compilation failed — generating output with stub scripts.\n"
            f"{harness_result.stderr}",
            file=sys.stderr,
        )


def _run_harness_phase(  # noqa: PLR0913 -- private helper; all 8 params are distinct required inputs
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    library_result: BuildExplorationResult,
    agent: str | None,
    state: DependencyState,
    state_file: Path,
    executor: EnvironmentExecutor,
) -> tuple[HarnessExplorationResult, list[str]]:
    """Probe harness compilation, persist any newly-discovered packages, and report status."""
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder.dependency_resolution import DependencySource

    print("Probing harness compilation ...")
    harness_result = build_harness(
        analysis, install_dir, workspace, library_result, executor, agent=agent
    )

    if harness_result.llm_used:
        print("Harness agent finished.")

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
        _print_harness_failure_message(harness_result, apt_hint_list, brew_hint_list)
    else:
        print("Successfully produced harness compilation!")

    return harness_result, brew_packages


def _generate_outputs(  # noqa: PLR0913 -- private helper; all 7 params are distinct required inputs
    analysis: AnalysisResult,
    local_output_path: Path,
    oss_output_path: Path,
    result: BuildExplorationResult,
    harness_result: HarnessExplorationResult,
    brew_packages: list[str],
    environment: Environment,
) -> int:
    """Write the local dev scaffold and OSS-Fuzz project, reporting their output paths."""
    from harnessbuddy.library_builder.local.generation import generate_local
    from harnessbuddy.library_builder.models import OutputDirectoryExistsError
    from harnessbuddy.library_builder.oss_fuzz.generation import generate_oss_fuzz

    try:
        local_result = generate_local(
            analysis, local_output_path, result, harness_result, brew_packages=brew_packages
        )
        oss_fuzz_result = generate_oss_fuzz(analysis, oss_output_path, result, harness_result)
    except OutputDirectoryExistsError as exc:
        print(f"Output directory already exists: {exc}", file=sys.stderr)
        return 1

    print(f"Environment:  {environment.value}")
    print(f"Local build:  {local_result.output_path}")
    print(f"OSS-Fuzz:     {oss_fuzz_result.output_path}")
    verification_command = _command_str(harness_result.command or result.command)
    if verification_command is not None:
        print(f"Verified with: {verification_command}")

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


def _check_environment_availability(
    executor: EnvironmentExecutor, environment: Environment
) -> int | None:
    """Return an exit code if the environment is unavailable, else None to proceed."""
    from harnessbuddy.library_builder.environments.base import EnvironmentUnavailableError

    try:
        executor.check_availability()
    except EnvironmentUnavailableError as exc:
        print(f"Environment '{environment.value}' is unavailable: {exc}", file=sys.stderr)
        return 1
    return None


def _handle_library_build_failure(
    result: BuildExplorationResult,
    environment: Environment,
    *,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
) -> int | None:
    """Report a failed library build. Returns an exit code to stop the pipeline, or
    None to continue past it — --skip-validation extends to also skip this per-stage
    environment gate (spec 009 research.md decision #7): both stages still run to
    produce the artifacts generation needs, but a failing stage no longer blocks
    progressing to generation.
    """
    from harnessbuddy.library_builder.stats import (
        RunStatus,
        agent_phase_stats_from_build,
        not_invoked_agent_stats,
    )

    print(
        f"Failed to produce valid build ({environment.value}): {result.stdout}",
        file=sys.stderr,
    )
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
        )
        return 1
    print(
        "--skip-validation set: continuing to the harness phase and generation "
        "despite the failed library build.",
        file=sys.stderr,
    )
    return None


def _report_library_build_result(
    result: BuildExplorationResult,
    environment: Environment,
    *,
    skip_validation: bool,
    base_output: Path,
    start_time: float,
) -> int | None:
    """Print the library-build outcome. Returns an exit code to stop the pipeline on
    failure (unless skip_validation), or None to continue to the harness phase."""
    if not result.succeeded:
        return _handle_library_build_failure(
            result,
            environment,
            skip_validation=skip_validation,
            base_output=base_output,
            start_time=start_time,
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


def _cmd_generate(args: argparse.Namespace) -> int:
    from harnessbuddy.core.paths import default_state_dir, project_dir, project_state_file
    from harnessbuddy.library_builder import dependency_resolution
    from harnessbuddy.library_builder.agents import BuildFailureError, LLMBudgetError
    from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze
    from harnessbuddy.library_builder.dependency_resolution import DependencySource
    from harnessbuddy.library_builder.environments.base import Environment
    from harnessbuddy.library_builder.stats import (
        RunStatus,
        agent_phase_stats_from_agent_error,
        agent_phase_stats_from_build,
        agent_phase_stats_from_harness,
        not_invoked_agent_stats,
    )

    start_time = time.monotonic()
    state_dir = default_state_dir()
    environment = Environment(args.environment)
    executor = _select_executor(environment)

    availability_rc = _check_environment_availability(executor, environment)
    if availability_rc is not None:
        return availability_rc

    source = _ingest_source(args, state_dir)
    if isinstance(source, int):
        return source

    try:
        analysis = analyze(source)
    except UnsupportedRepositoryError:
        print("No C/C++ build signals found in this repository.", file=sys.stderr)
        return 1

    local_output_path, oss_output_path = _resolve_output_paths(args, analysis)
    base_output = local_output_path.parent
    base_output.mkdir(parents=True, exist_ok=True)

    workspace = project_dir(state_dir, analysis.project_name)

    state_file = project_state_file(state_dir, analysis.project_name)
    state = dependency_resolution.load_state(state_file)
    agent = None if args.no_agents else args.agent

    try:
        result = _run_library_phase(analysis, workspace, agent, state, state_file, executor)
    except (BuildFailureError, LLMBudgetError) as exc:
        _merge_agent_error_dependencies(exc, state, state_file, DependencySource.LIBRARY_AGENT)
        print(
            f"Agent requires user action before the build can proceed:\n{exc.output}",
            file=sys.stderr,
        )
        _write_run_stats(
            base_output,
            start_time,
            agent_phase_stats_from_agent_error(exc.summary, exc.report),
            not_invoked_agent_stats(),
            RunStatus.FAILED_LIBRARY_BUILD,
            environment,
        )
        return 1
    rc = _report_library_build_result(
        result,
        environment,
        skip_validation=args.skip_validation,
        base_output=base_output,
        start_time=start_time,
    )
    if rc is not None:
        return rc

    install_dir = workspace / "install"
    try:
        harness_result, brew_packages = _run_harness_phase(
            analysis,
            install_dir,
            workspace,
            result,
            agent,
            state,
            state_file,
            executor,
        )
    except (BuildFailureError, LLMBudgetError) as exc:
        _merge_agent_error_dependencies(exc, state, state_file, DependencySource.HARNESS_AGENT)
        print(
            f"Agent requires user action before the harness build can proceed:\n{exc.output}",
            file=sys.stderr,
        )
        _write_run_stats(
            base_output,
            start_time,
            agent_phase_stats_from_build(result),
            agent_phase_stats_from_agent_error(exc.summary, exc.report),
            RunStatus.FAILED_HARNESS_BUILD,
            environment,
            result.compile_commands_path,
            result.command,
        )
        return 1

    rc = _generate_outputs(
        analysis,
        local_output_path,
        oss_output_path,
        result,
        harness_result,
        brew_packages,
        environment,
    )
    _write_run_stats(
        base_output,
        start_time,
        agent_phase_stats_from_build(result),
        agent_phase_stats_from_harness(harness_result),
        _final_run_status(result, harness_result),
        environment,
        result.compile_commands_path,
        harness_result.command or result.command,
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
            build_path, target_name=args.target_name, target_path=args.target_path
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
