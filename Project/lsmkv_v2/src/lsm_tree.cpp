#include "lsmkv/lsm_tree.h"
#include "lsmkv/compaction.h"
#include "lsmkv/sstable.h"
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <queue>
#include <set>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __APPLE__
#ifndef F_FULLFSYNC
#define F_FULLFSYNC 51
#endif
#endif

namespace lsmkv {

static Status DurableSyncDir(const std::string& dir) {
    int fd = open(dir.c_str(), O_RDONLY);
    if (fd < 0) return Status::IOError("open dir");
#ifdef __APPLE__
    fcntl(fd, F_FULLFSYNC);
#else
    fsync(fd);
#endif
    close(fd);
    return Status::OK();
}

static bool FileExists(const std::string& p) {
    struct stat st; return stat(p.c_str(), &st) == 0;
}

static std::string TmpPath(const std::string& dir, const std::string& name) {
    return dir + "/" + name;
}

static int64_t NowUs() {
    return std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

// ── Open ──────────────────────────────────────────────────────────────────────

LSMTree::LSMTree(const std::string& dir, const Options& opts)
    : dir_(dir), opts_(opts) {}

Status LSMTree::Open(const std::string& dir, const Options& opts,
                     std::unique_ptr<LSMTree>* out) {
    mkdir(dir.c_str(), 0755);
    auto db = std::unique_ptr<LSMTree>(new LSMTree(dir, opts));
    auto s  = db->Init();
    if (!s.ok()) return s;
    *out = std::move(db);
    return Status::OK();
}

Status LSMTree::Init() {
    cache_    = std::make_unique<BlockCache>(opts_.block_cache_bytes);
    manifest_ = std::make_unique<Manifest>(dir_, opts_);
    auto s    = manifest_->Load();
    if (!s.ok()) return s;

    wal_  = std::make_unique<WAL>(dir_ + "/wal.log", opts_);
    arena_ = std::make_unique<Arena>();
    mem_  = std::make_unique<SkipList>(arena_.get());

    CleanupOrphans();
    Recover();

    bg_thread_ = std::thread(&LSMTree::BackgroundLoop, this);
    return Status::OK();
}

LSMTree::~LSMTree() { Close(); }

void LSMTree::Close() {
    if (!shutdown_.exchange(true)) {
        {
            std::lock_guard<std::mutex> lk(bg_mu_);
        }
        bg_cv_.notify_all();
        if (bg_thread_.joinable()) bg_thread_.join();

        // FlushMemtable acquires rw_lock_ internally — do NOT hold it here.
        if (mem_ && mem_->Count() > 0) FlushMemtable();
        wal_->Close();
    }
}

// ── Write API ─────────────────────────────────────────────────────────────────

Status LSMTree::Put(const Slice& key, const Slice& value) {
    MaybeStallWrites();
    auto s = wal_->LogPut(key, value);   // Invariant 1: WAL first
    if (!s.ok()) return s;

    std::unique_lock<std::shared_mutex> wlk(rw_lock_);
    mem_->Put(key, value);
    if (mem_->ByteSize() >= opts_.memtable_size_bytes) {
        wlk.unlock();
        return FlushMemtable();
    }
    return Status::OK();
}

Status LSMTree::Delete(const Slice& key) {
    MaybeStallWrites();
    auto s = wal_->LogDelete(key);       // Invariant 1
    if (!s.ok()) return s;

    std::unique_lock<std::shared_mutex> wlk(rw_lock_);
    mem_->Delete(key);
    if (mem_->ByteSize() >= opts_.memtable_size_bytes) {
        wlk.unlock();
        return FlushMemtable();
    }
    return Status::OK();
}

// ── Read API ──────────────────────────────────────────────────────────────────

Status LSMTree::Get(const Slice& key, std::string* value) const {
    // 1. Check memtable — shared lock allows concurrent reads (v2 improvement)
    {
        std::shared_lock<std::shared_mutex> rlk(rw_lock_);
        const Entry* e = mem_->Get(key);
        if (e) {
            if (e->tombstone) return Status::NotFound("deleted");
            *value = e->value;
            return Status::OK();
        }
    }

    // 2. Snapshot levels under manifest lock
    auto levels = manifest_->Snapshot();

    // 3. Search SSTables — no lock held during I/O (POSIX: open fd survives unlink)
    for (int lvl = 0; lvl < static_cast<int>(levels.size()); ++lvl) {
        auto& files = levels[lvl];
        // L0: search newest file first (reverse order); L1+: forward order
        auto Search = [&](const std::string& path) -> Status {
            if (!FileExists(path)) return Status::NotFound("missing");
            Status s;
            auto r = SSTableReader::Open(path, cache_.get(), &s);
            if (!s.ok()) return s;
            bool tomb = false;
            s = r->Get(key, value, &tomb);
            if (s.ok()) return tomb ? Status::NotFound("tombstone") : Status::OK();
            return s;
        };

        if (lvl == 0) {
            for (int i = static_cast<int>(files.size()) - 1; i >= 0; --i) {
                auto s = Search(files[i]);
                if (s.ok() || !s.IsNotFound()) return s;
            }
        } else {
            for (auto& f : files) {
                auto s = Search(f);
                if (s.ok() || !s.IsNotFound()) return s;
            }
        }
    }
    return Status::NotFound("key not found");
}

Status LSMTree::Scan(const Slice& start, const Slice& end,
                     std::function<void(const Slice&,const Slice&)> fn) const {
    // Collect all sources with priorities (lowest = newest)
    struct SrcEntry {
        std::string key, value;
        bool        tombstone;
        int         pri;
        bool operator>(const SrcEntry& o) const {
            if (key != o.key) return key > o.key;
            return pri > o.pri;
        }
    };

    std::vector<SrcEntry> all;

    // Memtable
    {
        std::shared_lock<std::shared_mutex> rlk(rw_lock_);
        mem_->Scan(start, end, [&](const Entry& e){
            all.push_back({e.key, e.value, e.tombstone, 0});
        });
    }

    auto levels = manifest_->Snapshot();
    int pri = 1;
    for (int lvl = 0; lvl < static_cast<int>(levels.size()); ++lvl) {
        auto& files = levels[lvl];
        auto DoScan = [&](const std::string& path) {
            if (!FileExists(path)) return;
            Status s;
            auto r = SSTableReader::Open(path, cache_.get(), &s);
            if (!s.ok()) return;
            int p = pri++;
            r->Scan(start, end, [&](const Slice& k, const Slice& v, bool t){
                all.push_back({k.ToString(), v.ToString(), t, p});
            });
        };

        if (lvl == 0) {
            for (int i = static_cast<int>(files.size()) - 1; i >= 0; --i) DoScan(files[i]);
        } else {
            for (auto& f : files) DoScan(f);
        }
    }

    // Sort: key ASC, pri ASC (lowest pri = newest wins)
    std::sort(all.begin(), all.end(), [](const SrcEntry& a, const SrcEntry& b){
        if (a.key != b.key) return a.key < b.key;
        return a.pri < b.pri;
    });

    std::string last;
    for (auto& e : all) {
        if (e.key == last) continue;
        last = e.key;
        if (e.tombstone) continue;
        fn(Slice(e.key), Slice(e.value));
    }
    return Status::OK();
}

// ── Flush ─────────────────────────────────────────────────────────────────────

Status LSMTree::FlushMemtable() {
    // Swap memtable
    std::unique_ptr<SkipList> flushing;
    std::unique_ptr<Arena>    flushing_arena;
    {
        std::unique_lock<std::shared_mutex> wlk(rw_lock_);
        if (!mem_ || mem_->Count() == 0) return Status::OK();
        flushing       = std::move(mem_);
        flushing_arena = std::move(arena_);
        arena_ = std::make_unique<Arena>();
        mem_   = std::make_unique<SkipList>(arena_.get());
    }

    // Write SSTable to tmp
    int64_t ts   = NowUs();
    std::string tmp_path  = dir_ + "/L0_" + std::to_string(ts) + ".sst.tmp";
    std::string sst_path  = dir_ + "/L0_" + std::to_string(ts) + ".sst";

    SSTableWriter writer(tmp_path, opts_);
    flushing->Scan(Slice{}, Slice{}, [&](const Entry& e){
        writer.Add(Slice(e.key), Slice(e.value), e.tombstone);
    });
    auto s = writer.Finish();                      // fsync inside
    if (!s.ok()) { unlink(tmp_path.c_str()); return s; }

    if (rename(tmp_path.c_str(), sst_path.c_str()) != 0)
        return Status::IOError("rename L0 SSTable failed");
    DurableSyncDir(dir_);

    // Invariant 3: MANIFEST before WAL truncation
    s = manifest_->AddL0(sst_path);
    if (!s.ok()) return s;

    s = wal_->Truncate();   // NOW safe

    // Wake compaction thread
    bg_cv_.notify_one();
    return s;
}

// ── Background compaction ─────────────────────────────────────────────────────

void LSMTree::BackgroundLoop() {
    while (!shutdown_.load()) {
        {
            std::unique_lock<std::mutex> lk(bg_mu_);
            bg_cv_.wait_for(lk, std::chrono::seconds(2));
        }
        if (shutdown_) break;
        try { MaybeCompact(); } catch (...) {}
    }
}

void LSMTree::MaybeCompact() {
    auto levels = manifest_->Snapshot();
    if (static_cast<int>(levels[0].size()) >= opts_.l0_compact_trigger) {
        CompactLevel(0);
        return;
    }
    for (int lvl = 1; lvl < opts_.max_levels - 1; ++lvl) {
        size_t budget = opts_.base_level_bytes;
        for (int i = 1; i < lvl; ++i) budget *= opts_.level_multiplier;
        if (manifest_->LevelSize(lvl) > budget) {
            CompactLevel(lvl);
            return;
        }
    }
}

Status LSMTree::CompactLevel(int src_lvl) {
    int dst_lvl = src_lvl + 1;
    auto snap   = manifest_->Snapshot();

    std::vector<std::string> inputs = snap[src_lvl];
    for (auto& f : snap[dst_lvl]) inputs.push_back(f);
    if (inputs.empty()) return Status::OK();

    int64_t ts   = NowUs();
    std::string tmp = dir_ + "/L" + std::to_string(dst_lvl) + "_" +
                      std::to_string(ts) + ".sst.tmp";
    std::string out = dir_ + "/L" + std::to_string(dst_lvl) + "_" +
                      std::to_string(ts) + ".sst";

    bool deepest = (dst_lvl == opts_.max_levels - 1);
    size_t written = 0;
    auto s = RunCompaction(inputs, tmp, opts_, cache_.get(), deepest, &written);
    if (!s.ok()) { unlink(tmp.c_str()); return s; }

    std::map<int,std::vector<std::string>> rm_map, add_map;
    rm_map[src_lvl] = snap[src_lvl];
    rm_map[dst_lvl] = snap[dst_lvl];

    if (written > 0) {
        if (rename(tmp.c_str(), out.c_str()) != 0)
            return Status::IOError("rename compacted file failed");
        DurableSyncDir(dir_);
        add_map[dst_lvl] = {out};
    }

    // Invariant 2: MANIFEST is the commit point
    s = manifest_->ApplyCompaction(rm_map, add_map);

    // Delete old input files after MANIFEST is updated
    for (auto& f : inputs) {
        cache_->Evict(f);
        unlink(f.c_str());
    }

    // Wake writers that may be stalled on L0
    stall_cv_.notify_all();
    return s;
}

Status LSMTree::CompactAll() {
    for (int i = 0; i < opts_.max_levels - 1; ++i) {
        auto s = CompactLevel(i);
        if (!s.ok()) return s;
    }
    return Status::OK();
}

// ── Write stall ───────────────────────────────────────────────────────────────

void LSMTree::MaybeStallWrites() {
    while (true) {
        auto lvl = manifest_->Snapshot()[0];
        int  n   = static_cast<int>(lvl.size());
        if (n < opts_.l0_stop_trigger) break;
        std::unique_lock<std::mutex> lk(stall_mu_);
        stall_cv_.wait_for(lk, std::chrono::milliseconds(10));
    }
}

// ── Recovery ──────────────────────────────────────────────────────────────────

void LSMTree::Recover() {
    wal_->Replay([&](const WALRecord& rec){
        if (rec.op == OpType::PUT)
            mem_->Put(Slice(rec.key), Slice(rec.value));
        else
            mem_->Delete(Slice(rec.key));
    });
}

void LSMTree::CleanupOrphans() {
    auto known = manifest_->AllFiles();
    std::set<std::string> known_set(known.begin(), known.end());

    DIR* d = opendir(dir_.c_str());
    if (!d) return;
    struct dirent* ent;
    while ((ent = readdir(d)) != nullptr) {
        std::string name(ent->d_name);
        if (name.size() < 4) continue;
        bool is_sst = (name.size() >= 4 && name.substr(name.size()-4) == ".sst") ||
                      (name.find(".sst.tmp") != std::string::npos);
        if (!is_sst) continue;
        std::string full = dir_ + "/" + name;
        if (known_set.find(full) == known_set.end()) unlink(full.c_str());
    }
    closedir(d);
}

// ── Stats ─────────────────────────────────────────────────────────────────────

LSMTree::Stats LSMTree::GetStats() const {
    Stats s;
    {
        std::shared_lock<std::shared_mutex> rlk(rw_lock_);
        s.memtable_bytes = mem_->ByteSize();
        s.memtable_keys  = mem_->Count();
    }
    s.wal_bytes    = wal_->SizeBytes();
    s.cache_hits   = cache_->HitCount();
    s.cache_misses = cache_->MissCount();
    auto snap = manifest_->Snapshot();
    for (int i = 0; i < static_cast<int>(snap.size()); ++i) {
        size_t sz = manifest_->LevelSize(i);
        if (sz > 0) s.level_sizes.emplace_back(i, sz);
    }
    return s;
}

}  // namespace lsmkv
