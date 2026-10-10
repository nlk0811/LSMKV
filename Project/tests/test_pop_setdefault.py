"""
Tests for db.pop(), db.setdefault(), and key index invalidation after compaction.
"""
import os
import shutil
import threading
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── db.pop() ───────────────────────────────────────────────────────────────────

def test_pop_existing_key(db):
    db.put('k', 'v')
    result = db.pop('k')
    assert result == 'v'
    assert db.get('k') is None   # deleted


def test_pop_missing_key_returns_default(db):
    assert db.pop('absent') is None
    assert db.pop('absent', 'fallback') == 'fallback'


def test_pop_is_atomic(db):
    """Under concurrency, pop must return each value exactly once."""
    for i in range(20):
        db.put(f'item:{i:04d}', f'val{i}')

    popped = []
    errors = []

    def worker():
        for i in range(20):
            v = db.pop(f'item:{i:04d}')
            if v is not None:
                popped.append(v)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Each key popped at most once
    assert len(popped) == len(set(popped)), 'Duplicate pop results'


def test_pop_persists_delete(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.pop('k')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('k') is None
    db2.close()


def test_pop_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.pop('k')
    ro.close()


# ── db.setdefault() ────────────────────────────────────────────────────────────

def test_setdefault_sets_if_absent(db):
    result = db.setdefault('k', 'default')
    assert result == 'default'
    assert db.get('k') == 'default'


def test_setdefault_returns_existing(db):
    db.put('k', 'existing')
    result = db.setdefault('k', 'new_value')
    assert result == 'existing'
    assert db.get('k') == 'existing'   # not overwritten


def test_setdefault_idempotent(db):
    db.setdefault('k', 'first')
    db.setdefault('k', 'second')
    assert db.get('k') == 'first'   # second call is a no-op


def test_setdefault_concurrent_one_wins(db):
    """Under concurrency, exactly one thread sets the value."""
    setters = []

    def worker(val):
        result = db.setdefault('shared_key', val)
        setters.append(result)

    threads = [threading.Thread(target=worker, args=(f'val{i}',)) for i in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    # All threads must see the same value (the one that won)
    assert len(set(setters)) == 1, f'Multiple values set: {set(setters)}'
    # And that value must be in the database
    final = db.get('shared_key')
    assert final == setters[0]


def test_setdefault_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.setdefault('config', 'dark_theme')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('config') == 'dark_theme'
    db2.close()


def test_setdefault_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.setdefault('k', 'v')
    ro.close()


# ── key index invalidation after compaction ────────────────────────────────────

def test_key_index_accurate_after_multi_compaction(tmp_path):
    """After multiple L0→L1 compactions, the key index must reflect current files."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50000):
        db.put(f'key{i:08d}', 'v' * 400)
    time.sleep(4)   # allow compactions

    # Index must match manifest for all sorted levels
    for lvl_idx in db._sorted_levels:
        with db._lock:
            manifest_files = set(db._manifest.levels[lvl_idx])
        if lvl_idx in db._level_key_index:
            index_files = {p for _, p in db._level_key_index[lvl_idx]}
            assert index_files == manifest_files or not manifest_files, \
                f'L{lvl_idx} key index mismatch: index has {len(index_files)} files, manifest has {len(manifest_files)}'

    # All keys must still be readable
    for i in [0, 25000, 49999]:
        assert db.get(f'key{i:08d}') == 'v' * 400

    db.close()
