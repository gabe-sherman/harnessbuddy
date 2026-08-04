from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "package_names.json"
_DATA = json.loads(_DATA_FILE.read_text())
_SYSTEM_LIBS: frozenset[str] = frozenset(_DATA["system_libs"])
_APT_PACKAGES: dict[str, str] = _DATA["apt_packages"]


@dataclass
class PackageTranslation:
    apt_packages: list[str]
    unknown_libs: list[str]


def translate(lib_names: list[str]) -> PackageTranslation:
    """Translate raw lib names (e.g. ["zstd"]) to installable apt package names.

    System libs (m, pthread, dl, rt, resolv, c, gcc_s, stdc++) are silently dropped.
    Libs with no mapping go into unknown_libs. Deduplication preserves order.
    """
    apt: list[str] = []
    unknown: list[str] = []

    for lib in lib_names:
        if lib in _SYSTEM_LIBS:
            continue
        package = _APT_PACKAGES.get(lib)
        if package is None:
            unknown.append(lib)
            continue
        apt.append(package)

    return PackageTranslation(
        apt_packages=list(dict.fromkeys(apt)),
        unknown_libs=list(dict.fromkeys(unknown)),
    )
