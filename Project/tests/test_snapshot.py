"""
Tests for Snapshot (point-in-time read-only view) and db.flush().

A snapshot captures SSTable files at creation time.  Writes after the snapshot
are invisible.  The active memtable is excluded unless flush() is called first.
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.snapshot import Snapshot


@pytest.fixture
def flushed_db(tmp_path):
    """DB with 50 keys flushed to at least one SSTable."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50):
        db.put(f'key{i:03d}', f'val{i}')
    db.flush()
    yield db
    db.close()


# ── db.flush() ─────────────────────────────────────────────────────────────────

def test_flush_empties_memtable(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(10):
        db.put(f'k{i}', f'v{i}')
    db.flush()
    assert db.stats()['memtable_keys'] == 0
    db.close()


def test_flush_data_readable_after_flush(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('a', '1')
    db.put('b', '2')
    db.flush()
    assert db.get('a') == '1'
    assert db.get('b') == '2'
    db.close()


def test_flush_persists_across_reopen(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(20):
        db.put(f'pk{i:03d}', f'pv{i}')
    db.flush()
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    for i in [0, 10, 19]:
        assert db2.get(f'pk{i:03d}') == f'pv{i}'
    db2.close()


# ── Snapshot.get() ─────────────────────────────────────────────────────────────

def test_snapshot_sees_flushed_data(flushed_db):
    snap = flushed_db.snapshot()
    assert snap.get('key000') == 'val0'
    assert snap.get('key049') == 'val49'
    snap.close()


def test_snapshot_excludes_post_snapshot_writes(flushed_db):
    snap = flushed_db.snapshot()
    flushed_db.put('new_key', 'new_val')
    assert snap.get('new_key') is None   # not in snapshot
    assert flushed_db.get('new_key') == 'new_val'
    snap.close()


def test_snapshot_sees_old_value_after_overwrite(flushed_db):
    snap = flushed_db.snapshot()
    flushed_db.put('key000', 'OVERWRITTEN')
    assert snap.get('key000') == 'val0'       # snapshot: original
    assert flushed_db.get('key000') == 'OVERWRITTEN'  # db: updated
    snap.close()


def test_snapshot_excludes_memtable(tmp_path):
    """Snapshot only sees SSTables, not the active memtable."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('in_mem', 'value')   # NOT flushed
    snap = db.snapshot()
    assert snap.get('in_mem') is None   # not in any SSTable
    db.close()


def test_snapshot_includes_memtable_after_flush(tmp_path):
    """After flush(), a new snapshot does include those keys."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('flush_me', 'here')
    db.flush()
    snap = db.snapshot()
    assert snap.get('flush_me') == 'here'
    snap.close()
    db.close()


def test_snapshot_missing_key_returns_none(flushed_db):
    snap = flushed_db.snapshot()
    assert snap.get('nonexistent') is None
    snap.close()


# ── Snapshot.scan() ────────────────────────────────────────────────────────────

def test_snapshot_scan_returns_flushed_keys(flushed_db):
    snap = flushed_db.snapshot()
    keys = [k for k, _ in snap.scan()]
    assert len(keys) == 50
    assert 'key000' in keys and 'key049' in keys
    snap.close()


def test_snapshot_scan_range(flushed_db):
    snap = flushed_db.snapshot()
    results = list(snap.scan('key010', 'key020'))
    keys = [k for k, _ in results]
    assert keys == [f'key{i:03d}' for i in range(10, 20)]
    snap.close()


def test_snapshot_scan_excludes_post_snapshot(flushed_db):
    snap = flushed_db.snapshot()
    flushed_db.put('zzz_post', 'invisible')
    keys = [k for k, _ in snap.scan()]
    assert 'zzz_post' not in keys
    snap.close()


def test_snapshot_prefix_scan(flushed_db):
    snap = flushed_db.snapshot()
    results = dict(snap.prefix_scan('key00'))
    assert len(results) == 10
    assert 'key000' in results
    snap.close()


# ── Context manager ────────────────────────────────────────────────────────────

def test_snapshot_context_manager(flushed_db):
    with flushed_db.snapshot() as snap:
        assert snap.get('key000') == 'val0'


def test_snapshot_repr(flushed_db):
    snap = flushed_db.snapshot()
    r = repr(snap)
    assert 'Snapshot' in r
    snap.close()


# ── Concurrent snapshot and writes ────────────────────────────────────────────

def test_snapshot_stable_under_concurrent_writes(tmp_path):
    """Snapshot must remain stable while writes and compaction run concurrently."""
    import threading
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(100):
        db.put(f'base{i:04d}', f'v{i}')
    db.flush()

    snap = db.snapshot()
    errors = []

    def writer():
        for i in range(100, 200):
            db.put(f'base{i:04d}', f'v{i}')

    def reader():
        for i in range(0, 100, 10):
            v = snap.get(f'base{i:04d}')
            if v != f'v{i}':
                errors.append(f'base{i:04d}: expected v{i}, got {v!r}')

    threads = [threading.Thread(target=writer) if i == 0
               else threading.Thread(target=reader)
               for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == [], f'Snapshot corruption: {errors}'
    snap.close()
    db.close()
