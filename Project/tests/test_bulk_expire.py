"""
Tests for db.bulk_expire() and py.typed marker.
"""
import os
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── bulk_expire ───────────────────────────────────────────────────────────────

def test_bulk_expire_sets_ttl(db):
    for i in range(20):
        db.put(f'ns:{i:04d}', f'v{i}')
    n = db.bulk_expire('ns:', ttl_seconds=0.05)
    assert n == 20
    # Still readable before expiry
    assert db.has('ns:0000')
    time.sleep(0.1)
    # All expired
    assert sum(1 for _ in db.prefix_scan('ns:')) == 0


def test_bulk_expire_returns_count(db):
    for i in range(10):
        db.put(f'k{i:03d}', 'v')
    assert db.bulk_expire('k', ttl_seconds=100) == 10


def test_bulk_expire_empty_prefix(db):
    assert db.bulk_expire('nonexistent:', ttl_seconds=100) == 0


def test_bulk_expire_does_not_affect_other_keys(db):
    for i in range(5):
        db.put(f'target:{i}', 'v')
    for i in range(5):
        db.put(f'safe:{i}', 'v')
    db.bulk_expire('target:', ttl_seconds=0.05)
    time.sleep(0.1)
    assert sum(1 for _ in db.prefix_scan('target:')) == 0
    assert sum(1 for _ in db.prefix_scan('safe:')) == 5


def test_bulk_expire_large_prefix(db):
    """bulk_expire on > 1000 keys must execute multiple pipeline batches."""
    for i in range(2000):
        db.put(f'big:{i:06d}', f'v{i}')
    n = db.bulk_expire('big:', ttl_seconds=0.05)
    assert n == 2000
    time.sleep(0.1)
    assert sum(1 for _ in db.prefix_scan('big:')) == 0


def test_bulk_expire_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(10):
        db.put(f'k{i}', f'v{i}')
    db.bulk_expire('k', ttl_seconds=3600)   # 1 hour TTL
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    # All keys still present (not expired yet)
    assert db2.count('k', None) == 10
    # All have a TTL ~3600s
    for i in range(10):
        r = db2.ttl_remaining(f'k{i}')
        assert r is not None and 3590 < r <= 3601
    db2.close()


def test_bulk_expire_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.bulk_expire('k', ttl_seconds=100)
    ro.close()


# ── py.typed marker ───────────────────────────────────────────────────────────

def test_py_typed_marker_exists():
    """lsm_engine must include py.typed for PEP 561 compliance."""
    import lsm_engine
    pkg_dir = os.path.dirname(lsm_engine.__file__)
    marker = os.path.join(pkg_dir, 'py.typed')
    assert os.path.exists(marker), f'py.typed not found at {marker}'
