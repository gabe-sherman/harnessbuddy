"""Generation publishes the verified workspace as the output directory.

One output shape for both environments: the workspace copied verbatim, plus the host-side
resources — setup.sh, install/, the build's compile_commands.json, and a README naming the
environment that was verified.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.build_parameters import BuildParameters
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.generation import (
    GenerationInputs,
    MissingInstallTreeError,
    generate,
    usable_compile_commands,
)
from harnessbuddy.library_builder.models import (
    AnalysisResult,
    BuildExplorationResult,
    BuildSystem,
    HarnessExplorationResult,
)
from harnessbuddy.library_builder.workspace import materialize

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"

_EXPECTED_FILES = frozenset(
    {
        ".dockerignore",
        "Dockerfile",
        "README.md",
        "build.sh",
        "build_library.sh",
        "compile_harness.sh",
        "compile_harnesses.sh",
        "project.yaml",
        "setup.sh",
    }
)

_ALL_BUILD_SYSTEMS = [
    "cmake_repo",
    "meson_repo",
    "autotools_repo",
    "autotools_configure_repo",
    "autotools_autogen_repo",
    "makefile_repo",
]


def _analysis(fixture_name: str = "cmake_repo", *, repo_ref: str | None = None) -> AnalysisResult:
    source = RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name="mylib",
        repo_ref=repo_ref,
    )
    return analyze(source)


def _verified_workspace(tmp_path: Path, analysis: AnalysisResult) -> Path:
    """A workspace as a passing run leaves it: the project layout, a build_library.sh, and a
    populated install/ tree."""
    workspace = tmp_path / "workspace"
    materialize(workspace, analysis, parameters=BuildParameters.defaults())
    (workspace / "build_library.sh").write_text("#!/bin/bash\n# validated build\n")
    install = workspace / "install"
    (install / "lib").mkdir(parents=True)
    (install / "lib" / "libmylib.a").write_text("archive")
    (install / "include").mkdir()
    (install / "include" / "mylib.h").write_text("#pragma once\n")
    return workspace


def _inputs(
    analysis: AnalysisResult,
    workspace: Path,
    *,
    environment: Environment = Environment.LOCAL,
    system_packages: list[str] | None = None,
    agent_backend: str | None = None,
) -> GenerationInputs:
    return GenerationInputs(
        analysis=analysis,
        build=BuildExplorationResult(
            build_system=analysis.build_system,
            succeeded=True,
            command=["bash", "check_build.sh"],
            stdout="",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
            install_dir=workspace / "install",
            environment=environment,
        ),
        harness=HarnessExplorationResult(
            succeeded=True,
            command=["bash", "check_build.sh"],
            static_libs=[Path("libmylib.a")],
            include_dir=workspace / "install" / "include",
            transitive_link_flags=["-lpthread"],
            stdout="",
            stderr="",
            exit_code=0,
            environment=environment,
        ),
        system_packages=system_packages or [],
        environment=environment,
        agent_backend=agent_backend,
    )


def _generate(
    tmp_path: Path, *, fixture: str = "cmake_repo", **kwargs: object
) -> tuple[Path, AnalysisResult]:
    analysis = _analysis(fixture, repo_ref=kwargs.pop("repo_ref", None))  # type: ignore[arg-type]
    workspace = _verified_workspace(tmp_path, analysis)
    output = tmp_path / "output" / "mylib"
    generate(workspace, output, _inputs(analysis, workspace, **kwargs))  # type: ignore[arg-type]
    return output, analysis


# every expected file is present, for every build system and both environments


@pytest.mark.parametrize("fixture", _ALL_BUILD_SYSTEMS)
def test_all_files_generated(fixture: str, tmp_path: Path) -> None:
    output, _ = _generate(tmp_path, fixture=fixture)
    assert {p.name for p in output.iterdir()} >= _EXPECTED_FILES


@pytest.mark.parametrize("environment", list(Environment))
def test_install_tree_ships_for_both_environments(environment: Environment, tmp_path: Path) -> None:
    """compile_harness.sh links against install/, so an output directory without it hands the
    user a script that cannot run."""
    output, _ = _generate(tmp_path, environment=environment)
    assert list((output / "install" / "lib").glob("*.a"))
    assert list((output / "install" / "include").iterdir())


def test_generation_refuses_a_build_that_records_no_install_tree(tmp_path: Path) -> None:
    """Generation only runs after a verified build, so an unset install_dir means the result lost
    track of a tree that exists. Skipping it published an unusable project as a success."""
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    inputs = _inputs(analysis, workspace)
    inputs.build.install_dir = None
    with pytest.raises(MissingInstallTreeError, match="no install tree"):
        generate(workspace, tmp_path / "output" / "mylib", inputs)


def test_generation_refuses_an_install_tree_that_is_not_on_disk(tmp_path: Path) -> None:
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    inputs = _inputs(analysis, workspace)
    inputs.build.install_dir = tmp_path / "gone"
    with pytest.raises(MissingInstallTreeError, match="missing at"):
        generate(workspace, tmp_path / "output" / "mylib", inputs)


def test_generation_result_reports_the_project_and_path(tmp_path: Path) -> None:
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    output = tmp_path / "output" / "mylib"
    result = generate(workspace, output, _inputs(analysis, workspace))
    assert result.project_name == "mylib"
    assert result.output_path == output
    assert all(path.exists() for path in result.files)


# the workspace's validated scripts are copied verbatim, so what ships is what passed


@pytest.mark.parametrize(
    "name", ["build.sh", "build_library.sh", "compile_harness.sh", "compile_harnesses.sh"]
)
def test_scripts_are_copied_byte_identical(name: str, tmp_path: Path) -> None:
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    (workspace / name).write_text(f"#!/bin/bash\n# an agent edited {name}\n")
    output = tmp_path / "output" / "mylib"
    generate(workspace, output, _inputs(analysis, workspace))
    assert (output / name).read_text() == (workspace / name).read_text()


def test_copied_scripts_stay_executable(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    for name in ("build.sh", "build_library.sh", "compile_harness.sh", "compile_harnesses.sh"):
        assert (output / name).stat().st_mode & 0o111, name


def test_harness_source_copies_the_discovered_fuzzer_extension(tmp_path: Path) -> None:
    """Discovery may upgrade the stub from .c to .cc, so generation ships whichever one the
    validated workspace ended up with rather than re-deriving it."""
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    for stale in (workspace / "harness_source").glob("default_fuzzer.*"):
        stale.unlink()
    (workspace / "harness_source" / "default_fuzzer.cc").write_text("// discovered C++\n")
    (workspace / "harness_source" / "extra.c").write_text("// a second harness\n")
    output = tmp_path / "output" / "mylib"
    generate(workspace, output, _inputs(analysis, workspace))
    assert (output / "harness_source" / "default_fuzzer.cc").exists()
    assert (output / "harness_source" / "extra.c").exists()
    assert not (output / "harness_source" / "default_fuzzer.c").exists()


# Dockerfile


def test_dockerfile_drops_the_exploration_only_bear_dependency(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    assert "bear" not in (output / "Dockerfile").read_text()


def test_dockerfile_carries_the_discovered_packages(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path, system_packages=["libzstd-dev"])
    assert "libzstd-dev" in (output / "Dockerfile").read_text()


def test_dockerignore_excludes_the_build_products(tmp_path: Path) -> None:
    """The image rebuilds the library from its own clone, so sending install/ into the build
    context — hundreds of MB for a large library — only slows every build down."""
    output, _ = _generate(tmp_path)
    ignored = (output / ".dockerignore").read_text().split()
    assert "install/" in ignored
    assert "compile_commands.json" in ignored


# setup.sh


def test_setup_sh_clones_the_library(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    setup_sh = (output / "setup.sh").read_text()
    assert f'git clone --recursive {_FAKE_URL} "$SCRIPT_DIR/src"' in setup_sh


def test_setup_sh_checks_out_the_requested_ref(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path, repo_ref="v1.2.3")
    assert 'git -C "$SCRIPT_DIR/src" checkout v1.2.3' in (output / "setup.sh").read_text()


def test_setup_sh_has_no_checkout_without_a_ref(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    assert "checkout" not in (output / "setup.sh").read_text()


def test_setup_sh_updates_the_package_index_before_installing(tmp_path: Path) -> None:
    """Without the update, the install fails on a host with a stale index, and `set -e` aborts
    the script before the clone is usable."""
    content = (_generate(tmp_path, system_packages=["libzstd-dev"])[0] / "setup.sh").read_text()
    assert "apt-get update" in content
    assert content.index("apt-get update") < content.index("apt-get install")


def test_setup_sh_resolves_sudo_at_run_time(tmp_path: Path) -> None:
    """Resolved when the script runs rather than baked in, since the generating host's uid says
    nothing about the consuming host's."""
    content = (_generate(tmp_path, system_packages=["libzstd-dev"])[0] / "setup.sh").read_text()
    assert '"$(id -u)"' in content
    assert "$SUDO apt-get install" in content


