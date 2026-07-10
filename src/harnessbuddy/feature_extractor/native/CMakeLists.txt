cmake_minimum_required(VERSION 3.20)
project(feature_extractor CXX C)

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

list(GET LLVM_LIBRARY_DIRS 0 _llvm_library_dir)
set(CLANG_RESOURCE_DIR "${_llvm_library_dir}/clang/${LLVM_VERSION_MAJOR}")
message(STATUS "Using clang resource dir: ${CLANG_RESOURCE_DIR}")

include(FetchContent)
set(ENABLE_CJSON_TEST OFF CACHE BOOL "" FORCE)
set(ENABLE_CJSON_UTILS OFF CACHE BOOL "" FORCE)
set(CJSON_OVERRIDE_BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
set(CJSON_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
  cjson
  GIT_REPOSITORY https://github.com/DaveGamble/cJSON.git
  GIT_TAG c859b25da02955fef659d658b8f324b5cde87be3 # v1.7.19
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(cjson)

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
  ${cjson_SOURCE_DIR}
)
target_compile_definitions(feature_extractor PRIVATE ${LLVM_DEFINITIONS})
target_compile_definitions(feature_extractor PRIVATE
  CLANG_RESOURCE_DIR="${CLANG_RESOURCE_DIR}"
)

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
  cjson
)

install(TARGETS feature_extractor RUNTIME DESTINATION bin)
