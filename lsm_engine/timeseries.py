"""
TimeSeries — Persistent Time-Series Data Store
================================================
Stores (timestamp, value) pairs sorted by time.  Backed by the LSM tree's
natural sorted-key ordering.

Key format:
    __ts:{name}:{timestamp_us:020d}  →  float_value_string

Timestamps are stored as microseconds-since-epoch (20-digit zero-padded int)
for lexicographic sort order.  When two adds arrive at the same microsecond,
the counter is bumped until the key is unique.

Use cases:
  • Application metrics (latency, error rate, throughput)
  • IoT sensor readings
  • Audit logs with timestamps
  • System resource monitoring

Example
-------
    import time

    cpu = db.timeseries('cpu_usage')
    cpu.add(72.5)                          # uses current time
    cpu.add(85.0, timestamp=time.time())   # explicit timestamp

    now = time.time()
    one_min_ago = now - 60

    cpu.range(one_min_ago, now)            # [(ts, val), ...]
    cpu.average(one_min_ago, now)          # mean over last minute
    cpu.count()                            # total data points
    cpu.latest(5)                          # last 5 readings
    cpu.delete_before(one_min_ago)         # prune old data
"""

import time as _time
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

_US = 1_000_000   # microseconds per second


class TimeSeries:
    """Persistent time-series store backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _prefix(self) -> str:
        return f'__ts:{self._name}:'

    def _key(self, timestamp_s: float) -> str:
        ts_us = int(timestamp_s * _US)
        return f'__ts:{self._name}:{ts_us:020d}'

    def _ts_from_key(self, full_key: str) -> float:
        """Extract timestamp (seconds) from a full key."""
        ts_us = int(full_key[len(self._prefix()):])
        return ts_us / _US

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, value: float, timestamp: float = None) -> None:
        """Record a data point.

        timestamp: Unix timestamp in seconds (default: now).
        If two points share the same microsecond, the later one is stored at
        timestamp + 1µs to preserve uniqueness without loss.
        """
        ts_s  = timestamp if timestamp is not None else _time.time()
        ts_us = int(ts_s * _US)
        key   = f'__ts:{self._name}:{ts_us:020d}'
        # Bump µs until we find an unused key (rare under normal rates)
        while self._db.has(key):
            ts_us += 1
            key = f'__ts:{self._name}:{ts_us:020d}'
        self._db.put(key, str(value))

    def add_many(self, points) -> None:
        """Add multiple (value, timestamp) pairs.

        points: iterable of (value, timestamp) tuples.
        """
        for value, ts in points:
            self.add(value, ts)

    # ── read ──────────────────────────────────────────────────────────────────

    def range(self, start: float, end: float,
              limit: Optional[int] = None) -> List[Tuple[float, float]]:
        """Return [(timestamp, value)] for points in [start, end] (inclusive).

        Both start and end are Unix timestamps in seconds.

        limit — return at most this many points, oldest first.  Use limit= to
                avoid loading a huge time window into memory all at once.
        """
        start_key = f'__ts:{self._name}:{int(start * _US):020d}'
        end_key   = f'__ts:{self._name}:{int(end * _US) + 1:020d}'
        plen      = len(self._prefix())
        return [
            (int(k[plen:]) / _US, float(v))
            for k, v in self._db.scan(start_key, end_key, limit=limit)
        ]

    def latest(self, n: int = 1) -> List[Tuple[float, float]]:
        """Return the last n data points, newest last.

        Uses tail() — O(n) memory instead of O(total_points).
        For high-frequency series, prefer range() with a recent time window.
        """
        end_key = self._prefix()[:-1] + ';'
        tail    = self._db.tail(n, end=end_key)
        # tail() includes the end_key exclusive bound: filter to prefix only
        plen    = len(self._prefix())
        result  = [
            (int(k[plen:]) / _US, float(v))
            for k, v in tail
            if k.startswith(self._prefix())
        ]
        return result

    def first(self) -> Optional[Tuple[float, float]]:
        """Return the oldest (timestamp, value), or None if empty.

        O(log n) — uses scan(limit=1) instead of loading all points.
        """
        hit = next(
            self._db.scan(self._prefix(), self._prefix()[:-1] + ';', limit=1),
            None
        )
        if hit is None:
            return None
        k, v = hit
        plen = len(self._prefix())
        return (int(k[plen:]) / _US, float(v))

    # ── aggregation ───────────────────────────────────────────────────────────

    def _scan_range(self, start: float, end: float):
        """Stream (timestamp, value) pairs in [start, end] without materialising."""
        start_key = f'__ts:{self._name}:{int(start * _US):020d}'
        end_key   = f'__ts:{self._name}:{int(end * _US) + 1:020d}'
        plen      = len(self._prefix())
        for k, v in self._db.scan(start_key, end_key):
            yield int(k[plen:]) / _US, float(v)

    def count(self, start: float = None, end: float = None) -> int:
        """Count data points in [start, end].  Unbounded if None."""
        if start is not None and end is not None:
            return sum(1 for _ in self._scan_range(start, end))
        return sum(1 for _ in self._db.scan_keys(
            self._prefix(), self._prefix()[:-1] + ';'))

    def average(self, start: float, end: float) -> Optional[float]:
        """Average value in [start, end].  None if range is empty.  O(1) memory."""
        total = cnt = 0.0
        for _, v in self._scan_range(start, end):
            total += v
            cnt   += 1
        return total / cnt if cnt else None

    def sum(self, start: float, end: float) -> float:
        """Sum of values in [start, end].  O(1) memory."""
        return sum(v for _, v in self._scan_range(start, end))

    def minimum(self, start: float, end: float) -> Optional[float]:
        """Minimum value in [start, end].  O(1) memory."""
        result = None
        for _, v in self._scan_range(start, end):
            if result is None or v < result:
                result = v
        return result

    def maximum(self, start: float, end: float) -> Optional[float]:
        """Maximum value in [start, end].  O(1) memory."""
        result = None
        for _, v in self._scan_range(start, end):
            if result is None or v > result:
                result = v
        return result

    # ── maintenance ───────────────────────────────────────────────────────────

    def delete_before(self, timestamp: float) -> int:
        """Delete all data points strictly before timestamp.  Returns count."""
        end_key = f'__ts:{self._name}:{int(timestamp * _US):020d}'
        return self._db.range_delete(self._prefix(), end_key)

    def clear(self) -> int:
        """Delete all data points."""
        return self._db.prefix_delete(self._prefix())

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return f'TimeSeries({self._name!r}, points={len(self)})'
