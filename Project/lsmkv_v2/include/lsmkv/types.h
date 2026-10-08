#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>

namespace lsmkv {

// ── Slice ─────────────────────────────────────────────────────────────────────
// Non-owning reference to a byte sequence.  Inspired by LevelDB's Slice.
class Slice {
public:
    Slice() : data_(""), size_(0) {}
    Slice(const char* d, size_t n) : data_(d), size_(n) {}
    Slice(const std::string& s) : data_(s.data()), size_(s.size()) {}
    Slice(std::string_view sv) : data_(sv.data()), size_(sv.size()) {}
    Slice(const char* s) : data_(s), size_(strlen(s)) {}

    const char* data()  const { return data_; }
    size_t      size()  const { return size_; }
    bool        empty() const { return size_ == 0; }

    char operator[](size_t i) const { return data_[i]; }

    std::string ToString() const { return {data_, size_}; }
    std::string_view ToView() const { return {data_, size_}; }

    bool operator==(const Slice& b) const {
        return size_ == b.size_ && memcmp(data_, b.data_, size_) == 0;
    }
    bool operator!=(const Slice& b) const { return !(*this == b); }
    bool operator< (const Slice& b) const {
        size_t n = std::min(size_, b.size_);
        int r = memcmp(data_, b.data_, n);
        if (r != 0) return r < 0;
        return size_ < b.size_;
    }
    bool operator<=(const Slice& b) const { return !(b < *this); }
    bool operator> (const Slice& b) const { return b < *this; }
    bool operator>=(const Slice& b) const { return !(*this < b); }

private:
    const char* data_;
    size_t      size_;
};

}  // namespace lsmkv
