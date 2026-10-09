"""
Block Cache
===========
LRU cache for SSTable 4 KB data blocks.

The SSTableReader cache (in lsm_tree.py) keeps file descriptors, Bloom filters,
and sparse indexes in memory.  But each get() still seeks to and reads a data
block from disk.  For a hot working set that fits in memory, this adds ~100 µs
of I/O latency per lookup on NVMe.

BlockCache eliminates that by caching the raw block bytes keyed by
(sst_path, block_offset).  When a block is read it is stored; subsequent lookups
for the same block (same key, different or same caller) are served from memory.

Eviction: LRU via collections.OrderedDict — O(1) get, put, and LRU eviction.
Thread safety: a single Lock guards the OrderedDict.
Capacity: configurable in bytes; individual blocks are 4 KB by default.

Integration:
  LSMTree passes a shared BlockCache instance to every SSTableReader it opens.
  _evict_readers() also evicts all blocks for the deleted paths so stale data
  never accumulates.
"""

import threading
from collections import OrderedDict
from typing import Optional, Tuple

CacheKey = Tuple[str, int]   # (sst_path, block_offset)


class BlockCache:
    def __init__(self, capacity_bytes: int = 8 * 1024 * 1024):
        """
        capacity_bytes: maximum number of bytes to hold in cache.
            0 = disabled (all get/put calls are no-ops).
            Default: 8 MB.
        """
        self._capacity = capacity_bytes
        self._cache: OrderedDict[CacheKey, bytes] = OrderedDict()
        self._size   = 0
        self._lock   = threading.Lock()
        self.hits    = 0
        self.misses  = 0

    @property
    def enabled(self) -> bool:
        return self._capacity > 0

    def get(self, key: CacheKey) -> Optional[bytes]:
        if not self.enabled:
            return None
        with self._lock:
            data = self._cache.get(key)
            if data is not None:
                self._cache.move_to_end(key)   # mark as recently used
                self.hits += 1
                return data
            self.misses += 1
            return None

    def put(self, key: CacheKey, data: bytes):
        if not self.enabled or not data:
            return
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return
            # Evict LRU entries until there is space
            needed = len(data)
            while self._size + needed > self._capacity and self._cache:
                _, evicted = self._cache.popitem(last=False)
                self._size -= len(evicted)
            if needed <= self._capacity:
                self._cache[key] = data
                self._size += needed

    def evict_path(self, path: str):
        """Remove all cached blocks belonging to `path` (called before os.remove)."""
        if not self.enabled:
            return
        with self._lock:
            keys = [k for k in self._cache if k[0] == path]
            for k in keys:
                self._size -= len(self._cache.pop(k))

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                'block_cache_bytes':     self._size,
                'block_cache_capacity':  self._capacity,
                'block_cache_hits':      self.hits,
                'block_cache_misses':    self.misses,
                'block_cache_hit_rate':  round(self.hits / total, 4) if total else 0.0,
                'block_cache_entries':   len(self._cache),
            }

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._size = 0
