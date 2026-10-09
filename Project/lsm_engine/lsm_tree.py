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

MEMTABLE_LIMIT      = 4 * 1024 * 1024   # 4 MB before flush
L0_COMPACT_TRIGGER  = 4                  # trigger L0→L1 compaction
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
                 block_cache_bytes: int = 8 * 1024 * 1024):
        """
        sync_writes=True  (default) — group-commit WAL fsync.
        sync_writes=False — no fsync; OS decides when to flush (benchmarks only).
        compaction_rate_bytes_per_sec — throttle compaction I/O (0 = unlimited).
        compaction_threads — parallel compaction workers (default 2).
        block_cache_bytes — LRU cache for 4 KB SSTable data blocks (default 8 MB).
            Hot blocks stay in memory; reduces read latency from ~100 µs (NVMe)
            to ~1 µs for cached keys.  0 = disabled.
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
        self._compaction_limiter = RateLimiter(compaction_rate_bytes_per_sec)

        # Parallel compaction: thread pool + per-level locks.
        # Two jobs may run concurrently only if they don't share a level.
        # Locks are acquired in ascending level-number order to prevent deadlock.
        self._compaction_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, compaction_threads),
            thread_name_prefix='lsmkv-compact',
        )
        self._compaction_locks = {lvl: threading.Lock() for lvl in range(MAX_LEVELS)}
        self._compaction_futures: Dict[int, concurrent.futures.Future] = {}
        self._compaction_fut_lock = threading.Lock()

        # Live metrics, accessible via db.metrics or included in db.stats().
        self.metrics = EngineMetrics()

        self._cleanup_orphans()
        self._recover()

        self._shutdown = threading.Event()
        self._bg = threading.Thread(target=self._bg_loop, daemon=True)
        self._bg.start()

    # ── public API ─────────────────────────────────────────────────────────────

    def _maybe_stall_writes(self):
        """Apply back-pressure if L0 is accumulating faster than compaction drains it.

        L0 slowdown (>= L0_SLOWDOWN_TRIGGER files): sleep 1 ms per extra file.
        L0 stop      (>= L0_STOP_TRIGGER files):    block until L0 drains below stop.
        This prevents unbounded L0 growth under write bursts that outpace compaction.
        """
        with self._lock:
            n = len(self._manifest.levels[0])
        if n >= L0_STOP_TRIGGER:
            while True:
                time.sleep(0.005)
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
        """
        self._maybe_stall_writes()
        kb = _b(key)
        vb = _b(value)
        if ttl_seconds > 0:
            vb = _ttl_encode(vb, time.time() + ttl_seconds)
        with self._write_lock:
            self._wal.log_put(kb, vb)
            self._memtable.put(kb, vb)
            self.metrics.inc(puts=1, wal_records=1,
                             bytes_written_user=len(kb) + len(vb))
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._maybe_rotate()

    def delete(self, key):
        self._maybe_stall_writes()
        kb = _b(key)
        with self._write_lock:
            self._wal.log_delete(kb)
            self._memtable.delete(kb)
            self.metrics.inc(deletes=1, wal_records=1,
                             bytes_written_user=len(kb))
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._maybe_rotate()

    def write(self, batch: WriteBatch):
        """Apply a WriteBatch atomically: one lock, one WAL fsync, all memtable inserts."""
        if len(batch) == 0:
            return
        self._maybe_stall_writes()
        ops = list(batch)
        user_bytes = sum(len(k) + len(v) for _, k, v in ops)
        with self._write_lock:
            self._wal.log_batch([(op, key, value) for op, key, value in ops])
            for op, key, value in ops:
                if int(op) == 1:   # PUT
                    self._memtable.put(key, value)
                else:              # DELETE
                    self._memtable.delete(key)
            self.metrics.inc(batches=1, batch_ops=len(ops), wal_records=len(ops),
                             bytes_written_user=user_bytes)
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._maybe_rotate()

    def get(self, key) -> Optional[str]:
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
            order = reversed(lvl) if lvl_idx == 0 else iter(lvl)
            for path in order:
                rdr = self._get_reader(path)
                if rdr is None:
                    continue
                # Key-range pre-filter: skip files whose [first_key, last_key]
                # doesn't span kb.  Two byte comparisons vs 7 SHA-256 calls.
                # Applied to all levels; especially valuable for large L3+.
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
                    rdr = SSTableReader(path, block_cache=self._block_cache)
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

    def flush(self):
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
        lines += [
            f'│ block cache     {s.get("block_cache_bytes", 0):>10,} B   '
                f'hit={s.get("block_cache_hit_rate", 0):.1%}',
            f'│ puts            {s.get("puts", 0):>10,}   '
                f'gets={s.get("gets", 0):,}',
            f'│ get hit rate    {s.get("get_hit_rate", 0):>10.1%}',
            f'│ compactions     {s.get("compactions", 0):>10,}   '
                f'flushes={s.get("flushes", 0):,}',
        ]
        if 'write_amplification' in s:
            lines.append(f'│ write amp       {s["write_amplification"]:>10.2f}×')
        lines.append('└─────────────────────────────────────────────────────┘')
        return '\n'.join(lines)

    def close(self):
        self._shutdown.set()
        self._imm_pending.set()           # wake bg thread so it exits promptly
        self._bg.join(timeout=5)
        self._compaction_executor.shutdown(wait=True)   # drain in-flight jobs
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
            writer = SSTableWriter(tmp_path, bloom_capacity=max(len(mem), 100))
            for key, value, deleted in mem:
                writer.add(key, value or b'', tombstone=deleted)
            sz = writer.finish()
            os.rename(tmp_path, sst_path)
            fsync_dir(self.dir)
            with self._lock:
                self._manifest.add_l0(sst_path)
            self._imm = None   # data is in SSTable; safe to clear
            self.metrics.inc(flushes=1, bytes_flushed=sz)

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

        writer = SSTableWriter(tmp_path, bloom_capacity=max(len(mem), 100))
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

        S-08: multiple non-adjacent level pairs compact in parallel (e.g.
        L0→L1 and L2→L3 simultaneously).  Adjacent pairs serialise via
        per-level locks acquired inside _compact_into.
        """
        with self._lock:
            l0_count = len(self._manifest.levels[0])

        if l0_count >= L0_COMPACT_TRIGGER:
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

        self._compaction_limiter.reset()   # fresh token bucket per job
        try:
            written = compact(all_inputs, tmp_path, drop_tombstones=deepest,
                              rate_limiter=self._compaction_limiter)
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
        self.metrics.inc(compactions=1, bytes_compacted=bytes_in)

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
