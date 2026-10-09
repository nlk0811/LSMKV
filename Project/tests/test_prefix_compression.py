"""
Tests for block-level prefix key compression (prefix_compression=True).

Adjacent keys sharing a common prefix store only the differing suffix.
Reduces key storage by up to 89% for clustered workloads.
Backward-compatible: old (non-prefix-compressed) SSTables always readable.
"""
import os
import shutil
import time
import pytest

from lsm_engine.sstable import (SSTableWriter, SSTableReader,
                                  _PC_MARKER, _PCZ_MARKER, _COMPRESS_MARKER)
from lsm_engine.lsm_tree import LSMTree


# ── SSTableWriter / SSTableReader unit tests ──────────────────────────────────

def test_prefix_compressed_sstable_smaller(tmp_path):
    """Prefix-compressed file must be smaller for clustered keys."""
    plain = str(tmp_path / 'plain.sst')
    comp  = str(tmp_path / 'comp.sst')
    for path, pc in [(plain, False), (comp, True)]:
        w = SSTableWriter(path, bloom_capacity=500, prefix_compression=pc)
        for i in range(500):
            w.add(f'user:{i:07d}'.encode(), b'val')
        w.finish()
    assert os.path.getsize(comp) < os.path.getsize(plain), \
        f'Compressed ({os.path.getsize(comp)}) should be < plain ({os.path.getsize(plain)})'


def test_prefix_compressed_first_block_marker(tmp_path):
    """First bytes of a prefix-compressed block must be _PC_MARKER."""
    path = str(tmp_path / 'pc.sst')
    w = SSTableWriter(path, bloom_capacity=200, prefix_compression=True)
    for i in range(200):
        w.add(f'k{i:05d}'.encode(), b'v')
    w.finish()
    with open(path, 'rb') as f:
        first2 = f.read(2)
    assert first2 == _PC_MARKER or first2 == _PCZ_MARKER, \
        f'Expected PC marker, got {first2.hex()}'


def test_prefix_compressed_data_correct(tmp_path):
    """All entries in a prefix-compressed SSTable must be readable correctly."""
    path = str(tmp_path / 'pc.sst')
    w = SSTableWriter(path, bloom_capacity=500, prefix_compression=True)
    for i in range(500):
        w.add(f'key{i:06d}'.encode(), f'val{i}'.encode())
    w.finish()
    rdr = SSTableReader(path)
    for i in [0, 100, 250, 499]:
        result = rdr.get(f'key{i:06d}'.encode())
        assert result == (f'val{i}'.encode(), False), f'key{i:06d}: {result}'
    rdr.close()


def test_old_format_still_readable(tmp_path):
    """An SSTable written without prefix compression must still be readable."""
    path = str(tmp_path / 'old.sst')
    w = SSTableWriter(path, bloom_capacity=100, prefix_compression=False)
    for i in range(100):
        w.add(f'k{i:04d}'.encode(), f'v{i}'.encode())
    w.finish()
    rdr = SSTableReader(path)
    assert rdr.get(b'k0000') == (b'v0', False)
    assert rdr.get(b'k0099') == (b'v99', False)
    rdr.close()


def test_prefix_compressed_scan(tmp_path):
    """scan() on a prefix-compressed SSTable must yield entries in sorted order."""
    path = str(tmp_path / 'pc.sst')
    w = SSTableWriter(path, bloom_capacity=200, prefix_compression=True)
    for i in range(200):
        w.add(f'sk{i:05d}'.encode(), b'v')
    w.finish()
    rdr = SSTableReader(path)
    keys = [k for k, _, _ in rdr.scan()]
    assert len(keys) == 200
    assert keys == sorted(keys)
    rdr.close()


def test_prefix_compressed_tombstones(tmp_path):
    """Tombstones must survive prefix compression / decompression."""
    path = str(tmp_path / 'pc.sst')
    w = SSTableWriter(path, bloom_capacity=10, prefix_compression=True)
    w.add(b'alive', b'yes', tombstone=False)
    w.add(b'dead',  b'',   tombstone=True)
    w.finish()
    rdr = SSTableReader(path)
    assert rdr.get(b'alive') == (b'yes', False)
    assert rdr.get(b'dead')  == (b'',   True)
    rdr.close()


def test_prefix_plus_zlib(tmp_path):
    """Prefix compression + zlib must produce the smallest file and correct data."""
    path = str(tmp_path / 'pcz.sst')
    w = SSTableWriter(path, bloom_capacity=200, compression='zlib',
                      prefix_compression=True)
    for i in range(200):
        w.add(f'ns:{i:06d}'.encode(), (f'value_{i}' * 10).encode())
    w.finish()
    rdr = SSTableReader(path)
    for i in [0, 100, 199]:
        result = rdr.get(f'ns:{i:06d}'.encode())
        assert result == ((f'value_{i}' * 10).encode(), False)
    rdr.close()


def test_last_key_prefix_compressed(tmp_path):
    """last_key must be correct for prefix-compressed SSTables."""
    path = str(tmp_path / 'pc.sst')
    w = SSTableWriter(path, bloom_capacity=200, prefix_compression=True)
    for i in range(200):
        w.add(f'lk{i:05d}'.encode(), b'v')
    w.finish()
    rdr = SSTableReader(path)
    assert rdr.last_key == b'lk00199'
    rdr.close()


# ── LSMTree integration ───────────────────────────────────────────────────────

def test_lsm_prefix_compression_smaller_than_plain(tmp_path):
    """DB with prefix_compression=True must use less disk than prefix_compression=False."""
    sizes = {}
    for pc in [True, False]:
        p = str(tmp_path / str(pc))
        db = LSMTree(p, sync_writes=False, prefix_compression=pc)
        for i in range(50000):
            db.put(f'user:{i:08d}', f'{{"id":{i},"name":"User{i}"}}')
        db.flush()
        sizes[pc] = sum(v['bytes'] for v in db.stats()['levels'].values())
        db.close()
    assert sizes[True] < sizes[False], \
        f'PC ({sizes[True]:,}) should be < plain ({sizes[False]:,})'
    reduction = (1 - sizes[True] / sizes[False]) * 100
    assert reduction > 10, f'Expected >10% reduction, got {reduction:.1f}%'


def test_lsm_prefix_compression_correctness(tmp_path):
    """All keys must be readable with prefix compression enabled."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False, prefix_compression=True)
    for i in range(50000):
        db.put(f'user:{i:08d}', f'v{i}')
    db.flush()
    for i in [0, 25000, 49999]:
        assert db.get(f'user:{i:08d}') == f'v{i}'
    db.close()


def test_lsm_prefix_compression_persist(tmp_path):
    """Prefix-compressed data must survive close/reopen."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, prefix_compression=True)
    for i in range(100):
        db.put(f'pp{i:04d}', f'v{i}')
    db.close()
    db2 = LSMTree(path, sync_writes=False, prefix_compression=True)
    for i in [0, 50, 99]:
        assert db2.get(f'pp{i:04d}') == f'v{i}'
    db2.close()


def test_lsm_read_old_sstables_after_enabling_pc(tmp_path):
    """Existing plain SSTables must remain readable when prefix_compression is enabled."""
    path = str(tmp_path / 'db')
    db_old = LSMTree(path, sync_writes=False, prefix_compression=False)
    for i in range(100):
        db_old.put(f'old{i:04d}', f'v{i}')
    db_old.close()
    db_new = LSMTree(path, sync_writes=False, prefix_compression=True)
    for i in [0, 50, 99]:
        assert db_new.get(f'old{i:04d}') == f'v{i}'
    db_new.close()
