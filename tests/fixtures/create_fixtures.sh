#!/bin/bash
set -euo pipefail

# Creates minimal fixture repositories used by tests/library_builder/**tests.
# Each fixture contains only the files that analysis.py keys on.
# Safe to re-run — overwrites existing fixtures.

FIXTURES="$(cd "$(dirname "$0")" && pwd)/repos"

# cmake_repo — CMakeLists.txt, C header
mkdir -p "$FIXTURES/cmake_repo"
cat > "$FIXTURES/cmake_repo/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.10)
project(mylib C)
EOF
cat > "$FIXTURES/cmake_repo/mylib.h" <<'EOF'
#pragma once
void mylib_hello(void);
EOF

# meson_repo — meson.build, C++ header
mkdir -p "$FIXTURES/meson_repo"
cat > "$FIXTURES/meson_repo/meson.build" <<'EOF'
project('mylib', 'cpp')
EOF
cat > "$FIXTURES/meson_repo/mylib.hpp" <<'EOF'
#pragma once
void mylib_hello();
EOF

# autotools_repo — configure.ac only (no configure, no autogen.sh → AUTORECONF)
mkdir -p "$FIXTURES/autotools_repo"
cat > "$FIXTURES/autotools_repo/configure.ac" <<'EOF'
AC_INIT([mylib], [1.0])
AM_INIT_AUTOMAKE
AC_OUTPUT
EOF
cat > "$FIXTURES/autotools_repo/mylib.h" <<'EOF'
#pragma once
void mylib_hello(void);
EOF

# autotools_configure_repo — configure.ac + configure script present → CONFIGURE
mkdir -p "$FIXTURES/autotools_configure_repo"
cat > "$FIXTURES/autotools_configure_repo/configure.ac" <<'EOF'
AC_INIT([mylib], [1.0])
AM_INIT_AUTOMAKE
AC_OUTPUT
EOF
cat > "$FIXTURES/autotools_configure_repo/configure" <<'EOF'
#!/bin/sh
# pre-generated configure script
EOF
chmod +x "$FIXTURES/autotools_configure_repo/configure"
cat > "$FIXTURES/autotools_configure_repo/mylib.h" <<'EOF'
#pragma once
void mylib_hello(void);
EOF

# autotools_autogen_repo — configure.ac + autogen.sh, no configure → AUTOGEN
mkdir -p "$FIXTURES/autotools_autogen_repo"
cat > "$FIXTURES/autotools_autogen_repo/configure.ac" <<'EOF'
AC_INIT([mylib], [1.0])
AM_INIT_AUTOMAKE
AC_OUTPUT
EOF
cat > "$FIXTURES/autotools_autogen_repo/autogen.sh" <<'EOF'
#!/bin/sh
autoreconf -fiv
EOF
chmod +x "$FIXTURES/autotools_autogen_repo/autogen.sh"
cat > "$FIXTURES/autotools_autogen_repo/mylib.h" <<'EOF'
#pragma once
void mylib_hello(void);
EOF

# autotools_bootstrap_repo — configure.ac + bootstrap, no configure/autogen.sh → BOOTSTRAP
mkdir -p "$FIXTURES/autotools_bootstrap_repo"
cat > "$FIXTURES/autotools_bootstrap_repo/configure.ac" <<'EOF'
AC_INIT([mylib], [1.0])
AM_INIT_AUTOMAKE
AC_OUTPUT
EOF
cat > "$FIXTURES/autotools_bootstrap_repo/bootstrap" <<'EOF'
#!/bin/sh
autoreconf -fiv
EOF
chmod +x "$FIXTURES/autotools_bootstrap_repo/bootstrap"
cat > "$FIXTURES/autotools_bootstrap_repo/mylib.h" <<'EOF'
#pragma once
void mylib_hello(void);
EOF

# makefile_repo — Makefile, C header
mkdir -p "$FIXTURES/makefile_repo"
cat > "$FIXTURES/makefile_repo/Makefile" <<'EOF'
all:
	@echo build

.PHONY: all
EOF
cat > "$FIXTURES/makefile_repo/mylib.h" <<'EOF'
#pragma once
void mylib_hello(void);
EOF

# no_signals_repo — no C/C++ signals (triggers UnsupportedRepositoryError)
mkdir -p "$FIXTURES/no_signals_repo"
cat > "$FIXTURES/no_signals_repo/README.md" <<'EOF'
Not a C/C++ library.
EOF

echo "Fixtures created in $FIXTURES"
