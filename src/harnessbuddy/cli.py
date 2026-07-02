from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from harnessbuddy.core.repos import RepoSource
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
    )
    from harnessbuddy.library_builder.stats import AgentPhaseStats, RunStatus


class _ProjectState(TypedDict):
    version: int
    apt_packages: list[str]
    brew_packages: list[str]
    unknown_libs: list[str]
    sources: dict[str, list[str]]


def _empty_state() -> _ProjectState:
    return {
        "version": 1,
        "apt_packages": [],
        "brew_packages": [],
        "unknown_libs": [],
        "sources": {},
    }


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
        "--skip-validation",
        action="store_true",
        help="Skip oss-fuzz validation after project generation.",
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
        "output_dir",
        metavar="OUTPUT_DIR",
        help="Directory containing compile_commands.json to extract features from.",
    )


def _configure_generate_benchmark_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "output_dir",
        metavar="OUTPUT_DIR",
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
        "generate-benchmark",
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
    if args.command == "generate-benchmark":
        return _cmd_generate_benchmark(args)
    return 0


def load_project_state(state_file: Path) -> _ProjectState:
    """Load state.json for a project; return empty state if absent or malformed."""
    if not state_file.exists():
        return _empty_state()
    try:
        data = json.loads(state_file.read_text())
        state = _empty_state()
        if isinstance(data.get("apt_packages"), list):
            state["apt_packages"] = [str(p) for p in data["apt_packages"]]
        if isinstance(data.get("brew_packages"), list):
            state["brew_packages"] = [str(p) for p in data["brew_packages"]]
        if isinstance(data.get("unknown_libs"), list):
            state["unknown_libs"] = [str(p) for p in data["unknown_libs"]]
        if isinstance(data.get("sources"), dict):
            state["sources"] = {
                k: [str(p) for p in v] for k, v in data["sources"].items() if isinstance(v, list)
            }
        return state
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def save_project_state(state_file: Path, state: _ProjectState) -> None:
    """Write state.json for a project."""
    state_file.write_text(json.dumps(state, indent=2))


def merge_packages_into_state(
    state: _ProjectState,
    *,
    apt_packages: list[str],
    brew_packages: list[str],
    unknown_libs: list[str],
    source_tag: str,
) -> None:
    """Union new packages into state in-place, deduplicating while preserving order."""
    state["apt_packages"] = list(dict.fromkeys(state["apt_packages"] + apt_packages))
    state["brew_packages"] = list(dict.fromkeys(state["brew_packages"] + brew_packages))
    state["unknown_libs"] = list(dict.fromkeys(state["unknown_libs"] + unknown_libs))
    existing = state["sources"].get(source_tag, [])
    state["sources"][source_tag] = list(dict.fromkeys(existing + apt_packages))


def build_library(
    analysis: AnalysisResult,
    workspace: Path,
    *,
    agent: str | None = None,
    timeout: int = 300,
) -> BuildExplorationResult:
    """Run explore, then optionally fall back to an LLM agent if the build fails.

    Returns the final BuildExplorationResult. result.llm_used is True when the
    agent path was taken.
    """
    from harnessbuddy.library_builder.exploration import explore

    result = explore(analysis, workspace, timeout=timeout)
    if not result.succeeded:
        if agent is not None:
            from harnessbuddy.library_builder.agents import invoke_library_builder_agent

            print("Deterministic library build failed, invoking library build agent")
            print("=" * 25 + "Begin Agent Output" + "=" * 25)
            result = invoke_library_builder_agent(analysis, result, workspace, tool=agent)
        else:
            print("Library build failed and --agent argument was not provided ...")
    return result


