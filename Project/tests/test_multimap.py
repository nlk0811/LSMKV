"""
Tests for MultiMap (db.multimap()) — persistent one-to-many map.
"""
import pytest
from lsm_engine import LSMTree, MultiMap


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def mm(db):
    m = db.multimap('tags')
    m.add('python',   'art:1')
    m.add('python',   'art:2')
    m.add('database', 'art:1')
    m.add('web',      'art:3')
    return m


# ── Factory ───────────────────────────────────────────────────────────────────

def test_multimap_from_db(db):
    assert isinstance(db.multimap('test'), MultiMap)


def test_multimap_repr(mm):
    r = repr(mm)
    assert 'MultiMap' in r and 'tags' in r


# ── add / contains / count ────────────────────────────────────────────────────

def test_add_returns_true_for_new(mm):
    assert mm.add('python', 'art:99') is True


def test_add_returns_false_for_duplicate(mm):
    assert mm.add('python', 'art:1') is False


def test_add_many(mm):
    n = mm.add_many('python', 'art:3', 'art:4', 'art:5')
    assert n == 3


def test_contains_existing(mm):
    assert mm.contains('python', 'art:1')


def test_contains_absent_value(mm):
    assert not mm.contains('python', 'art:99')


def test_contains_absent_key(mm):
    assert not mm.contains('nonexistent', 'art:1')


def test_count_per_key(mm):
    assert mm.count('python') == 2
    assert mm.count('database') == 1
    assert mm.count('missing') == 0


def test_total_count(mm):
    assert mm.total_count() == 4


# ── get ───────────────────────────────────────────────────────────────────────

def test_get_returns_sorted(mm):
    result = mm.get('python')
    assert result == sorted(result)
    assert set(result) == {'art:1', 'art:2'}


def test_get_missing_key_empty(mm):
    assert mm.get('nonexistent') == []


def test_get_with_colon_in_value(db):
    """Values containing ':' must be stored and retrieved correctly."""
    m = db.multimap('m')
    m.add('k', 'val:with:colons')
    assert m.get('k') == ['val:with:colons']


def test_get_with_colon_in_key(db):
    """Keys containing ':' must be stored and retrieved correctly."""
    m = db.multimap('m')
    m.add('key:with:colons', 'val')
    assert m.get('key:with:colons') == ['val']


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_existing(mm):
    assert mm.remove('python', 'art:1') is True
    assert not mm.contains('python', 'art:1')
    assert mm.count('python') == 1


def test_remove_absent(mm):
    assert mm.remove('python', 'art:99') is False


def test_remove_all(mm):
    n = mm.remove_all('python')
    assert n == 2
    assert mm.get('python') == []
    assert mm.total_count() == 2   # database + web remain


def test_remove_all_absent_key(mm):
    assert mm.remove_all('nonexistent') == 0


def test_clear(mm):
    n = mm.clear()
    assert n == 4
    assert mm.total_count() == 0


# ── get_all_keys ──────────────────────────────────────────────────────────────

def test_get_all_keys(mm):
    keys = mm.get_all_keys()
    assert sorted(keys) == ['database', 'python', 'web']


def test_get_all_keys_after_remove_all(mm):
    mm.remove_all('python')
    keys = mm.get_all_keys()
    assert 'python' not in keys


# ── items / as_dict ───────────────────────────────────────────────────────────

def test_items(mm):
    pairs = list(mm.items())
    keys = [k for k, _ in pairs]
    assert 'python' in keys and 'database' in keys


def test_as_dict(mm):
    d = mm.as_dict()
    assert set(d['python']) == {'art:1', 'art:2'}
    assert d['database'] == ['art:1']


# ── persistence ───────────────────────────────────────────────────────────────

def test_multimap_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    m    = db.multimap('inv')
    m.add('tag:a', 'doc:1')
    m.add('tag:a', 'doc:2')
    m.add('tag:b', 'doc:1')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    m2  = db2.multimap('inv')
    assert set(m2.get('tag:a')) == {'doc:1', 'doc:2'}
    assert m2.total_count() == 3
    db2.close()
