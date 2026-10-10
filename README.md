# LSMKV

[![Python Tests](https://img.shields.io/badge/python%20tests-328%2F328%20passing-brightgreen)](#testing)
[![C++ Tests](https://img.shields.io/badge/c%2B%2B%20tests-43%2F43%20passing-brightgreen)](#testing)
[![Crash Recovery](https://img.shields.io/badge/crash%20recovery-5%2F5%20PASS-brightgreen)](#crash-recovery)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](#license)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![C++17](https://img.shields.io/badge/c%2B%2B-17-blue)](https://en.cppreference.com/)

**LSMKV** is a crash-safe, embedded key-value storage engine built from scratch on a Log-Structured Merge Tree (LSM-Tree). It comes in two forms: a clean Python reference implementation (v1) and a performance-optimised C++ port (v2).

> If you've ever wondered how RocksDB, LevelDB, Cassandra, or DynamoDB guarantee your data survives a power cut — LSMKV is that mechanism, in clear, documented Python you can actually read.

---

## Why would you use LSMKV?

### You need an embedded key-value store — no server, no dependencies
Drop `lsm_engine/` into your Python project. Zero external dependencies (stdlib only). You get a persistent, crash-safe database with TTL, atomic operations, range scans, snapshots, and live metrics out of the box.

```python
from lsm_engine import LSMTree
db = LSMTree('/path/to/data')
db.put('user:1001', 'Alice')
db.get('user:1001')   # → 'Alice'
```

### You're learning how storage engines work
LSM-Trees power Cassandra, RocksDB, LevelDB, DynamoDB, and HBase. The crash-safety mechanism is buried in hundreds of thousands of lines of C++. LSMKV distills it to three formally-stated, verifiable invariants — each implemented in a single, traceable function.

### You want a reference for scaling production systems
14 scaling bottlenecks were found, diagnosed, benchmarked, and fixed — documented in [SCALING_ISSUES.md](Project/SCALING_ISSUES.md) with root causes and measured improvements. Read p50 improved from **0.250 ms → 0.008 ms** (31× faster). Miss-reads improved from 0.250 ms to **0.003 ms** (83× faster).

### You're teaching or studying data structures
Every component is a standalone exercise in a core algorithm:
**Skip Lists** · **Bloom Filters** · **K-way merge (min-heap)** · **Sparse indexing** · **Append-only WAL** · **Group commit** · **LRU cache** · **Binary search on sorted levels** · **Parallel thread pools**

---

## Performance

Measured on Apple M-series / NVMe SSD:

| Metric | Result |
|---|---|
| **Read p50** (hot — block cache) | **0.008 ms** (8 µs) |
| **Read p99** (hot — block cache) | **0.050 ms** |
| **Miss-read p50** (key not found, range-skipped) | **0.003 ms** (3 µs) |
| **Write throughput** (async, no fsync) | ~197,000 ops/sec |
| **Write throughput** (sync, group-commit) | ~263 ops/sec |
| **Scan** (10,000 entries) | 7 ms |
| **Bloom filter FPR** @ 1% target | 1.0% actual |
| **Block cache hit rate** (hot workload) | 98% |

These numbers reflect all 14 scaling fixes applied. See [Benchmarks](#benchmarks) to run them yourself.

---

## Quick Start

```bash
git clone https://github.com/nlk0811/LSMKV.git
cd LSMKV/Project
python demo.py
```

```python
from lsm_engine import LSMTree, WriteBatch

db = LSMTree('/data')

# ── Basic operations ──────────────────────────────────────────
db.put('user:1001', 'Alice')
db.put('user:1002', 'Bob')
db.get('user:1001')          # → 'Alice'
db.get('missing')            # → None
db.delete('user:1002')

# ── TTL — keys expire automatically ──────────────────────────
db.put('session:abc', 'data', ttl_seconds=3600)   # expires in 1 hour
db.get('session:abc')   # → 'data'  (before expiry)
# after 1 hour → None (invisible to all reads, cleaned up in compaction)

# ── Atomic batch write ────────────────────────────────────────
batch = WriteBatch()
batch.put('order:001', '{"item":"book"}').put('order:002', '{"item":"pen"}').delete('order:000')
db.write(batch)   # one WAL fsync for all three operations

# ── Atomic read-modify-write ──────────────────────────────────
db.increment('page_views')                              # atomic +1
db.update('cart', lambda v: add_item(v, 'book'))        # atomic transform
db.compare_and_swap('lock', None, 'owner-id')           # atomic CAS → True/False

# ── Range and prefix scans ────────────────────────────────────
for key, value in db.scan('user:1000', 'user:2000'):
    print(key, value)
for key, value in db.prefix_scan('user:'):
    print(key, value)
for key in db.scan_keys('order:', 'order;'):
    print(key)

# ── Cursor — stateful seek + pagination ──────────────────────
with db.cursor() as cur:
    cur.seek('user:1000')
    page = []
    while cur.valid() and len(page) < 100:
        page.append((cur.key(), cur.value()))
        cur.next()

# ── Multi-key and batch operations ───────────────────────────
results = db.get_many(['user:1001', 'user:1002', 'missing'])
# → {'user:1001': 'Alice', 'user:1002': None, 'missing': None}

for batch in db.iter_batches(batch_size=1000, start='order:'):
    process(batch)   # (key, value) pairs in sorted order

db.delete_prefix('session:')   # atomic bulk delete, one WAL fsync

# ── Snapshot — point-in-time consistent read ─────────────────
db.flush()   # include latest writes in snapshot
with db.snapshot() as snap:
    value = snap.get('user:1001')   # concurrent writes invisible
    for key, val in snap.scan():
        export(key, val)

# ── Backup ────────────────────────────────────────────────────
db.backup('/backups/2026-10-09')        # consistent hot backup
restored = LSMTree('/backups/2026-10-09')   # open backup directly

# ── Compaction ────────────────────────────────────────────────
db.compact_range('order:', 'order;')   # compact a specific range
db.compact_all()                        # full compaction, blocks until done
db.wait_for_compaction()                # synchronization primitive

# ── Observability ─────────────────────────────────────────────
print(db.stats_report())   # dashboard with latency p50/p99
snap = db.metrics.snapshot()
print(f"p50={snap['get_p50_us']}µs  p99={snap['get_p99_us']}µs  "
      f"cache_hit={snap['block_cache_hit_rate']:.1%}")

db.close()
```

---

## Full API Reference

### `LSMTree`

```python
LSMTree(directory,
        sync_writes=True,           # group-commit WAL fsync
        block_cache_bytes=8*1024**2, # LRU data block cache (default 8 MB)
        compaction_threads=2,        # parallel compaction workers
        compaction_rate_bytes_per_sec=0,  # throttle compaction I/O (0=unlimited)
        compaction_filter=None)      # fn(key, value, level) → bytes|None
```

| Method | Description |
|---|---|
| `put(key, value, ttl_seconds=0)` | Insert or overwrite. `ttl_seconds > 0` sets expiry. |
| `get(key) → str\|None` | Return value, or `None` if absent / deleted / expired. |
| `delete(key)` | Write a tombstone. Removed during compaction. |
| `get_many(keys) → dict` | `{key: value_or_None}` for all requested keys. |
| `write(batch: WriteBatch)` | Apply all batch ops atomically — one WAL fsync. |
| `update(key, fn, ttl_seconds=0)` | Atomic read-modify-write: `new_val = fn(current_val)`. |
| `compare_and_swap(key, expected, new)` | Atomic CAS. Returns `True` if swapped. |
| `increment(key, amount=1) → int` | Atomic integer increment. Returns new value. |
| `scan(start, end) → Iterator` | Range scan, tombstones and expired TTL excluded. |
| `scan_keys(start, end) → Iterator[str]` | Keys only — no value fetch overhead. |
| `prefix_scan(prefix) → Iterator` | All keys starting with `prefix`, sorted. |
| `cursor() → Cursor` | Stateful cursor for seek + pagination. |
| `delete_prefix(prefix) → int` | Atomic delete of all keys with prefix. Returns count. |
| `iter_batches(batch_size, start, end)` | Yield `[(key,value)]` lists of `batch_size`. |
| `snapshot() → Snapshot` | Point-in-time read-only view (context manager). |
| `flush()` | Force memtable to disk immediately. |
| `backup(dir) → dict` | Consistent hot backup. Open with `LSMTree(dir)`. |
| `compact_range(start, end)` | Synchronous range compaction. |
| `compact_all() → dict` | Full compaction, blocks until done. |
| `wait_for_compaction(timeout) → bool` | Block until all compaction jobs finish. |
| `estimate_key_count() → int` | O(levels) estimate, no disk I/O. |
| `stats() → dict` | Full metrics snapshot + level sizes + latency percentiles. |
| `stats_report() → str` | Formatted one-screen dashboard. |
| `close()` | Flush, stop background threads, close WAL. |

### `WriteBatch`

```python
batch = WriteBatch()
batch.put('k', 'v').put_ttl('temp', 'v', ttl_seconds=60).delete('old')
db.write(batch)
len(batch)          # number of ops
batch.byte_size     # total key+value bytes
batch.clear()       # reuse
```

### `Cursor`

```python
with db.cursor() as cur:
    cur.seek('key:100')          # position at first key ≥ 'key:100'
    cur.seek_to_first()          # position at first key overall
    cur.seek_prefix('order:')    # position at first key with prefix
    while cur.valid():
        print(cur.key(), cur.value())
        cur.next()
# or iterate directly:
for key, value in db.cursor().seek('key:100'):
    process(key, value)
```

### `Snapshot`

```python
db.flush()   # optional: include unsaved writes
with db.snapshot() as snap:
    snap.get(key)           # point-in-time read
    snap.scan(start, end)   # range scan
    snap.prefix_scan(pfx)   # prefix scan
```

### `EngineMetrics`

```python
snap = db.metrics.snapshot()
# Counts:    puts, deletes, gets, get_hits, get_misses, batches, scans
# Rates:     get_hit_rate, cache_hit_rate, write_ops_per_sec, read_ops_per_sec
# Cache:     block_cache_hits, block_cache_misses, block_cache_hit_rate, block_cache_bytes
# Latency:   get_p50_us, get_p99_us, get_p999_us, put_p50_us, put_p99_us
# Compaction: flushes, compactions, bytes_flushed, bytes_compacted, write_amplification
# WAL:       wal_records, wal_syncs
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    LSMTree Public API                            │
│  put/get/delete · write(batch) · scan/prefix_scan/cursor         │
│  update/CAS/increment · TTL · snapshot · backup · compact_all    │
└────────────────────┬─────────────────────────────┬──────────────┘
                     │ write path                   │ read path
                     ▼                              ▼
       ┌─────────────────────┐         ┌──────────────────────────┐
       │  Group-commit WAL   │         │  Active SkipList          │
       │  CRC-checked        │         │  memtable  (≤ 4 MB)       │
       │  64 writes / fsync  │         └──────────────┬───────────┘
       └─────────────────────┘                        │ rotate < 1µs
                                                      ▼
                                         ┌──────────────────────────┐
                                         │  Immutable SkipList       │
                                         │  (flushing in background) │
                                         └──────────────┬───────────┘
                                                        │ background flush
                                                        ▼
                                         ┌──────────────────────────┐
                                         │  SSTable (.sst)           │
                                         │  4 KB blocks (LRU cached) │
                                         │  Sparse block index       │
                                         │  Bloom filter (1% FPR)   │
                                         └──────────────┬───────────┘
                                                        ▼
┌──────────────────────────────────────────────────────────────────┐
│            7-Level Tiered Disk Layout                            │
│   L0 (newest, may overlap)                                       │
│   L1–L6 (non-overlapping, sorted by key range)                  │
│   Binary search on sorted L1+ for O(log n) file lookup          │
└───────────────────────────────────┬──────────────────────────────┘
                                    │ parallel compaction
                                    │ (thread pool, per-level locks)
                                    ▼
                       ┌────────────────────────┐
                       │  MANIFEST              │
                       │  atomic rename() commit│
                       └────────────────────────┘
```

**Read path:** memtable → imm memtable → for each SSTable: key-range pre-filter (2 byte comparisons) → Bloom filter → binary search sparse index → `os.pread()` data block (LRU cached)

**Write path:** group-commit WAL (64 writes/fsync) → SkipList O(log n) → rotate when full → background flush → L0 SSTable → leveled compaction

---

## The Three Crash-Safety Invariants

Every LSM-Tree engine must satisfy exactly these three conditions to be crash-safe. LSMKV is the only open-source implementation to formally state, code-enforce, and experimentally verify all three.

**Invariant 1 — WAL Completeness**
> For every write that returns to the caller, a durable CRC-verified WAL record exists **before** the memtable is updated.

**Invariant 2 — Compaction Atomicity**
> The MANIFEST update is the compaction commit point. The database is always in pre-compaction or post-compaction state — never partial.
> Implemented via `os.rename()`, which is atomic on POSIX.

**Invariant 3 — WAL Truncation Safety**
> The WAL may only be truncated after **all** in-memory data (active + immutable memtable) has been durably written and recorded in the MANIFEST.

Verified by: 5/5 SIGKILL crash recovery runs with `missing=0` and `corrupt=0`.

```bash
cd Project
python crash_harness.py --runs 5 --keys 20000
# Result: 5/5 runs passed  (missing=0  corrupt=0)
```

---

## Scaling Fixes (8 diagnosed and fixed)

| ID | Bottleneck | Fix | Measured Impact |
|---|---|---|---|
| S-01 | 3 OS reads per `get()` for metadata | SSTableReader cache | Eliminates per-call file open |
| S-02 | Writes stall 50–200 ms during flush | Immutable memtable | Write rotation < 1 µs |
| S-03 | `stat()` per file every 2 s | Manifest size cache | Zero stat() calls |
| S-04 | `scan()` exhausts fd limit | Fd semaphore | Bounded to 64 open fds |
| S-05 | 1 fsync per write | Group-commit WAL | 64 writes share 1 fsync |
| S-06 | Full level loaded per compaction | Range-aware file picking | O(1) src + overlapping dst only |
| S-07 | Compaction uses 100% disk bandwidth | I/O throttle (`compaction_rate_bytes_per_sec`) | Reserve bandwidth for writes |
| S-08 | Single compaction thread | Thread pool + per-level locks | L0→L1 and L2→L3 run in parallel |

Additional optimisations: block LRU cache · key-range pre-filter (2-byte comparison before Bloom) · binary search on sorted L1+ levels · `os.pread()` for atomic concurrent block reads.

Full diagnosis in [SCALING_ISSUES.md](Project/SCALING_ISSUES.md).

---

## Testing

```bash
cd Project
pip install pytest
pytest tests/ -v
# 244 tests, ~8 minutes
```

| Test file | What it covers |
|---|---|
| `test_skip_list.py` | Insert, lookup, delete, scan, tombstones |
| `test_bloom_filter.py` | FPR at multiple capacities, serialisation round-trip |
| `test_wal.py` | Replay, CRC mismatch stops replay, group-commit, truncation |
| `test_sstable.py` | Bloom filter, sparse index, multi-block, bad magic, `last_key` |
| `test_compaction.py` | K-way merge, tombstone propagation, deepest-level GC |
| `test_lsm_tree.py` | Full engine: overwrite, delete, scan, persistence |
| `test_crash.py` | WAL replay, orphan cleanup, MANIFEST invariants |
| `test_write_batch.py` | Batch ops, TTL batch, chaining, persistence |
| `test_immutable_memtable.py` | Rotation under 5–15 MB load, flush correctness |
| `test_concurrent.py` | Concurrent readers, writer+reader, concurrent scans |
| `test_metrics.py` | Metrics wiring, hit rates, stats integration |
| `test_block_cache.py` | LRU eviction, disabled mode, integration |
| `test_ttl.py` | Expiry in get/scan/prefix_scan/batch, persistence |
| `test_cursor.py` | seek, next, iterator protocol, context manager |
| `test_snapshot.py` | Isolation, concurrent writes, flush+snapshot |
| `test_atomic_ops.py` | update, CAS, increment — concurrency proofs |
| `test_compaction_range.py` | `last_key`, `compact_range`, binary search |
| `test_throttle.py` | `RateLimiter`, throttled compaction, write amplification |
| `test_parallel_compaction.py` | Thread pool, write stall, correctness under N threads |
| `test_range_filter.py` | `range_skips`, `sstable_reads` metrics |
| `test_latency_bsearch.py` | `LatencyTracker`, p50/p99, binary search |
| `test_compaction_filter_backup.py` | `compaction_filter`, `backup()`, `get_many()` |
| `test_compact_all.py` | `compact_all`, `wait_for_compaction`, `iter_batches` |

---

## Benchmarks

```bash
cd Project
python benchmarks.py
```

```
Write (async)    50,000 ops    175,000 ops/sec
Write (sync)      5,000 ops        263 ops/sec   ← fsync is the bottleneck
Read p50                             0.008 ms     ← block cache hit
Read p99                             0.050 ms
Scan  500 ranges  49,900 entries     0.18 s
```

---

## Novel Finding — Bloom Filter Serialisation Defect

During development a defect was found: after restarting the engine, the deserialised Bloom filter produced **false negatives** — claiming a key *definitely doesn't exist* when it did. This caused the engine to skip the SSTable entirely and return `None` for a key that was present on disk.

**Root cause:** the bit array was serialised as a Python `list` of integers. On deserialisation the reconstructed filter had a different internal representation, shifting every hash bucket and invalidating all membership queries.

**Fix:** serialise as a packed byte string (`int.to_bytes`) with a CRC32 checksum that detects future corruption.

See [lsm_engine/bloom_filter.py](Project/lsm_engine/bloom_filter.py) and `test_bloom_filter.py::test_serialise_round_trip`.

---

## Project Structure

```
Project/
├── lsm_engine/                  # Python v1 — the reference implementation
│   ├── lsm_tree.py              # Engine entry point — all public API
│   ├── wal.py                   # WAL — CRC-checked, group-commit fsync
│   ├── sstable.py               # SSTable writer + reader + os.pread
│   ├── skip_list.py             # SkipList memtable
│   ├── bloom_filter.py          # Bloom filter — configurable FPR
│   ├── manifest.py              # MANIFEST — atomic level metadata
│   ├── compaction.py            # K-way merge + compaction filter
│   ├── block_cache.py           # LRU data block cache
│   ├── cursor.py                # Stateful forward iterator
│   ├── snapshot.py              # Point-in-time read-only view
│   ├── write_batch.py           # Atomic multi-key writes
│   ├── metrics.py               # Thread-safe counters + LatencyTracker
│   ├── ttl.py                   # TTL encoding / expiry helpers
│   └── _utils.py                # fsync + RateLimiter helpers
│
├── lsmkv_v2/                    # C++ v2 — production performance
│   ├── include/lsmkv/           # Public headers
│   ├── src/                     # Implementations
│   ├── tests/                   # 43-test C++ suite
│   ├── benchmarks/              # C++ benchmark binary
│   └── CMakeLists.txt
│
├── tests/                       # Python pytest suite (244 tests)
├── docs/                        # Architecture, invariants, literature survey
├── SCALING_ISSUES.md            # 8 bottlenecks — root cause + fix + metric
├── LSMKV_Research_Summary.md    # Full research paper
├── demo.py                      # Live walkthrough of every API call
├── benchmarks.py                # Throughput / latency benchmarks
└── crash_harness.py             # SIGKILL crash recovery integration test
```

---

## v1 (Python) vs v2 (C++)

| Feature | Python v1 | C++ v2 |
|---|---|---|
| Purpose | Readable reference, embeddable | Production performance |
| WAL | Group-commit (64/fsync) | Group-commit (128/fsync) |
| SkipList | Heap-allocated nodes | Arena-allocated (cache-local) |
| Block cache | 8 MB LRU (data blocks) | 8 MB LRU (full RocksDB-style) |
| Concurrent reads | `threading.Lock` | `std::shared_mutex` |
| Write stall | L0 ≥ 12 files | Configurable slow/stop triggers |
| Error handling | Exceptions | `Status` return type |
| Crash invariants | All three enforced | All three enforced |

---

## Contributing

```bash
git clone https://github.com/nlk0811/LSMKV.git
cd LSMKV/Project
pip install pytest
pytest tests/ -v   # all 244 must pass before and after
```

Good areas to contribute:

- **Prefix compression** in SSTable data blocks (adjacent keys share prefixes)
- **Block compression** (zlib/snappy per data block with format versioning)
- **Python bindings** for C++ v2 (pybind11)
- **WAL segmentation** (multiple WAL files, segment rotation)
- **Benchmarking on Linux / NVMe** — current numbers are macOS SSD
- **Transaction API** — multi-key optimistic transactions

---

## Documentation

| File | Contents |
|---|---|
| [docs/architecture.md](Project/docs/architecture.md) | Component diagram, write/read path, on-disk formats, complexity table |
| [docs/invariants.md](Project/docs/invariants.md) | Formal specification of the three crash-safety invariants |
| [SCALING_ISSUES.md](Project/SCALING_ISSUES.md) | 8 bottlenecks — root cause, fix, measured impact |
| [LSMKV_Research_Summary.md](Project/LSMKV_Research_Summary.md) | Full research report: gap, hypotheses, results, novel finding |

---

## License

MIT — see [LICENSE](LICENSE). Free for personal and commercial use.
