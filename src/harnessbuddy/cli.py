from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from harnessbuddy.library_builder.models import (
        AnalysisResult,
        BuildExplorationResult,
        HarnessExplorationResult,
    )


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
    return 0


def load_system_deps(analysis: AnalysisResult) -> None:
    """Populate analysis.system_packages from system_deps.json written by a prior agent run.

    system_deps.json lives in the source directory and is written by the library builder
    agent when it identifies required apt packages. Loading it here means subsequent runs
    can embed the packages into the generated Dockerfile and setup.sh without re-running
    the agent.
    """
    deps_file = analysis.source_path / "system_deps.json"
    if not deps_file.exists():
        return
    try:
        data = json.loads(deps_file.read_text())
        packages = data.get("apt_packages", [])
        if isinstance(packages, list):
            analysis.system_packages = [str(p) for p in packages]
    except (json.JSONDecodeError, OSError):
        pass


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
    if not result.succeeded and agent is not None:
        from harnessbuddy.library_builder.agents import invoke_library_builder_agent

        result = invoke_library_builder_agent(analysis, result, workspace, tool=agent)
    return result


def build_harness(
    analysis: AnalysisResult,
    install_dir: Path,
    workspace: Path,
    *,
    agent: str | None = None,
) -> HarnessExplorationResult:
    """Probe harness compilation, then optionally fall back to an LLM agent if it fails.

    Returns the final HarnessExplorationResult. result.llm_used is True when the
    agent path was taken.
    """
    from harnessbuddy.library_builder.harness_explorer import explore_harness_compilation

    result = explore_harness_compilation(install_dir, workspace, analysis.language)
    if not result.succeeded and agent is not None:
        from harnessbuddy.library_builder.agents import invoke_harness_builder_agent

        result = invoke_harness_builder_agent(analysis, result, install_dir, workspace, tool=agent)
    return result


def _cmd_generate(args: argparse.Namespace) -> int:
    from harnessbuddy.core.paths import default_state_dir, project_dir, project_state_file
    from harnessbuddy.core.repos import (
        NoCloneableOriginError,
        RepositoryNotFoundError,
        ingest_local,
        ingest_url,
    )
    from harnessbuddy.library_builder.analysis import UnsupportedRepositoryError, analyze

    state_dir = default_state_dir()

    try:
        if _is_url(args.repo_url):
            source = ingest_url(
                args.repo_url,
                project_name=args.project_name,
                repo_ref=args.repo_ref,
                state_dir=state_dir,
            )
        else:
            source = ingest_local(
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

    try:
        analysis = analyze(source)
    except UnsupportedRepositoryError:
        print("No C/C++ build signals found in this repository.", file=sys.stderr)
        return 1

    parent_path = Path(args.output) if args.output else Path.cwd()
    base_output = parent_path / analysis.project_name / "output"
    if base_output.exists():
        overwrite = input(f"Output directory {base_output} already exists. Overwrite? (y/n)")
        if overwrite != "y":
            print("Chose to not overwrite, exiting...")
            exit(0)
        shutil.rmtree(base_output)
    local_output_path = base_output / "local"
    oss_output_path = base_output / "oss-fuzz"

    workspace = project_dir(state_dir, analysis.project_name)
    load_system_deps(analysis)

    state_file = project_state_file(state_dir, analysis.project_name)
    state = load_project_state(state_file)
    if analysis.system_packages:
        merge_packages_into_state(
            state,
            apt_packages=analysis.system_packages,
            brew_packages=[],
            unknown_libs=[],
            source_tag="agent",
        )
        save_project_state(state_file, state)

    agent = None if args.no_agents else args.agent
    print(f"Running host build in {workspace} ...")
    if agent:
        print(f"Agent fallback enabled ({agent}).")

    result = build_library(analysis, workspace, agent=agent)

    if result.llm_used:
        print("Agent finished.")

    if not result.succeeded:
        print(f"Failed to produce valid build: {result.stdout}", file=sys.stderr)
        return 1

    print("Successfully produced library build!")
    from harnessbuddy.library_builder.local.generation import generate_local
    from harnessbuddy.library_builder.models import OutputDirectoryExistsError
    from harnessbuddy.library_builder.oss_fuzz.generation import generate_oss_fuzz
    from harnessbuddy.library_builder.package_names import translate as translate_packages

    install_dir = workspace / "install"
    print("Probing harness compilation ...")
    harness_result = build_harness(analysis, install_dir, workspace, agent=agent)

    if harness_result.llm_used:
        print("Harness agent finished.")

    # Translate any linker-reported missing libs to packages and persist immediately,
    # regardless of whether exploration succeeded or failed.
    translation = None
    if harness_result.missing_system_libs:
        translation = translate_packages(harness_result.missing_system_libs)
        merge_packages_into_state(
            state,
            apt_packages=translation.apt_packages,
            brew_packages=translation.brew_packages,
            unknown_libs=translation.unknown_libs,
            source_tag="linker",
        )
        save_project_state(state_file, state)

    # Apply accumulated apt packages so generators (Dockerfile, setup.sh) see them.
    analysis.system_packages = state["apt_packages"]
    brew_packages: list[str] = state["brew_packages"]

    if not harness_result.succeeded:
        if translation is not None:
            libs = ", ".join(harness_result.missing_system_libs)
            apt_hint = " ".join(translation.apt_packages) or "(none mapped)"
            brew_hint = " ".join(translation.brew_packages) or "(none mapped)"
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


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "git://", "ssh://", "git@"))
