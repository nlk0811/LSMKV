#include "test_framework.h"
#include "lsmkv/wal.h"
#include <cstdio>
#include <fstream>

using namespace lsmkv;

static std::string TmpWAL(const char* suffix = "") {
    return std::string("/tmp/lsmkv_test_wal") + suffix + ".log";
}

static void RemoveFile(const std::string& p) { remove(p.c_str()); }

TEST(WAL, LogPutAndReplay) {
    std::string path = TmpWAL("_put");
    RemoveFile(path);
    Options opts; opts.sync_writes = false;   // async for speed; correctness test
    {
        WAL wal(path, opts);
        ASSERT_OK(wal.LogPut(Slice("hello"), Slice("world")));
    }
    Options ro; ro.sync_writes = false;
    WAL wal2(path, ro);
    int count = 0;
    wal2.Replay([&](const WALRecord& r){
        ASSERT_EQ(r.op, OpType::PUT);
        ASSERT_EQ(r.key, "hello");
        ASSERT_EQ(r.value, "world");
        ++count;
    });
    ASSERT_EQ(count, 1);
    wal2.Close();
    RemoveFile(path);
}

TEST(WAL, LogDeleteAndReplay) {
    std::string path = TmpWAL("_del");
    RemoveFile(path);
    Options opts; opts.sync_writes = false;
    { WAL wal(path, opts); ASSERT_OK(wal.LogDelete(Slice("gone"))); }
    Options ro; ro.sync_writes = false;
    WAL wal2(path, ro);
    int count = 0;
    wal2.Replay([&](const WALRecord& r){
        ASSERT_EQ(r.op, OpType::DELETE);
        ASSERT_EQ(r.key, "gone");
        ++count;
    });
    ASSERT_EQ(count, 1);
    wal2.Close();
    RemoveFile(path);
}

TEST(WAL, MultipleRecords) {
    std::string path = TmpWAL("_multi");
    RemoveFile(path);
    Options opts; opts.sync_writes = false;
    {
        WAL wal(path, opts);
        for (int i = 0; i < 50; ++i)
            ASSERT_OK(wal.LogPut(Slice("k" + std::to_string(i)),
                                  Slice("v" + std::to_string(i))));
    }
    Options ro; ro.sync_writes = false;
    WAL wal2(path, ro);
    int count = 0;
    wal2.Replay([&](const WALRecord&){ ++count; });
    ASSERT_EQ(count, 50);
    wal2.Close();
    RemoveFile(path);
}

TEST(WAL, TruncateClearsWAL) {
    std::string path = TmpWAL("_trunc");
    RemoveFile(path);
    Options opts; opts.sync_writes = false;
    WAL wal(path, opts);
    wal.LogPut(Slice("x"), Slice("y"));
    ASSERT_TRUE(wal.SizeBytes() > 0);
    ASSERT_OK(wal.Truncate());
    ASSERT_EQ(wal.SizeBytes(), 0u);
    wal.Close();
    RemoveFile(path);
}

TEST(WAL, CorruptedCRCStopsReplay) {
    std::string path = TmpWAL("_crc");
    RemoveFile(path);
    Options opts; opts.sync_writes = false;
    { WAL wal(path, opts); wal.LogPut(Slice("k"), Slice("v")); }
    // Corrupt a byte in the middle
    {
        std::fstream f(path, std::ios::in | std::ios::out | std::ios::binary);
        f.seekp(5); char c; f.read(&c, 1); f.seekp(5);
        char bad = static_cast<char>(c ^ 0xFF); f.write(&bad, 1);
    }
    Options ro; ro.sync_writes = false;
    WAL wal2(path, ro);
    int count = 0;
    wal2.Replay([&](const WALRecord&){ ++count; });
    ASSERT_EQ(count, 0);
    wal2.Close();
    RemoveFile(path);
}

TEST(WAL, GroupCommitBatching) {
    // Verify group-commit: many concurrent submits, all succeed
    std::string path = TmpWAL("_gc");
    RemoveFile(path);
    Options opts; opts.sync_writes = true; opts.group_commit_max = 32;
    WAL wal(path, opts);
    std::vector<std::thread> threads;
    for (int i = 0; i < 64; ++i) {
        threads.emplace_back([&, i]{
            auto s = wal.LogPut(Slice("k" + std::to_string(i)),
                                 Slice("v" + std::to_string(i)));
            ASSERT_OK(s);
        });
    }
    for (auto& t : threads) t.join();
    wal.Close();

    Options ro; ro.sync_writes = false;
    WAL wal2(path, ro);
    int count = 0;
    wal2.Replay([&](const WALRecord&){ ++count; });
    ASSERT_EQ(count, 64);
    wal2.Close();
    RemoveFile(path);
}
