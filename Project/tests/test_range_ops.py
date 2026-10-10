"""
Tests for range_delete(), ceiling_key(), and floor_key().
"""
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        d.put(f'k{i:04d}', f'v{i}')
    yield d
    d.close()


# ── range_delete ──────────────────────────────────────────────────────────────

def test_range_delete_basic(db):
    n = db.range_delete('k0010', 'k0020')
    assert n == 10
    for i in range(10, 20):
        assert db.get(f'k{i:04d}') is None
    assert db.get('k0009') == 'v9'   # before range
    assert db.get('k0020') == 'v20'  # at end (exclusive)


def test_range_delete_returns_count(db):
    n = db.range_delete('k0000', 'k0010')
    assert n == 10


def test_range_delete_empty_range(db):
    n = db.range_delete('zzz0', 'zzz9')
    assert n == 0


def test_range_delete_unbounded_start(db):
    n = db.range_delete(None, 'k0005')
    assert n == 5   # k0000–k0004
    assert db.get('k0000') is None
    assert db.get('k0005') is not None


def test_range_delete_unbounded_end(db):
    before = db.count()
    n = db.range_delete('k0090', None)
    assert n == 10   # k0090–k0099
    assert db.count() == before - 10


def test_range_delete_all(db):
    n = db.range_delete()
    assert n == 100
    assert db.count() == 0


def test_range_delete_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.range_delete('k', 'z')
    ro.close()


def test_range_delete_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(20):
        db.put(f'k{i:04d}', f'v{i}')
    db.range_delete('k0005', 'k0015')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    for i in range(5, 15):
        assert db2.get(f'k{i:04d}') is None
    for i in [0, 4, 15, 19]:
        assert db2.get(f'k{i:04d}') == f'v{i}'
    db2.close()


# ── ceiling_key ────────────────────────────────────────────────────────────────

def test_ceiling_exact_match(db):
    assert db.ceiling_key('k0050') == 'k0050'


def test_ceiling_between_keys(db):
    assert db.ceiling_key('k0050a') == 'k0051'


def test_ceiling_before_all_keys(db):
    assert db.ceiling_key('aaa') == 'k0000'


def test_ceiling_past_all_keys(db):
    assert db.ceiling_key('zzz') is None


def test_ceiling_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert db.ceiling_key('any') is None
    db.close()


def test_ceiling_first_key(db):
    assert db.ceiling_key('k0000') == 'k0000'


# ── floor_key ──────────────────────────────────────────────────────────────────

def test_floor_exact_match(db):
    assert db.floor_key('k0050') == 'k0050'


def test_floor_between_keys(db):
    assert db.floor_key('k0050a') == 'k0050'


def test_floor_past_all_keys(db):
    assert db.floor_key('zzz') == 'k0099'


def test_floor_before_all_keys(db):
    assert db.floor_key('aaa') is None


def test_floor_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert db.floor_key('any') is None
    db.close()


def test_floor_last_key(db):
    assert db.floor_key('k0099') == 'k0099'


def test_ceiling_and_floor_symmetric(db):
    """ceiling(k) and floor(k) both return k when k exists."""
    for i in [0, 50, 99]:
        k = f'k{i:04d}'
        assert db.ceiling_key(k) == k
        assert db.floor_key(k) == k
