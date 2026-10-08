#pragma once
#include <cstddef>
#include <vector>

namespace lsmkv {

// Arena allocator — allocates from large blocks, frees all at once.
// Used by SkipList so node allocation is O(1) and cache-friendly.
// Not thread-safe; external synchronisation required.
class Arena {
public:
    Arena();
    ~Arena();

    Arena(const Arena&)            = delete;
    Arena& operator=(const Arena&) = delete;

    // Allocate n bytes.  Aligned to pointer size.
    char* Allocate(size_t n);

    // Total memory allocated from OS (approximate usage).
    size_t MemoryUsage() const { return memory_usage_; }

private:
    char* AllocateFallback(size_t n);
    char* AllocateNewBlock(size_t n);

    static constexpr size_t kBlockSize = 4096;

    char*  alloc_ptr_;
    size_t alloc_remaining_;
    size_t memory_usage_;

    std::vector<char*> blocks_;
};

}  // namespace lsmkv
