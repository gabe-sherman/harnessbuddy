from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from harnessbuddy.core.files import write_executable
from harnessbuddy.core.subprocesses import Runner, run_command
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import (
    HarnessExplorationResult,
    Language,
    LinkConfiguration,
)
from harnessbuddy.library_builder.scripts import (
    HARNESS_SOURCE_DIR,
    build_harness_script,
    build_harnesses_script,
    write_default_fuzzer,
)
from harnessbuddy.library_builder.timeouts import HARNESS_PROBE_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5

# Always linked, not just discovered from undefined-symbol parsing: glibc's pthread_*
# entry points are weak symbols, so a static link that omits -lpthread silently falls
# back to no-op stubs instead of erroring, which _resolve_flags can never catch.
_DEFAULT_LINK_FLAGS = ["-lpthread"]

_PATTERNS_FILE = Path(__file__).parent / "symbol_patterns.json"

# Each flag maps to a compiled regex that ORs all of its patterns together.
# Patterns use anchors (^/$) to avoid false substring matches.
_LIB_PATTERNS: dict[str, re.Pattern[str]] = {
    flag: re.compile("|".join(patterns))
    for flag, patterns in json.loads(_PATTERNS_FILE.read_text()).items()
}

# C++ ABI symbols that appear when a C library pulls in a C++ static archive.
# Matches operator new/delete, exception-handling (__cxa_), and personality routine.
_CXX_ABI_RE = re.compile(r"operator (?:new|delete)\b|__cxa_|__gxx_personality")


def explore_harness_compilation(  # noqa: PLR0913 -- public API; every param is a distinct required input
    install_dir: Path,
    workdir: Path,
    language: Language,
    *,
    extra_include_paths: list[str] | None = None,
    extra_library_paths: list[str] | None = None,
    environment: Environment = Environment.LOCAL,
    run: Runner | None = None,
) -> HarnessExplorationResult:
    """Test harness compilation against install artifacts to discover transitive deps.

    Uses --whole-archive to force all library symbols in, which surfaces every undefined
    transitive dependency. Retries up to _MAX_ATTEMPTS times, accumulating resolved -l
    flags from linker errors, seeded with _DEFAULT_LINK_FLAGS (flags needed regardless of
    whether linking surfaces them as undefined symbols). Returns the result regardless of
    success; callers use HarnessExplorationResult.succeeded to decide behaviour.

    extra_include_paths/extra_library_paths are fixed inputs (e.g. from a prior agent's
    AgentReport) threaded unchanged into every returned HarnessExplorationResult.

    The generated scripts are environment-independent, but entering them is not:
    environment.harness_probe_command decides how each attempt starts the build, and its
    docstring explains why the container goes through OSS-Fuzz's `compile` instead of running
    compile_harnesses.sh directly. environment is also recorded on the returned result. run
    defaults to running the command as a host subprocess; callers running this inside a
    container pass a run primitive that wraps the command in a `docker run` invocation
    instead.
    """
    extra_include_paths = extra_include_paths or []
    extra_library_paths = extra_library_paths or []
    lib_dir = install_dir / "lib"
    include_dir = install_dir / "include"

    static_libs = sorted(lib_dir.glob("*.a"))
    logger.debug(
        "Discovered static libraries [%s] from dir %s",
        " ".join(str(p) for p in static_libs),
        lib_dir,
    )
    if not static_libs:
        return HarnessExplorationResult(
            succeeded=False,
            command=[],
            static_libs=[],
            include_dir=include_dir,
            transitive_link_flags=[],
            stdout="",
            stderr="no *.a files found in install/lib",
            exit_code=-1,
            extra_include_paths=extra_include_paths,
            extra_library_paths=extra_library_paths,
            environment=environment,
        )

    use_cpp = language == Language.CPP
    harness_src_dir = workdir / HARNESS_SOURCE_DIR
    harness_src_dir.mkdir(exist_ok=True)
    # _materialize_workspace/LocalExecutor.run_library_build already wrote this stub (so the
    # atomic gate's non-empty-/out check has something to find before discovery ever runs) —
    # this call is idempotent when that already happened, and a fallback when discovery runs
    # without it (e.g. called directly in tests). Discovery upgrades this same file's
    # extension in place on a CXX finding, rather than probing with a separate throwaway
    # file, so the discovered language can never desync from what final generation copies.
    harness_path = write_default_fuzzer(harness_src_dir, language)

    script_path = workdir / "compile_harness.sh"
    batch_script_path = workdir / "compile_harnesses.sh"
    runner = run if run is not None else run_command
    extra_flags: list[str] = list(_DEFAULT_LINK_FLAGS)
    seen_flags: set[str] = set(_DEFAULT_LINK_FLAGS)
    last_stdout = ""
    last_stderr = ""
    last_exit = -1
    diagnostic = ""

    for _ in range(_MAX_ATTEMPTS):
        write_executable(
            script_path,
            build_harness_script(
                LinkConfiguration(
                    static_libs=static_libs,
                    transitive_link_flags=extra_flags,
                    extra_library_paths=extra_library_paths,
                    extra_include_paths=extra_include_paths,
                ),
                whole_archive=True,
                harness_cflags=os.environ.get("CFLAGS"),
                harness_cxxflags=os.environ.get("CXXFLAGS"),
            ),
        )
        write_executable(batch_script_path, build_harnesses_script())
        command = environment.harness_probe_command
        result = runner(command, workdir, HARNESS_PROBE_TIMEOUT_SECONDS)
        last_stdout = result.stdout
        last_stderr = result.stderr
        last_exit = result.exit_code
        diagnostic = result.output

        if result.exit_code == 0:
            return HarnessExplorationResult(
                succeeded=True,
                command=command,
                static_libs=static_libs,
                include_dir=include_dir,
                transitive_link_flags=extra_flags,
                stdout=last_stdout,
                stderr=last_stderr,
                exit_code=last_exit,
                script_path=script_path,
                extra_include_paths=extra_include_paths,
                extra_library_paths=extra_library_paths,
                environment=environment,
            )

        upgraded_to_cxx = False
        if not use_cpp and _requires_cxx(diagnostic):
            logger.debug("Upgrading harness build to use CXX instead of CC")
            harness_path.unlink(missing_ok=True)
            harness_path = write_default_fuzzer(harness_src_dir, Language.CPP)
            use_cpp = True
            upgraded_to_cxx = True

        new_flags = _resolve_flags(diagnostic, seen_flags)
        if not new_flags and not upgraded_to_cxx:
            break
        extra_flags.extend(new_flags)
        seen_flags.update(new_flags)

    return HarnessExplorationResult(
        succeeded=False,
        command=[],
        static_libs=static_libs,
        include_dir=include_dir,
        transitive_link_flags=extra_flags,
        stdout=last_stdout,
        stderr=last_stderr,
        exit_code=last_exit,
        missing_system_libs=extract_missing_system_libs(diagnostic),
        extra_include_paths=extra_include_paths,
        extra_library_paths=extra_library_paths,
        environment=environment,
    )


