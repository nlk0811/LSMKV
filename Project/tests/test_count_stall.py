"""
Tests for db.count() and event-based write-stall wakeup.
"""
import os
import time
import threading
import pytest

from lsm_engine.lsm_tree import LSMTree, L0_STOP_TRIGGER


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        d.put(f'k{i:04d}', f'v{i}')
    yield d
    d.close()


# ── db.count() ────────────────────────────────────────────────────────────────

def test_count_all(db):
    assert db.count() == 100


def test_count_range(db):
    # k000x → 10 keys (k0000–k0009)
    assert db.count('k000', 'k001') == 10


def test_count_unbounded(db):
    assert db.count() == 100


def test_count_after_delete(db):
    for i in range(10):
        db.delete(f'k{i:04d}')
    assert db.count() == 90


def test_count_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert db.count() == 0
    db.close()


def test_count_empty_range(db):
    assert db.count('zzz', 'zzz9') == 0


def test_count_prefix(db):
    # All keys start with 'k' — but all 100 are in range
    assert db.count('k', None) == 100


def test_count_with_flushed_data(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'p{i:06d}', 'v')
    db.flush()
    assert db.count() == 5000
    db.close()


# ── Event-based write stall wakeup ─────────────────────────────────────────────

def test_l0_drained_event_exists(db):
    """_l0_drained must be a threading.Event."""
    assert hasattr(db, '_l0_drained')
    assert hasattr(db._l0_drained, 'wait')
    assert hasattr(db._l0_drained, 'set')


def test_l0_drained_wakes_waiter(db):
    """Setting _l0_drained must wake a waiting thread immediately."""
    woke = [False]
    woke_time = [None]
    start_time = time.monotonic()

    def waiter():
        db._l0_drained.wait(timeout=2.0)
        woke[0] = True
        woke_time[0] = time.monotonic()

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.02)
    db._l0_drained.set()
    t.join(timeout=1.0)

    assert woke[0], 'Waiter was not woken by set()'
    assert woke_time[0] - start_time < 0.5, 'Wakeup took too long'


def test_stall_wakeup_no_deadlock(tmp_path):
    """Write stall release must not deadlock even under concurrent load."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    errors = []

    def writer():
        try:
            for i in range(100):
                db.put(f'stall{i:06d}', 'v' * 400)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)

    assert errors == [], f'Writer errors: {errors}'
    db.close()
