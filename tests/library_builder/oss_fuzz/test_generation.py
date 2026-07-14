from __future__ import annotations

from pathlib import Path

import pytest

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.analysis import analyze
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import AutotoolsSetup, BuildExplorationResult, BuildSystem
from harnessbuddy.library_builder.oss_fuzz.generation import generate_oss_fuzz
from harnessbuddy.library_builder.oss_fuzz.workspace import write_dockerfile

_FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"

_ALL_BUILD_SYSTEMS = [
    "cmake_repo",
    "meson_repo",
    "autotools_repo",
    "autotools_configure_repo",
    "autotools_autogen_repo",
    "makefile_repo",
]

_EXPECTED_TOP_LEVEL_FILES = frozenset(
    {
        "project.yaml",
        "Dockerfile",
        "build.sh",
        "build_library.sh",
        "compile_harnesses.sh",
    }
)


def _analysis(fixture_name: str, *, repo_ref: str | None = None):  # type: ignore[no-untyped-def]
    source = RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name="mylib",
        repo_ref=repo_ref,
    )
    return analyze(source)


def _fake_exploration(
    succeeded: bool = True, *, environment: Environment = Environment.OSS_FUZZ
) -> BuildExplorationResult:
    return BuildExplorationResult(
        build_system=BuildSystem.CMAKE,
        succeeded=succeeded,
        command=["cmake", "-B", "/tmp/build"],
        stdout="-- Configuring done",
        stderr="",
        exit_code=0 if succeeded else 1,
        duration_seconds=1.2,
        environment=environment,
    )


