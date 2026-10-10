"""
LSM Tree Engine
================
Ties together SkipList, WAL, SSTable, Manifest, and Compaction into a
complete key-value storage engine with the following API:

    db = LSMTree('/path/to/data')
    db.put('key', 'value')
    db.get('key')                    -> 'value' | None
    db.delete('key')
    db.scan('a', 'z')                -> Iterator[(key, value)]
    db.prefix_scan('user:')          -> Iterator[(key, value)]
    db.write(WriteBatch(...))        -> None  (atomic multi-key write)
    db.close()

Crash-safety invariants (see docs/invariants.md for full specification):
  1. WAL Completeness  – WAL is written before memtable is updated.
  2. Compaction Atomicity – MANIFEST update is the compaction commit point.
  3. WAL Truncation Safety – WAL truncated only after MANIFEST records the SSTable.

Scaling fixes applied (see SCALING_ISSUES.md):
  S-01 SSTableReader cache  – readers stay open; no per-get file metadata reload.
  S-02 Immutable memtable   – writes rotate to a fresh memtable instantly; flush
                               happens in the background thread, not under the
                               write lock.
  S-03 Manifest file-size cache – level_size() reads from a dict, not stat().
  S-04 Scan fd semaphore    – at most MAX_SCAN_FDS SSTable file descriptors are
                               open concurrently during any scan.
  S-05 Group-commit WAL     – up to group_commit_max records share one fsync.
"""

import concurrent.futures
import heapq
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .block_cache  import BlockCache
from .bloom_filter import BloomFilter
from .compaction   import compact
from .cursor       import Cursor
from .manifest     import Manifest, MAX_LEVELS
from .snapshot     import Snapshot
from .metrics      import EngineMetrics
from .skip_list    import SkipList
from .sstable      import SSTableReader, SSTableWriter
from .ttl          import encode as _ttl_encode, decode as _ttl_decode, is_expired as _ttl_expired
from .wal          import WAL, OpType
from .write_batch  import WriteBatch
from ._utils       import fsync_fd, fsync_dir, RateLimiter

class ReadOnlyError(Exception):
    """Raised when a write operation is attempted on a read-only LSMTree."""


MEMTABLE_LIMIT      = 4 * 1024 * 1024   # 4 MB before flush
L0_COMPACT_TRIGGER  = 4                  # trigger L0→L1 compaction
# Bloom filter FPR by destination level.
# Shallower levels (L0-L2) are accessed frequently → keep FPR low.
# Deeper levels (L5-L6) have many files and binary search narrows candidates;
# a higher FPR saves memory at the cost of occasional extra disk reads.
_BLOOM_FPR_BY_LEVEL = {0: 0.01, 1: 0.01, 2: 0.01, 3: 0.02, 4: 0.05, 5: 0.10, 6: 0.10}
L0_SLOWDOWN_TRIGGER = 8                  # start slowing writes (1 ms per extra file)
L0_STOP_TRIGGER     = 12                 # stall writes completely until L0 drains
BASE_LEVEL_BYTES    = 10 * 1024 * 1024  # 10 MB budget for L1
LEVEL_MULTIPLIER    = 10                 # each level is 10× larger than the previous
MAX_SCAN_FDS        = 64                 # max SSTable fds open at once during a scan


# ── Merge helper ──────────────────────────────────────────────────────────────

@dataclass(order=True)
class _E:
    key:  bytes
    pri:  int
    val:  bytes = field(compare=False, default=b'')
    tomb: bool  = field(compare=False, default=False)
    it:   Any   = field(compare=False, default=None)


# ── helpers ───────────────────────────────────────────────────────────────────

def _b(x) -> bytes:
    return x.encode() if isinstance(x, str) else x

def _s(x) -> Optional[str]:
    if x is None:
        return None
    return x.decode() if isinstance(x, bytes) else x

def _prefix_end(prefix: bytes) -> Optional[bytes]:
    """Smallest bytes value that is greater than every key starting with prefix."""
    ba = bytearray(prefix)
    for i in range(len(ba) - 1, -1, -1):
        if ba[i] < 0xFF:
            ba[i] += 1
            return bytes(ba[:i + 1])
    return None  # all 0xFF — no upper bound


# ── Engine ────────────────────────────────────────────────────────────────────

