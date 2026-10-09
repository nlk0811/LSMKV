# LSMKV

[![Python Tests](https://img.shields.io/badge/python%20tests-161%2F161%20passing-brightgreen)](#testing)
[![C++ Tests](https://img.shields.io/badge/c%2B%2B%20tests-43%2F43%20passing-brightgreen)](#testing)
[![Crash Recovery](https://img.shields.io/badge/crash%20recovery-5%2F5%20PASS-brightgreen)](#crash-recovery)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](#license)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![C++17](https://img.shields.io/badge/c%2B%2B-17-blue)](https://en.cppreference.com/)

**LSMKV** is a crash-safe, embedded key-value storage engine built from scratch on a Log-Structured Merge Tree (LSM-Tree). It comes in two forms: a clean Python reference implementation (v1) and a performance-optimised C++ port (v2).

> If you've ever wondered how RocksDB, LevelDB, Cassandra, or DynamoDB guarantee your data survives a power cut — LSMKV is that mechanism, in ~1 200 lines of clear, documented Python.

---

## Why would you use LSMKV?

### You're learning how storage engines work
LSM-Trees are everywhere in production databases, but the crash-safety mechanism is buried in hundreds of thousands of lines of C++. LSMKV distills it to three formally-stated invariants and implements each one in a single, traceable function. Every decision has a reason; every reason is documented.

### You're building something that needs a small embedded KV store
LSMKV is self-contained (zero dependencies beyond the Python standard library). Drop `lsm_engine/` into your project, call `LSMTree('/path/to/data')`, and you have a persistent, crash-safe key-value store with range scans, prefix scans, atomic batch writes, and live metrics.

### You're teaching or studying data structures
The codebase is a live exercise in:
- **Skip Lists** — probabilistic sorted in-memory structure (memtable)
- **Bloom Filters** — space-efficient probabilistic set membership
- **K-way merge with a min-heap** — compaction of sorted SSTable files
- **Sparse indexing** — binary search to find the right 4 KB block on disk
- **Append-only logging** — crash recovery via WAL replay
- **Group commit** — batching concurrent fsyncs for throughput

### You want a reference for crash-safe design at scale
Five scaling bottlenecks have been found, diagnosed, and fixed (see [SCALING_ISSUES.md](Project/SCALING_ISSUES.md)). Each fix is traceable to a concrete failure mode and a measurable improvement.

---

## Features

- **Crash-safe** — three formally-stated invariants, verified by a SIGKILL crash harness
- **Full API** — `put`, `get`, `delete`, `scan`, `prefix_scan`, `write` (batch), `close`
- **WriteBatch** — atomic multi-key writes with one WAL fsync for the whole batch
- **Prefix scan** — `db.prefix_scan('user:')` yields all keys starting with a prefix
- **Immutable memtable** — writes rotate to a fresh memtable in < 1 µs; flush is background
- **Group-commit WAL** — up to 64 concurrent writes share one fsync
- **SSTableReader cache** — open file descriptors + Bloom filters + indexes stay in memory
- **Live metrics** — per-operation counters via `db.metrics.snapshot()`
- **WAL** — CRC-checked, replay on recovery
- **SSTable** — 4 KB data blocks, sparse index, per-file Bloom filter (1% FPR)
- **Leveled compaction** — 7-level tiered compaction, background thread, tombstone GC
- **MANIFEST** — atomic POSIX `rename()` as the compaction commit point
- **Scan fd semaphore** — concurrent scans share at most 64 open SSTable file descriptors
- **Cursor API** — stateful seek + pagination: `db.cursor().seek(key)`, context-manager, iterator protocol
- **TTL** — `put(key, value, ttl_seconds=N)`, `WriteBatch.put_ttl()`, auto-filtered in scan()
- **Block-level LRU cache** — hot 4 KB data blocks stay in memory; default 8 MB
- **Parallel compaction** — thread pool, per-level locks, non-adjacent levels run concurrently
- **Write-stall back-pressure** — slowdown at L0 ≥ 8 files; hard stop at L0 ≥ 12
- **Compaction I/O throttling** — `compaction_rate_bytes_per_sec` prevents starving writes
- **`delete_prefix(prefix)`** — atomic namespace cleanup, one WAL fsync
- **`scan_keys()`** — iterate keys only, no value fetch overhead
- **`compact_range(start, end)`** — manual synchronous range compaction
- **`estimate_key_count()`** — O(levels) key count estimate, no disk I/O
- **Write amplification metric** — `snapshot()["write_amplification"]`
- **`stats_report()`** — formatted dashboard summary string
- **Zero dependencies** — Python standard library only (v1)
- **C++ v2** — group-commit WAL, arena-backed SkipList, LRU block cache, write stall, `shared_mutex`

---

## Quick Start

### Python (v1)

**Requirements:** Python 3.9+, no external packages.

```bash
git clone https://github.com/nlk0811/LSMKV.git
cd LSMKV/Project
python demo.py
```

**Embed in your own project:**

```python
from lsm_engine import LSMTree, WriteBatch

db = LSMTree('/path/to/data')

# Basic operations
db.put('user:1001', '{"name": "Alice", "age": 30}')
db.put('user:1002', '{"name": "Bob",   "age": 25}')
print(db.get('user:1001'))   # '{"name": "Alice", "age": 30}'
print(db.get('missing'))     # None

db.delete('user:1002')
print(db.get('user:1002'))   # None  (tombstone)

# Range scan
for key, value in db.scan('user:1000', 'user:2000'):
    print(key, value)

# Prefix scan — no need to compute the end key
for key, value in db.prefix_scan('user:'):
    print(key, value)

# Atomic batch write — one WAL fsync for all operations
batch = WriteBatch()
batch.put('order:001', '{"item": "book"}')
batch.put('order:002', '{"item": "pen"}')
batch.delete('order:000')
db.write(batch)

# Live metrics
snap = db.metrics.snapshot()
print(f"writes: {snap['puts']}  reads: {snap['gets']}  "
      f"cache_hit_rate: {snap['cache_hit_rate']:.1%}")

db.close()
```

**Data survives restarts:**
```python
db = LSMTree('/path/to/data')
print(db.get('user:1001'))   # still there after restart
db.close()
```

### C++ (v2)

```bash
cd LSMKV/Project/lsmkv_v2
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

```cpp
#include "lsmkv/lsm_tree.h"
using namespace lsmkv;

std::unique_ptr<LSMTree> db;
LSMTree::Open("/path/to/data", Options{}, &db);

db->Put("key", "value");

std::string val;
db->Get("key", &val);

db->Scan("a", "z", [](const Slice& k, const Slice& v) {
    std::cout << k.ToString() << " -> " << v.ToString() << "\n";
});

db->Close();
```

---

## API Reference

### Python `LSMTree`

| Method | Signature | Description |
|---|---|---|
| Constructor | `LSMTree(directory, sync_writes=True)` | Open or create a database. `sync_writes=False` disables per-write durability (benchmarks only). |
| `put` | `put(key, value)` | Insert or overwrite a key. Accepts `str` or `bytes`. |
| `get` | `get(key) -> str \| None` | Return value or `None` if missing or deleted. |
| `delete` | `delete(key)` | Write a tombstone. Removed during compaction. |
| `write` | `write(batch: WriteBatch)` | Apply all batch operations atomically with one WAL fsync. |
| `scan` | `scan(start=None, end=None) -> Iterator[(str, str)]` | Range scan, inclusive start / exclusive end. Tombstones excluded. |
| `prefix_scan` | `prefix_scan(prefix) -> Iterator[(str, str)]` | Yield all keys that start with `prefix`, sorted. |
| `scan_keys` | `scan_keys(start=None, end=None) -> Iterator[str]` | Keys only, no value fetch overhead. |
| `cursor` | `cursor() -> Cursor` | Stateful Cursor for seek + pagination. Use as context manager. |
| `delete_prefix` | `delete_prefix(prefix) -> int` | Atomic delete of all keys with prefix. Returns count. |
| `compact_range` | `compact_range(start=None, end=None)` | Synchronous range compaction across all levels. |
| `estimate_key_count` | `estimate_key_count() -> int` | Fast key count estimate using Bloom filter capacities. |
| `stats_report` | `stats_report() -> str` | Formatted multi-line engine summary for dashboards. |
| `stats` | `stats() -> dict` | Engine stats + full metrics snapshot merged into one dict. |
| `close` | `close()` | Flush memtable, stop background thread, close WAL. |

### `WriteBatch`

```python
from lsm_engine import WriteBatch

batch = WriteBatch()
batch.put('k1', 'v1').put('k2', 'v2').delete('k0')  # chainable
print(len(batch))       # 3
print(batch.byte_size)  # total key+value bytes

db.write(batch)
batch.clear()   # reuse the batch object
```

### `EngineMetrics`

```python
snap = db.metrics.snapshot()
# Keys: puts, deletes, gets, get_hits, get_misses, get_hit_rate,
#       cache_hits, cache_misses, cache_hit_rate, bloom_skips,
#       flushes, bytes_flushed, compactions, bytes_compacted,
#       scans, scan_entries, wal_records, wal_syncs,
#       write_ops_per_sec, read_ops_per_sec, uptime_seconds
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│               LSMTree (API)                                     │
│  put / get / delete / write(batch) / scan / prefix_scan         │
└───────────┬─────────────────────────────────┬───────────────────┘
            │ write path                       │ read path
            ▼                                  ▼
┌───────────────────┐              ┌─────────────────────────┐
│  Group-commit WAL │              │  Active SkipList         │
│  CRC-checked      │              │  memtable (≤ 4 MB)       │
│  64 writes/fsync  │              └──────────┬──────────────┘
└───────────────────┘                         │ rotate (< 1 µs)
                                              ▼
                                ┌─────────────────────────┐
                                │  Immutable SkipList      │
                                │  (being flushed in bg)   │
                                └──────────┬──────────────┘
                                           │ background flush
                                           ▼
                                ┌─────────────────────────┐
                                │   SSTable (.sst)         │
                                │   4 KB data blocks       │
                                │   Sparse block index     │
                                │   Bloom filter (1% FPR)  │
                                │   [cached in memory]     │
                                └──────────┬──────────────┘
                                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                 7-Level Tiered Disk Layout                       │
│   L0 (newest) ── L1 ── L2 ── L3 ── L4 ── L5 ── L6 (oldest)   │
│   Each level is 10× larger than the previous.                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │ background compaction (k-way merge)
                               ▼
                ┌──────────────────────────┐
                │   MANIFEST               │
                │   atomic rename() commit │
                └──────────────────────────┘
```

### Read path
1. Active memtable — O(log n), in memory
2. Immutable memtable (if a background flush is in progress) — O(log n)
3. For each level L0 → L6, for each SSTable:
   - Bloom filter (cached in memory) — skip if definite miss
   - Sparse index (cached in memory) — binary search to find the right block
   - Disk read — scan the 4 KB block

### Write path
1. WAL: submit record to group-commit thread, block until fsynced (O(1) amortised)
2. SkipList insert — O(log n)
3. When memtable hits 4 MB: atomic swap to immutable, fresh memtable in < 1 µs
4. Background thread: flush immutable → L0 SSTable, update MANIFEST

---

## The Three Crash-Safety Invariants

### Invariant 1 — WAL Completeness
> For every write that returns to the caller, a durable, CRC-verified WAL record exists before the memtable is updated.

### Invariant 2 — Compaction Atomicity
> The MANIFEST update is the compaction commit point. The database is always in either the pre-compaction or post-compaction state — never partial.

Implemented via `os.rename()`, which is atomic on POSIX. Orphaned SSTable files are cleaned on startup by `_cleanup_orphans()`.

### Invariant 3 — WAL Truncation Safety
> The WAL may only be truncated after all in-memory data (both active and immutable memtable) has been durably written **and** recorded in the MANIFEST.

With the immutable memtable, background flushes do **not** truncate the WAL (the active memtable still has unrecorded records). Only the full flush (`_flush_memtable`) truncates, after flushing both immutable and active memtable.

---

## Testing

### Python test suite (161 tests)

```bash
cd Project
pip install pytest
pytest tests/ -v
```

| Test file | What it covers |
|---|---|
| `test_skip_list.py` | Insert, lookup, delete, scan, tombstones, sorted order |
| `test_bloom_filter.py` | FPR at multiple capacities, serialisation round-trip |
| `test_wal.py` | Put/delete replay, CRC mismatch stops replay, truncation |
| `test_sstable.py` | Bloom filter, sparse index, multi-block round-trip, bad magic |
| `test_compaction.py` | K-way merge, tombstone propagation, deepest-level GC |
| `test_lsm_tree.py` | Full engine: overwrite, delete, scan, persistence, concurrent |
| `test_crash.py` | WAL replay, orphan cleanup, MANIFEST invariants |
| `test_write_batch.py` | Batch put/delete, chaining, persistence, large batch |
| `test_immutable_memtable.py` | Rotation under load, reads during flush, multi-flush |
| `test_concurrent.py` | Concurrent readers, writer+reader, concurrent scans |
| `test_metrics.py` | Metrics wiring, hit rates, stats integration |

### C++ test suite (43 tests)

```bash
cd Project/lsmkv_v2
cmake -S . -B build && cmake --build build -j$(nproc)
./build/lsmkv_tests
```

---

## Crash Recovery

```bash
cd Project
python crash_harness.py --runs 5 --keys 20000
```

Spawns a writer subprocess, sends SIGKILL at a random point mid-write, reopens the database, verifies every acknowledged write is present and uncorrupted.

```
Result: 5/5 runs passed  (missing=0  corrupt=0)
```

---

## Benchmarks

```bash
cd Project
python benchmarks.py
```

On Apple M-series:

| Benchmark | Result |
|---|---|
| Write throughput (async) | ~191 000 ops/sec |
| Write throughput (sync, group-commit) | ~262 ops/sec |
| fsync overhead | **910× slowdown** — the durability/throughput trade-off |
| Read p50 | 0.25 ms |
| Read p99 | 0.63 ms |
| Bloom filter FPR @ 1% target | 1.06% actual |

---

## Scaling Fixes Applied

Five production-grade scaling improvements have been applied to the Python v1 engine. See [SCALING_ISSUES.md](Project/SCALING_ISSUES.md) for full details.

| ID | Fix | Impact |
|---|---|---|
| S-01 | **SSTableReader cache** | Eliminates 3 OS reads per `get()` for immutable metadata |
| S-02 | **Immutable memtable** | Write rotation takes < 1 µs; flush is background — no stalls |
| S-03 | **Manifest file-size cache** | Zero stat() calls in the 2-second compaction heartbeat |
| S-04 | **Scan fd semaphore** | Concurrent scans share ≤ 64 file descriptors; no EMFILE |
| S-05 | **Group-commit WAL** | Up to 64 concurrent writes share one fsync |

---

## v1 → v2 Improvements

| Feature | Python v1 | C++ v2 |
|---|---|---|
| Write throughput | Group-commit WAL | Group-commit WAL (128 writes/fsync) |
| SkipList | Heap-allocated nodes | Arena-allocated (cache-local) |
| Block cache | SSTableReader cache (fd + index + Bloom) | LRU block cache (full data blocks) |
| Concurrent reads | threading.Lock (exclusive) | `shared_mutex` (parallel reads) |
| Write stall | None | Slow/stop writers when L0 fills |
| Error handling | Exceptions | `Status` return type throughout |

---

## Novel Finding — Bloom Filter Serialisation Defect

During development, a defect was found: after restarting the engine, the deserialized Bloom filter produced **false negatives** — reporting that a key *definitely does not exist* when it did. Root cause: the bit array was serialised as a Python `list` of integers; on deserialisation the reconstructed filter had a different internal layout, causing `may_contain()` to miss genuine members. Fixed by serialising as a packed byte string with a CRC32 checksum.

See `lsm_engine/bloom_filter.py` and `tests/test_bloom_filter.py::test_serialise_round_trip`.

---

## Project Structure

```
Project/
├── lsm_engine/              # Python v1 — the reference implementation
│   ├── lsm_tree.py          #   Engine entry point, public API
│   ├── skip_list.py         #   SkipList memtable
│   ├── wal.py               #   WAL — CRC-checked, group-commit fsync
│   ├── sstable.py           #   SSTable writer + reader
│   ├── bloom_filter.py      #   Bloom filter (configurable FPR)
│   ├── manifest.py          #   MANIFEST — atomic level metadata, file-size cache
│   ├── compaction.py        #   K-way merge compaction
│   ├── write_batch.py       #   WriteBatch — atomic multi-key writes
│   ├── metrics.py           #   EngineMetrics — thread-safe live counters
│   └── _utils.py            #   fsync helpers
│
├── lsmkv_v2/                # C++ v2 — performance-optimised port
│   ├── include/lsmkv/       #   Public headers
│   ├── src/                 #   Implementations
│   ├── tests/               #   C++ test suite (43 tests)
│   ├── benchmarks/          #   C++ benchmark binary
│   └── CMakeLists.txt
│
├── tests/                   # Python pytest suite (85 tests)
├── docs/                    # Architecture, invariants, literature survey
├── SCALING_ISSUES.md        # Bottleneck registry — found, diagnosed, fixed
├── demo.py                  # Walkthrough of every API call
├── benchmarks.py            # Write/read/scan/Bloom benchmarks
└── crash_harness.py         # SIGKILL crash recovery integration test
```

---

## Contributing

Some areas where contributions are welcome:

- **Range-partitioned compaction** (S-06) — bound memory for very large deployments
- **Compaction I/O throttling** (S-07) — prevent compaction from starving writes
- **Thread-pool compaction** (S-08) — parallel compaction on multi-core machines
- **Python bindings** for C++ v2 (pybind11)
- **Prefix compression** in SSTable data blocks
- **TTL support** — keys expire after N seconds, dropped during compaction
- **Benchmarking on Linux/NVMe** — current numbers are on macOS SSD

```bash
git clone https://github.com/nlk0811/LSMKV.git
cd LSMKV/Project
pip install pytest
pytest tests/ -v    # all 85 tests must pass before and after your change
```

---

## Documentation

| File | Contents |
|---|---|
| [`docs/architecture.md`](Project/docs/architecture.md) | Component diagram, write/read path, on-disk formats, complexity table |
| [`docs/invariants.md`](Project/docs/invariants.md) | Formal specification of the three crash-safety invariants |
| [`SCALING_ISSUES.md`](Project/SCALING_ISSUES.md) | Bottleneck registry with root causes, fixes, and planned work |
| [`LSMKV_Research_Summary.md`](Project/LSMKV_Research_Summary.md) | Full research report with hypotheses, results, and conclusions |

---

## License

MIT License — see [`LICENSE`](LICENSE).
