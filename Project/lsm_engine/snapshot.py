"""
Snapshot — Point-in-Time Read-Only View
=========================================
A snapshot captures the set of SSTables that existed at a given moment and
exposes get() / scan() that are guaranteed to see only data written before the
snapshot was taken, regardless of concurrent writes happening afterwards.

Key properties:
  • Isolation: writes after the snapshot are invisible.
  • Consistency: the snapshot sees a complete, compaction-consistent view of
    the SSTables captured.
  • Lightweight: captures only file-path lists — no data is copied.
  • Thread-safe: the underlying SSTableReader cache is shared; pread ensures
    concurrent reads on the same file don't race.

Limitation: the active memtable is NOT included unless db.flush() is called
first.  Keys that have been put() but not yet flushed to an SSTable will be
missing from the snapshot.

Usage:
    db.flush()              # optional: include memtable data in snapshot
    with db.snapshot() as snap:
        value = snap.get('user:1001')
        for key, value in snap.scan('order:', 'order;'):
            process(key, value)
    # snapshot is released; no resources to free (readers stay cached)

    # Or without context manager:
    snap = db.snapshot()
    snap.get('key')
    snap.close()
"""

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple

from .sstable import SSTableReader
from .ttl import decode as _ttl_decode, is_expired as _ttl_expired


def _b(x) -> bytes:
    return x.encode() if isinstance(x, str) else x

def _s(x) -> Optional[str]:
    return x.decode() if isinstance(x, bytes) else x


@dataclass(order=True)
class _E:
    key:  bytes
    pri:  int
    val:  bytes = field(compare=False, default=b'')
    tomb: bool  = field(compare=False, default=False)
    it:   Any   = field(compare=False, default=None)


class Snapshot:
    """Read-only point-in-time view of the database."""

    def __init__(self, db):
        # Capture the level list atomically under the manifest lock
        with db._lock:
            self._levels: List[List[str]] = [list(lvl) for lvl in db._manifest.levels]
        self._db = db

    # ── public API ─────────────────────────────────────────────────────────────

    def get(self, key) -> Optional[str]:
        """Return the value for key at snapshot time, or None if absent."""
        kb = _b(key)
        for lvl_idx, lvl in enumerate(self._levels):
            order = reversed(lvl) if lvl_idx == 0 else iter(lvl)
            for path in order:
                rdr = self._db._get_reader(path)
                if rdr is None:
                    continue
                # Key-range pre-filter
                fk, lk = rdr.first_key, rdr.last_key
                if fk and lk and (kb < fk or kb > lk):
                    continue
                try:
                    hit = rdr.get(kb)
                    if hit is not None:
                        val, tombstone = hit
                        if tombstone:
                            return None
                        val, exp = _ttl_decode(val)
                        if _ttl_expired(exp):
                            return None
                        return _s(val)
                except Exception:
                    continue
        return None

    def scan(self,
             start = None,
             end   = None) -> Iterator[Tuple[str, str]]:
        """Range scan over the snapshot, tombstones and expired TTL excluded."""
        sb = _b(start) if start else None
        eb = _b(end)   if end   else None

        sources: List[Any] = []
        scan_readers: List[SSTableReader] = []   # own these fds; close in finally

        for lvl_idx, lvl in enumerate(self._levels):
            order = list(reversed(lvl)) if lvl_idx == 0 else lvl
            for path in order:
                cached = self._db._get_reader(path)
                if cached is None:
                    continue
                fk, lk = cached.first_key, cached.last_key
                if fk and lk:
                    if (eb and fk >= eb) or (sb and lk < sb):
                        continue
                # Template optimisation: borrow index + Bloom from cached reader.
                # Only opens a new fd (no 3-seek metadata reload).
                scan_rdr = SSTableReader(path,
                                         block_cache=self._db._block_cache,
                                         _template=cached)
                scan_readers.append(scan_rdr)
                sources.append(scan_rdr.scan(sb, eb))

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
                yield _s(e.key), _s(val)
        finally:
            for rdr in scan_readers:
                try:
                    rdr.close()
                except Exception:
                    pass

    def prefix_scan(self, prefix) -> Iterator[Tuple[str, str]]:
        """Yield all snapshot entries whose key starts with prefix."""
        from .lsm_tree import _prefix_end
        pb  = _b(prefix)
        end = _prefix_end(pb)
        return self.scan(
            _s(pb)  if pb  else None,
            _s(end) if end else None,
        )

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def close(self):
        """Release the snapshot (no resources to free; readers remain cached)."""
        self._levels = []

    def __enter__(self) -> 'Snapshot':
        return self

    def __exit__(self, *_):
        self.close()

    def __repr__(self) -> str:
        total = sum(len(lvl) for lvl in self._levels)
        return f'Snapshot({total} SSTables)'