def test_setup_sh_says_so_when_no_packages_were_discovered(tmp_path: Path) -> None:
    content = (_generate(tmp_path)[0] / "setup.sh").read_text()
    assert "apt-get" not in content
    assert "No build dependencies" in content


@pytest.mark.parametrize("packages", [[], ["libzstd-dev", "zlib1g-dev"]])
def test_generated_scripts_pass_shellcheck(packages: list[str], tmp_path: Path) -> None:
    """Nothing in the pipeline runs setup.sh, so a linter is the cheapest guard against a plain
    defect in it."""
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    output, _ = _generate(tmp_path, system_packages=packages)
    scripts = sorted(str(p) for p in output.glob("*.sh"))
    result = subprocess.run(
        ["shellcheck", "--severity=warning", *scripts], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


# compile_commands.json


def _workspace_compile_commands(workspace: Path, *, name: str = "compile_commands.json") -> Path:
    """A capture describing a build tree that is really there, as the gate's would be.

    The directory and the source file are created, not just named: an entry pointing at neither
    is one a clang tool cannot replay, and generation drops those.
    """
    (workspace / "build").mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "mylib.c").write_text("int mylib(void) { return 0; }\n")
    path = workspace / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "directory": f"{workspace}/build",
                    "file": f"{workspace}/src/mylib.c",
                    "arguments": ["clang", f"-I{workspace}/src/include", "-c", "mylib.c"],
                    "command": f"clang -I{workspace}/src/include -c mylib.c",
                }
            ]
        )
    )
    return path


