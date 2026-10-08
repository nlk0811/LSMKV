cmake_minimum_required(VERSION 3.16)
project(LSMKV_v2 VERSION 2.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ── Compiler flags ──────────────────────────────────────────────────────────────
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang|AppleClang")
    add_compile_options(-Wall -Wextra -O2 -g)
endif()

# ── Core library ───────────────────────────────────────────────────────────────
set(LSMKV_SOURCES
    src/arena.cpp
    src/bloom_filter.cpp
    src/skip_list.cpp
    src/wal.cpp
    src/sstable.cpp
    src/block_cache.cpp
    src/compaction.cpp
    src/manifest.cpp
    src/lsm_tree.cpp
)

add_library(lsmkv STATIC ${LSMKV_SOURCES})
target_include_directories(lsmkv PUBLIC include)

# ── Tests ──────────────────────────────────────────────────────────────────────
set(TEST_SOURCES
    tests/test_main.cpp
    tests/test_bloom_filter.cpp
    tests/test_skip_list.cpp
    tests/test_wal.cpp
    tests/test_sstable.cpp
    tests/test_lsm_tree.cpp
    tests/test_crash.cpp
)

add_executable(lsmkv_tests ${TEST_SOURCES})
target_include_directories(lsmkv_tests PRIVATE include tests)
target_link_libraries(lsmkv_tests PRIVATE lsmkv)

# ── Benchmarks ─────────────────────────────────────────────────────────────────
add_executable(lsmkv_bench benchmarks/bench_main.cpp)
target_include_directories(lsmkv_bench PRIVATE include)
target_link_libraries(lsmkv_bench PRIVATE lsmkv)

# ── macOS needs -lpthread implicitly via std::thread ───────────────────────────
find_package(Threads REQUIRED)
target_link_libraries(lsmkv PUBLIC Threads::Threads)
