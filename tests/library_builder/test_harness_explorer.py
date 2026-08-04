from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from harnessbuddy.core.subprocesses import RunResult
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.harness_explorer import (
    explore_harness_compilation,
    lib_names_from_link_flags,
)
from harnessbuddy.library_builder.models import Language

# lib_names_from_link_flags


def test_lib_names_from_link_flags_strips_prefix() -> None:
    assert lib_names_from_link_flags(["-lzstd", "-lz", "-llzma"]) == ["zstd", "z", "lzma"]


def test_lib_names_from_link_flags_empty_list() -> None:
    assert lib_names_from_link_flags([]) == []


# explore_harness_compilation — C -> C++ probe upgrade on C++ ABI leakage


def test_upgrades_to_cxx_on_known_abi_symbol(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    results = [
        RunResult(
            stdout="",
            stderr="undefined reference to `__cxa_throw'",
            exit_code=1,
            duration_seconds=0.1,
        ),
        RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ]

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        side_effect=results,
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is True
    assert (tmp_path / "harness_source" / "default_fuzzer.cc").exists()
    assert not (tmp_path / "harness_source" / "default_fuzzer.c").exists()


def test_upgrades_to_cxx_on_arbitrary_demangled_symbol(tmp_path: Path) -> None:
    """A symbol outside _CXX_ABI_RE's known set (e.g. a libc++ std:: destructor) must
    still trigger the C -> C++ probe upgrade, since it can only come from C++ code."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    results = [
        RunResult(
            stdout="",
            stderr="undefined reference to `std::__1::locale::~locale()'",
            exit_code=1,
            duration_seconds=0.1,
        ),
        RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ]

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        side_effect=results,
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is True
    assert (tmp_path / "harness_source" / "default_fuzzer.cc").exists()
    assert not (tmp_path / "harness_source" / "default_fuzzer.c").exists()


def test_upgrades_to_cxx_on_mangled_itanium_symbol(tmp_path: Path) -> None:
    """A raw (undemangled) Itanium-mangled symbol must also trigger the upgrade."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    results = [
        RunResult(
            stdout="",
            stderr="undefined reference to `_ZNSt3__16localeD1Ev'",
            exit_code=1,
            duration_seconds=0.1,
        ),
        RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ]

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        side_effect=results,
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is True
    assert (tmp_path / "harness_source" / "default_fuzzer.cc").exists()


def test_does_not_upgrade_to_cxx_on_plain_c_symbol(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(
            stdout="",
            stderr="undefined reference to `some_c_function'",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is False
    assert (tmp_path / "harness_source" / "default_fuzzer.c").exists()
    assert not (tmp_path / "harness_source" / "default_fuzzer.cc").exists()


# explore_harness_compilation — default link flags


def test_default_link_flags_include_pthread(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.transitive_link_flags == ["-lpthread"]
    assert result.script_path is not None
    assert "-lpthread" in result.script_path.read_text()


def test_default_link_flags_not_duplicated_when_rediscovered(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    # -lpthread is already seeded as a default, so an undefined pthread_create reference
    # (which _symbol_to_flag also maps to -lpthread) must not add a second copy of it.
    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(
            stdout="",
            stderr="undefined reference to `pthread_create'",
            exit_code=1,
            duration_seconds=0.1,
        ),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is False
    assert result.transitive_link_flags == ["-lpthread"]


# explore_harness_compilation — script_path on success


def test_script_path_set_on_success(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is True
    assert result.script_path == tmp_path / "compile_harness.sh"
    assert result.script_path is not None
    assert result.script_path.exists()
    assert (tmp_path / "compile_harnesses.sh").exists()


def test_script_path_unset_on_failure(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(
            stdout="", stderr="undefined reference to `foo'", exit_code=1, duration_seconds=0.1
        ),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.succeeded is False
    assert result.script_path is None


# explore_harness_compilation — one script text, but a per-environment way into it


def _probe_commands(tmp_path: Path, environment: Environment) -> list[list[str]]:
    """Run one successful probe attempt, returning the commands the runner was handed."""
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    seen_commands: list[list[str]] = []

    def fake_runner(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        seen_commands.append(command)
        return RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1)

    result = explore_harness_compilation(
        install_dir, tmp_path, Language.C, environment=environment, run=fake_runner
    )
    assert result.succeeded is True
    return seen_commands


def test_probe_enters_through_compile_in_the_oss_fuzz_environment(tmp_path: Path) -> None:
    """The generated scripts are environment-independent; entering them is not. In the
    container the probe goes through OSS-Fuzz's own `compile`, because that is what resolves
    SANITIZER_FLAGS into CFLAGS/CXXFLAGS and exports LIB_FUZZING_ENGINE=-fsanitize=fuzzer.
    Running compile_harnesses.sh directly there links against the /usr/lib/libFuzzingEngine.a
    the image's ENV names but compile_libfuzzer has not created — and patching only the engine
    flag yields a target with no sanitizer instrumentation at all."""
    assert _probe_commands(tmp_path, Environment.OSS_FUZZ) == [["bash", "-c", "compile"]]


def test_probe_runs_the_batch_script_directly_on_the_host(tmp_path: Path) -> None:
    """There is no `compile` on a host and nothing for it to assemble, so the generated
    script is entered directly."""
    assert _probe_commands(tmp_path, Environment.LOCAL) == [["bash", "compile_harnesses.sh"]]


def test_probe_runs_the_batch_script_in_the_local_environment(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    seen_commands: list[list[str]] = []

    def fake_runner(command: list[str], _cwd: Path, _timeout: int) -> RunResult:
        seen_commands.append(command)
        return RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1)

    result = explore_harness_compilation(install_dir, tmp_path, Language.C, run=fake_runner)

    assert result.succeeded is True
    assert seen_commands == [["bash", "compile_harnesses.sh"]]


# explore_harness_compilation — extra_include_paths / extra_library_paths threading


def test_extra_paths_default_to_empty_list(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = explore_harness_compilation(install_dir, tmp_path, Language.C)

    assert result.extra_include_paths == []
    assert result.extra_library_paths == []


def test_extra_paths_threaded_through_success(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(stdout="", stderr="", exit_code=0, duration_seconds=0.1),
    ):
        result = explore_harness_compilation(
            install_dir,
            tmp_path,
            Language.C,
            extra_include_paths=["/usr/include/foo"],
            extra_library_paths=["/usr/lib/x86_64-linux-gnu"],
        )

    assert result.extra_include_paths == ["/usr/include/foo"]
    assert result.extra_library_paths == ["/usr/lib/x86_64-linux-gnu"]


def test_extra_paths_threaded_through_terminal_failure(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    (install_dir / "lib").mkdir(parents=True)
    (install_dir / "lib" / "libfoo.a").write_text("stub")
    (install_dir / "include").mkdir()

    with patch(
        "harnessbuddy.library_builder.harness_explorer.run_command",
        return_value=RunResult(
            stdout="", stderr="undefined reference to `foo'", exit_code=1, duration_seconds=0.1
        ),
    ):
        result = explore_harness_compilation(
            install_dir,
            tmp_path,
            Language.C,
            extra_include_paths=["/usr/include/foo"],
            extra_library_paths=["/usr/lib/x86_64-linux-gnu"],
        )

    assert result.extra_include_paths == ["/usr/include/foo"]
    assert result.extra_library_paths == ["/usr/lib/x86_64-linux-gnu"]
