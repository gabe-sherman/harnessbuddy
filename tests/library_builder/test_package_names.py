from __future__ import annotations

from harnessbuddy.library_builder.package_names import translate


def test_known_lib_returns_its_apt_package() -> None:
    result = translate(["zstd"])
    assert result.apt_packages == ["libzstd-dev"]
    assert result.unknown_libs == []


def test_system_libs_are_dropped() -> None:
    result = translate(["m", "pthread", "dl", "rt", "resolv", "c", "gcc_s", "stdc++"])
    assert result.apt_packages == []
    assert result.unknown_libs == []


def test_unknown_lib_goes_to_unknown_libs() -> None:
    result = translate(["proprietary_thing"])
    assert result.apt_packages == []
    assert result.unknown_libs == ["proprietary_thing"]


def test_ssl_and_crypto_deduplicate_to_one_package() -> None:
    assert translate(["ssl", "crypto"]).apt_packages == ["libssl-dev"]


def test_preserves_input_order() -> None:
    result = translate(["zstd", "lz4", "bz2"])
    assert result.apt_packages == ["libzstd-dev", "liblz4-dev", "libbz2-dev"]


def test_mixed_known_system_and_unknown() -> None:
    result = translate(["zstd", "m", "weird_lib"])
    assert result.apt_packages == ["libzstd-dev"]
    assert result.unknown_libs == ["weird_lib"]


def test_empty_input() -> None:
    result = translate([])
    assert result.apt_packages == []
    assert result.unknown_libs == []
