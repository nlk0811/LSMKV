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

## FIXED (continued)

### [S-06] Compaction reads entire level into memory for large deployments
**Files:** `lsm_engine/sstable.py`, `lsm_engine/lsm_tree.py`
**Status:** FIXED (iteration 3)

**What broke:** The compaction k-way merge opened ALL input SSTableReaders and
loaded their full sparse indexes into memory. For L1+ compaction, that meant
merging every file in both the source and destination level at once. With 1 000
input files at L6, the index metadata alone consumed hundreds of megabytes and
the heap fan-in K was unboundedly large.

**Fix:**
1. Added `last_key` property to `SSTableReader` — reads the final entry of the
   last data block, cached on first access, no format change required.
2. Added `_overlapping_files(candidates, reference)` to `LSMTree` — uses
   `first_key` + `last_key` from the reader cache to find only the dst files
   whose key range overlaps the src files' bounding box. Unknown ranges are
   included conservatively.
3. Modified `_compact_into()`:
   - **L0**: merges all L0 files (they overlap each other) but only the L1
     files whose range overlaps — not the entire L1.
   - **L1+**: picks ONE src file at a time + only overlapping dst files.
     Remaining src files are left for subsequent background ticks, so each
     individual compaction job is bounded by O(1 src file + overlapping dst
     files) rather than O(all files in two levels).
4. Added `compact_range(start, end)` public API — triggers a synchronous
   compaction of [start, end) across all levels. Useful before bulk reads
   to reduce read amplification, or to force tombstone collection in a range.

**Measured benefit:** Per-compaction fan-in drops from O(N_src + N_dst) to
O(1 + overlap_count). For a balanced 7-level tree, typical overlap at L3+ is
10–15 files rather than hundreds. Peak memory per compaction job is now
O(overlap_count × index_size).

**Tests:** `tests/test_compaction_range.py` (8 tests) — last_key correctness,
multi-block ordering, caching, compact_range correctness/unbounded/out-of-range,
and full L0→L1 cycle with persistence.

---

### [S-07] No rate-limiting or back-pressure on compaction I/O
**Files:** `lsm_engine/_utils.py`, `lsm_engine/compaction.py`, `lsm_engine/lsm_tree.py`
**Status:** FIXED (iteration 4)

**What broke:** Background compaction consumed 100% of available I/O bandwidth.
Under heavy write loads the compaction thread competed with the write path for
disk bandwidth, causing write latency spikes.

**Fix:**
1. `RateLimiter` class in `_utils.py` — token-bucket that sleeps when
   bytes_written / rate > elapsed. 1 ms sleep granularity; checked every 256
   entries to avoid per-entry overhead. `reset()` clears debt between jobs.
2. `compact()` in `compaction.py` accepts optional `rate_limiter` parameter.
3. `LSMTree(compaction_rate_bytes_per_sec=N)` — 0 = unlimited (default).
   Example: `50 * 1024 * 1024` reserves bandwidth for the write path.

**Bonus: write amplification metric.** `bytes_written_user` counter tracks
key+value bytes per put/delete/batch. `snapshot()` computes
`write_amplification = (bytes_flushed + bytes_compacted) / bytes_written_user`
when > 0. Typical sustained values: 10–30× under heavy writes.

**Tests:** `tests/test_throttle.py` (9 tests).

---

### [S-08] Single-threaded compaction
**File:** `lsm_engine/lsm_tree.py`
**Status:** FIXED (iteration 5)

**What broke:** One background thread handled all compactions. At very high
write rates, L0 accumulated faster than one thread could compact it, increasing
read amplification and eventually causing write stalls.

**Fix:**
1. `ThreadPoolExecutor(max_workers=compaction_threads)` — default 2 workers.
   `LSMTree(compaction_threads=N)` exposes this as a constructor parameter.
2. Per-level locks `_compaction_locks[lvl]` — one lock per level 0..MAX_LEVELS.
   `_compact_into()` acquires both `lock[src]` and `lock[dst]` using
   non-blocking `acquire(blocking=False)`. If either is held by another job,
   the current job returns immediately and the bg loop retries next tick.
   Acquiring in ascending level order prevents deadlock.
   Result: L0→L1 and L2→L3 run concurrently; L0→L1 and L1→L2 serialise.
3. `_submit_compaction(lvl)` submits a future to the pool; deduplicates by
   checking whether the previous future for that level is still running.
4. **Write-stall back-pressure** (also added here):
   - `L0_SLOWDOWN_TRIGGER = 8`: sleep `(n - 8 + 1) ms` per extra L0 file
   - `L0_STOP_TRIGGER = 12`: block writes completely until L0 drains below 12
   This matches the C++ v2 design and prevents L0 from growing unboundedly
   under write bursts that temporarily outpace even parallel compaction.

**Tests:** `tests/test_parallel_compaction.py` (8 tests) — correctness under
threads=1/2/4, deletes, persist+reopen, concurrent writers, stall release.

---

## Bottleneck Sequence (order in which each becomes binding)

| Load Level | Binding Constraint | Issue | Status |
|---|---|---|---|
| > 500 puts/sec (sync) | WAL fsync per write | S-05 | **FIXED** |
| > 1 000 gets/sec | SSTable metadata reloaded per get | S-01 | **FIXED** |
| Sustained writes | Write stalls during flush | S-02 | **FIXED** |
| > 10 concurrent scans | File descriptor exhaustion | S-04 | **FIXED** |
| Background loop | stat() calls per compaction tick | S-03 | **FIXED** |
| > 100 M keys | Compaction memory / fan-in | S-06 | **FIXED** |
| Heavy concurrent write | Compaction I/O contention | S-07 | **FIXED** |
| Multi-core machines | Single compaction thread | S-08 | **FIXED** |
