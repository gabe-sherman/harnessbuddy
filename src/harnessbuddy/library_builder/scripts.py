from __future__ import annotations

from harnessbuddy.library_builder.models import AutotoolsSetup, BuildSystem

_HOST_ENV_FALLBACKS = (
    '\nCC="${CC:-cc}"\n'
    'CXX="${CXX:-c++}"\n'
    'CFLAGS="${CFLAGS:-}"\n'
    'CXXFLAGS="${CXXFLAGS:-}"\n'
)


def build_library_script(
    build_system: BuildSystem,
    source_dir: str,
    build_dir: str,
    install_dir: str,
    env_file: str,
    *,
    host_fallbacks: bool = False,
    autotools_setup: AutotoolsSetup | None = None,
) -> str:
    """Generate a build_library.sh script with parameterized paths.

    Args:
        build_system: detected build system.
        source_dir: path string for the source directory.
        build_dir: path string for the build directory (relative or absolute).
        install_dir: path string for the install prefix.
        env_file: path string where build.env will be written.
        host_fallbacks: when True, add CC/CXX/CFLAGS/CXXFLAGS defaults for host builds.
        autotools_setup: autotools bootstrap variant (only used when build_system is AUTOTOOLS).
    """
    header = '#!/bin/bash\nset -euo pipefail\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
    if host_fallbacks:
        header += _HOST_ENV_FALLBACKS
    body = _build_body(build_system, source_dir, build_dir, install_dir, autotools_setup)
    footer = (
        f"\ncat > {env_file} <<'EOF'\n"
        f'HB_INCLUDE_FLAGS="-I{install_dir}/include"\n'
        f'HB_LIBRARY_FLAGS="-L{install_dir}/lib"\n'
        "EOF\n"
    )
    return header + body + footer


def _build_body(
    build_system: BuildSystem,
    source_dir: str,
    build_dir: str,
    install_dir: str,
    autotools_setup: AutotoolsSetup | None = None,
) -> str:
    if build_system == BuildSystem.CMAKE:
        return (
            "\n"
            "# build system: cmake\n"
            "\n"
            f"cmake -B {build_dir} -S {source_dir} \\\n"
            '  -DCMAKE_C_COMPILER="$CC" \\\n'
            '  -DCMAKE_CXX_COMPILER="$CXX" \\\n'
            '  -DCMAKE_C_FLAGS="$CFLAGS" \\\n'
            '  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \\\n'
            f"  -DCMAKE_INSTALL_PREFIX={install_dir} \\\n"
            "  -DBUILD_SHARED_LIBS=OFF\n"
            f"cmake --build {build_dir} -- -j$(nproc)\n"
            f"cmake --install {build_dir}\n"
        )
    if build_system == BuildSystem.MESON:
        return (
            "\n"
            "# build system: meson\n"
            "\n"
            'CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"  meson setup {build_dir} {source_dir} \\\n"
            f"    --prefix={install_dir} --default-library=static\n"
            f"ninja -C {build_dir}\n"
            f"ninja -C {build_dir} install\n"
        )
    if build_system == BuildSystem.AUTOTOOLS:
        if autotools_setup == AutotoolsSetup.AUTOGEN:
            # sometimes autogen already runs configure, run distclean to reset directory state
            setup_step = f"(cd {source_dir} && ./autogen.sh && make distclean)\n"
        elif autotools_setup == AutotoolsSetup.AUTORECONF:
            setup_step = f"(cd {source_dir} && autoreconf -fiv)\n"
        else:
            setup_step = ""
        return (
            "\n"
            "# build system: autotools\n"
            "\n"
            + setup_step
            + f"mkdir -p {build_dir}\n"
            "(\n"
            f"  cd {build_dir}\n"
            '  CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"    {source_dir}/configure --prefix={install_dir} --enable-static --disable-shared\n"
            "  make -j$(nproc)\n"
            "  make install\n"
            ")\n"
        )
    if build_system == BuildSystem.MAKEFILE:
        return (
            "\n"
            "# build system: makefile\n"
            "\n"
            f"make -C {source_dir} -j$(nproc) \\\n"
            '  CC="$CC" CXX="$CXX" CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS" \\\n'
            f"  PREFIX={install_dir}\n"
            f"make -C {source_dir} install PREFIX={install_dir}\n"
        )
    return "\n# build system: unknown\n"