def test_compile_commands_ships_inside_the_project_directory(tmp_path: Path) -> None:
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    output = tmp_path / "output" / "mylib"
    _workspace_compile_commands(workspace)
    generate(workspace, output, _inputs(analysis, workspace))
    assert (output / "compile_commands.json").is_file()


def test_compile_commands_paths_keep_pointing_at_the_build_tree(tmp_path: Path) -> None:
    """Tooling that consumes the shipped file chdirs into each entry's directory before it reads
    anything, and the output ships install/ alone — so the entries name the workspace, which is
    the only place that build tree exists."""
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    output = tmp_path / "output" / "mylib"
    _workspace_compile_commands(workspace)
    generate(workspace, output, _inputs(analysis, workspace))
    entry = json.loads((output / "compile_commands.json").read_text())[0]
    assert str(output) not in json.dumps(entry)
    assert entry["directory"] == f"{workspace}/build"
    assert entry["file"] == f"{workspace}/src/mylib.c"


def test_readme_says_where_the_compile_commands_paths_point(tmp_path: Path) -> None:
    """Otherwise the paths read as this directory's own, and every one of them is missing."""
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    output = tmp_path / "output" / "mylib"
    _workspace_compile_commands(workspace)
    generate(workspace, output, _inputs(analysis, workspace))
    assert str(workspace) in (output / "README.md").read_text()


