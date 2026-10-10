"""
Tests for db.ttl_remaining() and db.size_on_disk().
"""
import time
import pytest

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── ttl_remaining ─────────────────────────────────────────────────────────────

def test_ttl_remaining_absent_key(db):
    assert db.ttl_remaining('missing') is None


def test_ttl_remaining_key_without_ttl(db):
    db.put('k', 'v')
    assert db.ttl_remaining('k') is None


def test_ttl_remaining_key_with_ttl(db):
    db.put('k', 'v', ttl_seconds=10)
    r = db.ttl_remaining('k')
    assert r is not None
    assert 9.0 < r <= 10.0, f'Expected ~10s, got {r}'


def test_ttl_remaining_expired_key(db):
    """Expired key still in memtable has negative remaining."""
    db.put('k', 'v', ttl_seconds=0.05)
    time.sleep(0.1)
    r = db.ttl_remaining('k')
    assert r is not None and r < 0, f'Expected negative, got {r}'


def test_ttl_remaining_deleted_key(db):
    db.put('k', 'v', ttl_seconds=100)
    db.delete('k')
    assert db.ttl_remaining('k') is None


def test_ttl_remaining_after_touch(db):
    db.put('k', 'v', ttl_seconds=1)
    db.touch('k', ttl_seconds=100)
    r = db.ttl_remaining('k')
    assert r is not None and r > 90, f'Expected ~100s after touch, got {r}'


def test_ttl_remaining_flushed_to_sstable(tmp_path):
    """ttl_remaining must work for keys flushed to SSTables."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'k{i:06d}', 'x' * 1000)
    db.put('ttl_key', 'val', ttl_seconds=3600)
    db.flush()
    r = db.ttl_remaining('ttl_key')
    assert r is not None and 3595 < r <= 3601, f'Expected ~3600s, got {r}'
    assert db.ttl_remaining('k000000') is None   # no TTL
    db.close()


def test_ttl_remaining_no_ttl_in_sstable(tmp_path):
    """Keys without TTL return None even in SSTables."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'k{i:06d}', 'x' * 1000)
    db.flush()
    assert db.ttl_remaining('k000000') is None
    db.close()


# ── size_on_disk ──────────────────────────────────────────────────────────────

def test_size_on_disk_zero_for_empty_db(db):
    assert db.size_on_disk() == 0


def test_size_on_disk_positive_after_flush(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    # Write a small amount (below 4MB auto-flush threshold)
    for i in range(100):
        db.put(f'k{i:04d}', 'v' * 100)
    # Before flush — only in memtable, may still be 0 on disk
    size_before = db.size_on_disk()
    db.flush()
    size_after = db.size_on_disk()
    assert size_after > 0, f'After flush, size_on_disk should be > 0, got {size_after}'
    db.close()


def test_size_on_disk_increases_with_more_data(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'a{i:06d}', 'v' * 1000)
    db.flush()
    size_a = db.size_on_disk()

    for i in range(5000):
        db.put(f'b{i:06d}', 'v' * 1000)
    db.flush()
    size_b = db.size_on_disk()

    assert size_b > size_a, f'Size should grow: {size_a} → {size_b}'
    db.close()


def test_size_on_disk_excludes_wal(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        db.put(f'k{i}', 'v')
    # WAL has data but not flushed yet
    assert db.size_on_disk() == 0   # SSTable files only, not WAL
    assert db.stats()['wal_bytes'] > 0
    db.close()
