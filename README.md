# LSMKV

[![Python Tests](https://img.shields.io/badge/python%20tests-62%2F62%20passing-brightgreen)](#testing)
[![C++ Tests](https://img.shields.io/badge/c%2B%2B%20tests-43%2F43%20passing-brightgreen)](#testing)
[![Crash Recovery](https://img.shields.io/badge/crash%20recovery-5%2F5%20PASS-brightgreen)](#crash-recovery)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](#license)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![C++17](https://img.shields.io/badge/c%2B%2B-17-blue)](https://en.cppreference.com/)

**LSMKV** is a crash-safe, embedded key-value storage engine built from scratch on a Log-Structured Merge Tree (LSM-Tree). It comes in two forms: a clean Python reference implementation (v1) and a performance-optimised C++ port (v2).

> If you've ever wondered how RocksDB, LevelDB, Cassandra, or DynamoDB guarantee your data survives a power cut — LSMKV is that mechanism, in ~1 000 lines of clear, commented Python.

---

## Why would you use LSMKV?

### You're learning how storage engines work
LSM-Trees are everywhere in production databases, but the crash-safety mechanism is buried in hundreds of thousands of lines of C++. LSMKV distills it to three formally-stated invariants and implements each one in a single, traceable function. Every decision has a reason; every reason is documented.

### You're building something that needs a small embedded KV store
LSMKV is self-contained (zero dependencies beyond the Python standard library). Drop `lsm_engine/` into your project, call `LSMTree('/path/to/data')`, and you have a persistent, crash-safe key-value store with range scans.

### You're teaching or studying data structures
The codebase is a live exercise in:
- **Skip Lists** — probabilistic sorted in-memory structure (memtable)
- **Bloom Filters** — space-efficient probabilistic set membership
- **K-way merge with a min-heap** — compaction of sorted SSTable files
- **Sparse indexing** — binary search to find the right 4 KB block on disk
- **Append-only logging** — crash recovery via WAL replay

### You want a reference for crash-safe design
No existing paper formally states the *minimum* conditions for crash-safe LSM operation on standard disk storage as testable, verifiable invariants. This project does exactly that — and proves them with a kill-9 crash harness.

---

## Features

- **Crash-safe** — three formally-stated invariants, verified by a SIGKILL crash harness
- **Full API** — `put`, `get`, `delete`, `scan` (range), `close`
- **WAL** (Write-Ahead Log) — CRC-checked, replay on recovery
- **SSTable** — 4 KB data blocks, sparse index, per-file Bloom filter
- **Bloom filters** — configurable FPR (default 1%), skip unnecessary disk reads
- **Leveled compaction** — 7-level tiered compaction, background thread, tombstone GC
- **MANIFEST** — atomic POSIX `rename()` as the compaction commit point
- **Zero dependencies** — Python standard library only (v1)
- **C++ v2** — group-commit WAL, arena-backed SkipList, LRU block cache, write stall, `shared_mutex` for concurrent reads

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
from lsm_engine.lsm_tree import LSMTree

db = LSMTree('/path/to/data')

db.put('user:1001', '{"name": "Alice", "age": 30}')
db.put('user:1002', '{"name": "Bob",   "age": 25}')

print(db.get('user:1001'))   # '{"name": "Alice", "age": 30}'
print(db.get('missing'))     # None

db.delete('user:1002')
print(db.get('user:1002'))   # None  (tombstone)

# Range scan — yields (key, value) tuples in sorted order
for key, value in db.scan('user:1000', 'user:2000'):
    print(key, value)

db.close()  # flushes memtable, joins background thread
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
db->Get("key", &val);   // val = "value"

db->Delete("key");

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
| Constructor | `LSMTree(directory, sync_writes=True)` | Open or create a database at `directory`. `sync_writes=False` disables per-write fsync for higher throughput (unsafe for crash durability). |
| `put` | `put(key, value)` | Insert or overwrite a key. Both key and value accept `str` or `bytes`. |
| `get` | `get(key) -> str \| None` | Return value or `None` if the key does not exist or was deleted. |
| `delete` | `delete(key)` | Write a tombstone. Key will read as `None` and is removed during compaction. |
| `scan` | `scan(start=None, end=None) -> Iterator[(str, str)]` | Range scan, inclusive start / exclusive end. Pass `None` for unbounded. Tombstones are excluded. |
| `stats` | `stats() -> dict` | Returns memtable size, key count, WAL size, per-level file count and byte size. |
| `close` | `close()` | Flush the memtable to disk, stop the background compaction thread, close the WAL. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         LSMTree (API)                           │
│              put / get / delete / scan / close                  │
└───────────┬─────────────────────────────────┬───────────────────┘
            │ write path                       │ read path
            ▼                                  ▼
┌───────────────────┐              ┌─────────────────────────┐
│  WAL (append-log) │              │  SkipList memtable       │
│  CRC-checked      │              │  O(log n) get/put/delete │
│  Crash recovery   │              └──────────┬──────────────┘
└───────────────────┘                         │ flush when ≥ 4 MB
                                              ▼
                                ┌─────────────────────────┐
                                │   SSTable (.sst)         │
                                │   4 KB data blocks       │
                                │   Sparse block index     │
                                │   Bloom filter (1% FPR)  │
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
1. Check SkipList memtable — O(log n), in memory
2. For each level L0 → L6, for each SSTable:
   - Bloom filter check — if definite miss, skip the file
   - Binary search the sparse index — find the right 4 KB block
   - Scan the block — return on first match

### Write path
1. Append CRC-checked record to WAL (fsync)
2. Insert into SkipList memtable — O(log n)
3. When memtable ≥ 4 MB: flush to L0 SSTable, update MANIFEST, truncate WAL

---

## The Three Crash-Safety Invariants

These are the core research contribution. Together they guarantee zero data loss and zero corruption under an arbitrary SIGKILL at any point.

### Invariant 1 — WAL Completeness
> For every write that returns to the caller, a durable, CRC-verified WAL record exists before the memtable is updated.

A crash after the WAL write but before the memtable update is safe — WAL replay restores the write on restart.

### Invariant 2 — Compaction Atomicity
> The MANIFEST update is the compaction commit point. The database is always in either the pre-compaction or post-compaction state — never a partial state.

Implemented via `os.rename()`, which is atomic on POSIX. Any orphaned SSTable files are cleaned up on startup by `_cleanup_orphans()`.

### Invariant 3 — WAL Truncation Safety
> The WAL may only be truncated after the corresponding SSTable has been durably written **and** its path has been atomically recorded in the MANIFEST.

This closes the window between flush and WAL cleanup. If the process is killed between these two steps, WAL replay re-creates the memtable entries (harmless duplicates that compaction merges away).

For the full formal specification, see [`docs/invariants.md`](Project/docs/invariants.md).

---

## Testing

### Python test suite (62 tests)

```bash
cd Project
pip install pytest
pytest tests/ -v
```

Tests cover each component independently:

| File | What it tests |
|---|---|
| `test_skip_list.py` | Insert, lookup, delete, scan, tombstones, sorted order |
| `test_bloom_filter.py` | FPR at multiple capacities, serialisation round-trip |
| `test_wal.py` | Put/delete replay, CRC mismatch stops replay, truncation |
| `test_sstable.py` | Bloom filter, sparse index, multi-block round-trip, bad magic |
| `test_compaction.py` | K-way merge, tombstone propagation, deepest-level GC |
| `test_lsm_tree.py` | Full engine: overwrite, delete, scan, persistence, concurrent |
| `test_crash.py` | WAL replay, orphan cleanup, MANIFEST invariants |

### C++ test suite (43 tests)

```bash
cd Project/lsmkv_v2
cmake -S . -B build && cmake --build build -j$(nproc)
./build/lsmkv_tests
```

---

## Crash Recovery

The `crash_harness.py` spawns a writer subprocess, sends SIGKILL at a random point mid-write, then reopens the database and verifies every acknowledged write is present and uncorrupted.

```bash
cd Project
python crash_harness.py --runs 5 --keys 20000
```

```
LSMKV Crash Recovery Harness
============================================================
Run 1/5  kill_after=4,218 keys … PASS  (written=4,218  missing=0  corrupt=0)
Run 2/5  kill_after=11,073 keys … PASS  (written=11,073  missing=0  corrupt=0)
Run 3/5  kill_after=7,954 keys … PASS  (written=7,954  missing=0  corrupt=0)
Run 4/5  kill_after=16,401 keys … PASS  (written=16,401  missing=0  corrupt=0)
Run 5/5  kill_after=2,887 keys … PASS  (written=2,887   missing=0  corrupt=0)

Result: 5/5 runs passed
```

---

## Benchmarks

```bash
cd Project
python benchmarks.py
```

Results on Apple M-series (your numbers will vary):

| Benchmark | Result |
|---|---|
| Write throughput (async, no fsync) | ~191,000 ops/sec |
| Write throughput (sync, fsync/write) | ~262 ops/sec |
| fsync overhead | **910× slowdown** — the durability-throughput trade-off |
| Read latency p50 | 0.250 ms |
| Read latency p99 | 0.629 ms |
| Bloom filter FPR @ 1% target | 1.06% actual |
| Bloom filter bits/key @ 1% FPR | 9.6 |

The 910× gap between sync and async writes is the practical cost of crash durability on NVMe/SSD. The C++ v2 reduces this with group-commit WAL batching (up to 128 writes per fsync).

### C++ v2 benchmarks

```bash
./Project/lsmkv_v2/build/lsmkv_bench
```

---

## v1 → v2 Improvements

| Feature | Python v1 | C++ v2 |
|---|---|---|
| Write throughput | ~262 ops/sec (sync) | Group-commit WAL, 128 writes/fsync |
| SkipList | Heap-allocated nodes | Arena-allocated (cache-local) |
| Block cache | None — every read hits disk | LRU block cache, 8 MB default |
| Concurrent reads | threading.Lock (exclusive) | `shared_mutex` (parallel reads) |
| Write stall | None | Slow/stop writers when L0 fills up |
| Error handling | Exceptions | `Status` return type throughout |

---

## Project Structure

```
Project/
├── lsm_engine/          # Python v1 — the reference implementation
│   ├── lsm_tree.py      #   Engine entry point, public API
│   ├── skip_list.py     #   SkipList memtable
│   ├── wal.py           #   Write-Ahead Log (CRC-checked)
│   ├── sstable.py       #   SSTable writer + reader
│   ├── bloom_filter.py  #   Bloom filter (configurable FPR)
│   ├── manifest.py      #   MANIFEST — atomic level metadata
│   ├── compaction.py    #   K-way merge compaction
│   └── _utils.py        #   fsync helpers
│
├── lsmkv_v2/            # C++ v2 — performance-optimised port
│   ├── include/lsmkv/   #   Public headers
│   ├── src/             #   Implementations
│   ├── tests/           #   C++ test suite (43 tests)
│   ├── benchmarks/      #   C++ benchmark binary
│   └── CMakeLists.txt
│
├── tests/               # Python pytest suite (62 tests)
├── docs/                # Architecture, invariants, literature survey
├── demo.py              # Walkthrough of every API call
├── benchmarks.py        # Write/read/scan/Bloom benchmarks
└── crash_harness.py     # SIGKILL crash recovery integration test
```

---

## Novel Finding — Bloom Filter Serialisation Defect

During development, a defect was discovered: after restarting the engine, the deserialized Bloom filter produced **false negatives** — reporting that a key *definitely does not exist* when it actually did. This bypassed the Bloom filter entirely and forced every read to scan the SSTable, but more critically, it violated the filter's correctness contract.

**Root cause:** The bit array was serialised as a Python `list` of integers, not as a raw bitfield. On deserialisation, the reconstructed filter had a different internal layout, causing `may_contain()` to miss keys that were genuinely present.

**Fix:** Serialise the bit array as a packed byte string (`int.to_bytes` / `from_bytes`), with a CRC32 checksum to detect further corruption.

See `lsm_engine/bloom_filter.py` and `tests/test_bloom_filter.py::test_serialise_round_trip`.

---

## Documentation

| File | Contents |
|---|---|
| [`docs/architecture.md`](Project/docs/architecture.md) | Component diagram, write/read path, on-disk formats, complexity table |
| [`docs/invariants.md`](Project/docs/invariants.md) | Formal specification of the three crash-safety invariants |
| [`docs/literature_survey.md`](Project/docs/literature_survey.md) | Survey of 26 LSM papers (2021–2025) |
| [`docs/research_gap.md`](Project/docs/research_gap.md) | Precise statement of the gap this project addresses |
| [`LSMKV_Research_Summary.md`](Project/LSMKV_Research_Summary.md) | Full research report with hypotheses, results, and conclusions |

---

## Contributing

Contributions are welcome. Some areas where help would be valuable:

- **Bloom filter partitioning** — per-level filter rather than per-file
- **Prefix compression** in SSTable data blocks
- **LRU block cache** for the Python v1 (already in C++ v2)
- **Python bindings** for the C++ v2 engine (pybind11)
- **Benchmarking on Linux/NVMe** — the current numbers are on macOS SSD

To contribute:

```bash
git clone https://github.com/nlk0811/LSMKV.git
cd LSMKV/Project
pip install pytest
pytest tests/ -v          # make sure all 62 tests pass before and after your change
```

Open a pull request with a description of what you changed and why.

---

## License

MIT License — see [`LICENSE`](LICENSE).

This project was built as part of an Advanced Data Structures and Algorithms course. The implementation is original; any resemblance to RocksDB or LevelDB internals reflects that LSM-Tree designs converge on the same correct primitives.
