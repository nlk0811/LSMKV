"""
Tests for Transaction (optimistic concurrency) and atomic_max/atomic_min.
"""
import threading
import pytest

from lsm_engine import LSMTree, Transaction
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── Transaction basics ────────────────────────────────────────────────────────

def test_transaction_commit_success(db):
    db.put('a', '10')
    with db.transaction() as txn:
        val = int(txn.get('a'))
        txn.put('a', str(val + 1))
    assert txn.committed is True
    assert db.get('a') == '11'


def test_transaction_get_observes_value(db):
    db.put('k', 'v')
    txn = db.transaction()
    assert txn.get('k') == 'v'


def test_transaction_has(db):
    db.put('k', 'v')
    txn = db.transaction()
    assert txn.has('k') is True
    assert txn.has('absent') is False


def test_transaction_commit_applies_batch(db):
    txn = db.transaction()
    txn.put('k1', 'v1').put('k2', 'v2').delete('k3')
    db.put('k3', 'old')
    txn.commit()
    assert db.get('k1') == 'v1'
    assert db.get('k2') == 'v2'
    assert db.get('k3') is None


def test_transaction_empty_commit(db):
    txn = db.transaction()
    assert txn.commit() is True   # no writes, no reads — trivially succeeds


def test_transaction_rollback(db):
    db.put('k', 'original')
    txn = db.transaction()
    txn.get('k')
    txn.put('k', 'modified')
    txn.rollback()
    txn.commit()   # commit after rollback — no writes
    assert db.get('k') == 'original'


def test_transaction_repr(db):
    txn = db.transaction()
    txn.get('k')
    txn.put('k', 'v')
    r = repr(txn)
    assert 'Transaction' in r


def test_transaction_len(db):
    txn = db.transaction()
    txn.put('k1', 'v1').put('k2', 'v2')
    assert len(txn) == 2


# ── Conflict detection ────────────────────────────────────────────────────────

def test_transaction_conflict_aborts(db):
    db.put('x', '100')
    txn = db.transaction()
    txn.get('x')       # observe x=100
    db.put('x', '999') # another writer
    txn.put('x', '200')
    assert txn.commit() is False
    assert db.get('x') == '999'   # original writer's value preserved


def test_transaction_no_conflict_when_read_unchanged(db):
    db.put('x', '100')
    txn = db.transaction()
    txn.get('x')
    # No concurrent modification
    txn.put('x', '200')
    assert txn.commit() is True
    assert db.get('x') == '200'


def test_transaction_conflict_on_absent_key(db):
    txn = db.transaction()
    txn.get('absent')        # observe None
    db.put('absent', 'now_exists')  # key was created
    assert txn.commit() is False


# ── Context manager ───────────────────────────────────────────────────────────

def test_transaction_context_auto_commit(db):
    db.put('k', '5')
    with db.transaction() as txn:
        txn.put('k', str(int(txn.get('k') or 0) + 1))
    assert txn.committed is True
    assert db.get('k') == '6'


def test_transaction_context_rollback_on_exception(db):
    db.put('k', 'safe')
    try:
        with db.transaction() as txn:
            txn.put('k', 'modified')
            raise ValueError('abort')
    except ValueError:
        pass
    assert db.get('k') == 'safe'


# ── db.transaction() factory ──────────────────────────────────────────────────

def test_transaction_from_db(db):
    assert isinstance(db.transaction(), Transaction)


def test_transaction_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.close()
    ro = LSMTree(path, read_only=True)
    txn = ro.transaction()
    txn.put('k', 'v')
    with pytest.raises(ReadOnlyError):
        txn.commit()
    ro.close()


# ── atomic_max / atomic_min ───────────────────────────────────────────────────

def test_atomic_max_sets_higher(db):
    db.put('peak', '50')
    assert db.atomic_max('peak', 75) == 75
    assert db.get('peak') == '75'


def test_atomic_max_keeps_current_if_higher(db):
    db.put('peak', '100')
    assert db.atomic_max('peak', 50) == 100
    assert db.get('peak') == '100'


def test_atomic_max_initializes_if_absent(db):
    assert db.atomic_max('new', 42) == 42
    assert db.get('new') == '42'


def test_atomic_min_sets_lower(db):
    db.put('floor', '50')
    assert db.atomic_min('floor', 30) == 30
    assert db.get('floor') == '30'


def test_atomic_min_keeps_current_if_lower(db):
    db.put('floor', '10')
    assert db.atomic_min('floor', 80) == 10
    assert db.get('floor') == '10'


def test_atomic_min_initializes_if_absent(db):
    assert db.atomic_min('new', 99) == 99
    assert db.get('new') == '99'


def test_atomic_max_concurrent(db):
    """Concurrent atomic_max calls must produce correct high-water mark."""
    db.put('hwm', '0')
    import random
    values = [random.randint(1, 1000) for _ in range(100)]
    results = []
    errors = []

    def worker(v):
        try:
            results.append(db.atomic_max('hwm', v))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(v,)) for v in values]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    final = int(db.get('hwm'))
    assert final == max(values), f'Expected {max(values)}, got {final}'
