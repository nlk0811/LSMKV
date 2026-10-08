"""
LSM Tree Engine
================
Ties together SkipList, WAL, SSTable, Manifest, and Compaction into a
complete key-value storage engine with the following API:

    db = LSMTree('/path/to/data')
    db.put('key', 'value')
    db.get('key')            -> 'value' | None
    db.delete('key')
    db.scan('a', 'z')        -> Iterator[(key, value)]
    db.close()

Crash-safety invariants (see docs/invariants.md for full specification):
  1. WAL Completeness  – WAL is written before memtable is updated.
  2. Compaction Atomicity – MANIFEST update is the compaction commit point.
  3. WAL Truncation Safety – WAL truncated only after MANIFEST records the SSTable.
"""

import heapq
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .bloom_filter import BloomFilter
from .compaction   import compact
from .manifest     import Manifest, MAX_LEVELS
from .skip_list    import SkipList
from .sstable      import SSTableReader, SSTableWriter
from .wal          import WAL, OpType
from ._utils       import fsync_fd, fsync_dir

MEMTABLE_LIMIT      = 4 * 1024 * 1024   # 4 MB before flush
L0_COMPACT_TRIGGER  = 4                  # flush L0 → L1 when this many L0 files exist
BASE_LEVEL_BYTES    = 10 * 1024 * 1024  # 10 MB budget for L1
LEVEL_MULTIPLIER    = 10                 # each level is 10× larger than the previous


# ── Merge helper ──────────────────────────────────────────────────────────────

@dataclass(order=True)
class _E:
    key:  bytes
    pri:  int
    val:  bytes = field(compare=False, default=b'')
    tomb: bool  = field(compare=False, default=False)
    it:   Any   = field(compare=False, default=None)


# ── Engine ────────────────────────────────────────────────────────────────────

