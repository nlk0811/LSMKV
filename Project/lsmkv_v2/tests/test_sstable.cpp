#include "test_framework.h"
#include "lsmkv/sstable.h"
#include "lsmkv/block_cache.h"
#include <cstdio>

using namespace lsmkv;

static std::string TmpSST(const char* s) { return std::string("/tmp/lsmkv_test_") + s + ".sst"; }
static void RM(const std::string& p) { remove(p.c_str()); }

static Status Build(const std::string& path,
                    const std::vector<std::tuple<std::string,std::string,bool>>& entries) {
    Options opts;
    SSTableWriter w(path, opts);
    for (auto& [k, v, t] : entries) w.Add(Slice(k), Slice(v), t);
    return w.Finish();
}

TEST(SSTable, GetExisting) {
    auto p = TmpSST("get");
    ASSERT_OK(Build(p, {{"a","1",false},{"b","2",false},{"c","3",false}}));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    std::string val; bool tomb;
    ASSERT_OK(r->Get(Slice("b"), &val, &tomb));
    ASSERT_EQ(val, "2");
    ASSERT_FALSE(tomb);
    RM(p);
}

TEST(SSTable, GetMissing) {
    auto p = TmpSST("miss");
    ASSERT_OK(Build(p, {{"a","1",false},{"c","3",false}}));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    std::string val; bool tomb;
    ASSERT_NOT_FOUND(r->Get(Slice("b"), &val, &tomb));
    RM(p);
}

TEST(SSTable, GetTombstone) {
    auto p = TmpSST("tomb");
    ASSERT_OK(Build(p, {{"dead","",true}}));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    std::string val; bool tomb;
    ASSERT_OK(r->Get(Slice("dead"), &val, &tomb));
    ASSERT_TRUE(tomb);
    RM(p);
}

TEST(SSTable, BloomRejectsMissing) {
    auto p = TmpSST("bloom");
    ASSERT_OK(Build(p, {{"x","1",false}}));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    ASSERT_FALSE(r->MayContain(Slice("zzz_definitely_absent_key")));
    RM(p);
}

TEST(SSTable, ScanAll) {
    auto p = TmpSST("scan");
    std::vector<std::tuple<std::string,std::string,bool>> entries;
    for (int i = 0; i < 50; ++i)
        entries.emplace_back("k" + std::to_string(i), "v", false);
    // sort entries by key
    std::sort(entries.begin(), entries.end());
    ASSERT_OK(Build(p, entries));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    int count = 0;
    r->Scan(Slice{}, Slice{}, [&](const Slice&, const Slice&, bool){ ++count; });
    ASSERT_EQ(count, 50);
    RM(p);
}

TEST(SSTable, MultiBlockRoundTrip) {
    auto p = TmpSST("multi");
    std::vector<std::tuple<std::string,std::string,bool>> entries;
    for (int i = 0; i < 200; ++i)
        entries.emplace_back("key" + std::to_string(i), std::string(100,'x'), false);
    std::sort(entries.begin(), entries.end());
    ASSERT_OK(Build(p, entries));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    for (int i = 0; i < 200; ++i) {
        std::string k = "key" + std::to_string(i), val; bool t;
        ASSERT_OK(r->Get(Slice(k), &val, &t));
    }
    RM(p);
}

TEST(SSTable, BlockCacheHit) {
    auto p = TmpSST("cache");
    ASSERT_OK(Build(p, {{"a","1",false},{"b","2",false}}));
    BlockCache cache(4*1024*1024);
    Status s;
    auto r = SSTableReader::Open(p, &cache, &s);
    ASSERT_OK(s);
    std::string val; bool tomb;
    r->Get(Slice("a"), &val, &tomb);  // miss
    r->Get(Slice("a"), &val, &tomb);  // hit
    ASSERT_TRUE(cache.HitCount() > 0);
    RM(p);
}
