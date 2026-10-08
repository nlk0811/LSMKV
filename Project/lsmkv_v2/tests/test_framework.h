#pragma once
#include <cstdio>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

struct TestCase { std::string name; std::function<void()> fn; };
inline std::vector<TestCase>& GetTests() {
    static std::vector<TestCase> v; return v;
}

#define TEST(suite, name) \
    static void suite##_##name(); \
    static struct _Reg_##suite##_##name { \
        _Reg_##suite##_##name() { \
            GetTests().push_back({#suite "/" #name, suite##_##name}); \
        } \
    } _reg_##suite##_##name; \
    static void suite##_##name()

#define ASSERT_TRUE(x) do { if (!(x)) { \
    fprintf(stderr, "    FAIL %s:%d  (" #x ")\n", __FILE__, __LINE__); \
    throw std::runtime_error("assertion"); } } while(0)

#define ASSERT_FALSE(x)   ASSERT_TRUE(!(x))
#define ASSERT_EQ(a,b)    ASSERT_TRUE((a)==(b))
#define ASSERT_NE(a,b)    ASSERT_TRUE((a)!=(b))
#define ASSERT_OK(s)      ASSERT_TRUE((s).ok())
#define ASSERT_NOT_FOUND(s) ASSERT_TRUE((s).IsNotFound())
