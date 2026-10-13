"""
Tests for db.rename_many() and db.touch_many() batch operations.
"""
import time
import pytest
from lsm_engine import LSMTree
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(20):
        d.put(f'src:{i:04d}', f'val{i}')
    yield d
    d.close()


# ── rename_many ───────────────────────────────────────────────────────────────

def test_rename_many_basic(db):
    n = db.rename_many({f'src:{i:04d}': f'dst:{i:04d}' for i in range(5)})
    assert n == 5


def test_rename_many_src_gone(db):
    db.rename_many({f'src:{i:04d}': f'dst:{i:04d}' for i in range(5)})
    for i in range(5):
        assert db.get(f'src:{i:04d}') is None


def test_rename_many_dst_has_value(db):
    db.rename_many({f'src:{i:04d}': f'dst:{i:04d}' for i in range(5)})
    for i in range(5):
        assert db.get(f'dst:{i:04d}') == f'val{i}'


def test_rename_many_untouched_keys_intact(db):
    db.rename_many({'src:0000': 'dst:0000'})
    for i in range(1, 20):
        assert db.get(f'src:{i:04d}') == f'val{i}'


def test_rename_many_absent_src_skipped(db):
    n = db.rename_many({'missing': 'new', 'src:0001': 'dst:0001'})
    assert n == 1
    assert db.get('new') is None
    assert db.get('dst:0001') == 'val1'


def test_rename_many_all_absent(db):
    assert db.rename_many({'x': 'y', 'a': 'b'}) == 0


def test_rename_many_empty(db):
    assert db.rename_many({}) == 0


def test_rename_many_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('old', 'value')
    d.rename_many({'old': 'new'})
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    assert d2.get('old') is None
    assert d2.get('new') == 'value'
    d2.close()


def test_rename_many_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.rename_many({'k': 'k2'})
    ro.close()


# ── touch_many ────────────────────────────────────────────────────────────────

def test_touch_many_returns_count(db):
    n = db.touch_many([f'src:{i:04d}' for i in range(5)], ttl_seconds=60)
    assert n == 5


def test_touch_many_absent_skipped(db):
    n = db.touch_many(['absent:1', 'absent:2'], ttl_seconds=60)
    assert n == 0


def test_touch_many_preserves_value(db):
    db.touch_many(['src:0000'], ttl_seconds=60)
    assert db.get('src:0000') == 'val0'


def test_touch_many_ttl_applied(db):
    db.touch_many([f'src:{i:04d}' for i in range(3)], ttl_seconds=0.1)
    time.sleep(0.2)
    for i in range(3):
        assert db.get(f'src:{i:04d}') is None


def test_touch_many_refreshes_expiring_keys(db):
    db.put('expiring', 'v', ttl_seconds=0.1)
    db.touch_many(['expiring'], ttl_seconds=60)
    time.sleep(0.2)
    assert db.get('expiring') == 'v'   # still alive after touch refreshed TTL


def test_touch_many_mixed_existing_absent(db):
    n = db.touch_many(['src:0000', 'absent', 'src:0001'], ttl_seconds=60)
    assert n == 2


def test_touch_many_empty_input(db):
    assert db.touch_many([], ttl_seconds=60) == 0


def test_touch_many_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.touch_many(['k'], ttl_seconds=60)
    ro.close()
