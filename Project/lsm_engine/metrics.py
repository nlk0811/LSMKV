"""
Engine Metrics
==============
Thread-safe counters and latency histograms updated on every operation.
Access via db.metrics or included in db.stats().
"""
import threading
import time


class LatencyTracker:
    """Bucketed histogram for tracking operation latency in microseconds.

    Buckets cover 1 µs to 10 s.  All values above the highest bucket are
    counted in the overflow bucket.  Percentile queries are O(buckets) ≈ O(1).
    """
    _BUCKETS_US = [1, 2, 5, 10, 25, 50, 100, 250, 500,
                   1_000, 2_500, 5_000, 10_000, 50_000, 100_000]

    def __init__(self):
        self._counts    = [0] * (len(self._BUCKETS_US) + 1)
        self._n         = 0
        self._total_us  = 0.0
        self._lock      = threading.Lock()

    def record(self, us: float):
        with self._lock:
            self._n += 1
            self._total_us += us
            for i, b in enumerate(self._BUCKETS_US):
                if us <= b:
                    self._counts[i] += 1
                    return
            self._counts[-1] += 1

    def percentile(self, p: float) -> float:
        """Return the p-th percentile in microseconds (p in [0, 100])."""
        with self._lock:
            if self._n == 0:
                return 0.0
            target = self._n * p / 100.0
            cumulative = 0
            for i, count in enumerate(self._counts):
                cumulative += count
                if cumulative >= target:
                    if i < len(self._BUCKETS_US):
                        return float(self._BUCKETS_US[i])
                    return float('inf')
        return float('inf')

    @property
    def mean_us(self) -> float:
        with self._lock:
            return self._total_us / max(1, self._n)

    @property
    def count(self) -> int:
        with self._lock:
            return self._n

    def snapshot(self, prefix: str) -> dict:
        """Return a dict with p50/p99/p999/mean keyed by prefix."""
        return {
            f'{prefix}_p50_us':  round(self.percentile(50),  1),
            f'{prefix}_p99_us':  round(self.percentile(99),  1),
            f'{prefix}_p999_us': round(self.percentile(99.9), 1),
            f'{prefix}_mean_us': round(self.mean_us,           1),
            f'{prefix}_count':   self.count,
        }


class EngineMetrics:
    def __init__(self):
        self._lock  = threading.Lock()
        self._start = time.monotonic()
        # Latency histograms (µs buckets)
        self.get_latency = LatencyTracker()
        self.put_latency = LatencyTracker()
        # writes
        self.puts    = 0
        self.deletes = 0
        self.batches = 0          # write_batch calls
        self.batch_ops = 0        # total ops across all batches
        # reads
        self.gets      = 0
        self.get_hits  = 0        # get() returned a non-None value
        self.get_misses = 0       # get() returned None
        # SSTable reader cache
        self.cache_hits   = 0     # _get_reader() returned cached reader
        self.cache_misses = 0     # _get_reader() had to open file
        # Read path filtering
        self.bloom_skips  = 0     # SSTables skipped by Bloom filter
        self.range_skips  = 0     # SSTables skipped by key-range pre-filter
        self.sstable_reads = 0    # SSTables where a block was actually read
        # flush / compaction
        self.flushes          = 0
        self.bytes_flushed    = 0
        self.compactions      = 0
        self.bytes_compacted  = 0
        # per-level compaction: {level: bytes_compacted_at_that_level}
        self.bytes_by_level: dict = {}
        # user-visible bytes written (key+value, excludes WAL/SSTable overhead)
        self.bytes_written_user = 0
        # scans
        self.scans        = 0
        self.scan_entries = 0     # total (key, value) pairs yielded
        # WAL
        self.wal_records  = 0
        self.wal_syncs    = 0     # number of fsyncs (1 per group commit or per write)
        self.wal_gc_batches = 0   # group-commit batches (when > 1 record per sync)
        self.wal_gc_records = 0   # total records across all group-commit batches

    def inc(self, **kwargs):
        """Increment one or more counters atomically."""
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, getattr(self, k) + v)

    def reset(self):
        """Reset all counters to zero and restart the uptime clock.

        Useful for measuring rates over a specific window:
            db.metrics.reset()
            time.sleep(60)
            snap = db.metrics.snapshot()
            print(f'{snap[\"puts\"]} puts in the last minute')
        """
        with self._lock:
            self._start = time.monotonic()
            for attr in ('puts', 'deletes', 'batches', 'batch_ops',
                         'gets', 'get_hits', 'get_misses',
                         'cache_hits', 'cache_misses',
                         'bloom_skips', 'range_skips', 'sstable_reads',
                         'flushes', 'bytes_flushed',
                         'compactions', 'bytes_compacted', 'bytes_written_user',
                         'scans', 'scan_entries',
                         'wal_records', 'wal_syncs', 'wal_gc_batches', 'wal_gc_records'):
                setattr(self, attr, 0)
            self.bytes_by_level.clear()
        self.get_latency  = LatencyTracker()
        self.put_latency  = LatencyTracker()

    def inc_level(self, level: int, bytes_compacted: int):
        """Track bytes compacted at a specific level for per-level write-amp stats."""
        with self._lock:
            self.bytes_by_level[level] = self.bytes_by_level.get(level, 0) + bytes_compacted

    def snapshot(self) -> dict:
        """Return a point-in-time snapshot with derived rates and ratios."""
        with self._lock:
            elapsed = max(time.monotonic() - self._start, 1e-9)
            total_writes = self.puts + self.deletes
            total_cache  = self.cache_hits + self.cache_misses
            d = {
                'uptime_seconds':       round(elapsed, 1),
                'puts':                 self.puts,
                'deletes':              self.deletes,
                'batches':              self.batches,
                'gets':                 self.gets,
                'get_hits':             self.get_hits,
                'get_misses':           self.get_misses,
                'get_hit_rate':         round(self.get_hits  / max(1, self.gets), 4),
                'cache_hits':           self.cache_hits,
                'cache_misses':         self.cache_misses,
                'cache_hit_rate':       round(self.cache_hits / max(1, total_cache), 4),
                'bloom_skips':          self.bloom_skips,
                'range_skips':          self.range_skips,
                'sstable_reads':        self.sstable_reads,
                'flushes':              self.flushes,
                'bytes_flushed':        self.bytes_flushed,
                'compactions':          self.compactions,
                'bytes_compacted':      self.bytes_compacted,
                'scans':                self.scans,
                'scan_entries':         self.scan_entries,
                'wal_records':          self.wal_records,
                'wal_syncs':            self.wal_syncs,
                'wal_gc_batches':       self.wal_gc_batches,
                'avg_gc_batch_size':    round(self.wal_gc_records / max(1, self.wal_gc_batches), 2),
                'bytes_written_user':   self.bytes_written_user,
                'write_ops_per_sec':    round(total_writes / elapsed, 1),
                'read_ops_per_sec':     round(self.gets     / elapsed, 1),
            }
            disk_written = self.bytes_flushed + self.bytes_compacted
            if self.bytes_written_user > 0:
                d['write_amplification'] = round(
                    disk_written / self.bytes_written_user, 2)
            if self.bytes_by_level:
                d['bytes_compacted_by_level'] = dict(self.bytes_by_level)
        d.update(self.get_latency.snapshot('get'))
        d.update(self.put_latency.snapshot('put'))
        return d
