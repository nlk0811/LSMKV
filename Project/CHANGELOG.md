# CHANGELOG
## LSMKV — Engineering Progress Log

Each entry is a discrete improvement: a bottleneck found, diagnosed, fixed, and verified.

---

## Iteration 18 — Concurrent Write Throughput: 261 → 1,040 ops/sec (4×)
**Problem:** `_write_lock` was held during `done.wait()` — the entire WAL fsync latency
(~4 ms). Concurrent writers serialised; group-commit never batched more than 1 record.  
**Fix:** Release `_write_lock` before `done.wait()`. Submit WAL record inside lock
(non-blocking), update memtable inside lock, release lock, then wait. Multiple writers
now queue records concurrently; gc-thread batches them in one fsync.  
**Impact:** 8 threads: 261 → 1,040 ops/sec (4× improvement).

## Iteration 17 — Sorted Levels Persist Across Restarts
**Problem:** `_sorted_levels` was session-local — lost on every `close()`. Binary search
only activated after the first compaction in each new session.  
**Fix:** `Manifest` now persists `sorted_levels` list in `MANIFEST.json`. `LSMTree.__init__`
reads it and populates `_sorted_levels` immediately.  
Also added: `memtable_size_bytes` tuning knob (default 4 MB) and `db.info()` for
live configuration inspection.

## Iteration 16 — Scan Metadata Borrowing: 3 Disk Seeks Eliminated per SSTable
**Problem:** Every `scan()` call opened N fresh `SSTableReader` instances, each loading
footer + index + Bloom filter from disk (3 seeks × N files = up to 250 ms at 100 files).  
**Fix:** `SSTableReader(_template=cached_reader)` borrows `_index` and `_bloom` from the
already-cached reader without disk I/O. A fresh fd is still opened for the data reads.

## Iteration 15 — Block-level zlib Compression (68% Storage Reduction)
**Problem:** SSTable data blocks stored verbatim; for structured data, 3–10× redundancy.  
**Fix:** `LSMTree(compression='zlib')` compresses each 4 KB block with zlib before writing.
Backward-compatible: old SSTables always readable. Block cache stores decompressed bytes
so hot reads pay zero decompression cost after first access.  
**Impact:** 68% disk reduction for JSON user records; 95% for highly repetitive data.

## Iteration 14 — compact_all(), wait_for_compaction(), iter_batches(), latency in stats
**Added:**
- `compact_all()` — flush + fully compact all levels; blocks until done.
- `wait_for_compaction(timeout)` — synchronization primitive for tests and benchmarks.
- `iter_batches(batch_size)` — streaming bulk export in fixed-size pages.
- `stats_report()` now shows read/write p50/p99 latency.

## Iteration 13 — Latency Histogram + Binary Search for Sorted L1+ Levels
**Problems:** No latency observability; L1+ file lookup O(n) even with range filter.  
**Fixes:**
- `LatencyTracker` — 15-bucket histogram; `get_p50_us`/`get_p99_us`/`put_p50_us` in
  `stats()`.
- After each compaction, dst_level files sorted by `first_key`; binary search reduces
  per-get comparisons from O(n files) to O(log n files) at L1+.

## Iteration 12 — compaction_filter, db.backup(), db.get_many()
**Added:**
- `compaction_filter=fn(key, value, level)` — drop or transform entries during compaction.
- `db.backup(dir)` — consistent hot backup using snapshot; opens as `LSMTree(dir)`.
- `db.get_many(keys)` — multi-key lookup, returns `{key: value_or_None}`.

## Iteration 11 — Atomic Read-Modify-Write: update(), CAS, increment()
**Problem:** No atomic multi-step writes; race conditions on counters and lock patterns.  
**Fix:** Three new methods, all serialised under `_write_lock`:
- `update(key, fn)` — apply fn(current) atomically.
- `compare_and_swap(key, expected, new)` — exact-match swap; 20-thread test: exactly 1 winner.
- `increment(key, N=1)` — atomic integer increment. 400 concurrent increments = 400 exactly.

