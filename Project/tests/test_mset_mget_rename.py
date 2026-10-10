"""
Tests for db.mset(), db.mget(), and db.rename().
"""
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── mset ──────────────────────────────────────────────────────────────────────

def test_mset_basic(db):
    db.mset({'k1': 'v1', 'k2': 'v2', 'k3': 'v3'})
    assert db.get('k1') == 'v1'
    assert db.get('k2') == 'v2'
    assert db.get('k3') == 'v3'


def test_mset_empty_dict(db):
    db.mset({})   # must not raise


def test_mset_with_ttl(db):
    db.mset({'k1': 'v1', 'k2': 'v2'}, ttl_seconds=0.05)
    assert db.has('k1') and db.has('k2')
    time.sleep(0.1)
    assert not db.has('k1') and not db.has('k2')


def test_mset_overwrite(db):
    db.mset({'k': 'original'})
    db.mset({'k': 'updated'})
    assert db.get('k') == 'updated'


def test_mset_large(db):
    mapping = {f'bulk:{i:06d}': f'val{i}' for i in range(1000)}
    db.mset(mapping)
    for i in [0, 500, 999]:
        assert db.get(f'bulk:{i:06d}') == f'val{i}'


def test_mset_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.mset({'a': '1', 'b': '2'})
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('a') == '1' and db2.get('b') == '2'
    db2.close()


def test_mset_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.mset({'k': 'v'})
    ro.close()


# ── mget ──────────────────────────────────────────────────────────────────────

def test_mget_basic(db):
    db.mset({'k1': 'v1', 'k2': 'v2'})
    result = db.mget(['k1', 'k2', 'missing'])
    assert result == {'k1': 'v1', 'k2': 'v2', 'missing': None}


def test_mget_empty_list(db):
    assert db.mget([]) == {}


def test_mget_all_missing(db):
    result = db.mget(['a', 'b', 'c'])
    assert all(v is None for v in result.values())


def test_mget_returns_dict(db):
    result = db.mget(['x'])
    assert isinstance(result, dict)


def test_mget_same_as_get_many(db):
    db.mset({'x': '1', 'y': '2'})
    assert db.mget(['x', 'y', 'z']) == db.get_many(['x', 'y', 'z'])


# ── rename ────────────────────────────────────────────────────────────────────

def test_rename_basic(db):
    db.put('old', 'value')
    result = db.rename('old', 'new')
    assert result is True
    assert db.get('old') is None
    assert db.get('new') == 'value'


def test_rename_absent_key(db):
    result = db.rename('nonexistent', 'dest')
    assert result is False
    assert db.get('dest') is None


def test_rename_preserves_value(db):
    db.put('src', 'the_data' * 100)
    db.rename('src', 'dst')
    assert db.get('dst') == 'the_data' * 100


def test_rename_overwrites_destination(db):
    db.put('src', 'new_val')
    db.put('dst', 'old_val')
    db.rename('src', 'dst')
    assert db.get('dst') == 'new_val'
    assert db.get('src') is None


def test_rename_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('old', 'val')
    db.rename('old', 'new')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('old') is None
    assert db2.get('new') == 'val'
    db2.close()


def test_rename_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.rename('k', 'k2')
    ro.close()
