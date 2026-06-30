from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "package_names.json"
_DATA = json.loads(_DATA_FILE.read_text())
_SYSTEM_LIBS: frozenset[str] = frozenset(_DATA["system_libs"])
_MAPPINGS: dict[str, dict[str, str | None]] = _DATA["mappings"]


@dataclass
class PackageTranslation:
    apt_packages: list[str]
    brew_packages: list[str]
    unknown_libs: list[str]


def translate(lib_names: list[str]) -> PackageTranslation:
    """Translate raw lib names (e.g. ["zstd"]) to installable package names.

    System libs (m, pthread, dl, rt, resolv, c, gcc_s, stdc++) are silently dropped.
    Libs with no mapping go into unknown_libs. Deduplication preserves order.
    """
    apt: list[str] = []
    brew: list[str] = []
    unknown: list[str] = []

    for lib in lib_names:
        if lib in _SYSTEM_LIBS:
            continue
        mapping = _MAPPINGS.get(lib)
        if mapping is None:
            unknown.append(lib)
            continue
        apt_pkg = mapping.get("apt")
        brew_pkg = mapping.get("brew")
        if apt_pkg is not None:
            apt.append(apt_pkg)
        if brew_pkg is not None:
            brew.append(brew_pkg)

    return PackageTranslation(
        apt_packages=list(dict.fromkeys(apt)),
        brew_packages=list(dict.fromkeys(brew)),
        unknown_libs=list(dict.fromkeys(unknown)),
    )
