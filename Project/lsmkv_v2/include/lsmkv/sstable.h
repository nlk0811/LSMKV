#pragma once
/*
 * SSTable — immutable, sorted on-disk file.
 *
 * v2 additions vs v1:
 *   - Block cache integration (avoids re-reading hot blocks)
 *   - Status return type everywhere (no silent failures)
 *   - Correct Bloom filter serialisation (bit_count explicit)
 *
 * File layout:
 *   [Data Block 0 … N]    raw entry sequences (4 KB each)
 *   [Index Block]          sparse index: first_key → (offset, size)
 *   [Bloom Filter Block]   serialised BloomFilter
 *   [Footer]               32-byte fixed tail
 *
 * Entry wire format (little-endian):
 *   key_len : 4 bytes (uint32)
 *   val_len : 4 bytes (uint32)
 *   flags   : 1 byte  (0x01 = tombstone)
 *   key     : key_len bytes
 *   value   : val_len bytes
 *
 * Footer (32 bytes):
 *   index_offset : 8 bytes (uint64)
 *   bloom_offset : 8 bytes (uint64)
 *   index_size   : 4 bytes (uint32)
 *   bloom_size   : 4 bytes (uint32)
 *   magic        : 8 bytes = "LSMKV2_!"
 */
#include "types.h"
#include "status.h"
#include "options.h"
#include "bloom_filter.h"
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace lsmkv {

class BlockCache;

// ── Writer ─────────────────────────────────────────────────────────────────────

class SSTableWriter {
public:
    SSTableWriter(const std::string& path, const Options& opts);
    ~SSTableWriter();

    Status Add(const Slice& key, const Slice& value, bool tombstone = false);
    Status Finish();                 // fsync + close; call exactly once
    size_t FileSize() const;

private:
    void FlushBlock();

    std::string path_;
    Options     opts_;
    int         fd_{-1};
    BloomFilter bloom_;

    std::string buf_;
    std::string first_key_in_block_;
    uint64_t    cur_offset_{0};

    struct IndexEntry { std::string first_key; uint64_t offset; uint32_t size; };
    std::vector<IndexEntry> index_;
};

// ── Reader ─────────────────────────────────────────────────────────────────────

class SSTableReader {
public:
    // Opens and validates the footer/index/bloom.
    static std::unique_ptr<SSTableReader> Open(const std::string& path,
                                               BlockCache*        cache,
                                               Status*            s);
    ~SSTableReader();

    // Returns NotFound if key absent, Corruption on read error.
    Status Get(const Slice& key, std::string* value, bool* tombstone) const;

    // Calls fn(key, value, tombstone) for each entry in [start, end).
    Status Scan(const Slice& start, const Slice& end,
                std::function<void(const Slice&, const Slice&, bool)> fn) const;

    bool        MayContain(const Slice& key) const;
    const std::string& FirstKey() const { return index_[0].first_key; }
    const std::string& Path()     const { return path_; }

private:
    SSTableReader() = default;
    Status LoadFooter();
    Status LoadIndex();
    Status LoadBloom();
    Status ReadBlock(uint64_t offset, uint32_t size, std::string* out) const;
    int    BlockIdx(const Slice& key) const;
    Status SearchBlock(const std::string& data, const Slice& key,
                       std::string* value, bool* tombstone) const;

    std::string path_;
    int         fd_{-1};
    BlockCache* cache_{nullptr};

    uint64_t index_offset_{}, bloom_offset_{};
    uint32_t index_size_{},   bloom_size_{};

    struct IndexEntry { std::string first_key; uint64_t offset; uint32_t size; };
    std::vector<IndexEntry> index_;
    BloomFilter bloom_;
};

}  // namespace lsmkv