class LSMTree:
    def __init__(self, directory: str, sync_writes: bool = True,
                 compaction_rate_bytes_per_sec: float = 0,
                 compaction_threads: int = 2,
                 block_cache_bytes: int = 8 * 1024 * 1024,
                 compaction_filter=None,
                 compression: str = 'none',
                 memtable_size_bytes: int = 4 * 1024 * 1024,
                 prefix_compression: bool = True,
                 l0_compact_size_bytes: int = 0,
                 read_only: bool = False,
                 on_flush=None,
                 on_compaction=None):
        """
        sync_writes=True  (default) — group-commit WAL fsync.
        sync_writes=False — no fsync; OS decides when to flush (benchmarks only).
        compaction_rate_bytes_per_sec — throttle compaction I/O (0 = unlimited).
        compaction_threads — parallel compaction workers (default 2).
        block_cache_bytes — LRU cache for 4 KB SSTable data blocks (default 8 MB).
        compaction_filter — callable(key: bytes, value: bytes, level: int) → bytes|None.
        compression: 'none' (default) or 'zlib'.
            When 'zlib', each SSTable data block is compressed before writing.
            Reduces disk usage by 30-90% for structured / JSON data.
        memtable_size_bytes: flush threshold for the active memtable (default 4 MB).
        prefix_compression: store key suffixes only within blocks — up to 89%
            key storage reduction for clustered keys.  Default True.
        l0_compact_size_bytes: also trigger L0 compaction when total L0 size
            exceeds this threshold (0 = count-only trigger, default).
        read_only: open in read-only mode (no WAL, no writes, no compaction).
        on_flush: optional callable(bytes_written: int, level: int) called after
            each memtable flush completes.  level is always 0 (L0 SSTable).
            Called from the background thread — must be thread-safe.
        on_compaction: optional callable(src_level: int, dst_level: int,
            bytes_in: int, bytes_out: int) called after each compaction completes.
            Called from a compaction worker thread — must be thread-safe.
        """
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

        self._lock        = threading.Lock()
        self._write_lock  = threading.Lock()

        self._memtable    = SkipList()
        self._manifest    = Manifest(directory)
        self._wal         = WAL(os.path.join(directory, 'wal.log'),
                                sync_writes=sync_writes)

        # Immutable memtable: populated on rotation, flushed by background thread.
        # Reads check both _memtable and _imm so nothing is missed mid-flush.
        self._imm: Optional[SkipList] = None
        self._imm_flush_lock = threading.Lock()   # one flush at a time
        self._imm_pending    = threading.Event()  # wakes bg thread immediately

        # SSTableReader cache: holds open fds + index + Bloom filter in memory.
        # Evicted when compaction removes files; drained on close().
        self._reader_cache: Dict[str, SSTableReader] = {}
        self._cache_lock   = threading.Lock()

        # Block cache: LRU cache for 4 KB data blocks across all open SSTables.
        # Shared across all SSTableReaders; evicted per-path on compaction.
        self._block_cache = BlockCache(block_cache_bytes)

        # Scan fd semaphore: prevents a single scan from exhausting the OS fd limit.
        self._scan_sem = threading.Semaphore(MAX_SCAN_FDS)

        # Compaction I/O rate limiter (unlimited when rate == 0).
        self._compaction_limiter  = RateLimiter(compaction_rate_bytes_per_sec)
        # Optional user-defined compaction filter.
        self._compaction_filter   = compaction_filter
        # SSTable compression: 'none' or 'zlib'.
        self._compression         = compression
        # SSTable prefix key compression (default True).
        self._prefix_compression  = prefix_compression
        # Memtable flush threshold (configurable; default 4 MB).
        self._memtable_limit      = memtable_size_bytes
        # Optional L0 size-based compact trigger (0 = disabled).
        self._l0_compact_size     = l0_compact_size_bytes
        # Read-only flag: disables all write paths.
        self._read_only           = read_only
        # Event hooks (called from background threads — must be thread-safe).
        self._on_flush       = on_flush
        self._on_compaction  = on_compaction

        # Event signalled after each L0 compaction so stalled writers wake up
        # immediately instead of polling with time.sleep().
        self._l0_drained = threading.Event()

        # Levels known to be sorted by first_key (enables binary search in get()).
        self._sorted_levels: set = set(self._manifest._sorted_levels)
        # Pre-built sorted (first_key, path) list per level.
        # Binary search uses this directly — zero _get_reader() calls during
        # comparison, avoiding _cache_lock contention on every search step.
        self._level_key_index: Dict[int, List[Tuple[bytes, str]]] = {}

        # Parallel compaction: skipped in read-only mode.
        if not read_only:
            self._compaction_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, compaction_threads),
                thread_name_prefix='lsmkv-compact',
            )
        else:
            self._compaction_executor = None  # not used in read-only mode
        self._compaction_locks = {lvl: threading.Lock() for lvl in range(MAX_LEVELS)}
        self._compaction_futures: Dict[int, concurrent.futures.Future] = {}
        self._compaction_fut_lock = threading.Lock()

        # Live metrics, accessible via db.metrics or included in db.stats().
        self.metrics = EngineMetrics()

        if not read_only:
            self._cleanup_orphans()
            self._recover()
            self._shutdown = threading.Event()
            self._bg = threading.Thread(target=self._bg_loop, daemon=True)
            self._bg.start()
        else:
            # Read-only: no background thread, no WAL recovery needed.
            # SSTables in the MANIFEST are the authoritative data source.
            self._shutdown = threading.Event()   # needed for close()

    # ── public API ─────────────────────────────────────────────────────────────

    def _check_writable(self):
        if self._read_only:
            raise ReadOnlyError(
                "This LSMTree was opened in read-only mode.  "
                "Use LSMTree(directory, read_only=False) to enable writes."
            )

    def _maybe_stall_writes(self):
        """Apply back-pressure if L0 is accumulating faster than compaction drains it.

        L0 slowdown (>= L0_SLOWDOWN_TRIGGER files): sleep 1 ms per extra file.
        L0 stop      (>= L0_STOP_TRIGGER files):    block until L0 drains below stop.

        The stop-stall uses event-based wakeup instead of a busy-poll loop.
        `_l0_drained` is signalled after each L0→L1 compaction completes,
        waking all blocked writers immediately rather than every 5 ms.
        This reduces CPU usage by ~100× under heavy write load with stalls.
        """
        with self._lock:
            n = len(self._manifest.levels[0])
        if n >= L0_STOP_TRIGGER:
            while True:
                # Wait for a L0 compaction to complete (or 100ms timeout as fallback)
                self._l0_drained.wait(timeout=0.1)
                self._l0_drained.clear()
                with self._lock:
                    n = len(self._manifest.levels[0])
                if n < L0_STOP_TRIGGER:
                    break
        elif n >= L0_SLOWDOWN_TRIGGER:
            time.sleep((n - L0_SLOWDOWN_TRIGGER + 1) * 0.001)

    def put(self, key, value, ttl_seconds: float = 0):
        """Insert or overwrite a key.

        ttl_seconds > 0 — the key expires that many seconds from now.
        Expired keys return None from get() and are skipped by scan().
        They are physically removed during deep compaction.

        Group-commit optimisation: the WAL record is submitted inside the
        write-lock (non-blocking) then the lock is released before waiting
        for the fsync event.  This lets concurrent writers overlap their WAL
        submissions so the gc-thread can batch them in one fsync.
        """
        self._check_writable()
        _t0 = time.perf_counter()
        self._maybe_stall_writes()
        kb = _b(key)
        vb = _b(value)
        if ttl_seconds > 0:
            vb = _ttl_encode(vb, time.time() + ttl_seconds)
        with self._write_lock:
            _done = self._wal.submit_put(kb, vb)   # non-blocking submit
            self._memtable.put(kb, vb)
            self.metrics.inc(puts=1, wal_records=1,
                             bytes_written_user=len(kb) + len(vb))
            if self._memtable.size_bytes >= self._memtable_limit:
                self._maybe_rotate()
        if _done:
            _done.wait()   # wait OUTSIDE write-lock — allows concurrent batching
        self.metrics.put_latency.record((time.perf_counter() - _t0) * 1_000_000)

    def delete(self, key):
        self._check_writable()
        self._maybe_stall_writes()
        kb = _b(key)
        with self._write_lock:
            _done = self._wal.submit_delete(kb)
            self._memtable.delete(kb)
            self.metrics.inc(deletes=1, wal_records=1,
                             bytes_written_user=len(kb))
            if self._memtable.size_bytes >= self._memtable_limit:
                self._maybe_rotate()
        if _done:
            _done.wait()

    def write(self, batch: WriteBatch):
        """Apply a WriteBatch atomically: one lock, one WAL fsync, all memtable inserts."""
        self._check_writable()
        if len(batch) == 0:
            return
        self._maybe_stall_writes()
        ops = list(batch)
        user_bytes = sum(len(k) + len(v) for _, k, v in ops)
        with self._write_lock:
            _done = self._wal.submit_batch([(op, key, value) for op, key, value in ops])
            for op, key, value in ops:
                if int(op) == 1:   # PUT
                    self._memtable.put(key, value)
                else:              # DELETE
                    self._memtable.delete(key)
            self.metrics.inc(batches=1, batch_ops=len(ops), wal_records=len(ops),
                             bytes_written_user=user_bytes)
            if self._memtable.size_bytes >= self._memtable_limit:
                self._maybe_rotate()
        if _done:
            _done.wait()

    def get(self, key) -> Optional[str]:
        _t0  = time.perf_counter()
        result = self._get(key)
        self.metrics.get_latency.record((time.perf_counter() - _t0) * 1_000_000)
        return result

    def _get(self, key) -> Optional[str]:
        kb = _b(key)
        self.metrics.inc(gets=1)

        # 1. Active memtable
        r = self._memtable.get(kb)
        if r is not None:
            val, deleted = r
            if deleted:
                self.metrics.inc(get_misses=1)
                return None
            val, exp = _ttl_decode(val)
            if _ttl_expired(exp):
                self.metrics.inc(get_misses=1)
                return None
            self.metrics.inc(get_hits=1)
            return _s(val)

        # 2. Immutable memtable (being flushed in background)
        imm = self._imm
        if imm is not None:
            r = imm.get(kb)
            if r is not None:
                val, deleted = r
                if deleted:
                    self.metrics.inc(get_misses=1)
                    return None
                val, exp = _ttl_decode(val)
                if _ttl_expired(exp):
                    self.metrics.inc(get_misses=1)
                    return None
                self.metrics.inc(get_hits=1)
                return _s(val)

        # 3. SSTables — snapshot levels to avoid holding lock during I/O
        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]

        for lvl_idx, lvl in enumerate(levels):
            if lvl_idx > 0 and lvl_idx in self._sorted_levels and lvl:
                # Binary search: O(log n) comparisons for sorted L1+ levels.
                path = self._binary_search_level(lvl_idx, lvl, kb)
                if path is None:
                    continue
                rdr = self._get_reader(path)
                if rdr is None:
                    continue
                self.metrics.inc(sstable_reads=1)
                try:
                    hit = rdr.get(kb)
                    if hit is not None:
                        val, tombstone = hit
                        if tombstone:
                            self.metrics.inc(get_misses=1)
                            return None
                        val, exp = _ttl_decode(val)
                        if _ttl_expired(exp):
                            self.metrics.inc(get_misses=1)
                            return None
                        self.metrics.inc(get_hits=1)
                        return _s(val)
                except Exception:
                    continue
            else:
                # Linear scan with key-range pre-filter (L0 + unsorted levels).
                order = reversed(lvl) if lvl_idx == 0 else iter(lvl)
                for path in order:
                    rdr = self._get_reader(path)
                    if rdr is None:
                        continue
                    fk = rdr.first_key
                    lk = rdr.last_key
                    if fk is not None and lk is not None and (kb < fk or kb > lk):
                        self.metrics.inc(range_skips=1)
                        continue
                    self.metrics.inc(sstable_reads=1)
                    try:
                        hit = rdr.get(kb)
                        if hit is not None:
                            val, tombstone = hit
                            if tombstone:
                                self.metrics.inc(get_misses=1)
                                return None
                            val, exp = _ttl_decode(val)
                            if _ttl_expired(exp):
                                self.metrics.inc(get_misses=1)
                                return None
                            self.metrics.inc(get_hits=1)
                            return _s(val)
                    except Exception:
                        continue

        self.metrics.inc(get_misses=1)
        return None

    def update(self, key, fn, ttl_seconds: float = 0):
        """Atomically apply fn(current_value) → new_value.  Raises ReadOnlyError if read_only.

        The entire read-compute-write is serialized under the write lock so no
        concurrent writer can interleave.  Concurrent readers will see either
        the old value or the new value — never a partial state.

        fn(current) receives the current value (str) or None if the key is
        absent/deleted.  It must return the new value (str/bytes) or None to
        delete the key.

        Returns the new value written (or None for a delete).

        Example — atomic counter increment:
            db.update('hits', lambda v: str(int(v or 0) + 1))
        """
        self._check_writable()
        _done = None
        with self._write_lock:
            current = self.get(key)
            new_val = fn(current)
            kb = _b(key)
            if new_val is None:
                _done = self._wal.submit_delete(kb)
                self._memtable.delete(kb)
                self.metrics.inc(deletes=1, wal_records=1, bytes_written_user=len(kb))
            else:
                vb = _b(new_val)
                if ttl_seconds > 0:
                    vb = _ttl_encode(vb, time.time() + ttl_seconds)
                _done = self._wal.submit_put(kb, vb)
                self._memtable.put(kb, vb)
                self.metrics.inc(puts=1, wal_records=1,
                                 bytes_written_user=len(kb) + len(vb))
            if self._memtable.size_bytes >= self._memtable_limit:
                self._maybe_rotate()
        if _done:
            _done.wait()
        return new_val

    def compare_and_swap(self, key, expected, new_value) -> bool:
        self._check_writable()
        """Atomic compare-and-swap: set key=new_value only if current == expected.

        Returns True if the swap occurred; False if the value didn't match.
        expected=None matches a missing or deleted key.
        new_value=None deletes the key on a successful swap.

        Example — update only if no concurrent write happened:
            if db.compare_and_swap('lock', None, 'owner_id'):
                ...  # acquired the lock
        """
        _done = None
        with self._write_lock:
            current = self.get(key)
            if current != expected:
                return False
            kb = _b(key)
            if new_value is None:
                _done = self._wal.submit_delete(kb)
                self._memtable.delete(kb)
                self.metrics.inc(deletes=1, wal_records=1, bytes_written_user=len(kb))
            else:
                vb = _b(new_value)
                _done = self._wal.submit_put(kb, vb)
                self._memtable.put(kb, vb)
                self.metrics.inc(puts=1, wal_records=1,
                                 bytes_written_user=len(kb) + len(vb))
            if self._memtable.size_bytes >= self._memtable_limit:
                self._maybe_rotate()
        if _done:
            _done.wait()
        return True

    def ttl_remaining(self, key) -> Optional[float]:
        """Return seconds until key expires, or None if no TTL set or key absent.

        Returns a negative number if the key exists with an expired TTL that
        has not yet been purged by compaction.

        Example:
            remaining = db.ttl_remaining('session:abc')
            if remaining is not None and remaining < 300:
                db.touch('session:abc', ttl_seconds=3600)  # renew
        """
        kb = _b(key)

        def _extract(val, deleted) -> Optional[float]:
            if deleted:
                return 'TOMB'   # sentinel
            _, exp = _ttl_decode(val)
            return None if exp is None else exp - time.time()

        # 1. Active memtable
        r = self._memtable.get(kb)
        if r is not None:
            result = _extract(*r)
            return None if result == 'TOMB' else result

        # 2. Immutable memtable
        imm = self._imm
        if imm is not None:
            r = imm.get(kb)
            if r is not None:
                result = _extract(*r)
                return None if result == 'TOMB' else result

        # 3. SSTables — same traversal as _get() but returns TTL info
        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]

        for lvl_idx, lvl in enumerate(levels):
            if lvl_idx > 0 and lvl_idx in self._sorted_levels and lvl:
                path = self._binary_search_level(lvl_idx, lvl, kb)
                if path is None:
                    continue
                rdr = self._get_reader(path)
                if rdr is None:
                    continue
                try:
                    hit = rdr.get(kb)
                    if hit is not None:
                        val, tombstone = hit
                        if tombstone:
                            return None
                        _, exp = _ttl_decode(val)
                        return None if exp is None else exp - time.time()
                except Exception:
                    continue
            else:
                order = reversed(lvl) if lvl_idx == 0 else iter(lvl)
                for path in order:
                    rdr = self._get_reader(path)
                    if rdr is None:
                        continue
                    fk, lk = rdr.first_key, rdr.last_key
                    if fk and lk and (kb < fk or kb > lk):
                        continue
                    try:
                        hit = rdr.get(kb)
                        if hit is not None:
                            val, tombstone = hit
                            if tombstone:
                                return None
                            _, exp = _ttl_decode(val)
                            return None if exp is None else exp - time.time()
                    except Exception:
                        continue
        return None

    def size_on_disk(self) -> int:
        """Return the total bytes used by all SSTable files on disk.

        This is the committed (flushed) data only — the active memtable and
        WAL are not included.  Use stats()['wal_bytes'] for WAL size.

        Example:
            print(f'Database is {db.size_on_disk() / 1024 / 1024:.1f} MB')
        """
        with self._lock:
            return sum(
                self._manifest._file_sizes.get(f, 0)
                for lvl in self._manifest.levels
                for f in lvl
            )

    def has(self, key) -> bool:
        """Return True if key exists and has not expired.

        Equivalent to `get(key) is not None` but communicates intent clearly.
        The Bloom filter and key-range pre-filter are still applied — the only
        difference from get() is the None/non-None check at the end.
        """
        return self.get(key) is not None

    def touch(self, key, ttl_seconds: float) -> bool:
        """Extend (or add) a TTL to an existing key without changing its value.

        Returns True if the key existed and the TTL was updated.
        Returns False if the key was absent (no new key is created).

        Example:
            db.put('session:abc', token, ttl_seconds=3600)
            # ... user is active ...
            db.touch('session:abc', ttl_seconds=3600)   # slide expiry window
        """
        self._check_writable()
        existed = [False]

        def _touch(v):
            if v is not None:
                existed[0] = True
                return v   # same value, re-encoded with new TTL by update()
            return None   # absent — don't create

        self.update(key, _touch, ttl_seconds=ttl_seconds)
        return existed[0]

    def atomic_append(self, key, item, separator: str = ',') -> str:
        """Atomically append item to a string list stored at key.

        If the key doesn't exist, sets it to item.  Otherwise appends
        separator + item.  Returns the new value.

        Example:
            db.atomic_append('tags:post1', 'python')
            db.atomic_append('tags:post1', 'storage')
            db.get('tags:post1')   # 'python,storage'
        """
        sep = separator

        def _append(v):
            return item if v is None else f'{v}{sep}{item}'

        return self.update(key, _append)

    def pop(self, key, default=None):
        """Atomically get and delete a key.  Returns the current value or default.

        Equivalent to `value = db.get(key); db.delete(key); return value`,
        but atomic — no other writer can interleave.

        Example — dequeue from a persistent queue:
            item = db.pop('queue:001')
        """
        self._check_writable()
        result = [default]

        def _pop(v):
            result[0] = v if v is not None else default
            return None   # delete the key

        self.update(key, _pop)
        return result[0]

    def setdefault(self, key, default_value) -> str:
        """Return the value for key, inserting default_value if key is absent.

        Atomic: if two threads call setdefault() concurrently on the same
        missing key, exactly one sets the value; both return the same result.

        Example:
            db.setdefault('config:theme', 'dark')  # sets if absent
            db.setdefault('config:theme', 'light') # returns 'dark' (already set)
        """
        self._check_writable()
        result = [default_value]

        def _setdefault(v):
            if v is not None:
                result[0] = v
                return v   # keep existing value
            return default_value   # set default

        self.update(key, _setdefault)
        return result[0]

    def increment(self, key, amount: int = 1) -> int:
        self._check_writable()
        """Atomically add amount to an integer value.  Returns the new value.

        If the key doesn't exist, treats the current value as 0.
        Raises ValueError if the existing value is not a valid integer string.

        Example:
            db.increment('page_views')       # +1
            db.increment('score', amount=10) # +10
            db.increment('balance', -5)      # -5
        """
        def _inc(v):
            return str(int(v or '0') + amount)
        result = self.update(key, _inc)
        return int(result)

    def scan(self,
             start = None,
             end   = None) -> Iterator[Tuple[str, str]]:
        """Range scan yielding (key, value), tombstones excluded.
        Caps open SSTable fds at MAX_SCAN_FDS via a semaphore.
        """
        self.metrics.inc(scans=1)
        sb = _b(start) if start else None
        eb = _b(end)   if end   else None

        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]

        imm = self._imm
        sources: List[Any] = [self._memtable.scan(sb, eb)]
        if imm is not None:
            sources.append(imm.scan(sb, eb))

        readers: List[SSTableReader] = []

        for lvl_idx, lvl in enumerate(levels):
            order = list(reversed(lvl)) if lvl_idx == 0 else lvl
            for path in order:
                if not os.path.exists(path):
                    continue
                # Key-range pre-filter for scan: skip reader if entirely outside [sb, eb)
                cached = self._reader_cache.get(path)
                if cached is not None:
                    fk = cached.first_key
                    lk = cached.last_key
                    if fk and lk:
                        if (eb is not None and fk >= eb) or (sb is not None and lk < sb):
                            self.metrics.inc(range_skips=1)
                            continue
                self._scan_sem.acquire()
                try:
                    # If a cached reader exists, borrow its index + Bloom filter
                    # to avoid 3 redundant disk seeks (footer, index, bloom).
                    template = self._reader_cache.get(path)
                    rdr = SSTableReader(path, block_cache=self._block_cache,
                                        _template=template)
                    readers.append(rdr)
                    sources.append(rdr.scan(sb, eb))
                except Exception:
                    self._scan_sem.release()

        entries = 0
        try:
            heap: list = []
            for pri, it in enumerate(sources):
                try:
                    k, v, d = next(it)
                    heapq.heappush(heap, _E(k, pri, v or b'', d, it))
                except StopIteration:
                    pass

            last: Optional[bytes] = None
            while heap:
                e = heapq.heappop(heap)
                try:
                    k, v, d = next(e.it)
                    heapq.heappush(heap, _E(k, e.pri, v or b'', d, e.it))
                except StopIteration:
                    pass
                if e.key == last:
                    continue
                last = e.key
                if e.tomb:
                    continue
                val, exp = _ttl_decode(e.val)
                if _ttl_expired(exp):
                    continue
                entries += 1
                yield _s(e.key), _s(val)
        finally:
            self.metrics.inc(scan_entries=entries)
            for rdr in readers:
                try:
                    rdr.close()
                except Exception:
                    pass
                self._scan_sem.release()

    def prefix_scan(self, prefix) -> Iterator[Tuple[str, str]]:
        """Yield all (key, value) pairs where key starts with prefix, sorted."""
        pb  = _b(prefix)
        end = _prefix_end(pb)
        return self.scan(
            _s(pb)  if pb  else None,
            _s(end) if end else None,
        )

    def scan_keys(self,
                  start = None,
                  end   = None) -> Iterator[str]:
        """Like scan() but yields only keys — no value fetch overhead."""
        for key, _ in self.scan(start, end):
            yield key

    def get_many(self, keys) -> Dict[str, Optional[str]]:
        """Retrieve multiple keys in one call.

        Returns a dict mapping each requested key to its value (str) or None
        if the key is absent, deleted, or TTL-expired.

        Example:
            results = db.get_many(['user:1', 'user:2', 'user:3'])
            # {'user:1': 'Alice', 'user:2': None, 'user:3': 'Carol'}
        """
        return {k: self.get(k) for k in keys}

    def backup(self, backup_dir: str) -> dict:
        """Create a consistent hot backup of all flushed SSTables.

        Flushes the memtable first so the backup includes the latest writes,
        then captures a snapshot and copies all SSTable files + MANIFEST into
        backup_dir.  The backup directory is a valid LSMTree database:
            restored = LSMTree(backup_dir)

        Returns {'files': int, 'bytes': int} with the backup totals.
        No data is lost or corrupted if a crash happens during the copy
        because the source files are immutable SSTables.
        """
        import shutil as _shutil
        self.flush()
        snap = self.snapshot()
        os.makedirs(backup_dir, exist_ok=True)

        files = 0
        total_bytes = 0
        for lvl in snap._levels:
            for path in lvl:
                if not os.path.exists(path):
                    continue
                dest = os.path.join(backup_dir, os.path.basename(path))
                _shutil.copy2(path, dest)
                total_bytes += os.path.getsize(dest)
                files += 1

        # Copy MANIFEST so the backup directory is self-contained
        manifest_src = os.path.join(self.dir, 'MANIFEST.json')
        if os.path.exists(manifest_src):
            _shutil.copy2(manifest_src, backup_dir)

        snap.close()
        return {'files': files, 'bytes': total_bytes}

    def wait_for_compaction(self, timeout_seconds: float = 60) -> bool:
        """Block until all scheduled compaction jobs finish.

        Returns True if all compactions completed within the timeout, False
        if the timeout expired with jobs still running.  Useful in tests and
        benchmarks where a known-clean state is required before measuring reads.
        """
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._compaction_fut_lock:
                still_running = [f for f in self._compaction_futures.values()
                                 if not f.done()]
            if not still_running:
                return True
            time.sleep(0.05)
        return False

    def compact_all(self, timeout_seconds: float = 300) -> dict:
        self._check_writable()
        """Flush all in-memory data and fully compact all levels.  Blocks until done.

        Useful before a backup, before performance benchmarking of read-heavy
        workloads, or to pre-compact a database before handing it to consumers.

        Algorithm:
          1. flush() — move memtable + imm to L0 SSTables.
          2. Repeat: schedule all eligible compactions, wait for them to finish,
             until no more compactions are triggered.

        Returns {'compactions_run': int, 'elapsed_seconds': float}.
        """
        t0 = time.monotonic()
        self.flush()

        compactions_before = self.metrics.snapshot()['compactions']
        while True:
            self._schedule_compactions()
            self.wait_for_compaction(timeout_seconds)
            compactions_after = self.metrics.snapshot()['compactions']
            if compactions_after == compactions_before:
                break   # no new compactions — fully compacted
            compactions_before = compactions_after

        return {
            'compactions_run':   self.metrics.snapshot()['compactions'],
            'elapsed_seconds':   round(time.monotonic() - t0, 2),
        }

    def filter_scan(self, filter_fn, start=None, end=None
                    ) -> Iterator[Tuple[str, str]]:
        """Scan and yield only (key, value) pairs that satisfy filter_fn.

        filter_fn(key: str, value: str) → bool.  Called for each live entry;
        entries where it returns False are skipped.  Tombstones and expired
        TTL keys are never passed to filter_fn.

        Example — find all users with a specific status:
            for key, val in db.filter_scan(
                lambda k, v: '"status":"active"' in v,
                start='user:', end='user;'):
                process(key, val)
        """
        for key, value in self.scan(start, end):
            if filter_fn(key, value):
                yield key, value

    def reverse_scan(self, start=None, end=None) -> Iterator[Tuple[str, str]]:
        """Scan in reverse sorted order (largest key first).

        Equivalent to `reversed(list(scan(start, end)))` but expressed as an
        iterator.  O(n) memory — all matching entries are buffered before
        any are yielded.  For very large ranges, prefer forward scan + client-
        side reversal or use `Cursor.seek_to_last()` for single-entry lookups.

        Example — most recent orders first:
            for key, val in db.reverse_scan('order:', 'order;'):
                display(key, val)
        """
        results = list(self.scan(start, end))
        yield from reversed(results)

    def count(self, start=None, end=None) -> int:
        """Return the exact number of live keys in [start, end).

        O(n) — scans the full range.  For large databases, prefer
        `estimate_key_count()` (O(levels)) when an approximate answer suffices.

        Example:
            total = db.count()
            active = db.count('user:', 'user;')
        """
        return sum(1 for _ in self.scan_keys(start, end))

    def key_stats(self, sample_size: int = 1000) -> dict:
        """Sample up to sample_size live entries and return size statistics.

        Useful for capacity planning and understanding storage layout.
        Returns key/value size distribution plus estimated total entries.

        Example:
            stats = db.key_stats(10_000)
            print(f'avg value size: {stats["val_size"]["avg"]:.0f} bytes')
        """
        key_sizes, val_sizes = [], []
        for key, val in self.scan():
            key_sizes.append(len(key.encode() if isinstance(key, str) else key))
            val_sizes.append(len(val.encode() if isinstance(val, str) else val)
                             if val is not None else 0)
            if len(key_sizes) >= sample_size:
                break
        n = len(key_sizes)
        if n == 0:
            return {'sampled': 0, 'key_size': {}, 'val_size': {}}

        def _stat(values):
            return {
                'min': min(values),
                'max': max(values),
                'avg': round(sum(values) / len(values), 1),
                'total': sum(values),
            }

        return {
            'sampled':   n,
            'key_size':  _stat(key_sizes),
            'val_size':  _stat(val_sizes),
        }

    def export(self, path: str, format: str = 'jsonl',
               start=None, end=None) -> int:
        """Export all live keys to a file.  Returns the number of entries written.

        format='jsonl': one JSON object per line — `{"k":"key","v":"value"}`.
        The output file is human-readable and suitable for migration, seeding
        test databases, or archiving a key range.

        Example:
            db.export('/tmp/users.jsonl', start='user:', end='user;')
        """
        import json as _json
        count = 0
        with open(path, 'w') as f:
            for key, value in self.scan(start, end):
                f.write(_json.dumps({'k': key, 'v': value}) + '\n')
                count += 1
        return count

    def import_(self, path: str, format: str = 'jsonl',
                batch_size: int = 1000,
                ttl_seconds: float = 0) -> int:
        """Import entries from a file previously exported with export().
        Returns the number of entries imported.

        Entries are inserted atomically in batches of batch_size for efficiency.
        If ttl_seconds > 0, all imported keys expire that many seconds from now.

        Example:
            db.import_('/tmp/users.jsonl')
        """
        self._check_writable()
        import json as _json
        count = 0
        batch = WriteBatch()

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = _json.loads(line)
                key, value = obj['k'], obj['v']
                if ttl_seconds > 0:
                    batch.put_ttl(key, value, ttl_seconds)
                else:
                    batch.put(key, value)
                count += 1
                if len(batch) >= batch_size:
                    self.write(batch)
                    batch = WriteBatch()

        if batch:
            self.write(batch)
        return count

    def clear(self, prefix: str = '') -> int:
        """Delete all keys (or all keys starting with prefix).  Returns count deleted.

        With no argument: deletes every key in the database.
        With a prefix: equivalent to delete_prefix(prefix).

        Example:
            db.clear('session:')   # clear all sessions
            db.clear()             # wipe the database
        """
        self._check_writable()
        if prefix:
            return self.delete_prefix(prefix)
        # Delete everything: scan all keys and batch-delete
        batch = WriteBatch()
        count = 0
        for key, _ in self.scan():
            batch.delete(key)
            count += 1
            if len(batch) >= 1000:
                self.write(batch)
                batch = WriteBatch()
        if batch:
            self.write(batch)
        return count

    def memory_usage(self) -> dict:
        """Estimate current in-memory footprint broken down by component.

        All values are in bytes.  'total_estimated' is the sum of the
        individual components; actual RSS may differ due to Python object
        overhead and allocator fragmentation.
        """
        memtable_b  = self._memtable.size_bytes
        imm_b       = self._imm.size_bytes if self._imm else 0
        bc_b        = self._block_cache.stats()['block_cache_bytes']

        # Reader cache: each open reader holds its sparse index + Bloom filter.
        index_b = 0
        bloom_b = 0
        with self._cache_lock:
            readers = list(self._reader_cache.values())
        for rdr in readers:
            if hasattr(rdr, '_index'):
                # Each entry: first_key bytes + 2 int64 + 1 int32 = key_len + 20
                index_b += sum(len(fk) + 20 for fk, _, _ in rdr._index)
            if hasattr(rdr, '_bloom'):
                bloom_b += len(rdr._bloom.bits)

        # Level key index: (first_key, path) pairs per level
        lki_b = sum(
            sum(len(fk) + len(p) + 8 for fk, p in pairs)
            for pairs in self._level_key_index.values()
        )

        breakdown = {
            'memtable_bytes':          memtable_b,
            'imm_memtable_bytes':      imm_b,
            'block_cache_bytes':       bc_b,
            'reader_cache_index_bytes':index_b,
            'reader_cache_bloom_bytes':bloom_b,
            'level_key_index_bytes':   lki_b,
        }
        breakdown['total_estimated']  = sum(breakdown.values())
        return breakdown

    def iter_batches(self,
                     batch_size: int = 100,
                     start = None,
                     end   = None):
        """Yield lists of (key, value) pairs in sorted order, `batch_size` at a time.

        Useful for bulk processing, ETL, or streaming exports without loading
        all keys into memory at once.

        Example — export in pages of 1000:
            for batch in db.iter_batches(batch_size=1000, start='user:'):
                write_to_warehouse(batch)
        """
        batch = []
        for kv in self.scan(start, end):
            batch.append(kv)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def flush(self):
        self._check_writable()
        """Force-flush the active memtable (and any pending immutable) to disk.

        Useful before taking a snapshot to ensure all recent writes are
        included, or before a graceful shutdown checkpoint.  Blocks until
        the flush is complete and the MANIFEST is updated.  The WAL is
        truncated after the flush.
        """
        with self._write_lock:
            if self._imm is not None:
                self._flush_imm()
            if len(self._memtable) > 0:
                self._flush_memtable()

    def snapshot(self) -> Snapshot:
        """Return a point-in-time read-only view of all flushed SSTables.

        The snapshot captures the set of SSTable files at this instant.
        Concurrent writes after the snapshot are invisible to it.
        The active memtable is NOT included unless flush() is called first.

        Example:
            db.flush()                    # include latest writes
            with db.snapshot() as snap:
                v = snap.get('key')
                for k, v in snap.scan():
                    ...
        """
        return Snapshot(self)

    def cursor(self) -> Cursor:
        """Return a new stateful Cursor over this database.

        The cursor starts in an invalid state; call seek() or seek_to_first()
        before reading.  Use as a context manager to ensure cleanup:

            with db.cursor() as cur:
                cur.seek('start_key')
                while cur.valid():
                    process(cur.key(), cur.value())
                    cur.next()
        """
        return Cursor(self)

    def delete_prefix(self, prefix) -> int:
        self._check_writable()
        """Delete all keys starting with prefix.  Returns the number of keys deleted.

        Implemented as a single WriteBatch so all deletes are applied atomically
        with one WAL fsync.  Scans the prefix range first to collect live keys,
        then writes tombstones for each.

        For large namespaces (millions of keys) this is memory-efficient because
        the batch is built lazily from the scan iterator.
        """
        batch = WriteBatch()
        for key, _ in self.prefix_scan(prefix):
            batch.delete(key)
        n = len(batch)
        if n > 0:
            self.write(batch)
        return n

    def stats_report(self) -> str:
        """Return a formatted multi-line string summarising engine state.

        Useful for dashboards, log lines, or quick inspection in a REPL.
        """
        s = self.stats()
        lines = [
            '┌─ LSMKV Engine Stats ────────────────────────────────┐',
            f'│ uptime          {s.get("uptime_seconds", 0):.1f}s',
            f'│ memtable        {s["memtable_bytes"]:>10,} B   '
                f'{s["memtable_keys"]:>7,} keys',
        ]
        if s.get('imm_bytes', 0):
            lines.append(f'│ imm (flushing)  {s["imm_bytes"]:>10,} B')
        lines.append(f'│ wal             {s["wal_bytes"]:>10,} B')
        for lvl, info in s.get('levels', {}).items():
            lines.append(
                f'│ {lvl:<4}           {info["bytes"]:>10,} B   '
                f'{info["files"]:>3} file(s)'
            )
        gp50 = s.get('get_p50_us', 0)
        gp99 = s.get('get_p99_us', 0)
        pp50 = s.get('put_p50_us', 0)
        pp99 = s.get('put_p99_us', 0)
        lines += [
            f'│ block cache     {s.get("block_cache_bytes", 0):>10,} B   '
                f'hit={s.get("block_cache_hit_rate", 0):.1%}',
            f'│ puts  {s.get("puts", 0):>10,}   p50={pp50:.0f}µs  p99={pp99:.0f}µs',
            f'│ gets  {s.get("gets", 0):>10,}   p50={gp50:.0f}µs  p99={gp99:.0f}µs',
            f'│ get hit rate    {s.get("get_hit_rate", 0):>10.1%}',
            f'│ compactions     {s.get("compactions", 0):>10,}   '
                f'flushes={s.get("flushes", 0):,}',
        ]
        if 'write_amplification' in s:
            lines.append(f'│ write amp       {s["write_amplification"]:>10.2f}×')
        lines.append('└─────────────────────────────────────────────────────┘')
        return '\n'.join(lines)

    def validate(self) -> dict:
        """Check database integrity and return a report.

        Verifies:
          - Every file listed in the MANIFEST exists on disk.
          - Every SSTable file is structurally valid (readable footer + index).
          - No orphaned .sst files exist in the data directory.
          - The MANIFEST itself is parseable.

        Returns {'ok': bool, 'issues': [str]}.  Safe to call on a live database;
        takes a snapshot of the MANIFEST under _lock so readers are not blocked.
        """
        issues = []
        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]

        for lvl_idx, lvl in enumerate(levels):
            for path in lvl:
                if not os.path.exists(path):
                    issues.append(f'L{lvl_idx}: file missing: {path}')
                    continue
                try:
                    rdr = SSTableReader(path, block_cache=self._block_cache)
                    if not rdr._index:
                        issues.append(f'L{lvl_idx}: empty index: {path}')
                    rdr.close()
                except Exception as e:
                    issues.append(f'L{lvl_idx}: corrupt file {os.path.basename(path)}: {e}')

        known = set(self._manifest.all_files())
        try:
            for name in os.listdir(self.dir):
                if name.endswith('.sst'):
                    full = os.path.join(self.dir, name)
                    if full not in known:
                        issues.append(f'Orphan SSTable: {name}')
        except OSError as e:
            issues.append(f'Cannot list data directory: {e}')

        if self._imm is not None:
            issues.append('Warning: immutable memtable pending flush (not a data loss risk)')

        return {'ok': len([i for i in issues if not i.startswith('Warning')]) == 0,
                'issues': issues}

    def info(self) -> dict:
        """Return the engine's active configuration as a plain dict.

        Useful for logging, debugging, and verifying that a database was
        opened with the expected settings.
        """
        return {
            'directory':                    self.dir,
            'read_only':                    self._read_only,
            'sync_writes':                  (self._wal._sync if not self._read_only else None),
            'compression':                  self._compression,
            'prefix_compression':           self._prefix_compression,
            'memtable_size_bytes':          self._memtable_limit,
            'block_cache_bytes':            self._block_cache._capacity,
            'compaction_threads':           (self._compaction_executor._max_workers
                                            if self._compaction_executor else 0),
            'compaction_rate_bytes_per_sec':self._compaction_limiter._rate,
            'l0_compact_size_bytes':        self._l0_compact_size,
            'on_flush':                     self._on_flush is not None,
            'on_compaction':                self._on_compaction is not None,
            'sorted_levels':                sorted(self._sorted_levels),
            'max_levels':                   MAX_LEVELS,
            'l0_compact_trigger':           L0_COMPACT_TRIGGER,
            'l0_slowdown_trigger':          L0_SLOWDOWN_TRIGGER,
            'l0_stop_trigger':              L0_STOP_TRIGGER,
        }

    def close(self):
        if self._read_only:
            self._drain_reader_cache()
            return
        self._shutdown.set()
        self._imm_pending.set()
        self._bg.join(timeout=5)
        self._compaction_executor.shutdown(wait=True)
        with self._write_lock:
            if self._imm is not None:
                self._flush_imm()
            if len(self._memtable) > 0:
                self._flush_memtable()
        self._wal.close()
        self._drain_reader_cache()

    def stats(self) -> Dict:
        with self._lock:
            levels = {}
            for i, lvl in enumerate(self._manifest.levels):
                if lvl:
                    sz = sum(self._manifest._file_sizes.get(f, 0) for f in lvl)
                    levels[f'L{i}'] = {'files': len(lvl), 'bytes': sz}
        base = {
            'memtable_bytes': self._memtable.size_bytes,
            'memtable_keys' : len(self._memtable),
            'imm_bytes'     : self._imm.size_bytes if self._imm else 0,
            'levels'        : levels,
            'wal_bytes'     : self._wal.size_bytes,
        }
        base.update(self.metrics.snapshot())
        base.update(self._block_cache.stats())
        return base

    def estimate_key_count(self) -> int:
        """Estimate the total number of live keys across memtable and all SSTables.

        Uses the Bloom filter capacity stored in each SSTable as a proxy for
        the number of entries written to that file.  Deduplication and tombstones
        mean the true live key count may be lower, but this is a fast O(levels)
        estimate that requires no disk I/O beyond what is already cached.
        """
        count = len(self._memtable)
        if self._imm is not None:
            count += len(self._imm)
        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]
        for lvl in levels:
            for path in lvl:
                rdr = self._get_reader(path)
                if rdr is not None:
                    # _bloom.capacity is the number of entries added at write time
                    count += rdr._bloom.capacity
        return count

    # ── reader cache ───────────────────────────────────────────────────────────

    def _get_reader(self, path: str) -> Optional[SSTableReader]:
        """Return a cached SSTableReader, opening it on first access.
        Keeps the fd, Bloom filter, and sparse index warm across calls.
        """
        with self._cache_lock:
            rdr = self._reader_cache.get(path)
            if rdr is not None:
                self.metrics.inc(cache_hits=1)
                return rdr
        if not os.path.exists(path):
            return None
        try:
            rdr = SSTableReader(path, block_cache=self._block_cache)
        except Exception:
            return None
        with self._cache_lock:
            existing = self._reader_cache.get(path)
            if existing is not None:
                rdr.close()
                self.metrics.inc(cache_hits=1)
                return existing
            self._reader_cache[path] = rdr
            self.metrics.inc(cache_misses=1)
        return rdr

    def _evict_readers(self, paths):
        """Close readers and evict block-cache entries for paths being deleted."""
        with self._cache_lock:
            for p in paths:
                rdr = self._reader_cache.pop(p, None)
                if rdr:
                    try:
                        rdr.close()
                    except Exception:
                        pass
        for p in paths:
            self._block_cache.evict_path(p)

    def _drain_reader_cache(self):
        with self._cache_lock:
            for rdr in self._reader_cache.values():
                try:
                    rdr.close()
                except Exception:
                    pass
            self._reader_cache.clear()

    # ── immutable memtable rotation ─────────────────────────────────────────────

    def _maybe_rotate(self):
        """Called under _write_lock when the active memtable is full.
        Fast path: swap to immutable, let bg thread flush.
        Slow path: if a previous imm is still pending, flush inline.
        """
        if self._imm is None:
            self._imm = self._memtable
            self._memtable = SkipList()
            self._imm_pending.set()   # wake bg thread
        else:
            # Previous flush still in progress — do it inline (rare stall)
            self._flush_memtable()

    def _flush_imm(self):
        """Flush _imm to an L0 SSTable. Does NOT truncate WAL.
        The active memtable still has records in the WAL that are not yet in
        any SSTable; the WAL can only be truncated after a full flush.
        """
        with self._imm_flush_lock:
            mem = self._imm
            if mem is None or len(mem) == 0:
                self._imm = None
                return
            ts       = int(time.time() * 1_000_000)
            tmp_path = os.path.join(self.dir, f'L0_{ts}.sst.tmp')
            sst_path = os.path.join(self.dir, f'L0_{ts}.sst')
            writer = SSTableWriter(tmp_path, bloom_capacity=max(len(mem), 100),
                                   compression=self._compression,
                                   prefix_compression=self._prefix_compression)
            for key, value, deleted in mem:
                writer.add(key, value or b'', tombstone=deleted)
            sz = writer.finish()
            os.rename(tmp_path, sst_path)
            fsync_dir(self.dir)
            with self._lock:
                self._manifest.add_l0(sst_path)
            self._imm = None   # data is in SSTable; safe to clear
            self.metrics.inc(flushes=1, bytes_flushed=sz)
            if self._on_flush:
                try:
                    self._on_flush(sz, 0)
                except Exception:
                    pass

    # ── flush (active memtable → SSTable + WAL truncation) ────────────────────

    def _flush_memtable(self):
        """Flush active memtable. Flushes pending _imm first if needed.
        Truncates the WAL — only safe once all in-memory data is in SSTables.
        Called while holding _write_lock.
        """
        # Ensure any pending immutable is flushed first (its records are in the WAL too)
        if self._imm is not None:
            self._flush_imm()

        if len(self._memtable) == 0:
            return
        mem = self._memtable
        self._memtable = SkipList()

        ts       = int(time.time() * 1_000_000)
        tmp_path = os.path.join(self.dir, f'L0_{ts}.sst.tmp')
        sst_path = os.path.join(self.dir, f'L0_{ts}.sst')

        writer = SSTableWriter(tmp_path, bloom_capacity=max(len(mem), 100),
                               compression=self._compression,
                               prefix_compression=self._prefix_compression)
        for key, value, deleted in mem:
            writer.add(key, value or b'', tombstone=deleted)
        sz = writer.finish()                         # includes fsync

        os.rename(tmp_path, sst_path)                # atomic on POSIX
        fsync_dir(self.dir)                          # make rename durable

        # Invariant 3: MANIFEST update BEFORE WAL truncation
        with self._lock:
            self._manifest.add_l0(sst_path)

        self._wal.truncate()                         # now safe
        self.metrics.inc(flushes=1, bytes_flushed=sz)
        if self._on_flush:
            try:
                self._on_flush(sz, 0)
            except Exception:
                pass

    # ── compaction ─────────────────────────────────────────────────────────────

    def _bg_loop(self):
        while not self._shutdown.is_set():
            self._imm_pending.wait(timeout=2)
            self._imm_pending.clear()
            try:
                if self._imm is not None:
                    self._flush_imm()
                self._schedule_compactions()
            except Exception:
                pass

    def _schedule_compactions(self):
        """Submit compaction jobs for all levels that are over budget.

        L0 is triggered by file count OR by total size (when l0_compact_size > 0).
        S-08: multiple non-adjacent level pairs compact in parallel.
        """
        with self._lock:
            l0_count = len(self._manifest.levels[0])
            l0_size  = self._manifest.level_size(0)

        size_trigger = (self._l0_compact_size > 0 and
                        l0_size >= self._l0_compact_size)
        if l0_count >= L0_COMPACT_TRIGGER or size_trigger:
            self._submit_compaction(0)

        for lvl in range(1, MAX_LEVELS - 1):
            budget = BASE_LEVEL_BYTES * (LEVEL_MULTIPLIER ** lvl)
            with self._lock:
                size = self._manifest.level_size(lvl)
            if size > budget:
                self._submit_compaction(lvl)

    def _submit_compaction(self, src_level: int):
        """Submit a compaction job for src_level if one isn't already running."""
        with self._compaction_fut_lock:
            existing = self._compaction_futures.get(src_level)
            if existing is not None and not existing.done():
                return   # already in progress
            future = self._compaction_executor.submit(self._compact_into, src_level)
            self._compaction_futures[src_level] = future

    def _binary_search_level(self, lvl_idx: int, files: list, key: bytes
                              ) -> Optional[str]:
        """Binary search in a sorted level.

        Uses the cached key index (pre-built sorted list of first_keys) when
        available — zero _get_reader() calls during comparisons, eliminating
        _cache_lock contention for every comparison step.

        Falls back to per-comparison _get_reader() if no index exists.
        """
        key_index = self._get_level_key_index(lvl_idx, files)

        if key_index is not None:
            # Fast path: pure array binary search, no locks during comparisons.
            lo, hi, result = 0, len(key_index) - 1, -1
            while lo <= hi:
                mid = (lo + hi) // 2
                if key_index[mid][0] <= key:
                    result = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if result == -1:
                return None
            path = key_index[result][1]
        else:
            # Slow fallback: call _get_reader() at each comparison.
            lo, hi, result = 0, len(files) - 1, -1
            while lo <= hi:
                mid = (lo + hi) // 2
                rdr = self._get_reader(files[mid])
                if rdr is None:
                    return None
                fk = rdr.first_key
                if fk is None:
                    return None
                if fk <= key:
                    result = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if result == -1:
                return None
            path = files[result]

        # One _get_reader() call to verify key <= last_key.
        rdr = self._get_reader(path)
        if rdr is None:
            return None
        lk = rdr.last_key
        if lk is not None and key > lk:
            return None
        return path

    def _sort_level_after_compaction(self, dst_level: int):
        """Sort dst_level files by first_key, mark it sorted, and cache the key index."""
        with self._lock:
            files = list(self._manifest.levels[dst_level])
        first_keys = {}
        for path in files:
            rdr = self._get_reader(path)
            if rdr and rdr.first_key:
                first_keys[path] = rdr.first_key
        if len(first_keys) == len(files) and files:
            with self._lock:
                self._manifest.sort_level(dst_level, first_keys)
            # Cache sorted (first_key, path) pairs — used by binary search
            # in _get() to avoid _get_reader() calls during comparisons.
            self._level_key_index[dst_level] = sorted(
                (fk, p) for p, fk in first_keys.items()
            )
            self._sorted_levels.add(dst_level)

    def _get_level_key_index(self, lvl_idx: int, files: list
                              ) -> Optional[List[Tuple[bytes, str]]]:
        """Return the cached key index for lvl_idx, building it lazily if needed."""
        idx = self._level_key_index.get(lvl_idx)
        if idx is not None:
            return idx
        # Build lazily — used when sorted_levels was loaded from MANIFEST but
        # _level_key_index wasn't yet populated (e.g. after a restart).
        pairs = []
        for path in files:
            rdr = self._get_reader(path)
            if rdr and rdr.first_key:
                pairs.append((rdr.first_key, path))
            else:
                return None   # can't build complete index
        self._level_key_index[lvl_idx] = pairs
        return pairs

    def _overlapping_files(self,
                           candidates: List[str],
                           reference:  List[str]) -> List[str]:
        """Return files from candidates whose key range overlaps any file in reference.

        Used by compaction to avoid merging the entire destination level when
        only a small slice of the key space is affected.  Files whose range
        cannot be determined (missing from cache, read error) are included
        conservatively so no data is silently skipped.
        """
        if not candidates or not reference:
            return candidates

        # Bounding key range of all reference files
        ref_min: Optional[bytes] = None
        ref_max: Optional[bytes] = None
        for p in reference:
            rdr = self._get_reader(p)
            if rdr is None:
                continue
            fk = rdr.first_key
            lk = rdr.last_key
            if fk is not None and (ref_min is None or fk < ref_min):
                ref_min = fk
            if lk is not None and (ref_max is None or lk > ref_max):
                ref_max = lk

        if ref_min is None or ref_max is None:
            return candidates   # cannot determine range; include everything

        result = []
        for p in candidates:
            rdr = self._get_reader(p)
            if rdr is None:
                continue
            fk = rdr.first_key
            lk = rdr.last_key
            if fk is None or lk is None:
                result.append(p)   # unknown range — include conservatively
                continue
            if fk <= ref_max and lk >= ref_min:
                result.append(p)
        return result

    def _compact_into(self, src_level: int):
        """Merge a bounded set of src + overlapping dst files into the next level.

        S-06: picks only overlapping dst files (bounded fan-in).
        S-08: acquires per-level locks in ascending order so concurrent jobs at
              non-adjacent levels run in parallel while adjacent pairs serialise.
              Uses non-blocking acquire — if a level is busy, we return immediately
              and the background loop retries on the next tick.
        """
        dst_level = src_level + 1
        lock_src = self._compaction_locks[src_level]
        lock_dst = self._compaction_locks[dst_level]

        if not lock_src.acquire(blocking=False):
            return   # another job is already compacting this src level
        try:
            if not lock_dst.acquire(blocking=False):
                return   # adjacent level is busy; retry next tick
            try:
                self._do_compact(src_level, dst_level)
            finally:
                lock_dst.release()
        finally:
            lock_src.release()

    def _do_compact(self, src_level: int, dst_level: int):
        """Inner compaction logic, called while both level locks are held."""
        with self._lock:
            src_files = list(self._manifest.levels[src_level])
            dst_files = list(self._manifest.levels[dst_level])

        if not src_files:
            return

        if src_level == 0:
            # L0 files can have overlapping key ranges — must merge all of them.
            # But only pull in the L1 files that actually overlap.
            picked_src = src_files
            picked_dst = self._overlapping_files(dst_files, src_files)
        else:
            # L1+: files are non-overlapping per level.
            # Pick the first (oldest) src file; merge it with overlapping dst files.
            # Remaining src files stay for the next compaction tick.
            picked_src = [src_files[0]]
            picked_dst = self._overlapping_files(dst_files, picked_src)

        all_inputs = picked_src + picked_dst
        ts         = int(time.time() * 1_000_000)
        tmp_path   = os.path.join(self.dir, f'L{dst_level}_{ts}.sst.tmp')
        sst_path   = os.path.join(self.dir, f'L{dst_level}_{ts}.sst')
        deepest    = (dst_level == MAX_LEVELS - 1)

        self._compaction_limiter.reset()
        bloom_fpr = _BLOOM_FPR_BY_LEVEL.get(dst_level, 0.01)
        try:
            written = compact(all_inputs, tmp_path, drop_tombstones=deepest,
                              rate_limiter=self._compaction_limiter,
                              compaction_filter=self._compaction_filter,
                              dst_level=dst_level,
                              compression=self._compression,
                              bloom_fpr=bloom_fpr,
                              prefix_compression=self._prefix_compression)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return

        if written > 0:
            os.rename(tmp_path, sst_path)
            fsync_dir(self.dir)
            fd = os.open(sst_path, os.O_RDONLY)
            try:
                fsync_fd(fd)
            finally:
                os.close(fd)
            new_files = [sst_path]
        else:
            new_files = []

        # Invariant 2: MANIFEST is the commit point.
        # Note: inputs only lists the PICKED files, not the entire level.
        with self._lock:
            self._manifest.apply_compaction(
                inputs ={src_level: picked_src, dst_level: picked_dst},
                outputs={dst_level: new_files},
            )

        self._evict_readers(all_inputs)
        bytes_in = sum(os.path.getsize(f) for f in all_inputs if os.path.exists(f))
        for f in all_inputs:
            try:
                os.remove(f)
            except OSError:
                pass
        bytes_out = sum(
            self._manifest._file_sizes.get(f, 0) for f in new_files
        ) if new_files else 0
        self.metrics.inc(compactions=1, bytes_compacted=bytes_in)
        self.metrics.inc_level(dst_level, bytes_in)
        # Invalidate key index for src_level (it lost picked_src files).
        # The index for dst_level is rebuilt by _sort_level_after_compaction().
        if src_level in self._level_key_index:
            del self._level_key_index[src_level]
        if self._on_compaction:
            try:
                self._on_compaction(src_level, dst_level, bytes_in, bytes_out)
            except Exception:
                pass
        # If L0 was just compacted, wake any writers stalled on the stop trigger.
        if src_level == 0:
            self._l0_drained.set()
        # Wake the bg loop immediately so it can check whether another level
        # is now over budget (compaction cascade: L0→L1 may push L1 over budget
        # requiring L1→L2, etc.).  Without this, the next check waits ~2s.
        if not self._shutdown.is_set():
            self._imm_pending.set()
        # Sort dst_level by first_key so future get()s can binary-search it.
        if dst_level > 0:
            self._sort_level_after_compaction(dst_level)

    def compact_range(self, start=None, end=None):
        """Force a synchronous compaction of keys in [start, end) across all levels.

        Triggers one compaction pass per level pair where any file overlaps
        the requested range.  Blocks until all triggered compactions complete.
        Useful before a bulk-read operation to reduce read amplification over
        a hot key range, or to force tombstone collection.

        Pass start=None / end=None for an unbounded compaction of everything.
        """
        sb = _b(start) if start else None
        eb = _b(end)   if end   else None

        for src_level in range(MAX_LEVELS - 1):
            with self._lock:
                src_files = list(self._manifest.levels[src_level])

            if not src_files:
                continue

            # Only compact if any src file overlaps the requested range
            in_range = []
            for p in src_files:
                rdr = self._get_reader(p)
                if rdr is None:
                    continue
                fk = rdr.first_key
                lk = rdr.last_key
                if fk is None or lk is None:
                    in_range.append(p)
                    continue
                if (sb is None or lk >= sb) and (eb is None or fk < eb):
                    in_range.append(p)

            if in_range:
                self._compact_into(src_level)

    # ── recovery ───────────────────────────────────────────────────────────────

    def _recover(self):
        """Replay WAL into the memtable (handles crash between flush and truncate)."""
        for op, key, value in self._wal.replay():
            if op == OpType.PUT:
                self._memtable.put(key, value)
            else:
                self._memtable.delete(key)

    def _cleanup_orphans(self):
        """Remove SSTable files in the data directory not referenced by MANIFEST."""
        known = set(self._manifest.all_files())
        for name in os.listdir(self.dir):
            if name.endswith('.sst') or name.endswith('.sst.tmp'):
                full = os.path.join(self.dir, name)
                if full not in known:
                    try:
                        os.remove(full)
                    except OSError:
                        pass
