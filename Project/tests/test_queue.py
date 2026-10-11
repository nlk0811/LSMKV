"""
Tests for the persistent FIFO Queue (db.queue()).
"""
import threading
import pytest

from lsm_engine import LSMTree, Queue


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def q(db):
    return db.queue('test')


# ── Basic operations ──────────────────────────────────────────────────────────

def test_queue_empty_initially(q):
    assert len(q) == 0
    assert not q


def test_queue_push_returns_length(q):
    assert q.push('a') == 1
    assert q.push('b') == 2


def test_queue_peek_does_not_remove(q):
    q.push('first')
    assert q.peek() == 'first'
    assert len(q) == 1


def test_queue_pop_fifo_order(q):
    q.push('first')
    q.push('second')
    q.push('third')
    assert q.pop() == 'first'
    assert q.pop() == 'second'
    assert q.pop() == 'third'


def test_queue_pop_empty_returns_none(q):
    assert q.pop() is None


def test_queue_peek_empty_returns_none(q):
    assert q.peek() is None


def test_queue_len_decrements_on_pop(q):
    q.push('a'); q.push('b')
    assert len(q) == 2
    q.pop()
    assert len(q) == 1


def test_queue_bool(q):
    assert not q
    q.push('item')
    assert q


# ── drain / clear ─────────────────────────────────────────────────────────────

def test_queue_drain(q):
    for i in range(5):
        q.push(f'i{i}')
    items = q.drain()
    assert items == [f'i{i}' for i in range(5)]
    assert len(q) == 0


def test_queue_iter(q):
    q.push('a'); q.push('b'); q.push('c')
    items = list(q)   # destructive iteration
    assert items == ['a', 'b', 'c']
    assert len(q) == 0


def test_queue_clear(q):
    for i in range(10):
        q.push(f'i{i}')
    q.clear()
    assert len(q) == 0
    assert q.pop() is None


# ── push_many ─────────────────────────────────────────────────────────────────

def test_queue_push_many(q):
    q.push_many(['a', 'b', 'c'])
    assert len(q) == 3
    assert q.pop() == 'a'


# ── repr ─────────────────────────────────────────────────────────────────────

def test_queue_repr(q):
    q.push('x')
    r = repr(q)
    assert 'Queue' in r and 'test' in r


# ── Factory ───────────────────────────────────────────────────────────────────

def test_queue_from_db(db):
    assert isinstance(db.queue('test'), Queue)


# ── Persistence ───────────────────────────────────────────────────────────────

def test_queue_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    q = db.queue('jobs')
    q.push('task_1')
    q.push('task_2')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    q2 = db2.queue('jobs')
    assert q2.pop() == 'task_1'
    assert q2.pop() == 'task_2'
    assert q2.pop() is None
    db2.close()


# ── Concurrent push ───────────────────────────────────────────────────────────

def test_queue_concurrent_push(tmp_path):
    """N concurrent pushers each push M items — all items must be present."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    q  = db.queue('c')
    N, M = 5, 20
    errors = []

    def pusher(tid):
        try:
            for j in range(M):
                q.push(f't{tid}j{j}')
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=pusher, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == [], f'Push errors: {errors}'
    assert len(q) == N * M, f'Expected {N*M}, got {len(q)}'
    db.close()


def test_queue_concurrent_pop(tmp_path):
    """M pushes followed by N concurrent poppers must retrieve all items."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    q  = db.queue('c')
    total = 50
    for i in range(total):
        q.push(f'item_{i}')

    popped = []
    lock   = __import__('threading').Lock()

    def popper():
        while True:
            item = q.pop()
            if item is None:
                break
            with lock:
                popped.append(item)

    threads = [__import__('threading').Thread(target=popper) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(popped) == total, f'Expected {total}, got {len(popped)}'
    assert len(set(popped)) == total, 'Duplicate items in concurrent pop!'
    db.close()
