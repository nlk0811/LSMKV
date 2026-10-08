#include "lsmkv/skip_list.h"
#include <cassert>
#include <cstdlib>
#include <random>

namespace lsmkv {

// ── Node ──────────────────────────────────────────────────────────────────────

SkipList::Node::Node(const std::string& k, const std::string& v,
                     bool tomb, int lvl, Arena* /*arena*/)
    : entry{k, v, tomb}, level(lvl) {
    for (int i = 0; i < kMaxLevel; ++i)
        forward[i].store(nullptr, std::memory_order_relaxed);
}

// ── SkipList ──────────────────────────────────────────────────────────────────

SkipList::SkipList(Arena* arena)
    : arena_(arena), max_level_(0), byte_size_(0), count_(0) {
    head_ = NewNode("", "", false, kMaxLevel - 1);
}

SkipList::Node* SkipList::NewNode(const std::string& k, const std::string& v,
                                   bool tomb, int lvl) {
    // Allocate the node from the arena; use placement new.
    char* mem = arena_->Allocate(sizeof(Node));
    return new(mem) Node(k, v, tomb, lvl, arena_);
}

int SkipList::RandomLevel() {
    static thread_local std::mt19937 rng{std::random_device{}()};
    static std::bernoulli_distribution coin(kProb);
    int lvl = 0;
    while (lvl < kMaxLevel - 1 && coin(rng)) ++lvl;
    return lvl;
}

SkipList::Node* SkipList::FindGreaterOrEqual(const Slice& key,
                                              Node** prev) const {
    Node* x = head_;
    int   lvl = max_level_;
    while (true) {
        Node* next = x->forward[lvl].load(std::memory_order_acquire);
        if (next && Slice(next->entry.key) < key) {
            x = next;
        } else {
            if (prev) prev[lvl] = x;
            if (lvl == 0) return next;
            --lvl;
        }
    }
}

void SkipList::Put(const Slice& key, const Slice& value) {
    Node* prev[kMaxLevel];
    Node* existing = FindGreaterOrEqual(key, prev);

    if (existing && Slice(existing->entry.key) == key) {
        // Update in place
        size_t old_vlen = existing->entry.value.size();
        existing->entry.value     = value.ToString();
        existing->entry.tombstone = false;
        byte_size_ += value.size();
        byte_size_ -= old_vlen;
        return;
    }

    int lvl = RandomLevel();
    if (lvl > max_level_) {
        for (int i = max_level_ + 1; i <= lvl; ++i) prev[i] = head_;
        max_level_ = lvl;
    }

    Node* node = NewNode(key.ToString(), value.ToString(), false, lvl);
    for (int i = 0; i <= lvl; ++i) {
        node->forward[i].store(
            prev[i]->forward[i].load(std::memory_order_relaxed),
            std::memory_order_relaxed);
        prev[i]->forward[i].store(node, std::memory_order_release);
    }
    byte_size_ += key.size() + value.size();
    ++count_;
}

void SkipList::Delete(const Slice& key) {
    Node* prev[kMaxLevel];
    Node* node = FindGreaterOrEqual(key, prev);

    if (node && Slice(node->entry.key) == key) {
        // Mark as tombstone in place
        byte_size_ -= node->entry.value.size();
        node->entry.value     = {};
        node->entry.tombstone = true;
        return;
    }
    // Insert tombstone node
    int lvl = RandomLevel();
    if (lvl > max_level_) {
        for (int i = max_level_ + 1; i <= lvl; ++i) prev[i] = head_;
        max_level_ = lvl;
    }
    Node* tn = NewNode(key.ToString(), "", true, lvl);
    for (int i = 0; i <= lvl; ++i) {
        tn->forward[i].store(
            prev[i]->forward[i].load(std::memory_order_relaxed),
            std::memory_order_relaxed);
        prev[i]->forward[i].store(tn, std::memory_order_release);
    }
    byte_size_ += key.size();
    ++count_;
}

const Entry* SkipList::Get(const Slice& key) const {
    Node* node = FindGreaterOrEqual(key, nullptr);
    if (node && Slice(node->entry.key) == key) return &node->entry;
    return nullptr;
}

void SkipList::Scan(const Slice& start, const Slice& end,
                    std::function<void(const Entry&)> fn) const {
    Node* node = start.empty()
        ? head_->forward[0].load(std::memory_order_acquire)
        : FindGreaterOrEqual(start, nullptr);

    while (node) {
        Slice k(node->entry.key);
        if (!end.empty() && k >= end) break;
        if (!(!start.empty() && k < start)) fn(node->entry);
        node = node->forward[0].load(std::memory_order_acquire);
    }
}

// ── Iterator ──────────────────────────────────────────────────────────────────

SkipList::Iterator::Iterator(const SkipList* list)
    : node_(nullptr), list_(list) {}

bool SkipList::Iterator::Valid() const { return node_ != nullptr; }

void SkipList::Iterator::SeekToFirst() {
    node_ = list_->head_->forward[0].load(std::memory_order_acquire);
}

void SkipList::Iterator::Seek(const Slice& target) {
    node_ = list_->FindGreaterOrEqual(target, nullptr);
}

void SkipList::Iterator::Next() {
    assert(Valid());
    node_ = node_->forward[0].load(std::memory_order_acquire);
}

const Entry& SkipList::Iterator::entry() const {
    assert(Valid());
    return node_->entry;
}

}  // namespace lsmkv
