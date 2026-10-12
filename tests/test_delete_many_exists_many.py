"""
Tests for db.delete_many() / mdel() and db.exists_many().
"""
import pytest
from lsm_engine import LSMTree
from lsm_engine.lsm_tree import ReadOnlyError


N = 30


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(N):
        d.put(f'k:{i:04d}', f'val{i}')
    yield d
    d.close()


# ── delete_many ───────────────────────────────────────────────────────────────

def test_delete_many_removes_keys(db):
    db.delete_many([f'k:{i:04d}' for i in range(5)])
    for i in range(5):
        assert db.get(f'k:{i:04d}') is None


def test_delete_many_leaves_others(db):
    db.delete_many([f'k:{i:04d}' for i in range(5)])
    assert db.get('k:0005') == 'val5'
    assert db.get(f'k:{N-1:04d}') == f'val{N-1}'


def test_delete_many_returns_count(db):
    n = db.delete_many([f'k:{i:04d}' for i in range(10)])
    assert n == 10


def test_delete_many_empty_returns_zero(db):
    assert db.delete_many([]) == 0


def test_delete_many_absent_keys_idempotent(db):
    n = db.delete_many(['absent:1', 'absent:2'])
    assert n == 2   # counts ops submitted, not keys that existed
    assert db.get('absent:1') is None


def test_delete_many_already_deleted_idempotent(db):
    db.delete_many(['k:0000'])
    assert db.get('k:0000') is None
    n = db.delete_many(['k:0000'])
    assert n == 1
    assert db.get('k:0000') is None


def test_delete_many_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    for i in range(10):
        d.put(f'k:{i}', str(i))
    d.delete_many([f'k:{i}' for i in range(5)])
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    for i in range(5):
        assert d2.get(f'k:{i}') is None
    for i in range(5, 10):
        assert d2.get(f'k:{i}') is not None
    d2.close()


def test_delete_many_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.delete_many(['k'])
    ro.close()


# ── mdel alias ────────────────────────────────────────────────────────────────

def test_mdel_alias(db):
    n = db.mdel([f'k:{i:04d}' for i in range(3)])
    assert n == 3
    assert all(db.get(f'k:{i:04d}') is None for i in range(3))


# ── exists_many ───────────────────────────────────────────────────────────────

def test_exists_many_all_present(db):
    keys = [f'k:{i:04d}' for i in range(5)]
    flags = db.exists_many(keys)
    assert all(flags[k] is True for k in keys)


def test_exists_many_all_absent(db):
    flags = db.exists_many(['x', 'y', 'z'])
    assert all(v is False for v in flags.values())


def test_exists_many_mixed(db):
    keys = ['k:0001', 'absent', 'k:0002']
    flags = db.exists_many(keys)
    assert flags['k:0001'] is True
    assert flags['k:0002'] is True
    assert flags['absent'] is False


def test_exists_many_after_delete(db):
    db.delete('k:0000')
    flags = db.exists_many(['k:0000', 'k:0001'])
    assert flags['k:0000'] is False
    assert flags['k:0001'] is True


def test_exists_many_empty_input(db):
    assert db.exists_many([]) == {}


def test_exists_many_returns_bool(db):
    flags = db.exists_many(['k:0000'])
    assert isinstance(flags['k:0000'], bool)


def test_exists_many_preserves_all_keys(db):
    keys = ['k:0001', 'missing', 'k:0002']
    flags = db.exists_many(keys)
    assert set(flags.keys()) == set(keys)
