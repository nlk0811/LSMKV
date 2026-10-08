#include "lsmkv/compaction.h"
#include "lsmkv/block_cache.h"
#include "lsmkv/sstable.h"
#include <cassert>
#include <unistd.h>
#include <memory>
#include <queue>
#include <string>
#include <vector>

namespace lsmkv {

struct MergeEntry {
    std::string key;
    std::string value;
    bool        tombstone;
    int         seq;   // reader index; lower = newer

    bool operator>(const MergeEntry& o) const {
        if (key != o.key) return key > o.key;
        return seq > o.seq;   // for equal keys, lower seq wins (min-heap wants smallest)
    }
};

Status RunCompaction(const std::vector<std::string>& paths,
                     const std::string&              out_path,
                     const Options&                  opts,
                     BlockCache*                     cache,
                     bool                            drop_tombstones,
                     size_t*                         written) {
    if (paths.empty()) { if (written) *written = 0; return Status::OK(); }

    // Open all readers
    std::vector<std::unique_ptr<SSTableReader>> readers;
    for (auto& p : paths) {
        Status s;
        auto r = SSTableReader::Open(p, cache, &s);
        if (!s.ok()) return s;
        readers.push_back(std::move(r));
    }

    // Prime the min-heap — smallest key first; for equal keys, lowest seq wins
    using Heap = std::priority_queue<MergeEntry,
                                     std::vector<MergeEntry>,
                                     std::greater<MergeEntry>>;

    // Iterators: one per reader
    struct Iter {
        SSTableReader*    r;
        std::vector<std::tuple<std::string,std::string,bool>> buf;
        size_t pos{0};
        bool   done{false};
        int    seq;

        void Advance(const Slice& start = Slice{}) {
            if (done) return;
            buf.clear(); pos = 0;
            r->Scan(start, Slice{}, [&](const Slice& k, const Slice& v, bool t){
                buf.emplace_back(k.ToString(), v.ToString(), t);
            });
            done = true;  // scan is full-table; we buffer everything
        }
    };

    // Buffer all entries (small enough for research purposes)
    std::vector<MergeEntry> all_entries;
    for (int i = 0; i < static_cast<int>(readers.size()); ++i) {
        readers[i]->Scan(Slice{}, Slice{},
            [&](const Slice& k, const Slice& v, bool t) {
                all_entries.push_back({k.ToString(), v.ToString(), t, i});
            });
    }

    // Sort: key ASC, then seq ASC (newest first for equal keys)
    std::sort(all_entries.begin(), all_entries.end(),
              [](const MergeEntry& a, const MergeEntry& b){
                  if (a.key != b.key) return a.key < b.key;
                  return a.seq < b.seq;
              });

    SSTableWriter w(out_path, opts);
    std::string last_key;
    size_t cnt = 0;

    for (auto& e : all_entries) {
        if (e.key == last_key) continue;   // dedup: first occurrence = newest
        last_key = e.key;
        if (drop_tombstones && e.tombstone) continue;
        w.Add(Slice(e.key), Slice(e.value), e.tombstone);
        ++cnt;
    }

    if (cnt > 0) {
        auto s = w.Finish();
        if (!s.ok()) return s;
    } else {
        // Nothing written — clean up the empty file that Finish() would create
        w.Finish();
        unlink(out_path.c_str());
    }

    if (written) *written = cnt;
    return Status::OK();
}

}  // namespace lsmkv
