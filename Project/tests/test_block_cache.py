"""
Tests for the block-level LRU cache and estimate_key_count().
"""
import os
import shutil
import time
import pytest

from lsm_engine.block_cache import BlockCache
from lsm_engine.lsm_tree import LSMTree


# ── BlockCache unit tests ──────────────────────────────────────────────────────

def test_cache_miss_then_hit():
    bc = BlockCache(1024 * 1024)
    key = ('a.sst', 0)
    assert bc.get(key) is None
    assert bc.misses == 1
    bc.put(key, b'hello world')
    data = bc.get(key)
    assert data == b'hello world'
    assert bc.hits == 1


def test_lru_eviction():
    """When capacity is exceeded, the least-recently-used block is evicted."""
    bc = BlockCache(capacity_bytes=10)   # tiny: 10 bytes
    k1, k2 = ('f.sst', 0), ('f.sst', 4096)
    bc.put(k1, b'12345')    # 5 bytes
    bc.put(k2, b'67890')    # 5 bytes — fills cache
    # Access k1 to make it the most recently used
    bc.get(k1)
    # Adding a third entry should evict k2 (LRU)
    k3 = ('f.sst', 8192)
    bc.put(k3, b'abcde')
    assert bc.get(k1) is not None, 'k1 should still be cached (recently used)'
    assert bc.get(k3) is not None, 'k3 should be cached (just added)'
    assert bc.get(k2) is None,     'k2 should have been evicted (LRU)'


def test_evict_path():
    bc = BlockCache(1024 * 1024)
    for i in range(5):
        bc.put(('x.sst', i * 4096), b'data' * 10)
    bc.put(('y.sst', 0), b'keep')
    bc.evict_path('x.sst')
    for i in range(5):
        assert bc.get(('x.sst', i * 4096)) is None
    assert bc.get(('y.sst', 0)) == b'keep'


def test_disabled_cache():
    """capacity=0 means all operations are no-ops."""
    bc = BlockCache(0)
    assert not bc.enabled
    bc.put(('a.sst', 0), b'data')
    assert bc.get(('a.sst', 0)) is None
    assert bc.hits == 0 and bc.misses == 0


def test_stats_keys():
    bc = BlockCache(1024)
    bc.put(('a.sst', 0), b'x' * 100)
    bc.get(('a.sst', 0))
    bc.get(('a.sst', 4096))
    s = bc.stats()
    for key in ('block_cache_bytes', 'block_cache_capacity', 'block_cache_hits',
                'block_cache_misses', 'block_cache_hit_rate', 'block_cache_entries'):
        assert key in s, f'Missing key: {key}'
    assert s['block_cache_hits'] == 1
    assert s['block_cache_misses'] == 1
    assert s['block_cache_hit_rate'] == 0.5


def test_no_duplicate_put():
    """Putting the same key twice should not double-count bytes."""
    bc = BlockCache(1024)
    k = ('dup.sst', 0)
    bc.put(k, b'first')
    size_after_first = bc.stats()['block_cache_bytes']
    bc.put(k, b'second')   # same key — should be a no-op
    assert bc.stats()['block_cache_bytes'] == size_after_first


# ── Integration: block cache with LSMTree ─────────────────────────────────────

def test_block_cache_serves_reads(tmp_path):
    """After a flush, repeated reads should accumulate block_cache_hits."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, block_cache_bytes=8 * 1024 * 1024)
    # Write enough to flush at least one SSTable
    for i in range(5000):
        db.put(f'bck{i:06d}', 'v' * 1000)
    time.sleep(1)

    # First pass: may be misses (blocks loaded for the first time)
    for i in range(0, 5000, 500):
        db.get(f'bck{i:06d}')
    stats_after_first = db.stats()

    # Second pass: should be hits (blocks already in cache)
    for i in range(0, 5000, 500):
        db.get(f'bck{i:06d}')
    stats_after_second = db.stats()

    assert stats_after_second['block_cache_hits'] > stats_after_first['block_cache_hits'], \
        'Second read pass should produce more cache hits'
    db.close()


def test_block_cache_in_stats(tmp_path):
    """db.stats() must include all block cache keys."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('x', 'y')
    s = db.stats()
    for key in ('block_cache_bytes', 'block_cache_hits', 'block_cache_misses',
                'block_cache_hit_rate'):
        assert key in s, f'stats() missing {key}'
    db.close()


def test_block_cache_disabled(tmp_path):
    """block_cache_bytes=0 must still produce correct reads."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, block_cache_bytes=0)
    for i in range(100):
        db.put(f'nc{i:04d}', f'val{i}')
    for i in range(100):
        assert db.get(f'nc{i:04d}') == f'val{i}'
    s = db.stats()
    assert s['block_cache_hits'] == 0   # disabled cache never hits
    db.close()


# ── estimate_key_count ─────────────────────────────────────────────────────────

def test_estimate_key_count_memtable_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50):
        db.put(f'ek{i:04d}', 'v')
    est = db.estimate_key_count()
    assert est >= 50, f'estimate {est} < 50 actual keys'
    db.close()


def test_estimate_key_count_after_flush(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    n = 5000
    for i in range(n):
        db.put(f'ek{i:06d}', 'x' * 1000)
    time.sleep(1)
    est = db.estimate_key_count()
    # Estimate includes memtable + SSTable bloom capacities
    assert est >= n, f'estimate {est} should be >= {n}'
    db.close()
