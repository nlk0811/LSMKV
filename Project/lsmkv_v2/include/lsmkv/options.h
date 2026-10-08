#pragma once
#include <cstddef>
#include <cstdint>

namespace lsmkv {

struct Options {
    // ── Memtable ──────────────────────────────────────────────────────────────
    size_t memtable_size_bytes = 4 * 1024 * 1024;   // 4 MB flush threshold

    // ── WAL ───────────────────────────────────────────────────────────────────
    bool   sync_writes       = true;                 // fsync per batch
    size_t group_commit_max  = 128;                  // max records per fsync batch
    int    group_commit_us   = 100;                  // max μs to wait for a full batch

    // ── SSTable ───────────────────────────────────────────────────────────────
    size_t block_size        = 4096;                 // 4 KB data blocks
    double bloom_fpr         = 0.01;                 // 1% false-positive rate

    // ── Block cache ───────────────────────────────────────────────────────────
    size_t block_cache_bytes = 8 * 1024 * 1024;     // 8 MB LRU block cache

    // ── Compaction ────────────────────────────────────────────────────────────
    int    l0_compact_trigger    = 4;                // L0 files → trigger L0→L1
    int    l0_slowdown_trigger   = 8;                // L0 files → slow writes
    int    l0_stop_trigger       = 12;               // L0 files → stall writes
    size_t base_level_bytes      = 10 * 1024 * 1024;// 10 MB for L1
    int    level_multiplier      = 10;               // each level 10× larger
    int    max_levels            = 7;
};

}  // namespace lsmkv
