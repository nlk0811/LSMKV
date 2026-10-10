"""
Tests for db.expire(), db.expireat(), and db.persist().
These complete the TTL management API (Redis-compatible).
"""
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── expire (alias for touch) ──────────────────────────────────────────────────

def test_expire_extends_ttl(db):
    db.put('k', 'v', ttl_seconds=0.05)
    db.expire('k', 1.0)
    time.sleep(0.07)
    assert db.has('k'), 'expire must extend TTL'


def test_expire_returns_true_for_existing(db):
    db.put('k', 'v')
    assert db.expire('k', 100) is True


def test_expire_returns_false_for_absent(db):
    assert db.expire('absent', 100) is False


def test_expire_does_not_create(db):
    db.expire('new', 100)
    assert db.get('new') is None


def test_expire_preserves_value(db):
    db.put('k', 'data')
    db.expire('k', 100)
    assert db.get('k') == 'data'


def test_expire_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.expire('k', 100)
    ro.close()


# ── expireat ──────────────────────────────────────────────────────────────────

def test_expireat_sets_absolute_expiry(db):
    db.put('k', 'v')
    future = time.time() + 10.0
    assert db.expireat('k', future) is True
    r = db.ttl_remaining('k')
    assert r is not None and 9.0 < r <= 10.0, f'Expected ~10s, got {r}'


def test_expireat_returns_false_for_absent(db):
    assert db.expireat('absent', time.time() + 100) is False


def test_expireat_past_timestamp_expires_quickly(db):
    db.put('k', 'v')
    past = time.time() - 1
    db.expireat('k', past)   # expires in ~0 seconds
    time.sleep(0.05)
    assert not db.has('k')


def test_expireat_preserves_value(db):
    db.put('k', 'data')
    db.expireat('k', time.time() + 3600)
    assert db.get('k') == 'data'


# ── persist ───────────────────────────────────────────────────────────────────

def test_persist_removes_ttl(db):
    db.put('k', 'v', ttl_seconds=100)
    assert db.ttl_remaining('k') is not None
    assert db.persist('k') is True
    assert db.ttl_remaining('k') is None


def test_persist_key_still_readable(db):
    db.put('k', 'val', ttl_seconds=100)
    db.persist('k')
    assert db.get('k') == 'val'


def test_persist_no_ttl_returns_false(db):
    db.put('k', 'v')   # no TTL
    assert db.persist('k') is False


def test_persist_absent_key_returns_false(db):
    assert db.persist('absent') is False


def test_persist_prevents_expiry(db):
    db.put('k', 'v', ttl_seconds=0.05)
    db.persist('k')   # remove TTL
    time.sleep(0.1)
    assert db.has('k'), 'persist must make key permanent'


def test_persist_after_expire(db):
    """expire then persist: key stays permanent."""
    db.put('k', 'v')
    db.expire('k', 0.05)   # set TTL
    db.persist('k')         # remove TTL
    time.sleep(0.1)
    assert db.has('k'), 'persist after expire must make key permanent'


def test_persist_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v', ttl_seconds=100)
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.persist('k')
    ro.close()
