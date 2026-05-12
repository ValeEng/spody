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

cmake_minimum_required(VERSION 3.10)
project(spody VERSION 0.1.0 LANGUAGES C)

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
# spody application
# -----------------------------------------------------------------------------
add_executable(spody
    src/main.c
)
target_link_libraries(spody PRIVATE spody_core)
target_include_directories(spody PRIVATE src)

if(UNIX OR APPLE)
    target_link_libraries(spody PRIVATE m)
endif()
