"""
Tests for db.map_values() — in-place batched value transformation.
"""
import pytest
from lsm_engine import LSMTree
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(20):
        d.put(f'k:{i:04d}', str(i))
    yield d
    d.close()


# ── basic transformation ──────────────────────────────────────────────────────

def test_map_values_transforms_values(db):
    db.map_values(lambda k, v: v + '_x')
    assert db.get('k:0001') == '1_x'
    assert db.get('k:0019') == '19_x'


def test_map_values_returns_changed_count(db):
    n = db.map_values(lambda k, v: v + '_new')
    assert n == 20


def test_map_values_identity_skips_unchanged(db):
    n = db.map_values(lambda k, v: v)
    assert n == 0


def test_map_values_only_counts_actually_changed(db):
    # value '0' maps to '0' (unchanged), rest change
    n = db.map_values(lambda k, v: str(int(v) * 2))
    assert n == 19   # item k:0000 has value '0' → '0', unchanged


# ── delete via None return ────────────────────────────────────────────────────

def test_map_values_none_deletes_key(db):
    db.map_values(lambda k, v: None if k == 'k:0005' else v)
    assert db.get('k:0005') is None
    assert db.get('k:0006') is not None


def test_map_values_delete_counts_correctly(db):
    n = db.map_values(lambda k, v: None if int(v) < 5 else v)
    assert n == 5   # k:0000..k:0004 deleted


def test_map_values_delete_removes_from_scan(db):
    db.map_values(lambda k, v: None if int(v) % 2 == 0 else v)
    remaining = list(db.scan_keys('k:', 'k;'))
    vals = [int(db.get(k)) for k in remaining]
    assert all(x % 2 == 1 for x in vals)


# ── range scoping ─────────────────────────────────────────────────────────────

def test_map_values_range(db):
    db.map_values(lambda k, v: 'marked', 'k:0005', 'k:0010')
    for i in range(5, 10):
        assert db.get(f'k:{i:04d}') == 'marked'
    assert db.get('k:0004') != 'marked'
    assert db.get('k:0010') != 'marked'


def test_map_values_empty_range(db):
    n = db.map_values(lambda k, v: v + 'x', 'zzz:', 'zzz;')
    assert n == 0


# ── batch_size parameter ──────────────────────────────────────────────────────

def test_map_values_batch_size_1(db):
    n = db.map_values(lambda k, v: v + '_b', batch_size=1)
    assert n == 20
    assert all(db.get(f'k:{i:04d}').endswith('_b') for i in range(20))


def test_map_values_batch_size_3(db):
    n = db.map_values(lambda k, v: v + '_c', batch_size=3)
    assert n == 20


def test_map_values_batch_larger_than_total(db):
    n = db.map_values(lambda k, v: v + '_d', batch_size=10000)
    assert n == 20


# ── persistence ───────────────────────────────────────────────────────────────

def test_map_values_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    for i in range(5):
        d.put(f'p:{i}', str(i))
    d.map_values(lambda k, v: v + '_saved')
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    for i in range(5):
        assert d2.get(f'p:{i}').endswith('_saved')
    d2.close()


# ── read-only guard ───────────────────────────────────────────────────────────

def test_map_values_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.map_values(lambda k, v: v + 'x')
    ro.close()


# ── edge cases ────────────────────────────────────────────────────────────────

def test_map_values_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.map_values(lambda k, v: v) == 0
    d.close()


def test_map_values_uses_key_in_transform(db):
    db.map_values(lambda k, v: k)  # replace value with key
    assert db.get('k:0007') == 'k:0007'
