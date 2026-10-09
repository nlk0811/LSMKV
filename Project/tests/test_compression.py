"""
Tests for block-level zlib compression (compression='zlib').

Verifies:
  - Compressed SSTables are smaller than uncompressed ones
  - All data reads correctly from compressed SSTables
  - Old (uncompressed) SSTables are still readable with a zlib-enabled engine
  - Compression is transparent across flush, compaction, close/reopen
  - Block cache works correctly with decompressed blocks
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.sstable import SSTableWriter, SSTableReader


# ── SSTableWriter / SSTableReader unit tests ──────────────────────────────────

def test_compressed_sstable_smaller(tmp_path):
    """A compressed SSTable must be smaller than an uncompressed one for structured data."""
    template = '{"user": %d, "name": "User%d", "status": "active"}'
    plain = str(tmp_path / 'plain.sst')
    comp  = str(tmp_path / 'comp.sst')

    for path, compr in [(plain, 'none'), (comp, 'zlib')]:
        w = SSTableWriter(path, bloom_capacity=1000, compression=compr)
        for i in range(1000):
            w.add(f'key{i:06d}'.encode(), (template % (i, i)).encode())
        w.finish()

    assert os.path.getsize(comp) < os.path.getsize(plain), \
        f'Compressed ({os.path.getsize(comp)}) should be < plain ({os.path.getsize(plain)})'


def test_compressed_sstable_data_correct(tmp_path):
    """All entries written compressed must be readable correctly."""
    path = str(tmp_path / 'test.sst')
    w = SSTableWriter(path, bloom_capacity=500, compression='zlib')
    for i in range(500):
        w.add(f'key{i:06d}'.encode(), f'value_{i}_data'.encode())
    w.finish()

    rdr = SSTableReader(path)
    for i in [0, 100, 250, 499]:
        result = rdr.get(f'key{i:06d}'.encode())
        assert result == (f'value_{i}_data'.encode(), False), \
            f'key{i:06d}: got {result}'
    rdr.close()


def test_uncompressed_sstable_still_readable(tmp_path):
    """An old-format (uncompressed) SSTable must be readable without errors."""
    path = str(tmp_path / 'old.sst')
    w = SSTableWriter(path, bloom_capacity=100, compression='none')
    for i in range(100):
        w.add(f'k{i:04d}'.encode(), f'v{i}'.encode())
    w.finish()

    # Open with default reader (no compression-awareness needed — backward compat)
    rdr = SSTableReader(path)
    assert rdr.get(b'k0000') == (b'v0', False)
    assert rdr.get(b'k0099') == (b'v99', False)
    rdr.close()


def test_compressed_scan(tmp_path):
    """Compressed SSTable scan must yield all entries in sorted order."""
    path = str(tmp_path / 'scan.sst')
    w = SSTableWriter(path, bloom_capacity=200, compression='zlib')
    for i in range(200):
        w.add(f'sk{i:05d}'.encode(), b'val')
    w.finish()

    rdr = SSTableReader(path)
    keys = [k for k, _, _ in rdr.scan()]
    assert keys == sorted(keys)
    assert len(keys) == 200
    rdr.close()


def test_compressed_tombstone_preserved(tmp_path):
    """Tombstones must survive compression/decompression."""
    path = str(tmp_path / 'tomb.sst')
    w = SSTableWriter(path, bloom_capacity=10, compression='zlib')
    w.add(b'alive', b'yes', tombstone=False)
    w.add(b'dead',  b'',   tombstone=True)
    w.finish()

    rdr = SSTableReader(path)
    assert rdr.get(b'alive') == (b'yes', False)
    assert rdr.get(b'dead')  == (b'',   True)
    rdr.close()


# ── LSMTree integration ───────────────────────────────────────────────────────

@pytest.fixture
def zlib_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False, compression='zlib')
    yield db
    db.close()


def test_lsm_zlib_put_get(zlib_db):
    zlib_db.put('hello', 'world')
    assert zlib_db.get('hello') == 'world'
    assert zlib_db.get('missing') is None


def test_lsm_zlib_delete(zlib_db):
    zlib_db.put('del', 'bye')
    zlib_db.delete('del')
    assert zlib_db.get('del') is None


def test_lsm_zlib_scan(zlib_db):
    for i in range(20):
        zlib_db.put(f'k{i:04d}', f'v{i}')
    results = list(zlib_db.scan('k0000', 'k0010'))
    assert len(results) == 10
    assert results[0] == ('k0000', 'v0')


def test_lsm_zlib_data_correct_after_flush(tmp_path):
    """All keys must be readable after a flush to compressed SSTables."""
    path = str(tmp_path / 'db')
    template = '{"id":%d,"name":"User%d","status":"active"}'
    db = LSMTree(path, sync_writes=False, compression='zlib')
    for i in range(5000):
        db.put(f'user:{i:06d}', template % (i, i))
    db.flush()
    for i in [0, 2500, 4999]:
        assert db.get(f'user:{i:06d}') == template % (i, i)
    db.close()


def test_lsm_zlib_compressed_smaller_than_plain(tmp_path):
    """Compressed DB must use less disk space for structured repetitive data."""
    # Use highly compressible data: long repetitive values (common in JSON payloads)
    big_val = '{"status":"active","role":"user","permissions":["read","write"],' \
              '"tags":["premium","verified"],"metadata":{"created":"2026-01-01"}}' * 3
    sizes = {}
    for label, compr in [('plain', 'none'), ('zlib', 'zlib')]:
        p = str(tmp_path / label)
        db = LSMTree(p, sync_writes=False, compression=compr)
        for i in range(10000):
            db.put(f'key{i:07d}', big_val)
        db.flush()
        s = db.stats()
        sizes[label] = sum(v['bytes'] for v in s['levels'].values())
        db.close()

    assert sizes['zlib'] < sizes['plain'], \
        f'zlib ({sizes["zlib"]:,}) should be < plain ({sizes["plain"]:,})'
    reduction = (1 - sizes['zlib'] / sizes['plain']) * 100
    assert reduction > 30, f'Expected >30% size reduction, got {reduction:.1f}%'


def test_lsm_zlib_persist_reopen(tmp_path):
    """Compressed data must survive close and reopen."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, compression='zlib')
    for i in range(100):
        db.put(f'p{i:04d}', f'val_{i}' * 20)
    db.close()

    # Reopen with same compression setting
    db2 = LSMTree(path, sync_writes=False, compression='zlib')
    for i in [0, 50, 99]:
        assert db2.get(f'p{i:04d}') == f'val_{i}' * 20
    db2.close()


def test_lsm_zlib_read_old_uncompressed_after_compression_enabled(tmp_path):
    """Opening an existing uncompressed DB with compression='zlib' must still read old data."""
    path = str(tmp_path / 'db')
    # Write without compression
    db_old = LSMTree(path, sync_writes=False, compression='none')
    for i in range(100):
        db_old.put(f'old{i:04d}', f'v{i}')
    db_old.close()

    # Reopen with compression (new writes will be compressed, old reads must still work)
    db_new = LSMTree(path, sync_writes=False, compression='zlib')
    for i in [0, 50, 99]:
        assert db_new.get(f'old{i:04d}') == f'v{i}', f'old{i:04d} not readable'
    # Add new compressed entries
    db_new.put('new_key', 'new_value')
    assert db_new.get('new_key') == 'new_value'
    db_new.close()


def test_lsm_zlib_ttl_works_with_compression(tmp_path):
    """TTL + compression must work together correctly."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False, compression='zlib')
    db.put('expires', 'soon', ttl_seconds=0.05)
    assert db.get('expires') == 'soon'
    time.sleep(0.1)
    assert db.get('expires') is None
    db.close()
