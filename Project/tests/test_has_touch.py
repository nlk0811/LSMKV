"""
Tests for db.has(), db.touch(), and immediate compaction cascade wakeup.
"""
import os
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── db.has() ──────────────────────────────────────────────────────────────────

def test_has_existing_key(db):
    db.put('k', 'v')
    assert db.has('k') is True


def test_has_missing_key(db):
    assert db.has('absent') is False


def test_has_deleted_key(db):
    db.put('k', 'v')
    db.delete('k')
    assert db.has('k') is False


def test_has_expired_ttl(db):
    db.put('k', 'v', ttl_seconds=0.05)
    assert db.has('k') is True
    time.sleep(0.1)
    assert db.has('k') is False


def test_has_after_flush(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'key{i:06d}', 'v' * 1000)
    db.flush()
    assert db.has('key000000') is True
    assert db.has('key999999') is False
    db.close()


# ── db.touch() ────────────────────────────────────────────────────────────────

def test_touch_extends_ttl(db):
    db.put('k', 'v', ttl_seconds=0.05)
    time.sleep(0.03)
    db.touch('k', ttl_seconds=1.0)   # extend well beyond original TTL
    time.sleep(0.05)                   # original TTL would have expired here
    assert db.has('k'), 'touch must extend TTL'
    assert db.get('k') == 'v', 'value must be unchanged after touch'


def test_touch_returns_true_for_existing(db):
    db.put('k', 'v', ttl_seconds=100)
    assert db.touch('k', ttl_seconds=200) is True


def test_touch_returns_false_for_absent(db):
    assert db.touch('missing', ttl_seconds=100) is False
    assert db.has('missing') is False   # must not create


def test_touch_does_not_create_missing_key(db):
    db.touch('new_key', ttl_seconds=100)
    assert db.get('new_key') is None


def test_touch_preserves_value(db):
    db.put('k', 'original_value', ttl_seconds=100)
    db.touch('k', ttl_seconds=200)
    assert db.get('k') == 'original_value'


def test_touch_on_non_ttl_key_adds_ttl(db):
    """touch() on a key without TTL should add one."""
    db.put('k', 'val')   # no TTL
    db.touch('k', ttl_seconds=0.05)
    time.sleep(0.1)
    assert db.has('k') is False   # now has TTL and should expire


def test_touch_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v', ttl_seconds=0.1)
    db.touch('k', ttl_seconds=3600)   # extend to 1 hour
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.has('k'), 'touched TTL must persist after reopen'
    db2.close()


def test_touch_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.touch('k', ttl_seconds=100)
    ro.close()


# ── Compaction cascade ────────────────────────────────────────────────────────

def test_compaction_cascade_triggers(tmp_path):
    """After L0→L1 compaction, the bg loop must re-check for L1→L2 quickly."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    # Write enough to overflow L1 budget (10 MB)
    for i in range(50000):
        db.put(f'key{i:08d}', 'v' * 400)
    time.sleep(5)
    s = db.stats()
    # If cascade works, we may see 2+ compactions (L0→L1, L1→L2)
    # Without cascade fix, second compaction would wait up to 2s
    assert s['compactions'] >= 1, f'Expected at least 1 compaction, got {s["compactions"]}'
    # All data must be correct after cascade
    for i in [0, 25000, 49999]:
        assert db.get(f'key{i:08d}') == 'v' * 400
    db.close()
