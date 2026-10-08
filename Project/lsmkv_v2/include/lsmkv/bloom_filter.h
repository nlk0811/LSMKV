#pragma once
/*
 * v2 Fix (from v1 finding): bit_count is stored EXPLICITLY in the serialised
 * header.  In v1 we derived it as len(bits)*8 which caused false negatives
 * whenever bit_count was not a multiple of 8.
 *
 * v2 Perf: Uses fast non-cryptographic double-hashing (MurmurHash3-inspired)
 * instead of SHA-256 — ~20× faster per probe.
 *
 * Wire format (little-endian, 24-byte header):
 *   capacity   : 4 bytes (uint32)
 *   hash_count : 4 bytes (uint32)
 *   fpr        : 4 bytes (float)
 *   _pad       : 4 bytes (reserved)
 *   bit_count  : 8 bytes (uint64)  ← EXPLICIT — THE v1 FIX
 *   bit_array  : ceil(bit_count/8) bytes
 *   crc32      : 4 bytes
 */
#include "types.h"
#include "status.h"
#include <vector>
#include <string>

namespace lsmkv {

class BloomFilter {
public:
    explicit BloomFilter(uint32_t capacity, double fpr = 0.01);

    void Add(const Slice& key);
    bool MayContain(const Slice& key) const;

    std::string Serialize() const;
    static BloomFilter Deserialize(const Slice& data, Status* s);

    uint64_t BitCount()   const { return bit_count_; }
    uint32_t HashCount()  const { return hash_count_; }
    double   Fpr()        const { return fpr_; }

private:
    // Default constructor — empty filter (always returns false for MayContain).
    BloomFilter() : capacity_(0), hash_count_(0), fpr_(0), bit_count_(0) {}
    void SetBit(uint64_t pos);
    bool GetBit(uint64_t pos) const;

    uint32_t          capacity_;
    uint32_t          hash_count_;
    double            fpr_;
    uint64_t          bit_count_;
    std::vector<uint8_t> bits_;

    friend class SSTableReader;  // needs default constructor to hold as member
};

}  // namespace lsmkv
