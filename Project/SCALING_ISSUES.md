# SCALING_ISSUES.md
## LSMKV — Scaling Bottleneck Registry

Each issue is ranked by the write/read load at which it first becomes the binding constraint.
Status: **FIXED** | **PLANNED** | **OPEN**

---

## FIXED

### [S-01] SSTableReader reopened on every `get()` call
**File:** `lsm_engine/lsm_tree.py`
**Status:** FIXED (iteration 1)

**What breaks:** Every `get()` call constructed a new `SSTableReader`, opening
the file, reading the 32-byte footer, loading the full sparse index, and loading
the Bloom filter — then closing everything again. At 1 000 reads/sec across 50
SSTables that was 150 000 raw OS operations per second for immutable metadata.

**Fix:** `_reader_cache: Dict[str, SSTableReader]` holds open readers between
calls. `_get_reader()` opens on first access; `_evict_readers()` closes entries
when compaction removes files; `_drain_reader_cache()` closes all on `close()`.
Double-open race handled: the second opener closes its reader and returns the
already-cached one. Cache hits/misses tracked in `EngineMetrics`.

---

### [S-02] Writes stall completely during memtable flush
**File:** `lsm_engine/lsm_tree.py`
**Status:** FIXED (iteration 2)

**What breaks:** `_flush_memtable()` ran while holding `_write_lock`. Writing a
4 MB memtable takes 50–200 ms on NVMe. During this entire window every `put()`
and `delete()` blocked. At > 500 puts/sec the engine stalled for seconds at a time.

**Fix:** Immutable memtable pattern. When `size_bytes >= MEMTABLE_LIMIT` and
no previous `_imm` is pending, the active memtable is atomically swapped to
`_imm`, a fresh `SkipList` is installed, and `_write_lock` is released in < 1 µs.
The background thread flushes `_imm` via `_flush_imm()` (no WAL truncation — the
active memtable still has records in the WAL). On the rare collision (second
rotation before first flush completes) the engine falls back to inline flush.

**Read path:** `get()` and `scan()` check both `_memtable` and `_imm`; keys in
`_imm` are always visible even while the background flush is in progress.

**Crash safety:** WAL is only truncated in `_flush_memtable()` (the full flush),
which calls `_flush_imm()` first. After both are in SSTables, the WAL truncation
is safe. Invariant 3 is preserved.

---

### [S-03] `level_size()` calls `os.path.getsize` per file every 2 seconds
**File:** `lsm_engine/manifest.py`
**Status:** FIXED (iteration 1)

**What breaks:** 200 stat() calls per compaction tick, 360 000 per hour, for
data that only changes when a compaction commits.

**Fix:** `_file_sizes: Dict[str, int]` populated in `_load()`, `add_l0()`, and
`apply_compaction()`. `level_size()` reads from the dict — zero syscalls.

---

### [S-04] `scan()` opens all SSTable file descriptors simultaneously
**File:** `lsm_engine/lsm_tree.py`
**Status:** FIXED (iteration 2)

**What breaks:** With 100 SSTables, one scan touched 100 file descriptors. Linux
default ulimit is 1 024 — a handful of concurrent scans exhausted it. On macOS
the default is 256, making this hit even sooner.

**Fix:** `self._scan_sem = threading.Semaphore(MAX_SCAN_FDS)` (64 by default).
Every SSTable opened for a scan acquires one slot; the slot is released in the
`finally` block when the reader is closed. Concurrent scans automatically queue
for available slots rather than failing with EMFILE.

---

### [S-05] No group-commit WAL in Python v1
**File:** `lsm_engine/wal.py`
**Status:** FIXED (iteration 2)

**What breaks:** Every `put()` called `fsync()` per record. fsync on NVMe takes
~100 µs, on SATA ~1 ms. Throughput was capped at 1/fsync_latency regardless of
CPU speed.

**Fix:** `WAL` now runs a `_gc_loop` background thread when `sync_writes=True`.
Writers submit `(record_bytes, done_event)` to `_gc_pending` and wait on
`done.wait()`. The gc thread drains up to `group_commit_max` (default 64)
records per iteration, writes all of them, calls one `fsync()`, then signals all
done events. Multiple concurrent writers automatically batch. `log_batch()` lets
a `WriteBatch` submit all its records as one gc-thread entry — N ops, 1 fsync.

**Durability guarantee unchanged:** callers still block until their record is
fsynced; the group commit only batches concurrent waiters.

---

## PLANNED

### [S-06] Compaction reads entire level into memory for large deployments
**File:** `lsm_engine/compaction.py`
**Status:** PLANNED (iteration 3)

**What breaks:** The compaction k-way merge opens all input SSTableReaders and
loads their full sparse indexes into memory. With 1 000 input files at L6
(possible at very large scale), the index metadata alone consumes hundreds of
megabytes, and the merge fan-in K is large enough to make the min-heap slow.

**Fix (planned):** Range-partition large compactions. Pick a key midpoint; compact
only the SSTables whose key ranges overlap `[start, midpoint)`, write one output
SSTable, then compact `[midpoint, end)`. This bounds per-compaction memory to
O(files_in_range × index_size) rather than O(all_files × index_size), and keeps
K small. Will add `compact_range(start, end)` as a public API too.

---

### [S-07] No rate-limiting or back-pressure on compaction I/O
**File:** `lsm_engine/lsm_tree.py`
**Status:** OPEN

**What breaks:** Background compaction uses 100% of available I/O bandwidth with
no throttling. Under a heavy write load the compaction thread can compete with
the write path for disk bandwidth, causing write latency spikes.

**Fix (planned):** Add `compaction_rate_bytes_per_sec` option (default unlimited).
Compaction writer tracks bytes written; if ahead of schedule, sleeps for
`bytes_written / rate - elapsed` seconds between blocks. This lets the operator
tune the compaction/write balance for their hardware.

---

### [S-08] Single-threaded compaction
**File:** `lsm_engine/lsm_tree.py`
**Status:** OPEN

**What breaks:** One background thread handles all compactions. At very high
write rates (L0 fills faster than one thread can compact), L0 grows past the
trigger, increasing read amplification and eventually causing write stalls even
with the immutable memtable.

**Fix (planned):** Thread-pool compaction executor. Allow up to N concurrent
compactions (one per level pair) so L0→L1 and L2→L3 can run in parallel on
multi-core machines. Requires a per-level lock to prevent overlapping compactions
on the same level pair.

---

## Bottleneck Sequence (order in which each becomes binding)

| Load Level | Binding Constraint | Issue | Status |
|---|---|---|---|
| > 500 puts/sec (sync) | WAL fsync per write | S-05 | **FIXED** |
| > 1 000 gets/sec | SSTable metadata reloaded per get | S-01 | **FIXED** |
| Sustained writes | Write stalls during flush | S-02 | **FIXED** |
| > 10 concurrent scans | File descriptor exhaustion | S-04 | **FIXED** |
| Background loop | stat() calls per compaction tick | S-03 | **FIXED** |
| > 100 M keys | Compaction memory / fan-in | S-06 | planned |
| Heavy concurrent write | Compaction I/O contention | S-07 | open |
| Multi-core machines | Single compaction thread | S-08 | open |
