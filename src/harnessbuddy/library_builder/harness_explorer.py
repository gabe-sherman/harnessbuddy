from __future__ import annotations

import json
import re
import stat
from pathlib import Path

from harnessbuddy.core.subprocesses import run_command
from harnessbuddy.library_builder.models import HarnessExplorationResult, Language
from harnessbuddy.library_builder.scripts import build_harness_script

_MAX_ATTEMPTS = 5

_PROBE_C = "#include <stddef.h>\n#include <stdint.h>\nint main(void) { return 0; }\n"
_PROBE_CC = '#include <stddef.h>\n#include <stdint.h>\nextern "C" int main(void) { return 0; }\n'

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


def explore_harness_compilation(
    install_dir: Path,
    workdir: Path,
    language: Language,
    *,
    extra_include_paths: list[str] | None = None,
    extra_library_paths: list[str] | None = None,
) -> HarnessExplorationResult:
    """Test harness compilation against install artifacts to discover transitive deps.

    Uses --whole-archive (or macOS equivalent) to force all library symbols in, which
    surfaces every undefined transitive dependency. Retries up to _MAX_ATTEMPTS times,
    accumulating resolved -l flags from linker errors. Returns the result regardless of
    success; callers use HarnessExplorationResult.succeeded to decide behaviour.

    extra_include_paths/extra_library_paths are fixed inputs (e.g. from a prior agent's
    AgentReport) threaded unchanged into every returned HarnessExplorationResult.
    """
    extra_include_paths = extra_include_paths or []
    extra_library_paths = extra_library_paths or []
    lib_dir = install_dir / "lib"
    include_dir = install_dir / "include"

    static_libs = sorted(lib_dir.glob("*.a"))
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
        )

    use_cpp = language == Language.CPP
    harness_src_dir = workdir / "harness_src"
    harness_src_dir.mkdir(exist_ok=True)
    probe_src = harness_src_dir / ("probe_harness.cc" if use_cpp else "probe_harness.c")
    probe_src.write_text(_PROBE_CC if use_cpp else _PROBE_C)

    script_path = workdir / "compile_harnesses.sh"
    extra_flags: list[str] = []
    seen_flags: set[str] = set()
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
        script_path.write_text(build_harness_script(intermediate, whole_archive=True))
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        command = ["bash", str(script_path.name)]
        result = run_command(command, workdir, timeout=60)
        last_stdout = result.stdout
        last_stderr = result.stderr
        last_exit = result.exit_code

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
            )

        upgraded_to_cxx = False
        if not use_cpp and _requires_cxx(last_stderr):
            probe_src.unlink(missing_ok=True)
            probe_src = harness_src_dir / "probe_harness.cc"
            probe_src.write_text(_PROBE_CC)
            use_cpp = True
            upgraded_to_cxx = True

        new_flags = _resolve_flags(last_stderr, seen_flags)
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
        missing_system_libs=_extract_missing_system_libs(last_stderr),
        extra_include_paths=extra_include_paths,
        extra_library_paths=extra_library_paths,
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
    return bool(_CXX_ABI_RE.search(stderr))


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
