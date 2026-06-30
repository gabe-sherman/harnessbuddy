from __future__ import annotations

from harnessbuddy.library_builder.package_names import translate


def test_known_lib_returns_apt_and_brew() -> None:
    result = translate(["zstd"])
    assert result.apt_packages == ["libzstd-dev"]
    assert result.brew_packages == ["zstd"]
    assert result.unknown_libs == []


def test_system_libs_are_dropped() -> None:
    result = translate(["m", "pthread", "dl", "rt", "resolv", "c", "gcc_s", "stdc++"])
    assert result.apt_packages == []
    assert result.brew_packages == []
    assert result.unknown_libs == []


def test_unknown_lib_goes_to_unknown_libs() -> None:
    result = translate(["proprietary_thing"])
    assert result.apt_packages == []
    assert result.brew_packages == []
    assert result.unknown_libs == ["proprietary_thing"]


def test_ssl_and_crypto_deduplicates_apt() -> None:
    result = translate(["ssl", "crypto"])
    assert result.apt_packages == ["libssl-dev"]
    assert result.brew_packages == ["openssl"]


def test_null_brew_entry_excluded() -> None:
    result = translate(["seccomp"])
    assert result.apt_packages == ["libseccomp-dev"]
    assert result.brew_packages == []


def test_null_brew_iconv_excluded() -> None:
    result = translate(["iconv"])
    assert result.apt_packages == ["libc6-dev"]
    assert result.brew_packages == []


def test_preserves_input_order() -> None:
    result = translate(["zstd", "lz4", "bz2"])
    assert result.apt_packages == ["libzstd-dev", "liblz4-dev", "libbz2-dev"]
    assert result.brew_packages == ["zstd", "lz4", "bzip2"]


def test_mixed_known_system_and_unknown() -> None:
    result = translate(["zstd", "m", "weird_lib"])
    assert result.apt_packages == ["libzstd-dev"]
    assert result.brew_packages == ["zstd"]
    assert result.unknown_libs == ["weird_lib"]


def test_empty_input() -> None:
    result = translate([])
    assert result.apt_packages == []
    assert result.brew_packages == []
    assert result.unknown_libs == []