def build_harness(
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    library_result: BuildExplorationResult,
    *,
    agent: str | None = None,
) -> HarnessExplorationResult:
    """Probe harness compilation, then optionally fall back to an LLM agent if it fails.

    Returns the final HarnessExplorationResult. result.llm_used is True when the
    agent path was taken. library_result's extra_include_paths/extra_library_paths
    (from the library-build agent's AgentReport, if any) are threaded into the probe.
    """
    from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

    result = explore_harness_compilation(
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
            analysis, result, HarnessPaths(install_dir=install_dir, workdir=workspace), tool=agent
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


def _run_library_phase(
    analysis: AnalysisResult,
    workspace: Path,
    agent: str | None,
    state: _ProjectState,
    state_file: Path,
) -> BuildExplorationResult:
    """Build the library, persisting any packages the library-build agent reported missing."""
    print(f"Running host build in {workspace} ...")
    if agent:
        print(f"Agent fallback enabled ({agent}).")

    result = build_library(analysis, workspace, agent=agent)

    if result.llm_used:
        print("Agent finished.")

    if result.missing_apt_packages or result.missing_brew_packages:
        merge_packages_into_state(
            state,
            apt_packages=result.missing_apt_packages,
            brew_packages=result.missing_brew_packages,
            unknown_libs=[],
            source_tag="library_agent",
        )
        save_project_state(state_file, state)

    return result


def _run_harness_phase(  # noqa: PLR0913 -- private helper; all 7 params are distinct required inputs
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    library_result: BuildExplorationResult,
    agent: str | None,
    state: _ProjectState,
    state_file: Path,
) -> tuple[HarnessExplorationResult, list[str]]:
    """Probe harness compilation, persist any newly-discovered packages, and report status."""
    from harnessbuddy.library_builder.harness_explorer import lib_names_from_link_flags
    from harnessbuddy.library_builder.package_names import translate as translate_packages

    print("Probing harness compilation ...")
    harness_result = build_harness(analysis, install_dir, workspace, library_result, agent=agent)

    if harness_result.llm_used:
        print("Harness agent finished.")

    # Translate every linked dependency to packages and persist immediately, regardless
    # of whether exploration succeeded or failed. This covers both libs the linker
    # reported missing (missing_system_libs) and libs it resolved silently because the
    # exploration host already had them (transitive_link_flags).
    linked_libs = list(
        dict.fromkeys(
            harness_result.missing_system_libs
            + lib_names_from_link_flags(harness_result.transitive_link_flags)
        )
    )
    translation = None
    if linked_libs:
        translation = translate_packages(linked_libs)
        merge_packages_into_state(
            state,
            apt_packages=translation.apt_packages,
            brew_packages=translation.brew_packages,
            unknown_libs=translation.unknown_libs,
            source_tag="linker",
        )
        save_project_state(state_file, state)

    if translation is not None and translation.unknown_libs:
        unknown = ", ".join(translation.unknown_libs)
        print(
            f"Warning: no known apt/brew package mapping for: {unknown}. "
            "Install these manually before building elsewhere.",
            file=sys.stderr,
        )

    # The harness-build agent reports its own apt/brew package names directly (drawn from
    # its general knowledge of the library's packaging), bypassing the translation table above.
    if harness_result.missing_apt_packages or harness_result.missing_brew_packages:
        merge_packages_into_state(
            state,
            apt_packages=harness_result.missing_apt_packages,
            brew_packages=harness_result.missing_brew_packages,
            unknown_libs=[],
            source_tag="harness_agent",
        )
        save_project_state(state_file, state)

    # Apply accumulated apt packages so generators (Dockerfile, setup.sh) see them.
    analysis.system_packages = state["apt_packages"]
    brew_packages: list[str] = state["brew_packages"]

    if not harness_result.succeeded:
        apt_hint_list = list(
            dict.fromkeys(
                (translation.apt_packages if translation else [])
                + harness_result.missing_apt_packages
            )
        )
        brew_hint_list = list(
            dict.fromkeys(
                (translation.brew_packages if translation else [])
                + harness_result.missing_brew_packages
            )
        )
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
    else:
        print("Successfully produced harness compilation!")

    return harness_result, brew_packages


def _generate_outputs(  # noqa: PLR0913 -- private helper; all 6 params are distinct required inputs
    analysis: AnalysisResult,
    local_output_path: Path,
    oss_output_path: Path,
    result: BuildExplorationResult,
    harness_result: HarnessExplorationResult,
    brew_packages: list[str],
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

    print(f"Local build:  {local_result.output_path}")
    print(f"OSS-Fuzz:     {oss_fuzz_result.output_path}")

    return 0


def _write_run_stats(
    base_output: Path,
    start_time: float,
    library_build_agent: AgentPhaseStats,
    harness_build_agent: AgentPhaseStats,
    status: RunStatus,
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
        ),
    )


def _cmd_generate(args: argparse.Namespace) -> int:
    from harnessbuddy.core.paths import default_state_dir, project_dir, project_state_file
    from harnessbuddy.library_builder.agents import BuildFailureError, LLMBudgetError
    from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze
    from harnessbuddy.library_builder.stats import (
        RunStatus,
        agent_phase_stats_from_agent_error,
        agent_phase_stats_from_build,
        agent_phase_stats_from_harness,
        not_invoked_agent_stats,
    )

    start_time = time.monotonic()
    state_dir = default_state_dir()

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
    state = load_project_state(state_file)
    agent = None if args.no_agents else args.agent

    try:
        result = _run_library_phase(analysis, workspace, agent, state, state_file)
    except (BuildFailureError, LLMBudgetError) as exc:
        if exc.report and (exc.report.missing_apt_packages or exc.report.missing_brew_packages):
            merge_packages_into_state(
                state,
                apt_packages=exc.report.missing_apt_packages,
                brew_packages=exc.report.missing_brew_packages,
                unknown_libs=[],
                source_tag="library_agent",
            )
            save_project_state(state_file, state)
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
        )
        return 1
    if not result.succeeded:
        print(f"Failed to produce valid build: {result.stdout}", file=sys.stderr)
        _write_run_stats(
            base_output,
            start_time,
            agent_phase_stats_from_build(result),
            not_invoked_agent_stats(),
            RunStatus.FAILED_LIBRARY_BUILD,
        )
        return 1
    print("Successfully produced library build!")

    install_dir = workspace / "install"
    try:
        harness_result, brew_packages = _run_harness_phase(
            analysis, install_dir, workspace, result, agent, state, state_file
        )
    except (BuildFailureError, LLMBudgetError) as exc:
        if exc.report and (exc.report.missing_apt_packages or exc.report.missing_brew_packages):
            merge_packages_into_state(
                state,
                apt_packages=exc.report.missing_apt_packages,
                brew_packages=exc.report.missing_brew_packages,
                unknown_libs=[],
                source_tag="harness_agent",
            )
            save_project_state(state_file, state)
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
        )
        return 1

    rc = _generate_outputs(
        analysis, local_output_path, oss_output_path, result, harness_result, brew_packages
    )
    _write_run_stats(
        base_output,
        start_time,
        agent_phase_stats_from_build(result),
        agent_phase_stats_from_harness(harness_result),
        RunStatus.SUCCESS if harness_result.succeeded else RunStatus.FAILED_HARNESS_BUILD,
    )
    return rc


