#include "test_framework.h"
#include "lsmkv/lsm_tree.h"
#include <cstdlib>
#include <dirent.h>
#include <thread>
#include <unistd.h>

using namespace lsmkv;

static std::string TestDir(const char* name) {
    return std::string("/tmp/lsmkv_test_db_") + name;
}

static void RmDir(const std::string& dir) {
    DIR* d = opendir(dir.c_str());
    if (!d) return;
    struct dirent* ent;
    while ((ent = readdir(d)) != nullptr) {
        std::string f = dir + "/" + ent->d_name;
        if (ent->d_name[0] == '.') continue;
        remove(f.c_str());
    }
    closedir(d);
    rmdir(dir.c_str());
}

static Options AsyncOpts() {
    Options o; o.sync_writes = false; return o;
}

TEST(LSMTree, PutGet) {
    auto dir = TestDir("put_get");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    ASSERT_OK(db->Put(Slice("hello"), Slice("world")));
    std::string val;
    ASSERT_OK(db->Get(Slice("hello"), &val));
    ASSERT_EQ(val, "world");
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, GetMissing) {
    auto dir = TestDir("missing");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    std::string val;
    ASSERT_NOT_FOUND(db->Get(Slice("nope"), &val));
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, Overwrite) {
    auto dir = TestDir("overwrite");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    db->Put(Slice("k"), Slice("v1"));
    db->Put(Slice("k"), Slice("v2"));
    std::string val;
    ASSERT_OK(db->Get(Slice("k"), &val));
    ASSERT_EQ(val, "v2");
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, Delete) {
    auto dir = TestDir("delete");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    db->Put(Slice("k"), Slice("v"));
    db->Delete(Slice("k"));
    std::string val;
    ASSERT_NOT_FOUND(db->Get(Slice("k"), &val));
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, ScanBasic) {
    auto dir = TestDir("scan");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    for (int i = 0; i < 10; ++i) {
        char buf[4]; snprintf(buf, sizeof(buf), "%02d", i);
        db->Put(Slice(buf, 2), Slice("v"));
    }
    std::vector<std::string> keys;
    db->Scan(Slice("03"), Slice("07"), [&](const Slice& k, const Slice&){
        keys.push_back(k.ToString());
    });
    ASSERT_EQ(keys.size(), 4u);
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, ScanExcludesTombstones) {
    auto dir = TestDir("scan_tomb");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    db->Put(Slice("a"), Slice("1"));
    db->Put(Slice("b"), Slice("2"));
    db->Delete(Slice("a"));
    std::vector<std::string> keys;
    db->Scan(Slice{}, Slice{}, [&](const Slice& k, const Slice&){ keys.push_back(k.ToString()); });
    ASSERT_FALSE(std::find(keys.begin(), keys.end(), "a") != keys.end());
    ASSERT_TRUE(std::find(keys.begin(), keys.end(), "b") != keys.end());
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, PersistenceAcrossFlush) {
    auto dir = TestDir("persist");
    RmDir(dir);
    {
        std::unique_ptr<LSMTree> db;
        ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
        for (int i = 0; i < 200; ++i)
            db->Put(Slice("pk" + std::to_string(i)), Slice("pv" + std::to_string(i)));
        db->FlushMemtable();
        db->Close();
    }
    {
        std::unique_ptr<LSMTree> db;
        ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
        for (int i = 0; i < 200; ++i) {
            std::string val;
            ASSERT_OK(db->Get(Slice("pk" + std::to_string(i)), &val));
            ASSERT_EQ(val, "pv" + std::to_string(i));
        }
        db->Close();
    }
    RmDir(dir);
}

TEST(LSMTree, LargeWriteRead) {
    auto dir = TestDir("large");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    for (int i = 0; i < 2000; ++i)
        db->Put(Slice("bigkey" + std::to_string(i)), Slice("value" + std::to_string(i)));
    for (int i = 0; i < 2000; ++i) {
        std::string val;
        ASSERT_OK(db->Get(Slice("bigkey" + std::to_string(i)), &val));
        ASSERT_EQ(val, "value" + std::to_string(i));
    }
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, StatsNonEmpty) {
    auto dir = TestDir("stats");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    db->Put(Slice("x"), Slice("y"));
    auto s = db->GetStats();
    ASSERT_TRUE(s.memtable_bytes > 0);
    db->Close();
    RmDir(dir);
}

TEST(LSMTree, ConcurrentReads) {
    // v2: shared_mutex allows multiple readers concurrently
    auto dir = TestDir("concurrent");
    RmDir(dir);
    std::unique_ptr<LSMTree> db;
    ASSERT_OK(LSMTree::Open(dir, AsyncOpts(), &db));
    for (int i = 0; i < 100; ++i)
        db->Put(Slice("k" + std::to_string(i)), Slice("v" + std::to_string(i)));

    std::vector<std::thread> readers;
    std::atomic<int> errors{0};
    for (int t = 0; t < 8; ++t) {
        readers.emplace_back([&]{
            for (int i = 0; i < 100; ++i) {
                std::string val;
                auto s = db->Get(Slice("k" + std::to_string(i)), &val);
                if (!s.ok()) ++errors;
            }
        });
    }
    for (auto& r : readers) r.join();
    ASSERT_EQ(errors.load(), 0);
    db->Close();
    RmDir(dir);
}
