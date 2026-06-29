#!/usr/bin/env bash

set -ex pipefail

if ! command -v make >/dev/null 2>&1; then
  echo "make not found. Installing Xcode Command Line Tools..."
  xcode-select --install
  echo "Please complete the installation, then rerun this script."
  exit 1
fi

# Install cmake version 3.31.12
CMAKE_VERSION=3.31.12

if command -v cmake >/dev/null 2>&1 && \
   [[ "$(cmake --version | head -n1 | awk '{print $3}')" == "$CMAKE_VERSION" ]]; then
    echo "CMake ${CMAKE_VERSION} is already installed."
else
    echo "Installing CMake ${CMAKE_VERSION}..."

    curl -L \
      "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-macos-universal.tar.gz" \
      -o "/tmp/cmake-${CMAKE_VERSION}.tar.gz"

    sudo mkdir -p "/opt/cmake-${CMAKE_VERSION}"
    sudo tar -xzf "/tmp/cmake-${CMAKE_VERSION}.tar.gz" \
      -C "/opt/cmake-${CMAKE_VERSION}" \
      --strip-components=1

    BIN_PATH="/opt/cmake-${CMAKE_VERSION}/CMake.app/Contents/bin"

    sudo ln -sf "${BIN_PATH}/cmake"  /usr/local/bin/cmake
    sudo ln -sf "${BIN_PATH}/ccmake" /usr/local/bin/ccmake
    sudo ln -sf "${BIN_PATH}/ctest"  /usr/local/bin/ctest
    sudo ln -sf "${BIN_PATH}/cpack"  /usr/local/bin/cpack

    echo "Installed CMake $(cmake --version | head -n1)"
fi

# install autools and other common tools
brew install autoconf automake libtool pkg-config

# install meson and common meson packages
brew install meson
python3 -m ensurepip --upgrade
python3 -m pip install --break-system-packages \
    meson \
    packaging \
    setuptools \
    wheel \
    jinja2 \
    pyyaml