def _cmd_extract_features(args: argparse.Namespace) -> int:
    from harnessbuddy.feature_extractor.extraction import (
        FeatureArtifactError,
        MissingCompileCommandsError,
        extract_features,
    )
    from harnessbuddy.feature_extractor.native_build import NativeBuildError

    output_dir = Path(args.output_dir)
    try:
        result = extract_features(output_dir)
    except (MissingCompileCommandsError, NativeBuildError, FeatureArtifactError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"Extracted {len(result.functions)} functions, {len(result.typedefs)} typedefs, "
        f"{len(result.macros)} macros, {len(result.enums)} enums, "
        f"{len(result.records)} records -> {output_dir / 'features.json'}"
    )
    return 0


def _cmd_generate_benchmark(args: argparse.Namespace) -> int:
    from harnessbuddy.feature_extractor.benchmark_yaml import generate_benchmark
    from harnessbuddy.feature_extractor.extraction import (
        FeatureArtifactError,
        MissingFeatureArtifactError,
    )

    output_dir = Path(args.output_dir)
    try:
        benchmark = generate_benchmark(
            output_dir, target_name=args.target_name, target_path=args.target_path
        )
    except (MissingFeatureArtifactError, FeatureArtifactError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"Generated benchmark for {len(benchmark.functions)} public functions -> "
        f"{output_dir / f'{benchmark.project}.yaml'}"
    )
    return 0


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "git://", "ssh://", "git@"))
