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
_PROBE_CC = (
    "#include <stddef.h>\n"
    "#include <stdint.h>\n"
    'extern "C" int main(void) { return 0; }\n'
)

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


def explore_harness_compilation(
    install_dir: Path,
    workdir: Path,
    language: Language,
) -> HarnessExplorationResult:
    """Test harness compilation against install artifacts to discover transitive deps.

    Uses --whole-archive (or macOS equivalent) to force all library symbols in, which
    surfaces every undefined transitive dependency. Retries up to _MAX_ATTEMPTS times,
    accumulating resolved -l flags from linker errors. Returns the result regardless of
    success; callers use HarnessExplorationResult.succeeded to decide behaviour.
    """
    lib_dir = install_dir / "lib"
    include_dir = install_dir / "include"

    static_libs = sorted(lib_dir.glob("*.a"))
    if not static_libs:
        return HarnessExplorationResult(
            succeeded=False,
            static_libs=[],
            include_dir=include_dir,
            transitive_link_flags=[],
            stdout="",
            stderr="no *.a files found in install/lib",
            exit_code=-1,
        )

    use_cpp = language == Language.CPP
    harness_src_dir = workdir / "harness_src"
    harness_src_dir.mkdir(exist_ok=True)
    probe_src = harness_src_dir / ("probe_harness.cc" if use_cpp else "probe_harness.c")
    probe_src.write_text(_PROBE_CC if use_cpp else _PROBE_C)

    script_path = workdir / "build_harness.sh"
    extra_flags: list[str] = []
    seen_flags: set[str] = set()
    last_stdout = ""
    last_stderr = ""
    last_exit = -1

    for _ in range(_MAX_ATTEMPTS):
        intermediate = HarnessExplorationResult(
            succeeded=False,
            static_libs=static_libs,
            include_dir=include_dir,
            transitive_link_flags=extra_flags,
            stdout="",
            stderr="",
            exit_code=-1,
        )
        script_path.write_text(build_harness_script(intermediate, whole_archive=True))
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        result = run_command(["bash", str(script_path.name)], workdir, timeout=60)
        last_stdout = result.stdout
        last_stderr = result.stderr
        last_exit = result.exit_code

        if result.exit_code == 0:
            return HarnessExplorationResult(
                succeeded=True,
                static_libs=static_libs,
                include_dir=include_dir,
                transitive_link_flags=extra_flags,
                stdout=last_stdout,
                stderr=last_stderr,
                exit_code=last_exit,
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
        static_libs=static_libs,
        include_dir=include_dir,
        transitive_link_flags=extra_flags,
        stdout=last_stdout,
        stderr=last_stderr,
        exit_code=last_exit,
        missing_system_libs=_extract_missing_system_libs(last_stderr),
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


def _symbol_to_flag(symbol: str) -> str | None:
    for flag, pattern in _LIB_PATTERNS.items():
        if pattern.search(symbol):
            return flag
    return None
