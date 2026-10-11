"""
Tests for db.counter() (Counter class) and db.rate_limit().
"""
import time
import threading
import pytest

from lsm_engine import LSMTree, Counter
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── Counter ───────────────────────────────────────────────────────────────────

def test_counter_starts_at_zero(db):
    c = db.counter('k')
    assert c.value == 0


def test_counter_incr(db):
    c = db.counter('k')
    assert c.incr() == 1
    assert c.incr() == 2
    assert c.incr(5) == 7


def test_counter_decr(db):
    c = db.counter('k')
    c.reset(10)
    assert c.decr() == 9
    assert c.decr(3) == 6


def test_counter_reset(db):
    c = db.counter('k')
    c.incr(); c.incr(); c.incr()
    c.reset()
    assert c.value == 0


def test_counter_reset_to_value(db):
    c = db.counter('k')
    c.reset(100)
    assert c.value == 100


def test_counter_set(db):
    c = db.counter('k')
    c.set(42)
    assert int(c) == 42


def test_counter_repr(db):
    c = db.counter('hits')
    c.incr(5)
    r = repr(c)
    assert 'Counter' in r and 'hits' in r and '5' in r


def test_counter_int_protocol(db):
    c = db.counter('k')
    c.reset(7)
    assert int(c) == 7


def test_counter_context_manager(db):
    with db.counter('k') as c:
        c.incr()
    assert db.get('k') == '1'


def test_counter_concurrent(db):
    """Concurrent increments must produce correct total."""
    c = db.counter('total')
    errors = []

    def worker():
        try:
            for _ in range(100):
                c.incr()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    assert c.value == 400


def test_counter_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    c = db.counter('k')
    c.incr(42)
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.counter('k').value == 42
    db2.close()


def test_counter_from_db_factory(db):
    assert isinstance(db.counter('k'), Counter)


# ── rate_limit ────────────────────────────────────────────────────────────────

def test_rate_limit_allows_within_limit(db):
    for _ in range(5):
        assert db.rate_limit('user:1', 5, 60) is True


def test_rate_limit_rejects_over_limit(db):
    for _ in range(5):
        db.rate_limit('user:1', 5, 60)
    assert db.rate_limit('user:1', 5, 60) is False


def test_rate_limit_separate_keys_independent(db):
    for _ in range(5):
        db.rate_limit('user:1', 5, 60)
    assert db.rate_limit('user:2', 5, 60) is True   # different key


def test_rate_limit_high_limit(db):
    for _ in range(1000):
        assert db.rate_limit('generous', 1000, 60) is True


def test_rate_limit_limit_one(db):
    assert db.rate_limit('strict', 1, 60) is True
    assert db.rate_limit('strict', 1, 60) is False
