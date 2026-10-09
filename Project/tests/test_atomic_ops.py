"""
Tests for atomic read-modify-write operations:
  - update(key, fn)         — apply a function atomically
  - compare_and_swap(key, expected, new_value) — atomic CAS
  - increment(key, amount)  — atomic integer increment
"""
import os
import shutil
import threading
import pytest

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── update() ─────────────────────────────────────────────────────────────────

def test_update_existing_key(db):
    db.put('k', 'hello')
    result = db.update('k', lambda v: v.upper())
    assert result == 'HELLO'
    assert db.get('k') == 'HELLO'


def test_update_missing_key_receives_none(db):
    result = db.update('missing', lambda v: 'default' if v is None else v)
    assert result == 'default'
    assert db.get('missing') == 'default'


def test_update_returns_none_deletes_key(db):
    db.put('del', 'bye')
    result = db.update('del', lambda v: None)
    assert result is None
    assert db.get('del') is None


def test_update_is_atomic_under_concurrency(db):
    """All concurrent updates must be serialized — no lost updates."""
    db.put('counter', '0')
    errors = []

    def worker():
        for _ in range(100):
            try:
                db.update('counter', lambda v: str(int(v or 0) + 1))
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    assert db.get('counter') == '400', f'got {db.get("counter")}'


def test_update_with_ttl(db):
    """update() with ttl_seconds applies TTL to the new value."""
    import time
    db.update('ttl_key', lambda v: 'expires', ttl_seconds=0.05)
    assert db.get('ttl_key') == 'expires'
    time.sleep(0.1)
    assert db.get('ttl_key') is None


def test_update_persists_across_reopen(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.update('persistent', lambda v: 'value')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('persistent') == 'value'
    db2.close()


# ── compare_and_swap() ────────────────────────────────────────────────────────

def test_cas_success(db):
    db.put('status', 'open')
    assert db.compare_and_swap('status', 'open', 'closed') is True
    assert db.get('status') == 'closed'


def test_cas_failure_wrong_expected(db):
    db.put('status', 'open')
    assert db.compare_and_swap('status', 'locked', 'closed') is False
    assert db.get('status') == 'open'   # unchanged


def test_cas_missing_key_with_none_expected(db):
    assert db.compare_and_swap('lock', None, 'owner') is True
    assert db.get('lock') == 'owner'


def test_cas_missing_key_with_wrong_expected(db):
    assert db.compare_and_swap('lock', 'someone', 'owner') is False
    assert db.get('lock') is None


def test_cas_delete_on_match(db):
    db.put('temp', 'value')
    assert db.compare_and_swap('temp', 'value', None) is True
    assert db.get('temp') is None


def test_cas_concurrent_only_one_wins(db):
    """Under concurrency, only one CAS should succeed."""
    db.put('resource', 'free')
    wins = []

    def try_acquire():
        if db.compare_and_swap('resource', 'free', 'taken'):
            wins.append(True)

    threads = [threading.Thread(target=try_acquire) for _ in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Exactly one thread should have won the CAS
    assert len(wins) == 1, f'Expected 1 winner, got {len(wins)}'
    assert db.get('resource') == 'taken'


# ── increment() ────────────────────────────────────────────────────────────────

def test_increment_from_zero(db):
    assert db.increment('n') == 1
    assert db.increment('n') == 2
    assert db.increment('n') == 3


def test_increment_existing_value(db):
    db.put('score', '10')
    assert db.increment('score', 5) == 15


def test_increment_negative(db):
    db.put('balance', '100')
    result = db.increment('balance', -30)
    assert result == 70


def test_increment_concurrent_exactness(db):
    """N concurrent increments must produce exactly N as the final value."""
    db.put('hits', '0')

    def worker():
        for _ in range(50):
            db.increment('hits')

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert db.get('hits') == '400'


def test_increment_invalid_value_raises(db):
    db.put('bad', 'not_a_number')
    with pytest.raises(ValueError):
        db.increment('bad')
