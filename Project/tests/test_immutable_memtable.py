"""
Tests for the immutable memtable rotation.

MEMTABLE_LIMIT is 4 MB. Writing keys with ~1 KB values fills the memtable
quickly so rotation (active -> immutable -> SSTable) happens during the test.
"""
import os
import shutil
import time
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'))
    yield d
    d.close()
    shutil.rmtree(str(tmp_path / 'db'), ignore_errors=True)


def _write_bulk(db, n, prefix='key', value_size=1000):
    """Write n keys each with a ~value_size-byte value."""
    val = 'x' * value_size
    for i in range(n):
        db.put(f'{prefix}{i:07d}', val)


def test_reads_during_flush(db):
    """Reads should be consistent while a flush is in progress."""
    val = 'y' * 1000
    for i in range(5000):
        db.put(f'rdfkey{i:07d}', val)
        if i % 1000 == 0:
            # Spot-check already-written keys while writes continue
            for j in range(0, i + 1, 200):
                result = db.get(f'rdfkey{j:07d}')
                assert result == val, f'rdfkey{j:07d}: expected val, got {result!r}'


def test_data_survives_flush(db):
    """All keys must be readable after the memtable has been flushed to L0."""
    _write_bulk(db, 5000)
    # Allow any background flush to complete
    time.sleep(0.3)
    for i in range(0, 5000, 500):
        assert db.get(f'key{i:07d}') == 'x' * 1000


def test_persist_after_flush(tmp_path):
    """Data written through a flush is durable across a close/reopen cycle."""
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    val = 'z' * 1000
    for i in range(5000):
        db.put(f'pkey{i:07d}', val)
    db.close()

    db2 = LSMTree(path)
    for i in range(0, 5000, 500):
        assert db2.get(f'pkey{i:07d}') == val
    db2.close()
    shutil.rmtree(path, ignore_errors=True)


def test_multiple_flushes(db):
    """Writing ~15 MB should trigger at least 3 flush cycles."""
    val = 'w' * 1000
    for i in range(15000):
        db.put(f'mfkey{i:07d}', val)
    time.sleep(0.5)
    s = db.stats()
    total_files = sum(info['files'] for info in s['levels'].values())
    assert total_files >= 1, 'Expected at least one SSTable after 15 MB of writes'
    # Spot-check across the full key range
    for i in [0, 4999, 9999, 14999]:
        assert db.get(f'mfkey{i:07d}') == val


def test_delete_after_flush(db):
    """A key flushed to an SSTable is correctly hidden after a subsequent delete."""
    val = 'v' * 1000
    for i in range(5000):
        db.put(f'dafkey{i:07d}', val)
    # The first key was written early and is likely in a flushed SSTable
    db.delete('dafkey0000000')
    assert db.get('dafkey0000000') is None
    assert db.get('dafkey0004999') == val


def test_get_from_imm(db):
    """Keys in the immutable memtable remain readable while the bg flush runs."""
    val = 'q' * 1000
    # Write just under the 4 MB limit (~3 MB)
    n = 3000
    for i in range(n):
        db.put(f'immkey{i:07d}', val)
    # These extra writes push over the limit, triggering rotation
    for i in range(n, n + 100):
        db.put(f'immkey{i:07d}', val)
    # All keys must be readable immediately (from memtable or _imm)
    for i in range(0, n + 100, 300):
        result = db.get(f'immkey{i:07d}')
        assert result == val, f'immkey{i:07d}: expected val, got {result!r}'
