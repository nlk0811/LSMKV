"""
Tests for the scan() metadata-borrowing optimisation.

When a cached SSTableReader already holds the index and Bloom filter in
memory, scan() creates a lightweight reader that borrows that metadata
instead of re-reading it from disk — saving 3 disk seeks per SSTable
per scan call.
"""
import os
import shutil
import time
import pytest

from lsm_engine.sstable import SSTableWriter, SSTableReader
from lsm_engine.lsm_tree import LSMTree


# ── SSTableReader._template ────────────────────────────────────────────────────

def test_template_reader_same_data(tmp_path):
    """A template-created reader must return the same results as a plain reader."""
    path = str(tmp_path / 'test.sst')
    w = SSTableWriter(path, bloom_capacity=500)
    for i in range(500):
        w.add(f'k{i:05d}'.encode(), f'v{i}'.encode())
    w.finish()

    plain    = SSTableReader(path)
    template = SSTableReader(path, _template=plain)

    for i in [0, 100, 250, 499]:
        assert plain.get(f'k{i:05d}'.encode()) == template.get(f'k{i:05d}'.encode())

    plain.close()
    template.close()


def test_template_reader_independent_fd(tmp_path):
    """Template reader must have its own file descriptor (independent seeks)."""
    path = str(tmp_path / 'test.sst')
    w = SSTableWriter(path, bloom_capacity=100)
    for i in range(100):
        w.add(f'key{i:04d}'.encode(), b'val')
    w.finish()

    source   = SSTableReader(path)
    template = SSTableReader(path, _template=source)

    # Closing the source must not affect the template reader
    source.close()
    assert template.get(b'key0000') == (b'val', False)
    template.close()


def test_template_reader_shares_index(tmp_path):
    """Template reader borrows index by reference — same object."""
    path = str(tmp_path / 'test.sst')
    w = SSTableWriter(path, bloom_capacity=50)
    for i in range(50):
        w.add(f'k{i:03d}'.encode(), b'v')
    w.finish()

    source   = SSTableReader(path)
    template = SSTableReader(path, _template=source)
    assert template._index is source._index   # same list object, no copy
    source.close()
    template.close()


def test_template_reader_scan(tmp_path):
    """Scan on a template reader must yield all entries correctly."""
    path = str(tmp_path / 'test.sst')
    w = SSTableWriter(path, bloom_capacity=200)
    for i in range(200):
        w.add(f'sc{i:05d}'.encode(), f'val{i}'.encode())
    w.finish()

    source   = SSTableReader(path)
    template = SSTableReader(path, _template=source)
    keys = [k for k, _, _ in template.scan()]
    assert len(keys) == 200
    assert keys == sorted(keys)
    source.close()
    template.close()


def test_template_reader_compressed(tmp_path):
    """Template optimisation works correctly with compressed SSTables."""
    path = str(tmp_path / 'comp.sst')
    w = SSTableWriter(path, bloom_capacity=200, compression='zlib')
    for i in range(200):
        w.add(f'ck{i:05d}'.encode(), (f'value_{i}_' * 20).encode())
    w.finish()

    source   = SSTableReader(path)
    template = SSTableReader(path, _template=source)
    for i in [0, 100, 199]:
        assert source.get(f'ck{i:05d}'.encode()) == template.get(f'ck{i:05d}'.encode())
    source.close()
    template.close()


# ── LSMTree.scan() uses template when cache is warm ───────────────────────────

def test_scan_correctness_after_gets(tmp_path):
    """scan() must be correct after get() has warmed the reader cache."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'key{i:06d}', f'val{i}')
    time.sleep(1)

    # Warm reader cache via get()
    for i in range(0, 5000, 100):
        db.get(f'key{i:06d}')

    # Now scan — template optimisation is active
    results = list(db.scan('key000000', 'key001000'))
    assert len(results) == 1000
    for key, value in results:
        idx = int(key[-6:])
        assert value == f'val{idx}'
    db.close()


def test_scan_without_cached_readers(tmp_path):
    """scan() must work correctly even when no cached readers exist (cold cache)."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(1000):
        db.put(f'cld{i:05d}', f'v{i}')
    db.flush()
    # Don't warm the cache — scan should fall back to loading metadata
    results = list(db.scan('cld00000', 'cld00100'))
    assert len(results) == 100
    db.close()


def test_scan_consistent_with_get(tmp_path):
    """Every key returned by scan must match what get() returns."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(500):
        db.put(f'cons{i:05d}', f'v{i}')
    db.flush()
    # Warm cache
    db.get('cons00000')
    # Scan and verify against get
    for key, value in db.scan('cons00000', 'cons00100'):
        assert db.get(key) == value
    db.close()
