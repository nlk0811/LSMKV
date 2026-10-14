"""
Tests for set_if_absent(), set_if_present(), getset(), getdel().
"""
import time
import pytest
from lsm_engine import LSMTree
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── set_if_absent (SET NX) ────────────────────────────────────────────────────

def test_set_if_absent_creates_new(db):
    assert db.set_if_absent('k', 'v') is True
    assert db.get('k') == 'v'


def test_set_if_absent_returns_false_if_exists(db):
    db.put('k', 'existing')
    assert db.set_if_absent('k', 'new') is False
    assert db.get('k') == 'existing'


def test_set_if_absent_after_delete(db):
    db.put('k', 'v')
    db.delete('k')
    assert db.set_if_absent('k', 'v2') is True
    assert db.get('k') == 'v2'


def test_set_if_absent_with_ttl_expires(db):
    db.set_if_absent('k', 'v', ttl_seconds=0.05)
    assert db.get('k') == 'v'
    time.sleep(0.1)
    assert db.get('k') is None


def test_set_if_absent_does_not_overwrite_with_ttl_if_exists(db):
    db.put('k', 'existing')
    db.set_if_absent('k', 'new', ttl_seconds=0.05)
    time.sleep(0.1)
    assert db.get('k') == 'existing'  # still alive — original had no TTL


def test_set_if_absent_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.set_if_absent('k', 'v')
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    assert d2.get('k') == 'v'
    d2.close()


def test_set_if_absent_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.set_if_absent('k', 'v')
    ro.close()


# ── set_if_present (SET XX) ───────────────────────────────────────────────────

def test_set_if_present_updates_existing(db):
    db.put('k', 'old')
    assert db.set_if_present('k', 'new') is True
    assert db.get('k') == 'new'


def test_set_if_present_returns_false_if_absent(db):
    assert db.set_if_present('k', 'v') is False
    assert db.get('k') is None


def test_set_if_present_with_ttl(db):
    db.put('k', 'v')
    db.set_if_present('k', 'v2', ttl_seconds=0.05)
    assert db.get('k') == 'v2'
    time.sleep(0.1)
    assert db.get('k') is None


def test_set_if_present_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.set_if_present('k', 'new')
    ro.close()


# ── getset ────────────────────────────────────────────────────────────────────

def test_getset_returns_old_value(db):
    db.put('k', 'old')
    old = db.getset('k', 'new')
    assert old == 'old'
    assert db.get('k') == 'new'


def test_getset_absent_returns_none(db):
    old = db.getset('k', 'v')
    assert old is None
    assert db.get('k') == 'v'


def test_getset_persists_new_value(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'old')
    d.getset('k', 'new')
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    assert d2.get('k') == 'new'
    d2.close()


def test_getset_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.getset('k', 'new')
    ro.close()


# ── getdel ────────────────────────────────────────────────────────────────────

def test_getdel_returns_value_and_deletes(db):
    db.put('k', 'v')
    val = db.getdel('k')
    assert val == 'v'
    assert db.get('k') is None


def test_getdel_absent_returns_none(db):
    assert db.getdel('k') is None


def test_getdel_idempotent(db):
    db.put('k', 'v')
    db.getdel('k')
    assert db.getdel('k') is None  # already gone


def test_getdel_persists_deletion(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.getdel('k')
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    assert d2.get('k') is None
    d2.close()


def test_getdel_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.getdel('k')
    ro.close()
