from __future__ import annotations

import json
import logging
import re
import stat
from pathlib import Path

from harnessbuddy.core.subprocesses import Runner, run_command
from harnessbuddy.library_builder.environments.base import Environment
from harnessbuddy.library_builder.models import HarnessExplorationResult, Language
from harnessbuddy.library_builder.scripts import build_harness_script, write_default_fuzzer

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

# Mirrors scripts.py's build_harness_script format, used to re-derive STATIC_LIBS /
# EXTRA_LINK_FLAGS from a (possibly agent-edited) compile_harnesses.sh after a fix, since
# an agent may hand-edit these variables directly rather than us regenerating them.
_STATIC_LIBS_BLOCK_RE = re.compile(r"STATIC_LIBS=\((.*?)\n\)", re.DOTALL)
_STATIC_LIB_ENTRY_RE = re.compile(r'"\$INSTALL_DIR/lib/([^"]+)"')
_EXTRA_LINK_FLAGS_RE = re.compile(r'^EXTRA_LINK_FLAGS=(?:"([^"]*)")?\s*$', re.MULTILINE)
_EXTRA_LIB_PATHS_RE = re.compile(r'^EXTRA_LIB_PATHS=(?:"([^"]*)")?\s*$', re.MULTILINE)
_BREW_LIB_PREFIX = "-L$(brew --prefix)/lib "


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

    Uses --whole-archive (or macOS equivalent) to force all library symbols in, which
    surfaces every undefined transitive dependency. Retries up to _MAX_ATTEMPTS times,
    accumulating resolved -l flags from linker errors, seeded with _DEFAULT_LINK_FLAGS
    (flags needed regardless of whether linking surfaces them as undefined symbols).
    Returns the result regardless of success; callers use HarnessExplorationResult.succeeded
    to decide behaviour.

    extra_include_paths/extra_library_paths are fixed inputs (e.g. from a prior agent's
    AgentReport) threaded unchanged into every returned HarnessExplorationResult.

    environment selects the generated script variant (Environment.OSS_FUZZ uses the base
    image's own $OUT/$LIB_FUZZING_ENGINE instead of local defaults) and is recorded on the
    returned result. run defaults to running the command as a host subprocess; callers
    running this inside a container pass a run primitive that wraps the command in a
    `docker run` invocation instead. For Environment.OSS_FUZZ, each attempt runs the base
    image's own `compile` entrypoint (not just the generated script directly), since that's
    what populates $LIB_FUZZING_ENGINE before compile_harnesses.sh links against it.
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

    oss_fuzz = environment is Environment.OSS_FUZZ
    # Matches the harness source directory name each generator uses (local/generation.py's
    # harness_src/ vs. oss_fuzz/generation.py's harness_source/), so a container-validated
    # script's $SCRIPT_DIR-relative HARNESS_DIR still resolves once copied verbatim (FR-008).
    harness_dir_name = "harness_source" if oss_fuzz else "harness_src"
    use_cpp = language == Language.CPP
    harness_src_dir = workdir / harness_dir_name
    harness_src_dir.mkdir(exist_ok=True)
    # _materialize_workspace/LocalExecutor.run_library_build already wrote this stub (so the
    # atomic gate's non-empty-/out check has something to find before discovery ever runs) —
    # this call is idempotent when that already happened, and a fallback when discovery runs
    # without it (e.g. called directly in tests). Discovery upgrades this same file's
    # extension in place on a CXX finding, rather than probing with a separate throwaway
    # file, so the discovered language can never desync from what final generation copies.
    harness_path = write_default_fuzzer(harness_src_dir, language)

    script_path = workdir / "compile_harnesses.sh"
    runner = run if run is not None else run_command
    extra_flags: list[str] = list(_DEFAULT_LINK_FLAGS)
    seen_flags: set[str] = set(_DEFAULT_LINK_FLAGS)
    last_stdout = ""
    last_stderr = ""
    last_exit = -1

    for _ in range(_MAX_ATTEMPTS):
        intermediate = HarnessExplorationResult(
            succeeded=False,
            command=[],
            static_libs=static_libs,
            include_dir=include_dir,
            transitive_link_flags=extra_flags,
            stdout="",
            stderr="",
            exit_code=-1,
            extra_include_paths=extra_include_paths,
            extra_library_paths=extra_library_paths,
        )
        script_path.write_text(
            build_harness_script(
                intermediate,
                whole_archive=True,
                harness_dir_name=harness_dir_name,
                oss_fuzz=oss_fuzz,
            )
        )
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        command = ["bash", "-c", "compile"] if oss_fuzz else ["bash", str(script_path.name)]
        result = runner(command, workdir, 60)
        last_stdout = result.stdout
        last_stderr = result.stderr
        last_exit = result.exit_code
        # Some Runner implementations (docker-streaming runners) merge stderr into stdout
        # and never populate .stderr — scan both so detection isn't blind to those.
        diagnostic = last_stdout + last_stderr

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
        missing_system_libs=_extract_missing_system_libs(last_stdout + last_stderr),
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
        symbol.startswith("_Z") or "::" in symbol
        for symbol in _extract_undefined_symbols(stderr)
    )


def _extract_missing_system_libs(stderr: str) -> list[str]:
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


def _validate_harness_artifacts(workdir: Path) -> list[str]:
    out_dir = workdir / "out"
    if not out_dir.exists() or not any(out_dir.iterdir()):
        return [f"no compiled harness binary found in {out_dir}"]
    return []


def reparse_link_config(
    script_text: str, static_libs: list[Path], transitive_link_flags: list[str]
) -> tuple[list[Path], list[str]]:
    """Re-derive STATIC_LIBS and EXTRA_LINK_FLAGS from compile_harnesses.sh's text.

    An agent fixing a harness link failure edits these variables directly rather than
    going through build_harness_script, so the structured HarnessExplorationResult it's
    handed can go stale. Falls back to the given values wherever the expected format
    isn't found (e.g. the agent restructured the script beyond these two variables).
    """
    block_match = _STATIC_LIBS_BLOCK_RE.search(script_text)
    if block_match:
        names = _STATIC_LIB_ENTRY_RE.findall(block_match.group(1))
        if names:
            static_libs = [Path(name) for name in names]

    flags_match = _EXTRA_LINK_FLAGS_RE.search(script_text)
    if flags_match:
        raw = (flags_match.group(1) or "").removeprefix(_BREW_LIB_PREFIX).strip()
        transitive_link_flags = raw.split() if raw else []

    return static_libs, transitive_link_flags


def reparse_lib_paths(script_text: str, extra_library_paths: list[str]) -> list[str]:
    """Re-derive extra library paths from an EXTRA_LIB_PATHS="-Lpath ..." line.

    Mirrors reparse_link_config's EXTRA_LINK_FLAGS handling: falls back to the given
    paths wherever the expected format isn't found in the (possibly agent-edited)
    compile_harnesses.sh text.
    """
    match = _EXTRA_LIB_PATHS_RE.search(script_text)
    if not match:
        return extra_library_paths
    raw = (match.group(1) or "").strip()
    if not raw:
        return []
    return [flag.removeprefix("-L") for flag in raw.split()]
