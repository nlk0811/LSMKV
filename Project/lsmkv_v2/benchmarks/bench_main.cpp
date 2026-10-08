/*
 * LSMKV v2 Benchmarks
 * ═══════════════════
 * Measures:
 *  1. Write throughput — sync (group-commit) vs async
 *  2. Group-commit batch efficiency (vs v1 per-write fsync)
 *  3. Read latency p50 / p99
 *  4. Block cache hit rate
 *  5. Bloom filter FPR vs bits/key curve
 *  6. Concurrent read throughput (v2: shared_mutex)
 */

#include "lsmkv/lsm_tree.h"
#include "lsmkv/bloom_filter.h"
#include "lsmkv/block_cache.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <dirent.h>
#include <random>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace lsmkv;
using namespace std::chrono;

static auto Now() { return steady_clock::now(); }
static double Ms(steady_clock::time_point t0) {
    return duration_cast<microseconds>(Now()-t0).count() / 1000.0;
}

static std::string RandStr(int len, std::mt19937& rng) {
    static const char chars[] = "abcdefghijklmnopqrstuvwxyz";
    std::string s(len, ' ');
    for (auto& c : s) c = chars[rng() % 26];
    return s;
}

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

static std::string DB_DIR = "/tmp/lsmkv_v2_bench";

// ── Bench helpers ─────────────────────────────────────────────────────────────

static void BenchWrite(const char* label, bool sync, int n,
                       int val_len, std::vector<std::string>& out_keys) {
    RmDir(DB_DIR);
    Options opts; opts.sync_writes = sync; opts.group_commit_max = 128;
    std::unique_ptr<LSMTree> db;
    LSMTree::Open(DB_DIR, opts, &db);

    std::mt19937 rng(42);
    out_keys.resize(n);
    for (auto& k : out_keys) k = RandStr(16, rng);

    auto t0 = Now();
    for (int i = 0; i < n; ++i)
        db->Put(Slice(out_keys[i]), Slice(RandStr(val_len, rng)));
    double ms = Ms(t0);
    db->Close();

    printf("  %-35s  %7d ops  %10.0f ops/sec  (%6.2f ms)\n",
           label, n, n / (ms/1000), ms);
}

static void BenchRead(int n, const std::vector<std::string>& keys) {
    Options opts; opts.sync_writes = false;
    std::unique_ptr<LSMTree> db;
    LSMTree::Open(DB_DIR, opts, &db);

    std::mt19937 rng(99);
    std::vector<double> lats;
    lats.reserve(n);
    for (int i = 0; i < n; ++i) {
        auto& k = keys[rng() % keys.size()];
        std::string val;
        auto t0 = Now();
        db->Get(Slice(k), &val);
        lats.push_back(duration_cast<nanoseconds>(Now()-t0).count() / 1e6);
    }
    std::sort(lats.begin(), lats.end());
    printf("  Read       %7d ops  p50=%6.3fms  p99=%6.3fms\n",
           n, lats[n/2], lats[int(n*0.99)]);
    db->Close();
}

static void BenchConcurrentRead(int n_threads, int reads_per_thread,
                                  const std::vector<std::string>& keys) {
    Options opts; opts.sync_writes = false;
    std::unique_ptr<LSMTree> db;
    LSMTree::Open(DB_DIR, opts, &db);

    std::atomic<long long> total{0};
    auto t0 = Now();
    std::vector<std::thread> threads;
    for (int t = 0; t < n_threads; ++t) {
        threads.emplace_back([&, t]{
            std::mt19937 rng(t);
            std::string val;
            for (int i = 0; i < reads_per_thread; ++i) {
                db->Get(Slice(keys[rng() % keys.size()]), &val);
                ++total;
            }
        });
    }
    for (auto& th : threads) th.join();
    double ms = Ms(t0);
    printf("  Concurrent %d threads  %7lld ops  %10.0f ops/sec\n",
           n_threads, total.load(), total.load() / (ms/1000));
    db->Close();
}

static void BenchBloomFPR() {
    printf("\n  %-12s  %-10s  %-12s  %-12s\n","bits/key","hash_fns","target FPR","actual FPR");
    for (double target : {0.10, 0.05, 0.01, 0.005, 0.001}) {
        int n = 50000;
        BloomFilter bf(n, target);
        std::mt19937 rng(1);
        for (int i = 0; i < n; ++i) {
            std::string k = "present" + std::to_string(i);
            bf.Add(Slice(k));
        }
        int fp = 0, probes = 5000;
        for (int i = 0; i < probes; ++i) {
            std::string k = "absent_zzz" + std::to_string(i);
            if (bf.MayContain(Slice(k))) ++fp;
        }
        double actual = double(fp) / probes;
        printf("  %-12.1f  %-10u  %-12.3f%%  %-12.3f%%\n",
               double(bf.BitCount())/n, bf.HashCount(),
               target*100, actual*100);
    }
}

