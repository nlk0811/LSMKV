#pragma once
/*
 * v2 NEW: LRU Block Cache
 * ────────────────────────
 * Caches hot SSTable data blocks in memory so repeated reads of the same
 * block do not incur disk I/O.
 *
 * Key: (sstable_path, block_offset) → block_data (std::string)
 * Policy: LRU eviction when capacity is exceeded.
 * Thread-safe: std::mutex guards the hash-map and LRU list.
 */
#include "status.h"
#include <cstdint>
#include <list>
#include <mutex>
#include <string>
#include <unordered_map>

namespace lsmkv {

class BlockCache {
public:
    explicit BlockCache(size_t capacity_bytes);

    // Returns false if not in cache.
    bool Lookup(const std::string& path, uint64_t offset,
                std::string* out) const;

    // Insert block into cache.  If cache is full, evict LRU entry.
    void Insert(const std::string& path, uint64_t offset,
                const std::string& data);

    // Evict all blocks for a given SSTable path (called when file is deleted).
    void Evict(const std::string& path);

    // Stats
    size_t HitCount()  const { return hits_; }
    size_t MissCount() const { return misses_; }
    double HitRate()   const;

private:
    struct Key {
        std::string path;
        uint64_t    offset;
        bool operator==(const Key& o) const {
            return offset == o.offset && path == o.path;
        }
    };
    struct KeyHash {
        size_t operator()(const Key& k) const {
            size_t h = std::hash<std::string>{}(k.path);
            h ^= std::hash<uint64_t>{}(k.offset) + 0x9e3779b9 + (h << 6) + (h >> 2);
            return h;
        }
    };

    using LRUList = std::list<std::pair<Key, std::string>>;
    using LRUMap  = std::unordered_map<Key, LRUList::iterator, KeyHash>;

    mutable std::mutex mu_;
    size_t          capacity_;
    mutable size_t  used_{0};
    mutable LRUList lru_;
    mutable LRUMap  map_;

    mutable size_t hits_{0};
    mutable size_t misses_{0};
};

}  // namespace lsmkv
