# =============================================================================
# SpOdy - top-level CMake build
# =============================================================================
# Application built on top of spody-core (the high-performance library).
# spody-core is consumed via a git submodule under external/spody-core.
#
# QUICK START
# -----------
#   git submodule update --init --recursive
#   mkdir build && cd build
#   cmake ..
#   cmake --build . --config Release        # Visual Studio / multi-config
#   cmake --build .                         # Makefile / Ninja
#
# The resulting executable is `spody` (a CLI). The Python GUI under python/
# is independent of this build and uses the executable via file-based IO.
# =============================================================================

cmake_minimum_required(VERSION 3.12)
project(spody VERSION 0.1.0 LANGUAGES C)
# 3.12 baseline: needed for the <Pkg>_ROOT find_package hint to be
# honored (policy CMP0074 defaults to NEW from 3.12). Used by the
# macOS workflow to point at libomp from a non-system prefix.

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Default to Release if the user did not pick one (single-config generators).
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# -----------------------------------------------------------------------------
# spody-core (submodule)
# -----------------------------------------------------------------------------
# Do NOT enable SPODY_BUILD_TVB here: tests/benchmarks of the library live in
# the spody-core repo and are run from there, not from the application build.
set(SPODY_BUILD_TVB OFF CACHE BOOL "" FORCE)
add_subdirectory(external/spody-core)

# -----------------------------------------------------------------------------
# tomlc99 (vendored TOML 1.0 parser - cktan/tomlc99, MIT)
# -----------------------------------------------------------------------------
# Single-translation-unit dependency, compiled as a small static lib and
# linked into the spody application. Not exported, not installed -- spody
# is the only consumer. See external/tomlc99/README.md for provenance.
add_library(tomlc99 STATIC external/tomlc99/toml.c)
target_include_directories(tomlc99 PUBLIC external/tomlc99)
if(MSVC)
    # Upstream already disables 4996; silence a couple of other benign
    # warnings that fire on this code base under /W3.
    target_compile_options(tomlc99 PRIVATE /wd4244 /wd4267)
endif()

# -----------------------------------------------------------------------------
# spody application
# -----------------------------------------------------------------------------
add_executable(spody
    src/main.c
    src/app_diagnostics.c
    src/app_io.c
    src/central_body.c
    src/dynamics_model.c
    src/toml_input.c
    src/sim_setup.c
    src/sim_run.c
)
target_link_libraries(spody PRIVATE spody_core tomlc99)
target_include_directories(spody PRIVATE src)

if(MSVC)
    # Silence CRT 'unsafe' warnings on fopen/snprintf -- same posture
    # the spody-core library already uses internally.
    target_compile_definitions(spody PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

if(UNIX OR APPLE)
    target_link_libraries(spody PRIVATE m)
endif()

# -----------------------------------------------------------------------------
# OpenMP -- parallel batch execution (cmd_batch in src/main.c)
# -----------------------------------------------------------------------------
# Default ON: the distributed bundle ships with OpenMP linked in so
# `batch.thread_number > 1` actually runs cases in parallel (see the
# manual chapter 7). Opt out with -DSPODY_WITH_OPENMP=OFF for a leaner
# build that explicitly rejects multi-thread batches at run time.
#
# macOS note: Apple Clang ships without libomp; install it via Homebrew
# (`brew install libomp`) and CMake's FindOpenMP locates it under
# /opt/homebrew/opt/libomp (arm64) or /usr/local/opt/libomp (intel)
# automatically.
option(SPODY_WITH_OPENMP
    "Link OpenMP into spody for parallel batch execution" ON)
if(SPODY_WITH_OPENMP)
    find_package(OpenMP REQUIRED COMPONENTS C)
    target_link_libraries(spody PRIVATE OpenMP::OpenMP_C)
    target_compile_definitions(spody PRIVATE SPODY_HAVE_OPENMP=1)
    message(STATUS "OpenMP for batch: enabled")
else()
    message(STATUS "OpenMP for batch: DISABLED  "
                   "(batch.thread_number > 1 will be rejected at run time)")
endif()
