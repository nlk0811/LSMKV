#include "lsmkv/block_cache.h"

namespace lsmkv {

BlockCache::BlockCache(size_t capacity_bytes)
    : capacity_(capacity_bytes), used_(0) {}

bool BlockCache::Lookup(const std::string& path, uint64_t offset,
                        std::string* out) const {
    std::lock_guard<std::mutex> lk(mu_);
    Key k{path, offset};
    auto it = map_.find(k);
    if (it == map_.end()) { ++misses_; return false; }
    // Move to front (most recently used)
    lru_.splice(lru_.begin(), lru_, it->second);
    *out = it->second->second;
    ++hits_;
    return true;
}

void BlockCache::Insert(const std::string& path, uint64_t offset,
                        const std::string& data) {
    std::lock_guard<std::mutex> lk(mu_);
    Key k{path, offset};
    auto it = map_.find(k);
    if (it != map_.end()) {
        // Already cached — move to front and update
        lru_.splice(lru_.begin(), lru_, it->second);
        used_ -= it->second->second.size();
        it->second->second = data;
        used_ += data.size();
        return;
    }
    // Evict LRU entries until we have room
    while (!lru_.empty() && used_ + data.size() > capacity_) {
        auto last = std::prev(lru_.end());
        used_ -= last->second.size();
        map_.erase(last->first);
        lru_.erase(last);
    }
    lru_.push_front({k, data});
    map_[k] = lru_.begin();
    used_ += data.size();
}

void BlockCache::Evict(const std::string& path) {
    std::lock_guard<std::mutex> lk(mu_);
    for (auto it = lru_.begin(); it != lru_.end(); ) {
        if (it->first.path == path) {
            used_ -= it->second.size();
            map_.erase(it->first);
            it = lru_.erase(it);
        } else {
            ++it;
        }
    }
}

double BlockCache::HitRate() const {
    std::lock_guard<std::mutex> lk(mu_);
    size_t total = hits_ + misses_;
    return total == 0 ? 0.0 : double(hits_) / total;
}

}  // namespace lsmkv
