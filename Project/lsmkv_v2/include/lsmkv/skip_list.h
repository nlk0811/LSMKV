#pragma once
/*
 * v2: SkipList backed by an Arena allocator — O(1) node allocation, better
 * cache locality, and bulk deallocation when the memtable is flushed.
 *
 * Thread safety: concurrent reads are safe under the GCC/Clang memory model
 * when combined with a write-side lock in LSMTree.
 */
#include "types.h"
#include "arena.h"
#include <atomic>
#include <cstdint>
#include <functional>
#include <optional>

namespace lsmkv {

struct Entry {
    std::string key;
    std::string value;
    bool        tombstone;
};

class SkipList {
public:
    static constexpr int kMaxLevel  = 16;
    static constexpr double kProb   = 0.5;

    // Forward-declare Node so Iterator can reference it before the full definition.
    struct Node;

    explicit SkipList(Arena* arena);
    ~SkipList() = default;

    SkipList(const SkipList&)            = delete;
    SkipList& operator=(const SkipList&) = delete;

    void Put(const Slice& key, const Slice& value);
    void Delete(const Slice& key);

    // Returns nullptr if not present.
    const Entry* Get(const Slice& key) const;

    // Iterates in sorted order, calling fn(entry) for each.
    // Pass empty Slices for unbounded scan.
    void Scan(const Slice& start, const Slice& end,
              std::function<void(const Entry&)> fn) const;

    size_t  ByteSize() const { return byte_size_; }
    size_t  Count()    const { return count_; }
    Arena*  GetArena() const { return arena_; }

    // Iterator
    class Iterator {
    public:
        explicit Iterator(const SkipList* list);
        bool     Valid() const;
        void     SeekToFirst();
        void     Seek(const Slice& target);
        void     Next();
        const Entry& entry() const;
    private:
        Node*           node_;    // SkipList::Node — forward-declared above
        const SkipList* list_;
    };

    // Node is public so Iterator can access it; NewNode/FindGreaterOrEqual are private.
    struct Node {
        Entry               entry;
        int                 level;
        std::atomic<Node*>  forward[kMaxLevel];

        Node(const std::string& k, const std::string& v, bool tomb, int lvl, Arena* arena);
    };

private:
    Node*  NewNode(const std::string& k, const std::string& v, bool tomb, int lvl);
    int    RandomLevel();
    Node*  FindGreaterOrEqual(const Slice& key, Node** prev) const;

    Arena* arena_;
    Node*  head_;
    int    max_level_;
    size_t byte_size_;
    size_t count_;
};

}  // namespace lsmkv
