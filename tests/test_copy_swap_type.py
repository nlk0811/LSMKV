"""
Tests for db.copy(), db.swap(), and db.key_type().
"""
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── copy ─────────────────────────────────────────────────────────────────────

def test_copy_basic(db):
    db.put('src', 'hello')
    assert db.copy('src', 'dst') is True
    assert db.get('dst') == 'hello'


def test_copy_original_unchanged(db):
    db.put('src', 'val')
    db.copy('src', 'dst')
    assert db.get('src') == 'val'


def test_copy_absent_key_returns_false(db):
    assert db.copy('absent', 'dst') is False
    assert db.get('dst') is None


def test_copy_overwrites_existing(db):
    db.put('src', 'new_val')
    db.put('dst', 'old_val')
    db.copy('src', 'dst')
    assert db.get('dst') == 'new_val'


def test_copy_with_ttl(db):
    db.put('src', 'val')
    db.copy('src', 'dst', ttl_seconds=0.05)
    assert db.get('dst') == 'val'
    time.sleep(0.1)
    assert db.get('dst') is None   # expired


def test_copy_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.copy('k', 'k2')
    ro.close()


def test_copy_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('src', 'val')
    db.copy('src', 'dst')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('dst') == 'val'
    db2.close()


# ── swap ─────────────────────────────────────────────────────────────────────

def test_swap_basic(db):
    db.put('a', 'val_a')
    db.put('b', 'val_b')
    assert db.swap('a', 'b') is True
    assert db.get('a') == 'val_b'
    assert db.get('b') == 'val_a'


def test_swap_absent_key_returns_false(db):
    db.put('a', 'val')
    assert db.swap('a', 'missing') is False
    assert db.get('a') == 'val'   # unchanged


def test_swap_both_absent_returns_false(db):
    assert db.swap('x', 'y') is False


def test_swap_same_value(db):
    db.put('a', 'same'); db.put('b', 'same')
    assert db.swap('a', 'b') is True
    assert db.get('a') == 'same' and db.get('b') == 'same'


def test_swap_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('x', 'X'); db.put('y', 'Y')
    db.swap('x', 'y')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('x') == 'Y' and db2.get('y') == 'X'
    db2.close()


def test_swap_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('a', 'v'); db.put('b', 'w')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.swap('a', 'b')
    ro.close()


# ── key_type ─────────────────────────────────────────────────────────────────

def test_key_type_string(db):
    db.put('k', 'v')
    assert db.key_type('k') == 'string'


def test_key_type_none(db):
    assert db.key_type('absent') == 'none'


def test_key_type_none_after_delete(db):
    db.put('k', 'v')
    db.delete('k')
    assert db.key_type('k') == 'none'


def test_key_type_expired(db):
    db.put('k', 'v', ttl_seconds=0.05)
    time.sleep(0.1)
    assert db.key_type('k') == 'expired'


def test_key_type_string_before_expiry(db):
    db.put('k', 'v', ttl_seconds=10)
    assert db.key_type('k') == 'string'
