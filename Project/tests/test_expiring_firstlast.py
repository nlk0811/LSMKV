"""
Tests for expiring_soon(), first_key(), and last_key().
"""
import time
import pytest

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(10):
        d.put(f'k{i:04d}', f'v{i}')
    yield d
    d.close()


# ── expiring_soon ─────────────────────────────────────────────────────────────

def test_expiring_soon_finds_short_ttl(db):
    db.put('soon', 'v', ttl_seconds=10)
    db.put('later', 'v', ttl_seconds=300)
    results = list(db.expiring_soon(60))
    keys = [k for k,_,_ in results]
    assert 'soon' in keys
    assert 'later' not in keys


def test_expiring_soon_no_ttl_excluded(db):
    db.put('permanent', 'v')   # no TTL
    db.put('soon', 'v', ttl_seconds=5)
    keys = [k for k,_,_ in db.expiring_soon(60)]
    assert 'permanent' not in keys
    assert 'soon' in keys


def test_expiring_soon_ttl_values_correct(db):
    db.put('k', 'v', ttl_seconds=30)
    results = list(db.expiring_soon(60))
    k, v, ttl = results[0]
    assert v == 'v' and 29 < ttl <= 30


def test_expiring_soon_range(db):
    db.put('ns:a', 'v', ttl_seconds=10)
    db.put('ns:b', 'v', ttl_seconds=10)
    db.put('other:c', 'v', ttl_seconds=10)
    keys = [k for k,_,_ in db.expiring_soon(60, start='ns:', end='ns;')]
    assert 'ns:a' in keys and 'ns:b' in keys
    assert 'other:c' not in keys


def test_expiring_soon_empty_when_no_ttl(db):
    # All fixture keys have no TTL
    assert list(db.expiring_soon(3600)) == []


# ── first_key ─────────────────────────────────────────────────────────────────

def test_first_key_basic(db):
    assert db.first_key() == 'k0000'


def test_first_key_range(db):
    assert db.first_key('k0005', 'k0010') == 'k0005'


def test_first_key_empty_range(db):
    assert db.first_key('zzz') is None


def test_first_key_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert db.first_key() is None
    db.close()


# ── last_key ──────────────────────────────────────────────────────────────────

def test_last_key_basic(db):
    assert db.last_key() == 'k0009'


def test_last_key_range(db):
    assert db.last_key('k0003', 'k0008') == 'k0007'


def test_last_key_empty_range(db):
    assert db.last_key('zzz') is None


def test_last_key_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert db.last_key() is None
    db.close()


def test_first_and_last_symmetric(db):
    """first_key and last_key are exact opposites over a range."""
    fk = db.first_key('k0003', 'k0008')
    lk = db.last_key('k0003', 'k0008')
    assert fk == 'k0003'
    assert lk == 'k0007'
    assert fk < lk