class LSMTree:
    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

        self._lock       = threading.Lock()   # guards manifest + _imm
        self._write_lock = threading.Lock()   # serialises Put/Delete/Flush

        self._memtable = SkipList()
        self._manifest = Manifest(directory)
        self._wal      = WAL(os.path.join(directory, 'wal.log'))

        self._cleanup_orphans()
        self._recover()

        self._shutdown = threading.Event()
        self._bg = threading.Thread(target=self._bg_loop, daemon=True)
        self._bg.start()

    # ── public API ─────────────────────────────────────────────────────────────

    def put(self, key, value):
        kb = _b(key); vb = _b(value)
        with self._write_lock:
            self._wal.log_put(kb, vb)       # Invariant 1: WAL before memtable
            self._memtable.put(kb, vb)
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._flush_memtable()

    def delete(self, key):
        kb = _b(key)
        with self._write_lock:
            self._wal.log_delete(kb)        # Invariant 1
            self._memtable.delete(kb)
            if self._memtable.size_bytes >= MEMTABLE_LIMIT:
                self._flush_memtable()

    def get(self, key) -> Optional[str]:
        kb = _b(key)

        # 1. memtable
        r = self._memtable.get(kb)
        if r is not None:
            val, deleted = r
            return None if deleted else _s(val)

        # 2. SSTables — snapshot levels to avoid holding lock during I/O
        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]

        for lvl_idx, lvl in enumerate(levels):
            order = reversed(lvl) if lvl_idx == 0 else iter(lvl)
            for path in order:
                if not os.path.exists(path):
                    continue
                try:
                    rdr = SSTableReader(path)
                    hit = rdr.get(kb)
                    rdr.close()
                    if hit is not None:
                        val, tombstone = hit
                        return None if tombstone else _s(val)
                except Exception:
                    continue
        return None

    def scan(self,
             start = None,
             end   = None) -> Iterator[Tuple[str, str]]:
        """Range scan yielding (key, value), tombstones excluded."""
        sb = _b(start) if start else None
        eb = _b(end)   if end   else None

        with self._lock:
            levels = [list(lvl) for lvl in self._manifest.levels]

        sources: List[Any] = [self._memtable.scan(sb, eb)]
        readers: List[SSTableReader] = []

        for lvl_idx, lvl in enumerate(levels):
            order = list(reversed(lvl)) if lvl_idx == 0 else lvl
            for path in order:
                if not os.path.exists(path):
                    continue
                try:
                    rdr = SSTableReader(path)
                    readers.append(rdr)
                    sources.append(rdr.scan(sb, eb))
                except Exception:
                    pass

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
                yield _s(e.key), _s(e.val)
        finally:
            for rdr in readers:
                try:
                    rdr.close()
                except Exception:
                    pass

    def close(self):
        self._shutdown.set()
        self._bg.join(timeout=5)
        with self._write_lock:
            if len(self._memtable) > 0:
                self._flush_memtable()
        self._wal.close()

    def stats(self) -> Dict:
        with self._lock:
            levels = {}
            for i, lvl in enumerate(self._manifest.levels):
                if lvl:
                    sz = sum(os.path.getsize(f) for f in lvl if os.path.exists(f))
                    levels[f'L{i}'] = {'files': len(lvl), 'bytes': sz}
        return {
            'memtable_bytes': self._memtable.size_bytes,
            'memtable_keys' : len(self._memtable),
            'levels'        : levels,
            'wal_bytes'     : self._wal.size_bytes,
        }

    # ── flush (called while holding _write_lock) ───────────────────────────────

    def _flush_memtable(self):
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
        writer.finish()                          # includes fsync

        os.rename(tmp_path, sst_path)            # atomic on POSIX
        fsync_dir(self.dir)                      # make rename durable

        # Invariant 3: MANIFEST update BEFORE WAL truncation
        with self._lock:
            self._manifest.add_l0(sst_path)

        self._wal.truncate()                     # now safe

    # ── compaction ─────────────────────────────────────────────────────────────

    def _bg_loop(self):
        while not self._shutdown.is_set():
            self._shutdown.wait(timeout=2)
            try:
                self._check_compaction()
            except Exception:
                pass

    def _check_compaction(self):
        with self._lock:
            l0 = list(self._manifest.levels[0])

        if len(l0) >= L0_COMPACT_TRIGGER:
            self._compact_into(src_level=0)
            return

        for lvl in range(1, MAX_LEVELS - 1):
            budget = BASE_LEVEL_BYTES * (LEVEL_MULTIPLIER ** lvl)
            with self._lock:
                size = self._manifest.level_size(lvl)
            if size > budget:
                self._compact_into(src_level=lvl)
                return

    def _compact_into(self, src_level: int):
        dst_level = src_level + 1
        with self._lock:
            src_files = list(self._manifest.levels[src_level])
            dst_files = list(self._manifest.levels[dst_level])

        if not src_files:
            return

        all_inputs = src_files + dst_files          # src files are newer
        ts         = int(time.time() * 1_000_000)
        tmp_path   = os.path.join(self.dir, f'L{dst_level}_{ts}.sst.tmp')
        sst_path   = os.path.join(self.dir, f'L{dst_level}_{ts}.sst')
        deepest    = (dst_level == MAX_LEVELS - 1)

        try:
            written = compact(all_inputs, tmp_path, drop_tombstones=deepest)
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

        # Invariant 2: MANIFEST is the commit point
        with self._lock:
            self._manifest.apply_compaction(
                inputs ={src_level: src_files, dst_level: dst_files},
                outputs={dst_level: new_files},
            )

        for f in all_inputs:
            try:
                os.remove(f)
            except OSError:
                pass

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


# ── helpers ───────────────────────────────────────────────────────────────────

def _b(x) -> bytes:
    return x.encode() if isinstance(x, str) else x

def _s(x) -> Optional[str]:
    if x is None:
        return None
    return x.decode() if isinstance(x, bytes) else x
