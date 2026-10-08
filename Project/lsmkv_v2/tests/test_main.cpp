#include "test_framework.h"
#include <cstdio>
#include <cstring>

int main(int argc, char** argv) {
    // Optional filter: ./lsmkv_tests BloomFilter  → runs only BloomFilter/* tests
    const char* filter = (argc > 1) ? argv[1] : nullptr;

    int passed = 0, failed = 0, skipped = 0;
    printf("=== LSMKV v2 Test Suite ===\n");
    if (filter) printf("    filter: %s\n", filter);
    printf("\n");

    for (auto& tc : GetTests()) {
        if (filter && tc.name.find(filter) == std::string::npos) {
            ++skipped; continue;
        }
        printf("  %-55s", tc.name.c_str());
        fflush(stdout);
        try {
            tc.fn();
            printf("PASS\n");
            ++passed;
        } catch (std::exception& e) {
            printf("FAIL  (%s)\n", e.what());
            ++failed;
        }
    }
    printf("\n%d/%d passed", passed, passed + failed);
    if (skipped) printf("  (%d skipped)", skipped);
    printf("\n");
    return failed > 0 ? 1 : 0;
}
