cmake_minimum_required(VERSION 3.20)
project(feature_extractor CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Clang REQUIRED CONFIG)
find_package(LLVM REQUIRED CONFIG)

message(STATUS "Found LLVM ${LLVM_PACKAGE_VERSION}")
message(STATUS "Using ClangConfig.cmake in: ${Clang_DIR}")

add_executable(feature_extractor
  src/main.cpp
  src/extraction_action.cpp
  src/macro_callbacks.cpp
  src/json_writer.cpp
)

target_include_directories(feature_extractor PRIVATE include)
target_include_directories(feature_extractor SYSTEM PRIVATE
  ${LLVM_INCLUDE_DIRS}
  ${CLANG_INCLUDE_DIRS}
)
target_compile_definitions(feature_extractor PRIVATE ${LLVM_DEFINITIONS})

if(NOT LLVM_ENABLE_RTTI)
  target_compile_options(feature_extractor PRIVATE -fno-rtti)
endif()

target_compile_options(feature_extractor PRIVATE
  -Wall -Wextra -Werror -Wpedantic -Wshadow
)

target_link_libraries(feature_extractor PRIVATE
  clangTooling
  clangASTMatchers
  clangBasic
  clangFrontend
  clangAST
  clangLex
)

install(TARGETS feature_extractor RUNTIME DESTINATION bin)
