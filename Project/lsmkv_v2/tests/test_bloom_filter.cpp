#include "test_framework.h"
#include "lsmkv/bloom_filter.h"

using namespace lsmkv;

TEST(BloomFilter, BasicMembership) {
    BloomFilter bf(1000);
    for (int i = 0; i < 500; ++i) {
        std::string k = "key" + std::to_string(i);
        bf.Add(Slice(k));
    }
    for (int i = 0; i < 500; ++i) {
        std::string k = "key" + std::to_string(i);
        ASSERT_TRUE(bf.MayContain(Slice(k)));
    }
}

TEST(BloomFilter, NoFalseNegatives) {
    BloomFilter bf(200);
    for (int i = 0; i < 200; ++i) bf.Add(Slice("x" + std::to_string(i)));
    for (int i = 0; i < 200; ++i) ASSERT_TRUE(bf.MayContain(Slice("x" + std::to_string(i))));
}

TEST(BloomFilter, FPRWithinSpec) {
    int n = 10000;
    BloomFilter bf(n, 0.01);
    for (int i = 0; i < n; ++i) bf.Add(Slice("present" + std::to_string(i)));
    int fp = 0;
    for (int i = 0; i < 5000; ++i) {
        if (bf.MayContain(Slice("absent" + std::to_string(i)))) ++fp;
    }
    double actual = double(fp) / 5000;
    ASSERT_TRUE(actual < 0.05);  // 5× headroom over 1% target
}

// THE KEY v1 FIX TEST — this would FAIL on v1 code
TEST(BloomFilter, SerializeDeserializeRoundTrip) {
    BloomFilter bf(1000, 0.01);  // bit_count likely not multiple of 8
    std::vector<std::string> keys;
    for (int i = 0; i < 300; ++i) keys.push_back("word" + std::to_string(i));
    for (auto& k : keys) bf.Add(Slice(k));

    std::string data = bf.Serialize();
    Status s;
    BloomFilter bf2 = BloomFilter::Deserialize(Slice(data), &s);
    ASSERT_OK(s);

    // ALL keys must still be found — this was broken in v1
    for (auto& k : keys) ASSERT_TRUE(bf2.MayContain(Slice(k)));
}

TEST(BloomFilter, BitCountPreservedAfterDeserialize) {
    BloomFilter bf(997, 0.01);  // prime capacity → bit_count definitely not mult of 8
    std::string data = bf.Serialize();
    Status s;
    BloomFilter bf2 = BloomFilter::Deserialize(Slice(data), &s);
    ASSERT_OK(s);
    ASSERT_EQ(bf.BitCount(), bf2.BitCount());
    ASSERT_EQ(bf.HashCount(), bf2.HashCount());
}

TEST(BloomFilter, BadCRCDetected) {
    BloomFilter bf(100);
    bf.Add(Slice("test"));
    std::string data = bf.Serialize();
    data.back() ^= 0xFF;  // corrupt CRC
    Status s;
    BloomFilter::Deserialize(Slice(data), &s);
    ASSERT_TRUE(s.IsCorruption());
}