def test_readme_omits_the_compile_commands_section_when_none_shipped(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    assert "## compile_commands.json" not in (output / "README.md").read_text()


def test_compile_commands_is_absent_when_the_build_captured_none(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    assert not (output / "compile_commands.json").exists()


def test_compile_commands_found_in_the_build_directory_ships(tmp_path: Path) -> None:
    """CMake and Meson write it into build/ rather than the workspace root."""
    analysis = _analysis()
    workspace = _verified_workspace(tmp_path, analysis)
    output = tmp_path / "output" / "mylib"
    _workspace_compile_commands(workspace, name="build/compile_commands.json")
    generate(workspace, output, _inputs(analysis, workspace))
    assert (output / "compile_commands.json").is_file()


def _usable_entry(tmp_path: Path, **overrides: object) -> dict:
    source = tmp_path / "a.c"
    source.write_text("int a(void) { return 0; }\n")
    entry: dict = {
        "directory": str(tmp_path),
        "file": str(source),
        "arguments": ["clang", "-c", str(source)],
    }
    entry.update(overrides)
    return entry


def test_a_replayable_entry_survives(tmp_path: Path) -> None:
    entry = _usable_entry(tmp_path)
    assert json.loads(usable_compile_commands(json.dumps([entry]))) == [entry]


def test_an_entry_whose_directory_is_gone_is_dropped(tmp_path: Path) -> None:
    """CMake deletes its CMakeScratch/TryCompile-* dirs after configure, and a clang tool chdirs
    into every entry's directory — one that is gone aborts the whole run, not just that file."""
    entry = _usable_entry(tmp_path, directory=str(tmp_path / "CMakeScratch" / "TryCompile-abc123"))
    assert json.loads(usable_compile_commands(json.dumps([entry]))) == []


def test_a_cc1_entry_is_dropped(tmp_path: Path) -> None:
    """bear records the driver's -cc1 sub-invocation as well as the driver call, and the driver
    rejects its own cc1 flags when asked to replay them."""
    entry = _usable_entry(tmp_path, arguments=["clang", "-cc1", "-triple", "x86_64-linux-gnu"])
    assert json.loads(usable_compile_commands(json.dumps([entry]))) == []


def test_a_cc1_entry_recorded_as_a_command_string_is_dropped(tmp_path: Path) -> None:
    entry = _usable_entry(tmp_path, command="clang -cc1 -triple x86_64-linux-gnu")
    del entry["arguments"]
    assert json.loads(usable_compile_commands(json.dumps([entry]))) == []


def test_a_cmake_compiler_probe_is_dropped(tmp_path: Path) -> None:
    """CompilerIdC and friends compile under CMakeFiles/ and belong to no library."""
    probe = tmp_path / "CMakeFiles" / "3.29.2" / "CompilerIdC"
    probe.mkdir(parents=True)
    (probe / "CMakeCCompilerId.c").write_text("int main(void) { return 0; }\n")
    entry = {
        "directory": str(probe),
        "file": str(probe / "CMakeCCompilerId.c"),
        "arguments": ["clang", "-c", "CMakeCCompilerId.c"],
    }
    assert json.loads(usable_compile_commands(json.dumps([entry]))) == []


def test_a_missing_source_file_is_dropped(tmp_path: Path) -> None:
    entry = _usable_entry(tmp_path, file=str(tmp_path / "deleted.c"))
    assert json.loads(usable_compile_commands(json.dumps([entry]))) == []


# README — the output has to say which environment was actually verified


@pytest.mark.parametrize(
    ("environment", "verified", "unverified"),
    [
        (Environment.OSS_FUZZ, "oss-fuzz (in the container)", "host scripts"),
        (Environment.LOCAL, "local (on the host)", "container path"),
    ],
)
def test_readme_names_the_verified_environment(
    environment: Environment, verified: str, unverified: str, tmp_path: Path
) -> None:
    """The directory provisions both environments but a run verifies one, so without this it
    implies both were exercised."""
    output, _ = _generate(tmp_path, environment=environment)
    readme = (output / "README.md").read_text()
    assert f"Verified: {verified}" in readme
    assert unverified in readme


def test_readme_describes_the_run(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path, agent_backend="claude", repo_ref="v1.2.3")
    readme = (output / "README.md").read_text()
    assert BuildSystem.CMAKE.value in readme
    assert _FAKE_URL in readme
    assert "v1.2.3" in readme
    assert "claude" in readme


def test_readme_says_no_agent_was_used_when_none_was(tmp_path: Path) -> None:
    output, _ = _generate(tmp_path)
    assert "Repair agent: none" in (output / "README.md").read_text()


def test_readme_points_at_the_harness_stub(tmp_path: Path) -> None:
    """The link line was proven against a stub that calls nothing, so the README is where the
    user learns that writing a real harness is the next step."""
    readme = (_generate(tmp_path)[0] / "README.md").read_text()
    assert "harness_source/default_fuzzer.*" in readme
    assert "stub" in readme


def test_readme_lists_the_host_commands_in_order(tmp_path: Path) -> None:
    readme = (_generate(tmp_path)[0] / "README.md").read_text()
    assert readme.index("./setup.sh") < readme.index("./build_library.sh")
    assert readme.index("./build_library.sh") < readme.index("./compile_harnesses.sh")
