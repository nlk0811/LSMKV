"""
Tests for:
  - db.get_or_compute_once() — singleflight deduplicate pattern
  - db.scan_with_ttl()        — scan yielding TTL info
  - db.compact_level(n)       — targeted level compaction
"""
import os
import time
import threading
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── get_or_compute_once (singleflight) ────────────────────────────────────────

def test_get_or_compute_once_basic(db):
    calls = [0]
    def fn():
        calls[0] += 1
        return 'result'
    v = db.get_or_compute_once('k', fn)
    assert v == 'result'
    assert calls[0] == 1
    assert db.get('k') == 'result'


def test_get_or_compute_once_returns_existing(db):
    db.put('k', 'existing')
    calls = [0]
    def fn():
        calls[0] += 1
        return 'computed'
    v = db.get_or_compute_once('k', fn)
    assert v == 'existing'   # returns cached
    assert calls[0] == 0     # fn never called


def test_get_or_compute_once_with_ttl(db):
    v = db.get_or_compute_once('k', lambda: 'val', ttl_seconds=0.05)
    assert v == 'val'
    time.sleep(0.1)
    assert db.get('k') is None   # expired


def test_get_or_compute_once_singleflight_guarantee(db):
    """Under concurrency, fn() must be called exactly once."""
    calls = [0]

    def fn():
        time.sleep(0.05)   # simulate expensive computation
        calls[0] += 1
        return 'only_once'

    results = []
    errors  = []

    def worker():
        try:
            r = db.get_or_compute_once('shared', fn, ttl_seconds=100)
            results.append(r)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors  == [], f'Errors: {errors}'
    assert calls[0] == 1, f'fn() called {calls[0]} times, expected 1'
    assert all(r == 'only_once' for r in results), f'Inconsistent results: {set(results)}'


def test_get_or_compute_once_fn_returning_none(db):
    """fn() returning None must not create the key."""
    v = db.get_or_compute_once('k', lambda: None)
    assert v is None
    assert db.get('k') is None


def test_get_or_compute_once_fn_raises(db):
    """If fn() raises, the exception propagates and other waiters also get it."""
    def bad_fn():
        raise ValueError('compute failed')
    with pytest.raises(ValueError, match='compute failed'):
        db.get_or_compute_once('k', bad_fn)


def test_get_or_compute_once_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(Exception):   # ReadOnlyError or similar
        ro.get_or_compute_once('k', lambda: 'v')
    ro.close()


def test_get_or_compute_once_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.get_or_compute_once('k', lambda: 'persistent')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('k') == 'persistent'
    db2.close()


# ── scan_with_ttl ─────────────────────────────────────────────────────────────

def test_scan_with_ttl_no_ttl(db):
    db.put('k', 'v')
    results = list(db.scan_with_ttl())
    assert len(results) == 1
    key, val, ttl = results[0]
    assert key == 'k' and val == 'v' and ttl is None


def test_scan_with_ttl_with_ttl(db):
    db.put('k', 'v', ttl_seconds=10)
    results = list(db.scan_with_ttl())
    key, val, ttl = results[0]
    assert ttl is not None and 9.0 < ttl <= 10.0


def test_scan_with_ttl_mixed(db):
    db.put('a', 'permanent')
    db.put('b', 'expires', ttl_seconds=10)
    results = {k: ttl for k, _, ttl in db.scan_with_ttl()}
    assert results['a'] is None
    assert results['b'] is not None and results['b'] > 0


def test_scan_with_ttl_expired_invisible(db):
    """Expired keys must not appear in scan_with_ttl."""
    db.put('exp', 'gone', ttl_seconds=0.05)
    db.put('perm', 'here')
    time.sleep(0.1)
    keys = [k for k, _, _ in db.scan_with_ttl()]
    assert 'exp' not in keys
    assert 'perm' in keys


def test_scan_with_ttl_range(db):
    for i in range(10):
        db.put(f'k{i:04d}', f'v{i}', ttl_seconds=100)
    results = list(db.scan_with_ttl('k0003', 'k0007'))
    assert len(results) == 4
    assert all(ttl is not None for _, _, ttl in results)


# ── compact_level ─────────────────────────────────────────────────────────────

def test_compact_level_returns_dict(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(50000):
        db.put(f'k{i:08d}', 'v' * 400)
    time.sleep(3)
    result = db.compact_level(0)
    assert isinstance(result, dict)
    assert 'level' in result and 'compactions_run' in result and 'elapsed_seconds' in result
    assert result['level'] == 0
    db.close()


def test_compact_level_invalid_raises(db):
    with pytest.raises(ValueError):
        db.compact_level(99)
    with pytest.raises(ValueError):
        db.compact_level(-1)


def test_compact_level_data_intact(tmp_path):
    """Data must be readable after compact_level()."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(50000):
        db.put(f'k{i:08d}', f'v{i}')
    time.sleep(3)
    db.compact_level(0)
    for i in [0, 25000, 49999]:
        assert db.get(f'k{i:08d}') == f'v{i}'
    db.close()