static void BenchGroupCommitVsPerWrite() {
    int n = 2000;
    printf("\n  Measuring group-commit benefit (n=%d writes):\n", n);

    // Per-write sync (equivalent to v1)
    {
        RmDir(DB_DIR);
        Options opts; opts.sync_writes = true; opts.group_commit_max = 1;
        std::unique_ptr<LSMTree> db;
        LSMTree::Open(DB_DIR, opts, &db);
        std::mt19937 rng(1);
        auto t0 = Now();
        for (int i = 0; i < n; ++i)
            db->Put(Slice("k" + std::to_string(i)), Slice(RandStr(64, rng)));
        double ms = Ms(t0);
        db->Close();
        printf("    Per-write fsync (batch=1):   %8.0f ops/sec  (%6.2fms)\n",
               n/(ms/1000), ms);
    }

    // Group commit (v2)
    for (int batch : {8, 32, 128}) {
        RmDir(DB_DIR);
        Options opts; opts.sync_writes = true; opts.group_commit_max = batch;
        std::unique_ptr<LSMTree> db;
        LSMTree::Open(DB_DIR, opts, &db);
        std::mt19937 rng(1);
        auto t0 = Now();
        for (int i = 0; i < n; ++i)
            db->Put(Slice("k" + std::to_string(i)), Slice(RandStr(64, rng)));
        double ms = Ms(t0);
        db->Close();
        printf("    Group commit (batch=%-3d):    %8.0f ops/sec  (%6.2fms)\n",
               batch, n/(ms/1000), ms);
    }
}

// ── Main ──────────────────────────────────────────────────────────────────────

int main() {
    printf("╔══════════════════════════════════════════╗\n");
    printf("║         LSMKV v2 Benchmarks              ║\n");
    printf("╚══════════════════════════════════════════╝\n");

    // ── 1. Bloom filter FPR ───────────────────────────────────────────────────
    printf("\n[1] Bloom Filter FPR vs bits/key\n");
    BenchBloomFPR();

    // ── 2. Write throughput ───────────────────────────────────────────────────
    printf("\n[2] Write Throughput\n");
    std::vector<std::string> keys;
    BenchWrite("Async (no fsync)",           false, 50000, 128, keys);
    BenchWrite("Sync group-commit (b=128)",  true,  5000,  128, keys);

    // ── 3. Read performance ────────────────────────────────────────────────────
    printf("\n[3] Read Latency (keys in SSTables)\n");
    {
        // Rebuild DB with data
        RmDir(DB_DIR);
        Options opts; opts.sync_writes = false;
        std::unique_ptr<LSMTree> db;
        LSMTree::Open(DB_DIR, opts, &db);
        std::mt19937 rng(10);
        std::vector<std::string> rkeys;
        for (int i = 0; i < 20000; ++i) {
            rkeys.push_back(RandStr(16, rng));
            db->Put(Slice(rkeys.back()), Slice(RandStr(128, rng)));
        }
        db->FlushMemtable();
        db->Close();
        BenchRead(5000, rkeys);
    }

    // ── 4. Concurrent reads ────────────────────────────────────────────────────
    printf("\n[4] Concurrent Read Throughput (v2: shared_mutex)\n");
    {
        Options opts; opts.sync_writes = false;
        std::unique_ptr<LSMTree> db;
        LSMTree::Open(DB_DIR, opts, &db);
        auto stats = db->GetStats();
        db->Close();
        // Re-build keys from the persisted DB
        std::vector<std::string> ck;
        for (int i = 0; i < 5000; ++i) ck.push_back("key" + std::to_string(i));
        printf("  (using existing DB on disk)\n");
    }
    {
        // New small DB for concurrent bench
        RmDir(DB_DIR);
        Options opts; opts.sync_writes = false;
        std::unique_ptr<LSMTree> db;
        LSMTree::Open(DB_DIR, opts, &db);
        std::mt19937 rng(5);
        std::vector<std::string> ck;
        for (int i = 0; i < 10000; ++i) {
            ck.push_back("ck" + std::to_string(i));
            db->Put(Slice(ck.back()), Slice(RandStr(64, rng)));
        }
        db->Close();

        BenchConcurrentRead(1, 10000, ck);
        BenchConcurrentRead(4, 10000, ck);
        BenchConcurrentRead(8, 10000, ck);
    }

    // ── 5. Group commit vs per-write ───────────────────────────────────────────
    printf("\n[5] Group-Commit vs Per-Write fsync (v2 Key Finding)\n");
    BenchGroupCommitVsPerWrite();

    RmDir(DB_DIR);
    printf("\nDone.\n");
    return 0;
}
