#pragma once
/*
 * LSMTree — Main Public API
 * ──────────────────────────
 * v2 improvements over v1:
 *   - Group-commit WAL (790× write throughput improvement from v1 finding)
 *   - Arena-backed SkipList (better cache locality, O(1) alloc)
 *   - LRU Block cache (hot SSTable blocks stay in memory)
 *   - Write stall (slow/stop writers when L0 is full)
 *   - Shared mutex for concurrent reads without write blocking
 *   - Status return type throughout (no silent failures)
 *   - C++ iterator interface for scans
 *
 * Crash-safety invariants (same three as v1, now enforced in C++):
 *   1. WAL written before memtable updated
 *   2. MANIFEST update is compaction commit point
 *   3. WAL truncated only after MANIFEST records the SSTable
 */
#include "options.h"
#include "status.h"
#include "types.h"
#include "skip_list.h"
#include "wal.h"
#include "manifest.h"
#include "block_cache.h"
#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <thread>
#include <vector>

namespace lsmkv {

class LSMTree {
public:
    // Opens (or creates) a database at `directory`.
    static Status Open(const std::string& directory, const Options& opts,
                       std::unique_ptr<LSMTree>* db);

    ~LSMTree();

    LSMTree(const LSMTree&)            = delete;
    LSMTree& operator=(const LSMTree&) = delete;

    Status Put(const Slice& key, const Slice& value);
    Status Delete(const Slice& key);
    Status Get(const Slice& key, std::string* value) const;

    // Range scan: calls fn(key, value) for each live entry in [start, end).
    // Pass empty Slices for unbounded scan.
    Status Scan(const Slice& start, const Slice& end,
                std::function<void(const Slice&, const Slice&)> fn) const;

    // Flush memtable to disk immediately (also called by compaction and close).
    Status FlushMemtable();

    // Block until all pending background compactions complete.
    Status CompactAll();

    void Close();

    struct Stats {
        size_t memtable_bytes;
        size_t memtable_keys;
        size_t wal_bytes;
        size_t cache_hits;
        size_t cache_misses;
        std::vector<std::pair<int, size_t>> level_sizes;   // {level, bytes}
    };
    Stats GetStats() const;

private:
    LSMTree(const std::string& dir, const Options& opts);
    Status Init();

    void   Recover();
    void   CleanupOrphans();
    void   BackgroundLoop();
    void   MaybeCompact();
    Status CompactLevel(int src_level);
    void   MaybeStallWrites();

    std::string             dir_;
    Options                 opts_;

    // Memtable
    mutable std::shared_mutex rw_lock_;   // shared for reads, exclusive for writes/flush
    std::unique_ptr<Arena>    arena_;
    std::unique_ptr<SkipList> mem_;

    // Subsystems
    std::unique_ptr<WAL>        wal_;
    std::unique_ptr<Manifest>   manifest_;
    std::unique_ptr<BlockCache> cache_;

    // Background compaction
    std::thread              bg_thread_;
    std::mutex               bg_mu_;
    std::condition_variable  bg_cv_;
    std::atomic<bool>        shutdown_{false};

    // Write stall
    std::mutex              stall_mu_;
    std::condition_variable stall_cv_;
};

}  // namespace lsmkv
