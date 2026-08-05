"""Publishing the verified workspace as the output directory.

There is one output shape, whichever environment verified it: the workspace copied verbatim,
so the shipped scripts are the ones that passed, plus what only makes sense outside the
workspace — `setup.sh`, the built `install/` tree, the build's `compile_commands.json`, and a
README naming the environment this run actually exercised.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from harnessbuddy.core.files import copy_executable, write_executable
from harnessbuddy.library_builder import workspace
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    GenerationResult,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.scripts import HARNESS_SOURCE_DIR

# Copied from the workspace verbatim: the validated scripts, plus the OSS-Fuzz project files
# around them.
_COPIED_SCRIPTS = ("build.sh", "build_library.sh", "compile_harness.sh", "compile_harnesses.sh")
_COPIED_FILES = ("project.yaml",)

# Build products and run metadata: worth shipping, but never part of the docker build context,
# since the container rebuilds from its own fresh clone.
_DOCKERIGNORE = ("install/\n", "compile_commands.json\n", "stats.json\n", "logs/\n", "out/\n")


class MissingInstallTreeError(Exception):
    """Generation was asked to publish a build whose install tree it cannot find."""


@dataclass(frozen=True)
class GenerationInputs:
    """Everything generation needs beyond the workspace itself.

    Grouped because they all answer one question — what this run actually did — and each
    appears in the README as well as in a generated file.
    """

    analysis: AnalysisResult
    build: BuildExplorationResult
    harness: HarnessExplorationResult
    system_packages: list[str]
    environment: Environment
    agent_backend: str | None


def generate(workspace_dir: Path, output_path: Path, inputs: GenerationInputs) -> GenerationResult:
    """Copy the verified workspace to output_path and add the host-side resources.

    The output is itself an OSS-Fuzz project and a standalone host build tree at once:
    `docker build .` works, and so does `setup.sh` followed by `build_library.sh`.
    """
    output_path.mkdir(parents=True)
    files: list[Path] = [
        *_copy_project_files(workspace_dir, output_path, inputs.system_packages),
        *_copy_harness_source(workspace_dir, output_path),
        _write_setup_sh(output_path, inputs.analysis, inputs.system_packages),
        _write_dockerignore(output_path),
    ]
    _copy_install_tree(inputs.build, output_path)
    compile_commands = _copy_compile_commands(workspace_dir, output_path)
    if compile_commands is not None:
        files.append(compile_commands)
    files.append(
        write_readme(output_path, inputs, build_tree=workspace_dir if compile_commands else None)
    )

    return GenerationResult(
        project_name=inputs.analysis.project_name,
        output_path=output_path,
        files=files,
    )


def _copy_project_files(
    workspace_dir: Path, output_path: Path, system_packages: list[str]
) -> list[Path]:
    """Copy the scripts and project files the workspace validated, verbatim.

    The Dockerfile is the exception: the shipped copy must not depend on `bear`, which the
    workspace image carries only so compile_commands.json can be captured.
    """
    copied = [copy_executable(workspace_dir / name, output_path / name) for name in _COPIED_SCRIPTS]
    for name in _COPIED_FILES:
        shutil.copy2(workspace_dir / name, output_path / name)
        copied.append(output_path / name)

    dockerfile = output_path / "Dockerfile"
    dockerfile.write_text(
        workspace.strip_bear_dependency((workspace_dir / "Dockerfile").read_text())
    )
    workspace.inject_apt_packages(output_path, system_packages)
    copied.append(dockerfile)
    return copied


def _copy_harness_source(workspace_dir: Path, output_path: Path) -> list[Path]:
    """Copy harness_source/* verbatim, including whichever default_fuzzer extension discovery
    settled on."""
    source_dir = workspace_dir / HARNESS_SOURCE_DIR
    destination_dir = output_path / HARNESS_SOURCE_DIR
    destination_dir.mkdir()
    copied: list[Path] = []
    for entry in sorted(source_dir.iterdir()):
        destination = destination_dir / entry.name
        shutil.copy2(entry, destination)
        copied.append(destination)
    return copied


def _copy_install_tree(build: BuildExplorationResult, output_path: Path) -> None:
    """Publish the built library — the artifact a user links their first harness against.

    Shipped for every environment: compile_harness.sh links against install/, so an output
    directory without it hands the user a script that cannot run. Raises rather than skipping,
    because generation only runs after a verified build: a missing tree here means the result
    lost track of one that exists, and skipping it published an unusable project as a success.
    """
    install_dir = build.install_dir
    if install_dir is None:
        msg = (
            "the verified build result records no install tree, so the generated project has "
            "no library for compile_harness.sh to link against"
        )
        raise MissingInstallTreeError(msg)
    if not install_dir.is_dir():
        msg = f"the verified build's install tree is missing at {install_dir}"
        raise MissingInstallTreeError(msg)
    shutil.copytree(install_dir, output_path / "install", symlinks=True)


def _copy_compile_commands(workspace_dir: Path, output_path: Path) -> Path | None:
    """Copy compile_commands.json into the output directory, keeping its workspace paths.

    A compilation database only means anything next to the build tree it describes, and the
    output directory ships `install/` alone -- no `src/`, no `build/`. So the paths are left
    pointing at `.harnessbuddy/<project>/`, where the tree that produced them still stands;
    rewriting them to the output directory named files that were never copied there, and
    tooling chdirs into each entry's `directory` before it reads anything.
    """
    captured = workspace.find_compile_commands(workspace_dir)
    if captured is None:
        return None
    destination = output_path / "compile_commands.json"
    destination.write_text(usable_compile_commands(captured.read_text()))
    return destination


def _write_dockerignore(output_path: Path) -> Path:
    """Keep the build products out of the docker build context.

    The image rebuilds the library from its own clone, so sending install/ — hundreds of MB
    for a large library — would only slow every build down.
    """
    path = output_path / ".dockerignore"
    path.write_text("".join(_DOCKERIGNORE))
    return path


def _write_setup_sh(
    output_path: Path, analysis: AnalysisResult, system_packages: list[str]
) -> Path:
    """Write setup.sh — clone the library and install its build dependencies.

    The host counterpart of the Dockerfile's clone and apt layers, so it uses the same
    `apt-get update && apt-get install` form. `sudo` is resolved when the script runs, since
    the generating host's uid says nothing about the consuming host's.
    """
    lines = [
        "#!/bin/bash\n",
        "set -euo pipefail\n",
        "\n",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n',
        "\n",
        f'git clone --recursive {analysis.clone_url} "$SCRIPT_DIR/src"\n',
    ]
    if analysis.repo_ref is not None:
        lines.append(f'git -C "$SCRIPT_DIR/src" checkout {analysis.repo_ref}\n')
    lines.append("\n")
    if system_packages:
        lines += [
            'if [ "$(id -u)" -eq 0 ]; then\n',
            '  SUDO=""\n',
            "else\n",
            '  SUDO="sudo"\n',
            "fi\n",
            "$SUDO apt-get update\n",
            f"$SUDO apt-get install -y --no-install-recommends {' '.join(system_packages)}\n",
        ]
    else:
        lines.append("# No build dependencies were discovered for this library.\n")
    return write_executable(output_path / "setup.sh", "".join(lines))


def usable_compile_commands(text: str) -> str:
    """Drop the entries a clang tool cannot replay, keeping the rest verbatim.

    `bear` records every compiler exec the build made, not just the ones that compiled library
    sources. Three kinds of entry come back unusable, and each is fatal rather than noisy:
    a `directory` CMake has since deleted (its `CMakeScratch/TryCompile-*` dirs) aborts the
    whole run, because a clang tool chdirs there before it parses; a raw `-cc1` invocation is
    rejected argument by argument when the driver is asked to replay it; and CMake's own
    `CMakeFiles/` probe sources are not part of the library at all.
    """
    entries = json.loads(text)
    return json.dumps([entry for entry in entries if _entry_is_usable(entry)], indent=2)


def _entry_is_usable(entry: dict) -> bool:
    directory, file = entry.get("directory"), entry.get("file")
    if not isinstance(directory, str) or not isinstance(file, str):
        return False
    if not Path(directory).is_dir() or not Path(file).is_file():
        return False
    if "CMakeFiles" in Path(file).parts:
        return False
    arguments = entry.get("arguments")
    if isinstance(arguments, list) and "-cc1" in arguments:
        return False
    command = entry.get("command")
    return not (isinstance(command, str) and "-cc1" in command.split())


def _compile_commands_section(build_tree: Path | None) -> str:
    """The README's account of where the shipped compilation database points.

    A database is only readable next to the tree it describes, and this directory is not that
    tree, so the section names the one it is — and says nothing at all when no database shipped.
    """
    if build_tree is None:
        return ""
    return f"""
## compile_commands.json

`compile_commands.json` describes the build this run made, so its paths point into that
build tree at `{build_tree}` — not into this directory, which ships `install/` alone.
It reads correctly for as long as that tree stands; re-run HarnessBuddy to refresh it.
"""


def write_readme(output_path: Path, inputs: GenerationInputs, *, build_tree: Path | None) -> Path:
    """Write README.md — what to run, and what this run actually proved.

    The directory provisions both environments but only one was exercised, so the README says
    which. Otherwise it implies the host scripts and the Dockerfile were both verified.

    build_tree is the workspace the shipped compile_commands.json describes, or None when the
    build captured none; naming it is what stops a reader from taking those paths for this
    directory's own.
    """
    analysis = inputs.analysis
    verified, unverified = (
        ("oss-fuzz (in the container)", "The host scripts (setup.sh, build_library.sh) are")
        if inputs.environment is Environment.OSS_FUZZ
        else (
            "local (on the host)",
            "The container path (Dockerfile, build.sh under `compile`) is",
        )
    )
    harness_stub = f"{HARNESS_SOURCE_DIR}/default_fuzzer.*"
    text = f"""# {analysis.project_name} — fuzzing build

Prepared by HarnessBuddy. This directory is both an OSS-Fuzz project and a standalone
host build tree.

## What this run verified

- **Verified: {verified}.** {unverified} generated but was not executed by this run.
- Build system: {analysis.build_system.value}
- Repository: {analysis.clone_url} (ref: {analysis.repo_ref or "default branch"})
- Repair agent: {inputs.agent_backend or "none"}
- Verified with: `{" ".join(inputs.harness.command or inputs.build.command) or "n/a"}`

## Run it on the host

```bash
./setup.sh              # clone the library and install its build dependencies
./build_library.sh      # build and install it into install/
./compile_harnesses.sh  # compile every harness in {HARNESS_SOURCE_DIR}/ into out/
```

`install/` already holds the library this run built, so `compile_harnesses.sh` works
without re-running the first two steps.

## Run it as an OSS-Fuzz project

```bash
docker build -t {analysis.project_name}:fuzz .
docker run --rm --entrypoint bash {analysis.project_name}:fuzz -c compile
```

Or copy this directory into `oss-fuzz/projects/{analysis.project_name}` and use
`infra/helper.py build_fuzzers {analysis.project_name}`.

## Write a real harness

`{harness_stub}` is a stub: it returns 0 without calling the library. Drop your own
harness source into `{HARNESS_SOURCE_DIR}/` — `compile_harnesses.sh` compiles every
`.c`/`.cc`/`.cpp`/`.cxx` file in there and names each binary after its source.

Note that the link line was proven against the stub, so it confirms the static archives
and their system dependencies link — not that the installed headers in `install/include`
are usable.
{_compile_commands_section(build_tree)}"""
    path = output_path / "README.md"
    path.write_text(text)
    return path
