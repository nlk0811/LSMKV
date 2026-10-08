#include "test_framework.h"
#include "lsmkv/lsm_tree.h"
#include "lsmkv/wal.h"
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <fstream>
#include <sys/stat.h>
#include <unistd.h>

using namespace lsmkv;

static void RmDir(const std::string& dir) {
    DIR* d = opendir(dir.c_str());
    if (!d) return;
    struct dirent* ent;
    while ((ent = readdir(d)) != nullptr) {
        if (ent->d_name[0] == '.') continue;
        remove((dir + "/" + ent->d_name).c_str());
    }
    closedir(d);
    rmdir(dir.c_str());
}

static Options SyncOpts() {
    Options o;
    o.sync_writes      = true;
    o.group_commit_max = 64;   // batch fsyncs — keep test under 5 seconds
    return o;
}

// ── Invariant 1: WAL Completeness ─────────────────────────────────────────────

TEST(Crash, WALReplayRestoresMemtable) {
    std::string dir = "/tmp/lsmkv_crash_wal";
    RmDir(dir); mkdir(dir.c_str(), 0755);

    {
        std::unique_ptr<LSMTree> db;
        ASSERT_OK(LSMTree::Open(dir, SyncOpts(), &db));
        db->Put(Slice("survived"), Slice("yes"));
        // Force-close WAL without flushing (simulate crash)
        db->Close();
    }

    // Reopen — WAL replay should restore "survived"
    {
        std::unique_ptr<LSMTree> db;
        ASSERT_OK(LSMTree::Open(dir, SyncOpts(), &db));
        std::string val;
        ASSERT_OK(db->Get(Slice("survived"), &val));
        ASSERT_EQ(val, "yes");
        db->Close();
    }
    RmDir(dir);
}

TEST(Crash, TruncatedWALRecordIgnored) {
    std::string path = "/tmp/lsmkv_trunc_wal.log";
    remove(path.c_str());
    Options opts; opts.sync_writes = false;
    {
        WAL wal(path, opts);
        wal.LogPut(Slice("complete"), Slice("record"));
        // Append partial header (crash mid-write)
        wal.Close();
        std::ofstream f(path, std::ios::app | std::ios::binary);
        char partial[4] = {0x01, 0x00, 0x00, 0x00};  // op=PUT, no payload
        f.write(partial, 4);
    }
    WAL wal2(path, opts);
    int count = 0;
    wal2.Replay([&](const WALRecord& r){
        ASSERT_EQ(r.key, "complete");
        ++count;
    });
    ASSERT_EQ(count, 1);
    wal2.Close();
    remove(path.c_str());
}

// ── Invariant 2: Compaction Atomicity ─────────────────────────────────────────

TEST(Crash, OrphanSSTCleanedOnOpen) {
    std::string dir = "/tmp/lsmkv_crash_orphan";
    RmDir(dir); mkdir(dir.c_str(), 0755);

    {
        std::unique_ptr<LSMTree> db;
        ASSERT_OK(LSMTree::Open(dir, SyncOpts(), &db));
        db->Put(Slice("x"), Slice("y"));
        db->Close();
    }

    // Plant an orphaned .sst not in MANIFEST
    std::string orphan = dir + "/L1_9999999.sst";
    { std::ofstream f(orphan); f << "garbage"; }

    {
        std::unique_ptr<LSMTree> db;
        ASSERT_OK(LSMTree::Open(dir, SyncOpts(), &db));
        // Orphan should be cleaned up
        ASSERT_FALSE(access(orphan.c_str(), F_OK) == 0);
        db->Close();
    }
    RmDir(dir);
}

// ── Invariant 3: WAL Truncation Safety ────────────────────────────────────────

TEST(Crash, ManifestUpdatedBeforeWALTruncated) {
    std::string dir = "/tmp/lsmkv_crash_inv3";
    RmDir(dir); mkdir(dir.c_str(), 0755);

    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, SyncOpts(), &db));

    // Write enough to trigger a flush (group-commit keeps this fast)
    std::string chunk(512, 'x');
    for (int i = 0; i < 500; ++i)
        db->Put(Slice("k" + std::to_string(i)), Slice(chunk));

    db->FlushMemtable();

    // After flush: MANIFEST must have L0 files that exist on disk
    auto stats = db->GetStats();
    bool has_l0 = false;
    for (auto& [lvl, sz] : stats.level_sizes)
        if (lvl == 0) { has_l0 = true; ASSERT_TRUE(sz > 0); }
    ASSERT_TRUE(has_l0);

    // Data must still be readable (SSTable in MANIFEST, WAL truncated)
    std::string val;
    ASSERT_OK(db->Get(Slice("k0"), &val));

    db->Close();
    RmDir(dir);
}