def _validated_workspace(tmp_path: Path) -> Path:
    """A minimal stand-in for the workspace OssFuzzExecutor._materialize_workspace and
    explore() leave behind — every file generate_oss_fuzz now requires to exist (FR-005),
    since there's no template-rendering fallback for a missing/incomplete workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "project.yaml").write_text("homepage: workspace-validated\n")
    (workspace / "Dockerfile").write_text(
        "FROM gcr.io/oss-fuzz-base/base-builder:ubuntu-24-04\n# validated Dockerfile\n"
    )
    (workspace / "build.sh").write_text("#!/bin/bash\n# validated build.sh\n")
    (workspace / "build_library.sh").write_text("#!/bin/bash\n# validated build\n")
    (workspace / "compile_harnesses.sh").write_text("#!/bin/bash\n# validated harness\n")
    harness_source = workspace / "harness_source"
    harness_source.mkdir()
    (harness_source / "default_fuzzer.cc").write_text("// discovered CXX is required\n")
    (harness_source / "extra_helper.c").write_text("// other harness_source content\n")
    return workspace


def _exploration_for(
    workspace: Path, *, environment: Environment = Environment.OSS_FUZZ
) -> BuildExplorationResult:
    exploration = _fake_exploration(environment=environment)
    exploration.script_path = workspace / "build_library.sh"
    return exploration


# generate_oss_fuzz smoke tests — happy path against a fully validated workspace


@pytest.mark.parametrize("fixture_name", _ALL_BUILD_SYSTEMS)
def test_all_files_generated(fixture_name: str, tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis(fixture_name), tmp_path / "out", _exploration_for(workspace)
    )
    for name in _EXPECTED_TOP_LEVEL_FILES:
        assert (result.output_path / name).exists(), f"missing: {name}"
    assert any((result.output_path / "harness_source").glob("default_fuzzer.*"))


def test_generation_result_output_path(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    out = tmp_path / "out"
    result = generate_oss_fuzz(_analysis("cmake_repo"), out, _exploration_for(workspace))
    assert result.output_path == out


def test_generation_result_project_name(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    assert result.project_name == "mylib"


def test_generation_result_all_files_exist(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    assert all(f.is_file() for f in result.files)


def test_existing_output_dir_raises(tmp_path: Path) -> None:
    output_path = tmp_path / "out"
    output_path.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        generate_oss_fuzz(_analysis("cmake_repo"), output_path)


# autotools setup detection — a property of analyze(), exercised here since these
# fixtures live under this package's fixtures/repos


def test_autotools_setup_configure(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("autotools_configure_repo")
    assert result.autotools_setup == AutotoolsSetup.CONFIGURE


def test_autotools_setup_autogen(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("autotools_autogen_repo")
    assert result.autotools_setup == AutotoolsSetup.AUTOGEN


def test_autotools_setup_autoreconf(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("autotools_repo")
    assert result.autotools_setup == AutotoolsSetup.AUTORECONF


def test_non_autotools_setup_is_none(tmp_path: Path) -> None:  # noqa: ARG001
    result = _analysis("cmake_repo")
    assert result.autotools_setup is None


# generate_oss_fuzz fails loudly without a fully validated oss-fuzz workspace — reachable
# in practice via --skip-validation after a Docker image build failure (the workspace
# never gets materialized that far) or a mismatched/absent exploration result; there is
# no template-rendering fallback to fall back to (FR-005 removed it).


def test_generate_oss_fuzz_raises_without_exploration(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"project\.yaml"):
        generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out")


def test_generate_oss_fuzz_raises_when_exploration_environment_mismatched(tmp_path: Path) -> None:
    """A local-environment result is never copied verbatim into oss-fuzz output (FR-008) —
    it wasn't validated against the container this project targets, even though its
    directory happens to contain every expected file."""
    workspace = _validated_workspace(tmp_path)
    exploration = _exploration_for(workspace, environment=Environment.LOCAL)
    with pytest.raises(FileNotFoundError):
        generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", exploration)


def test_generate_oss_fuzz_raises_when_script_path_unset(tmp_path: Path) -> None:
    exploration = _fake_exploration()
    with pytest.raises(FileNotFoundError):
        generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", exploration)


def test_generate_oss_fuzz_error_names_the_missing_file(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    (workspace / "compile_harnesses.sh").unlink()
    with pytest.raises(FileNotFoundError, match=r"compile_harnesses\.sh"):
        generate_oss_fuzz(_analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace))


# workspace copy — project.yaml/build.sh/build_library.sh/compile_harnesses.sh/
# harness_source/* copied verbatim from the validated workspace (T016, T020, FR-005);
# the Dockerfile is copied too, but with its exploration-only "bear" apt dependency
# stripped (research.md #5).


def test_workspace_copy_project_yaml_byte_identical(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    content = (result.output_path / "project.yaml").read_text()
    assert content == (workspace / "project.yaml").read_text()


def test_workspace_copy_build_sh_byte_identical(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    content = (result.output_path / "build.sh").read_text()
    assert content == (workspace / "build.sh").read_text()


def test_workspace_copy_build_library_sh_byte_identical(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    content = (result.output_path / "build_library.sh").read_text()
    assert content == (workspace / "build_library.sh").read_text()


def test_workspace_copy_compile_harnesses_sh_byte_identical(tmp_path: Path) -> None:
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    content = (result.output_path / "compile_harnesses.sh").read_text()
    assert content == (workspace / "compile_harnesses.sh").read_text()


def test_workspace_copy_harness_source_includes_discovered_default_fuzzer(tmp_path: Path) -> None:
    """The validated workspace's default_fuzzer.cc (discovery having upgraded it from .c to
    .cc on a CXX finding) is copied verbatim, not clobbered by a fresh write_default_fuzzer
    call derived from the (possibly stale) static analysis language."""
    workspace = _validated_workspace(tmp_path)
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    output_names = {p.name for p in (result.output_path / "harness_source").iterdir()}
    assert "extra_helper.c" in output_names
    assert "default_fuzzer.cc" in output_names
    assert "default_fuzzer.c" not in output_names
    assert (result.output_path / "harness_source" / "extra_helper.c").read_text() == (
        workspace / "harness_source" / "extra_helper.c"
    ).read_text()


def test_default_fuzzer_synthesized_when_workspace_harness_source_is_empty(tmp_path: Path) -> None:
    """generate_oss_fuzz's harness_source copy is the one lenient exception to "the
    workspace must have everything" — an empty (or missing) harness_source falls back to
    a synthesized stub rather than raising, since default_fuzzer.* is always producible
    from analysis alone."""
    workspace = _validated_workspace(tmp_path)
    for entry in (workspace / "harness_source").iterdir():
        entry.unlink()
    result = generate_oss_fuzz(
        _analysis("cmake_repo"), tmp_path / "out", _exploration_for(workspace)
    )
    assert any((result.output_path / "harness_source").glob("default_fuzzer.*"))


@pytest.mark.parametrize(
    "fixture_name",
    ["cmake_repo", "autotools_autogen_repo"],
    ids=["bear_is_only_package", "bear_alongside_autotools_packages"],
)
def test_workspace_copy_dockerfile_strips_bear_dependency(
    fixture_name: str, tmp_path: Path
) -> None:
    """The Dockerfile's live workspace copy always includes bear (research.md #5),
    which must never ship — covering both the case where bear is the sole apt package
    (immediately followed by a newline, not a space) and where it shares the
    install line with other packages, since a naive "bear " string replace only
    catches the latter."""
    workspace = _validated_workspace(tmp_path)
    analysis = _analysis(fixture_name)
    write_dockerfile(workspace, analysis, include_bear=True)
    result = generate_oss_fuzz(analysis, tmp_path / "out", _exploration_for(workspace))
    content = (result.output_path / "Dockerfile").read_text()
    assert "bear" not in content


def test_workspace_copy_dockerfile_preserves_agent_edits_elsewhere(tmp_path: Path) -> None:
    """Stripping bear must not clobber unrelated agent-applied fixes to the rest of the
    Dockerfile — the copy, not a regeneration from analysis, is the source of truth."""
    workspace = _validated_workspace(tmp_path)
    analysis = _analysis("cmake_repo")
    write_dockerfile(workspace, analysis, include_bear=True)
    (workspace / "Dockerfile").write_text(
        (workspace / "Dockerfile").read_text() + "# agent fix: added -DFOO=1\n"
    )
    result = generate_oss_fuzz(analysis, tmp_path / "out", _exploration_for(workspace))
    content = (result.output_path / "Dockerfile").read_text()
    assert "# agent fix: added -DFOO=1" in content
