#include "lsmkv/bloom_filter.h"
#include <cassert>
#include <cmath>
#include <functional>
#include <cstring>
#include <stdexcept>

namespace lsmkv {

// ── Fast double-hashing (MurmurHash3-inspired 64-bit) ─────────────────────────
// v2 improvement: ~20× faster than SHA-256 used in v1.

static uint64_t Hash64(const char* data, size_t n, uint64_t seed) {
    uint64_t h = seed ^ (uint64_t(n) * 0x9e3779b97f4a7c15ULL);
    while (n >= 8) {
        uint64_t v = 0;
        memcpy(&v, data, 8);
        v ^= v >> 33;
        v *= 0xff51afd7ed558ccdULL;
        h = (h ^ v) * 0xc4ceb9fe1a85ec53ULL;
        h ^= h >> 33;
        data += 8; n -= 8;
    }
    if (n > 0) {
        uint64_t v = 0;
        memcpy(&v, data, n);
        h ^= v;
        h *= 0xff51afd7ed558ccdULL;
        h ^= h >> 33;
    }
    return h;
}

// Double-hashing: h_i = h1 + i*h2, avoids a second full hash call.
static void ProbePositions(const Slice& key, uint32_t hash_count,
                           uint64_t bit_count,
                           std::function<void(uint64_t)> fn) {
    uint64_t h1 = Hash64(key.data(), key.size(), 0xdeadbeefdeadbeefULL);
    uint64_t h2 = (h1 >> 17) | (h1 << 47);     // rotate left 47
    if (h2 == 0) h2 = 1;                         // avoid degenerate case
    for (uint32_t i = 0; i < hash_count; ++i) {
        fn((h1 + uint64_t(i) * h2) % bit_count);
    }
}

// ── Constructor ───────────────────────────────────────────────────────────────

BloomFilter::BloomFilter(uint32_t capacity, double fpr)
    : capacity_(capacity), fpr_(fpr) {
    // Optimal bit_count: m = -n * ln(p) / (ln2)^2
    double ln2 = std::log(2.0);
    bit_count_  = static_cast<uint64_t>(
        std::ceil(-double(capacity) * std::log(fpr) / (ln2 * ln2)));
    // Optimal hash_count: k = (m/n) * ln2
    hash_count_ = std::max(1u, static_cast<uint32_t>(
        std::round(double(bit_count_) / capacity * ln2)));
    bits_.resize((bit_count_ + 7) / 8, 0);
}

// ── Bit operations ────────────────────────────────────────────────────────────

void BloomFilter::SetBit(uint64_t pos) {
    bits_[pos >> 3] |= uint8_t(1) << (pos & 7);
}

bool BloomFilter::GetBit(uint64_t pos) const {
    return (bits_[pos >> 3] >> (pos & 7)) & 1;
}

// ── Public API ────────────────────────────────────────────────────────────────

void BloomFilter::Add(const Slice& key) {
    ProbePositions(key, hash_count_, bit_count_,
                   [this](uint64_t pos){ SetBit(pos); });
}

bool BloomFilter::MayContain(const Slice& key) const {
    bool found = true;
    ProbePositions(key, hash_count_, bit_count_,
                   [&](uint64_t pos){ found &= GetBit(pos); });
    return found;
}

// ── Serialisation ─────────────────────────────────────────────────────────────
// Header (24 bytes, little-endian):
//   capacity(4) hash_count(4) fpr_as_float(4) _pad(4) bit_count(8)
// Then: bit array bytes
// Then: crc32(4)

static uint32_t CRC32(const uint8_t* buf, size_t n) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < n; ++i) {
        crc ^= buf[i];
        for (int k = 0; k < 8; ++k)
            crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1));
    }
    return ~crc;
}

static void WriteU32LE(uint32_t v, char* p) {
    p[0] = v & 0xFF; p[1] = (v>>8)&0xFF; p[2] = (v>>16)&0xFF; p[3] = (v>>24)&0xFF;
}
static void WriteU64LE(uint64_t v, char* p) {
    for (int i=0;i<8;i++) p[i] = (v>>(8*i))&0xFF;
}
static uint32_t ReadU32LE(const char* p) {
    return uint32_t(uint8_t(p[0]))|(uint32_t(uint8_t(p[1]))<<8)|
           (uint32_t(uint8_t(p[2]))<<16)|(uint32_t(uint8_t(p[3]))<<24);
}
static uint64_t ReadU64LE(const char* p) {
    uint64_t v=0;
    for(int i=0;i<8;i++) v|=uint64_t(uint8_t(p[i]))<<(8*i);
    return v;
}

std::string BloomFilter::Serialize() const {
    std::string out;
    out.resize(24 + bits_.size() + 4);
    char* p = out.data();

    WriteU32LE(capacity_,   p);    p+=4;
    WriteU32LE(hash_count_, p);    p+=4;
    float fpr_f = static_cast<float>(fpr_);
    memcpy(p, &fpr_f, 4);          p+=4;
    WriteU32LE(0,           p);    p+=4;  // padding
    WriteU64LE(bit_count_,  p);    p+=8;  // ← THE v1 FIX

    memcpy(p, bits_.data(), bits_.size()); p += bits_.size();

    // CRC over header + bits
    uint32_t crc = CRC32(reinterpret_cast<const uint8_t*>(out.data()),
                          out.size() - 4);
    WriteU32LE(crc, p);
    return out;
}

BloomFilter BloomFilter::Deserialize(const Slice& data, Status* s) {
    if (data.size() < 28) {  // min: 24 header + 0 bits + 4 crc
        *s = Status::Corruption("BloomFilter data too short");
        return BloomFilter{};
    }
    const char* p = data.data();
    size_t      n = data.size();

    uint32_t expected_crc = ReadU32LE(p + n - 4);
    uint32_t actual_crc   = CRC32(reinterpret_cast<const uint8_t*>(p), n - 4);
    if (expected_crc != actual_crc) {
        *s = Status::Corruption("BloomFilter CRC mismatch");
        return BloomFilter{};
    }

    BloomFilter bf;
    bf.capacity_   = ReadU32LE(p);    p+=4;
    bf.hash_count_ = ReadU32LE(p);    p+=4;
    float fpr_f;   memcpy(&fpr_f, p, 4); bf.fpr_ = fpr_f; p+=4;
    p+=4;  // skip padding
    bf.bit_count_  = ReadU64LE(p);    p+=8;  // ← correct: use stored value

    size_t bits_bytes = n - 24 - 4;
    bf.bits_.assign(reinterpret_cast<const uint8_t*>(p),
                    reinterpret_cast<const uint8_t*>(p) + bits_bytes);

    *s = Status::OK();
    return bf;
}

}  // namespace lsmkv
