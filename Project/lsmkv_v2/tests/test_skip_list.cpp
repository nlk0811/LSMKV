#include "test_framework.h"
#include "lsmkv/skip_list.h"
#include "lsmkv/arena.h"

using namespace lsmkv;

static std::pair<std::unique_ptr<Arena>, std::unique_ptr<SkipList>> MakeSL() {
    auto arena = std::make_unique<Arena>();
    auto sl    = std::make_unique<SkipList>(arena.get());
    return {std::move(arena), std::move(sl)};
}

TEST(SkipList, PutAndGet) {
    auto [a, sl] = MakeSL();
    sl->Put(Slice("hello"), Slice("world"));
    const Entry* e = sl->Get(Slice("hello"));
    ASSERT_TRUE(e != nullptr);
    ASSERT_EQ(e->value, "world");
    ASSERT_FALSE(e->tombstone);
}

TEST(SkipList, GetMissing) {
    auto [a, sl] = MakeSL();
    ASSERT_TRUE(sl->Get(Slice("nope")) == nullptr);
}

TEST(SkipList, Overwrite) {
    auto [a, sl] = MakeSL();
    sl->Put(Slice("k"), Slice("v1"));
    sl->Put(Slice("k"), Slice("v2"));
    auto e = sl->Get(Slice("k"));
    ASSERT_EQ(e->value, "v2");
}

TEST(SkipList, DeleteExisting) {
    auto [a, sl] = MakeSL();
    sl->Put(Slice("k"), Slice("v"));
    sl->Delete(Slice("k"));
    auto e = sl->Get(Slice("k"));
    ASSERT_TRUE(e != nullptr);
    ASSERT_TRUE(e->tombstone);
}

TEST(SkipList, PutAfterDelete) {
    auto [a, sl] = MakeSL();
    sl->Put(Slice("k"), Slice("v1"));
    sl->Delete(Slice("k"));
    sl->Put(Slice("k"), Slice("v2"));
    auto e = sl->Get(Slice("k"));
    ASSERT_TRUE(e && !e->tombstone);
    ASSERT_EQ(e->value, "v2");
}

TEST(SkipList, SortedOrder) {
    auto [a, sl] = MakeSL();
    for (std::string k : {"dog","ant","cat","bee"}) sl->Put(Slice(k), Slice("x"));
    std::vector<std::string> result;
    sl->Scan(Slice{}, Slice{}, [&](const Entry& e){ result.push_back(e.key); });
    auto sorted = result;
    std::sort(sorted.begin(), sorted.end());
    ASSERT_EQ(result, sorted);
}

TEST(SkipList, RangeScan) {
    auto [a, sl] = MakeSL();
    for (int i = 0; i < 10; ++i) {
        char buf[4]; snprintf(buf, sizeof(buf), "%02d", i);
        sl->Put(Slice(buf, 2), Slice("v"));
    }
    std::vector<std::string> result;
    sl->Scan(Slice("03"), Slice("07"), [&](const Entry& e){ result.push_back(e.key); });
    ASSERT_EQ(result.size(), 4u);
}

TEST(SkipList, LargeInsertion) {
    auto [a, sl] = MakeSL();
    for (int i = 0; i < 5000; ++i) {
        char buf[16]; snprintf(buf, sizeof(buf), "key%06d", i);
        sl->Put(Slice(buf), Slice("v"));
    }
    for (int i = 0; i < 5000; ++i) {
        char buf[16]; snprintf(buf, sizeof(buf), "key%06d", i);
        ASSERT_TRUE(sl->Get(Slice(buf)) != nullptr);
    }
}

TEST(SkipList, IteratorBasic) {
    auto [a, sl] = MakeSL();
    for (int i = 0; i < 5; ++i) sl->Put(Slice(std::to_string(i)), Slice("v"));
    SkipList::Iterator it(sl.get());
    it.SeekToFirst();
    int count = 0;
    while (it.Valid()) { ++count; it.Next(); }
    ASSERT_EQ(count, 5);
}

TEST(SkipList, ArenaMemoryUsage) {
    auto [a, sl] = MakeSL();
    // Head node is allocated on construction, so usage > 0 already.
    size_t before = a->MemoryUsage();
    ASSERT_TRUE(before > 0);
    sl->Put(Slice("k"), Slice("v"));
    ASSERT_TRUE(a->MemoryUsage() >= before);
}
