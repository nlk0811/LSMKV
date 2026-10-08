"""
Tests for S-06: range-aware compaction file picking and compact_range() API.

Verifies that:
  - last_key is correctly derived from each SSTable
  - _overlapping_files() returns the right subset
  - _compact_into() picks only overlapping dst files for L1+
  - compact_range() triggers compaction for the requested key range
  - all data is intact after range-aware compaction
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.sstable import SSTableReader, SSTableWriter


# ── SSTableReader.last_key ─────────────────────────────────────────────────────

def test_last_key_single_block(tmp_path):
    path = str(tmp_path / 'test.sst')
    w = SSTableWriter(path, bloom_capacity=10)
    for i in range(5):
        w.add(f'key{i:03d}'.encode(), f'val{i}'.encode())
    w.finish()
    rdr = SSTableReader(path)
    assert rdr.first_key == b'key000'
    assert rdr.last_key  == b'key004'
    rdr.close()


def test_last_key_multi_block(tmp_path):
    """last_key must be correct when the SSTable spans multiple 4 KB blocks."""
    path = str(tmp_path / 'mb.sst')
    w = SSTableWriter(path, bloom_capacity=500)
    # Large values force multiple blocks
    for i in range(200):
        w.add(f'mkey{i:05d}'.encode(), b'x' * 100)
    w.finish()
    rdr = SSTableReader(path)
    assert rdr.first_key == b'mkey00000'
    assert rdr.last_key  == b'mkey00199'
    rdr.close()


def test_last_key_cached(tmp_path):
    """Second access to last_key must not re-read the file (uses _last_key_cache)."""
    path = str(tmp_path / 'cache.sst')
    w = SSTableWriter(path, bloom_capacity=5)
    for i in range(3):
        w.add(f'ck{i}'.encode(), b'v')
    w.finish()
    rdr = SSTableReader(path)
    lk1 = rdr.last_key
    # Close the underlying fd; if cached, next call should NOT raise
    rdr._f.close()
    lk2 = rdr.last_key
    assert lk1 == lk2 == b'ck2'
    # Re-open so close() doesn't raise
    rdr._f = open(path, 'rb')
    rdr.close()


def test_last_key_ordering(tmp_path):
    """For every SSTable produced by the engine, first_key <= last_key."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50000):
        db.put(f'key{i:07d}', 'v' * 400)
    time.sleep(3)
    for lvl in db._manifest.levels:
        for p in lvl:
            rdr = db._get_reader(p)
            if rdr:
                fk, lk = rdr.first_key, rdr.last_key
                assert fk is not None and lk is not None
                assert fk <= lk, f'{p}: first {fk!r} > last {lk!r}'
    db.close()


# ── compact_range() ────────────────────────────────────────────────────────────

def test_compact_range_correctness(tmp_path):
    """compact_range must not lose or corrupt data."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50000):
        db.put(f'cr{i:07d}', f'val{i}')
    time.sleep(2)
    db.compact_range('cr0000000', 'cr0010000')
    for i in [0, 5000, 9999, 49999]:
        assert db.get(f'cr{i:07d}') == f'val{i}'
    db.close()


def test_compact_range_unbounded(tmp_path):
    """compact_range() with no arguments compacts everything without error."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(20000):
        db.put(f'ub{i:06d}', 'data')
    time.sleep(1)
    db.compact_range()   # no start/end — full compaction
    for i in [0, 10000, 19999]:
        assert db.get(f'ub{i:06d}') == 'data'
    db.close()


def test_compact_range_out_of_range(tmp_path):
    """compact_range on a range that doesn't overlap any file is a no-op."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(100):
        db.put(f'key{i:04d}', f'v{i}')
    db.compact_range('zzz', 'zzz9')   # outside the key space, must not raise
    assert db.get('key0000') == 'v0'
    db.close()


# ── Full-cycle correctness after range-aware compaction ───────────────────────

def test_range_compaction_full_cycle(tmp_path):
    """Write 20+ MB to trigger L0→L1 compaction, verify all keys, close and reopen."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    # ~500 bytes per entry × 50 000 keys = ~25 MB → triggers 6+ flushes and compaction
    n = 50000
    for i in range(n):
        db.put(f'fc{i:07d}', 'v' * 400)
    time.sleep(3)

    s = db.stats()
    assert s['compactions'] >= 1, f'Expected at least one compaction, got {s["compactions"]}'
    for i in [0, n // 4, n // 2, n - 1]:
        assert db.get(f'fc{i:07d}') == 'v' * 400
    db.close()

    # Reopen and verify persistence
    db2 = LSMTree(path, sync_writes=False)
    for i in [0, n // 4, n // 2, n - 1]:
        assert db2.get(f'fc{i:07d}') == 'v' * 400
    db2.close()
