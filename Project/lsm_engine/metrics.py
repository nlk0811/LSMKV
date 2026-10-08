"""
Engine Metrics
==============
Thread-safe counters updated on every operation.
Access via db.metrics or included in db.stats().
"""
import threading
import time


class EngineMetrics:
    def __init__(self):
        self._lock  = threading.Lock()
        self._start = time.monotonic()
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
        # Bloom filter
        self.bloom_skips  = 0     # SSTables skipped by Bloom filter
        # flush / compaction
        self.flushes          = 0
        self.bytes_flushed    = 0
        self.compactions      = 0
        self.bytes_compacted  = 0
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
                'write_ops_per_sec':    round(total_writes / elapsed, 1),
                'read_ops_per_sec':     round(self.gets     / elapsed, 1),
            }
        return d