def _resolve_flags(stderr: str, already_seen: set[str]) -> list[str]:
    symbols = _extract_undefined_symbols(stderr)
    resolved: dict[str, str] = {}
    for sym in symbols:
        flag = _symbol_to_flag(sym)
        if flag and flag not in already_seen and flag not in resolved:
            resolved[flag] = flag
    return list(resolved.values())


def _extract_undefined_symbols(stderr: str) -> list[str]:
    symbols: list[str] = []
    # Linux ld: undefined reference to `symbol'
    for m in re.finditer(r"undefined reference to `([^']+)'", stderr):
        symbols.append(m.group(1))
    # macOS ld (compact): Undefined symbol: _symbol
    for m in re.finditer(r'[Uu]ndefined symbol[: ]+[_"]?([A-Za-z_][A-Za-z0-9_]*)', stderr):
        symbols.append(m.group(1))
    # macOS ld (verbose): "  "_Symbol", referenced from:"
    for m in re.finditer(r'"_([A-Za-z_][A-Za-z0-9_]*)",\s+referenced from:', stderr):
        symbols.append(m.group(1))
    return symbols


def _requires_cxx(stderr: str) -> bool:
    if _CXX_ABI_RE.search(stderr):
        return True
    return any(
        symbol.startswith("_Z") or "::" in symbol for symbol in _extract_undefined_symbols(stderr)
    )


def extract_missing_system_libs(stderr: str) -> list[str]:
    libs: list[str] = []
    # macOS ld: library 'zstd' not found
    for m in re.finditer(r"ld: library '([^']+)' not found", stderr):
        libs.append(m.group(1))
    # Linux ld: cannot find -lzstd
    for m in re.finditer(r"ld: cannot find -l([^\s:]+)", stderr):
        libs.append(m.group(1))
    return list(dict.fromkeys(libs))  # deduplicate, preserve order


def lib_names_from_link_flags(flags: list[str]) -> list[str]:
    """Strip the "-l" prefix from transitive_link_flags entries.

    Every entry in transitive_link_flags is "-l<name>" (every key in
    symbol_patterns.json is "-l<name>"), matching the bare-name input
    package_names.translate() expects.
    """
    return [flag.removeprefix("-l") for flag in flags]


def _symbol_to_flag(symbol: str) -> str | None:
    for flag, pattern in _LIB_PATTERNS.items():
        if pattern.search(symbol):
            return flag
    return None


def validate_harness_artifacts(workdir: Path) -> list[str]:
    out_dir = workdir / "out"
    if not out_dir.exists() or not any(out_dir.iterdir()):
        return [f"no compiled harness binary found in {out_dir}"]
    return []