## Iteration 10 — Snapshot Reads + os.pread + db.flush()
**Added:**
- `snapshot()` — point-in-time read-only view; concurrent writes invisible.
- `os.pread()` in `SSTableReader._read_block()` — atomic positional read; eliminates
  seek+read race on shared fds, reduces syscall count from 2 to 1.
- `flush()` — force memtable to disk without closing the engine.

## Iteration 9 — Key-Range Pre-filter (83× Faster Miss-Reads)
**Problem:** Every `get()` called the Bloom filter for every SSTable, even those whose
entire key range was outside the target key. 7 SHA-256 operations per useless Bloom probe.  
**Fix:** Check `first_key ≤ kb ≤ last_key` (2 byte comparisons) before the Bloom probe.
Files outside range are skipped entirely.  
**Impact:** Miss-read latency: 0.250 ms → 0.003 ms (83×). New metrics: `range_skips`,
`sstable_reads`.

## Iteration 8 — Cursor API, scan_keys(), delete_prefix(), stats_report()
**Added:**
- `Cursor` — stateful seek + pagination with context-manager support and iterator protocol.
- `scan_keys()` — yield keys only, no value fetch overhead.
- `delete_prefix(prefix)` — atomic bulk delete of a key namespace.
- `stats_report()` — formatted one-screen engine dashboard.

## Iteration 7 — TTL (Time-To-Live) Key Expiry
**Added:** `put(key, value, ttl_seconds=N)` and `WriteBatch.put_ttl()`. Expired keys are
invisible to `get()` and `scan()`. Physically removed during deep compaction (same
code path as tombstones). Zero wire-format changes — TTL encoded as a 12-byte value prefix.

## Iteration 6 — Block-Level LRU Cache + estimate_key_count()
**Problem:** Actual 4 KB data blocks re-read from disk on every get() cache miss.  
**Fix:** `BlockCache` (LRU, OrderedDict, thread-safe) shared across all SSTableReaders.
Stores decompressed block bytes keyed by `(sst_path, block_offset)`.  
**Impact:** Hot read p50: 0.250 ms → 0.008 ms (31×).  
Added: `estimate_key_count()` — O(levels) estimate using Bloom filter capacities.

## Iterations 1–5 — The Eight Scaling Bottlenecks (S-01 through S-08)

| ID | Bottleneck | Fix | Key Metric |
|---|---|---|---|
| S-01 | 3 OS reads per `get()` for metadata | SSTableReader cache | Eliminates per-call file open |
| S-02 | Writes stall 50–200 ms during flush | Immutable memtable | Write rotation < 1 µs |
| S-03 | `stat()` per file every 2 s | Manifest size cache | Zero stat() calls |
| S-04 | `scan()` exhausts fd limit | Fd semaphore | Bounded to 64 open fds |
| S-05 | 1 fsync per write | Group-commit WAL | 64 writes share 1 fsync |
| S-06 | Full level loaded per compaction | Range-aware file picking | O(1) src + overlapping dst |
| S-07 | Compaction uses 100% disk I/O | Rate limiter | Reserve bandwidth for writes |
| S-08 | Single compaction thread | Thread pool + per-level locks | Parallel non-adjacent levels |

Also shipped in iterations 1–5: WriteBatch, prefix_scan, EngineMetrics, write-stall
back-pressure (L0 slowdown/stop), compaction I/O throttling.

---

## Performance Summary (before → after)

| Metric | Original | Final | Change |
|---|---|---|---|
| Read p50 | 0.250 ms | **0.008 ms** | **31×** |
| Miss-read p50 | 0.250 ms | **0.003 ms** | **83×** |
| Concurrent sync writes (8 threads) | 261 ops/sec | **1,040 ops/sec** | **4×** |
| Write stall during flush | 50–200 ms | **< 1 µs** | eliminated |
| Storage (structured JSON) | 1.0× | **0.32×** | **68% smaller** |
| Tests | 62 | **277** | +347% |
| API methods | 5 | **25+** | +400% |
