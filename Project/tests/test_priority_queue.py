"""
Tests for PriorityQueue (db.priority_queue()) — persistent priority queue.
"""
import pytest
from lsm_engine import LSMTree, PriorityQueue


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def pq(db):
    return db.priority_queue('tasks')


# ── Factory ───────────────────────────────────────────────────────────────────

def test_priority_queue_from_db(db):
    assert isinstance(db.priority_queue('test'), PriorityQueue)


def test_priority_queue_repr(pq):
    r = repr(pq)
    assert 'PriorityQueue' in r and 'tasks' in r


# ── push / pop ordering ───────────────────────────────────────────────────────

def test_highest_priority_popped_first(pq):
    pq.push('low',    priority=1)
    pq.push('urgent', priority=10)
    pq.push('normal', priority=5)
    item, pri = pq.pop()
    assert item == 'urgent' and pri == 10


def test_full_ordering(pq):
    pq.push('c', priority=3)
    pq.push('a', priority=1)
    pq.push('b', priority=2)
    order = [(i, p) for i, p in pq.drain()]
    assert [p for _, p in order] == sorted([p for _, p in order], reverse=True)


def test_fifo_within_same_priority(pq):
    pq.push('first',  priority=5)
    pq.push('second', priority=5)
    pq.push('third',  priority=5)
    items = [pq.pop()[0] for _ in range(3)]
    assert items == ['first', 'second', 'third']


def test_default_priority_zero(pq):
    pq.push('item_a')
    pq.push('item_b')
    item, pri = pq.pop()
    assert pri == 0


# ── peek ──────────────────────────────────────────────────────────────────────

def test_peek_does_not_remove(pq):
    pq.push('task', priority=5)
    item, pri = pq.peek()
    assert item == 'task'
    assert pq.count() == 1   # still in queue


def test_peek_empty_returns_none(pq):
    assert pq.peek() == (None, None)


# ── count / len / bool ────────────────────────────────────────────────────────

def test_count_and_len(pq):
    pq.push('a'); pq.push('b')
    assert pq.count() == 2
    assert len(pq) == 2


def test_bool_empty(pq):
    assert not pq


def test_bool_nonempty(pq):
    pq.push('x')
    assert pq


# ── pop empty ─────────────────────────────────────────────────────────────────

def test_pop_empty_returns_none(pq):
    assert pq.pop() == (None, None)


# ── drain ─────────────────────────────────────────────────────────────────────

def test_drain_returns_all_sorted(pq):
    pq.push_many([('a', 3), ('b', 1), ('c', 2)])
    items = pq.drain()
    priorities = [p for _, p in items]
    assert priorities == sorted(priorities, reverse=True)
    assert pq.count() == 0


# ── clear ─────────────────────────────────────────────────────────────────────

def test_clear(pq):
    pq.push_many([('a', 1), ('b', 2), ('c', 3)])
    n = pq.clear()
    assert n == 3
    assert pq.count() == 0
    assert pq.pop() == (None, None)


# ── push_many ─────────────────────────────────────────────────────────────────

def test_push_many(pq):
    seqs = pq.push_many([('x', 5), ('y', 3), ('z', 7)])
    assert len(seqs) == 3
    assert pq.count() == 3


# ── persistence ───────────────────────────────────────────────────────────────

def test_priority_queue_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    pq   = db.priority_queue('jobs')
    pq.push('task_a', priority=3)
    pq.push('task_b', priority=7)
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    pq2 = db2.priority_queue('jobs')
    item, pri = pq2.pop()
    assert item == 'task_b' and pri == 7   # highest priority first
    db2.close()


# ── mixed priority operations ─────────────────────────────────────────────────

def test_interleaved_push_pop(pq):
    pq.push('low', priority=1)
    assert pq.pop()[0] == 'low'
    pq.push('high', priority=100)
    pq.push('med', priority=50)
    assert pq.pop()[0] == 'high'
    assert pq.pop()[0] == 'med'
    assert pq.pop() == (None, None)
