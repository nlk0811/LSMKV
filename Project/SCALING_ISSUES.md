# SCALING_ISSUES.md
## LSMKV — Scaling Bottleneck Registry

Each issue is ranked by the write/read load at which it first becomes the binding constraint.
Status: **FIXED** | **PLANNED** | **OPEN**

---

## FIXED

### [S-01] SSTableReader reopened on every `get()` call
**File:** `lsm_engine/lsm_tree.py`  
**Status:** FIXED (iteration 1)

**What breaks:** Every `get()` call constructs a new `SSTableReader`, which opens
the file, reads the 32-byte footer (seek + read), loads the full sparse index
(read), and loads the Bloom filter (read) — then closes everything again after
the lookup. At 1 000 reads/sec across 50 SSTables that is 150 000 raw OS file
operations per second just for immutable metadata that never changes between
compactions.

**Why it breaks:** `SSTableReader.__init__` always does three seeks/reads
(footer → index → Bloom filter) regardless of whether the data was just loaded
a millisecond ago.

**Fix:** Added `_reader_cache: Dict[str, SSTableReader]` in `LSMTree`. Readers are
opened on first access and kept alive. `_evict_readers()` closes and removes
entries when compaction deletes the backing files. `_drain_reader_cache()` closes
all readers on `close()`. The double-open race (two threads miss the cache
simultaneously) is handled: the second opener closes its reader and returns the
already-cached one.

**Measured impact:** Read p50 latency drops by the cost of 3 random seeks per
SSTable level traversed. On NVMe (100 µs seek) across 4 levels with a Bloom
miss rate of 1%: saves ~300 µs per missed key lookup.

---

### [S-03] `level_size()` calls `os.path.getsize` per file every 2 seconds
**File:** `lsm_engine/manifest.py`  
**Status:** FIXED (iteration 1)

**What breaks:** The background compaction loop calls `_check_compaction()` every
2 seconds. For each of the 6 non-L0 levels it calls `manifest.level_size(lvl)`,
which iterates every file in that level and calls `os.path.getsize()` — a syscall
per file. With 200 SSTables spread across levels that is 200 stat() calls every
2 seconds, 6 000/minute, 360 000/hour — entirely wasted I/O for data that only
changes when a compaction commits.

**Why it breaks:** File sizes are immutable after an SSTable is written. There is
no reason to re-stat them on every compaction check.

**Fix:** Added `_file_sizes: Dict[str, int]` in `Manifest`. Sizes are recorded in
`add_l0()` and `apply_compaction()` (when new output files are registered), loaded
from disk on startup in `_load()`, and removed when input files are evicted.
`level_size()` now reads from the dict — zero syscalls.

---

## PLANNED

### [S-02] Writes stall completely during memtable flush
**File:** `lsm_engine/lsm_tree.py`  
**Status:** PLANNED (iteration 2)

**What breaks:** `_flush_memtable()` is called while holding `_write_lock`. Writing
a 4 MB memtable to disk takes 50–200 ms on NVMe, up to 2 seconds on spinning
disk. During this entire window every `put()` and `delete()` blocks. At sustained
write rates (> ~500 puts/sec) the engine stalls for seconds at a time.

**Why it breaks:** The active memtable and the flush operation share the same lock.
There is no separation between "accept new writes" and "flush the old data".

**Fix (planned):** Implement an immutable memtable pattern:
1. When `size_bytes >= MEMTABLE_LIMIT`, atomically swap `_memtable → _imm_memtable`,
   create a fresh `_memtable`, and **release** `_write_lock`.
2. Flush `_imm_memtable` to disk outside the write lock (background thread or
   inline after lock release).
3. Read path must check both `_memtable` and `_imm_memtable`.
4. `_imm_memtable` is cleared only after the SSTable + MANIFEST are durable
   (Invariant 3 still holds — WAL is not truncated until MANIFEST is updated).

**Expected impact:** Write stalls eliminated for workloads where flush < inter-flush
interval. p99 write latency drops from seconds to < 1 ms.

---

### [S-04] `scan()` opens all SSTable file descriptors simultaneously
**File:** `lsm_engine/lsm_tree.py`  
**Status:** PLANNED (iteration 3)

**What breaks:** `scan()` opens one `SSTableReader` per SSTable before starting
iteration. With 100 SSTables and a default Linux `ulimit -n` of 1 024, only ~10
concurrent scans are possible before `open()` returns EMFILE. On macOS the default
is 256, making this hit even sooner.

**Why it breaks:** The heap-merge implementation requires all iterators to be
live simultaneously so it can advance whichever yields the smallest next key.

**Fix (planned):** Limit concurrent open readers with a semaphore
(`threading.Semaphore(MAX_SCAN_READERS = 128)`). Use the reader cache (already
implemented for `get()`) to share open readers between concurrent scans. For
very deep level stacks, implement a two-phase scan: first pass uses Bloom filters
to identify which SSTables overlap the requested range, then only opens those.

---

### [S-05] No group-commit WAL in Python v1
**File:** `lsm_engine/wal.py`  
**Status:** PLANNED (iteration 4)

**What breaks:** Every `put()` does a `flush()` + `fsync()` on the WAL file.
`fsync` on NVMe takes ~100 µs, on SATA SSD ~1 ms, on spinning disk ~10 ms.
With `sync_writes=True` (the safe default) throughput is capped at
1 / fsync_latency = 1 000–10 000 ops/sec regardless of CPU speed.

**Why it breaks:** Each write is committed independently. There is no batching of
multiple in-flight writes into a single fsync — a technique RocksDB calls
"group commit" and which the C++ v2 already implements (up to 128 writes/fsync,
yielding ~790× higher throughput than per-write fsync on the same hardware).

**Fix (planned):** Add a `GroupCommitWAL` class with a background flusher thread.
Writers append to an in-memory buffer and wait on a `Condition`. The flusher
wakes up every 100 µs (or when the buffer hits 128 records), does one
`flush()+fsync()`, then notifies all waiting writers. Writers return only after
their record has been fsynced. This matches the C++ v2 design exactly.

---

### [S-06] Compaction reads entire SSTable files into memory at once
**File:** `lsm_engine/compaction.py`  
**Status:** OPEN

**What breaks:** The compaction k-way merge opens all input SSTableReaders and
iterates them. Each reader loads its full sparse index into memory on open.
The data blocks are read block-by-block (good), but with 1 000 input files
(possible in L6 at large scale) the index metadata alone consumes hundreds of
megabytes.

**Fix (planned):** Partition compaction into range-bounded sub-jobs. Pick a key
midpoint, compact only SSTables whose range overlaps [start, midpoint), write
one output file, then compact [midpoint, end). This bounds per-compaction memory
to O(files_in_range × index_size) rather than O(all_files × index_size).

---

## Bottleneck Sequence (order in which each becomes binding)

| Load Level | Binding Constraint | Issue |
|---|---|---|
| > 500 puts/sec (sync) | WAL fsync per write | S-05 |
| > 1 000 gets/sec | SSTable metadata reloaded per get | **S-01 ✓ fixed** |
| Sustained writes | Write stalls during flush | S-02 |
| > 10 concurrent scans | File descriptor exhaustion | S-04 |
| > 100 M keys on disk | Compaction memory explosion | S-06 |
| Background loop | stat() calls per compaction tick | **S-03 ✓ fixed** |
