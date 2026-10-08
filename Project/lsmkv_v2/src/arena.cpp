#include "lsmkv/arena.h"
#include <cassert>
#include <cstdlib>

namespace lsmkv {

Arena::Arena() : alloc_ptr_(nullptr), alloc_remaining_(0), memory_usage_(0) {}

Arena::~Arena() {
    for (char* b : blocks_) free(b);
}

char* Arena::Allocate(size_t n) {
    assert(n > 0);
    if (n <= alloc_remaining_) {
        char* result  = alloc_ptr_;
        alloc_ptr_    += n;
        alloc_remaining_ -= n;
        return result;
    }
    return AllocateFallback(n);
}

char* Arena::AllocateFallback(size_t n) {
    if (n > kBlockSize / 4) {
        // Large allocation — give it its own block.
        return AllocateNewBlock(n);
    }
    // Waste the remaining space in the current block.
    alloc_ptr_       = AllocateNewBlock(kBlockSize);
    alloc_remaining_ = kBlockSize;

    char* result      = alloc_ptr_;
    alloc_ptr_       += n;
    alloc_remaining_ -= n;
    return result;
}

char* Arena::AllocateNewBlock(size_t n) {
    char* result  = static_cast<char*>(malloc(n));
    if (!result) abort();
    blocks_.push_back(result);
    memory_usage_ += n;
    return result;
}

}  // namespace lsmkv
