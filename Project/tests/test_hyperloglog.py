"""
Tests for HyperLogLog (db.hyperloglog()) — approximate cardinality estimation.
"""
import pytest
from lsm_engine import LSMTree, HyperLogLog


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def hll(db):
    return db.hyperloglog('visitors')


# ── Factory ───────────────────────────────────────────────────────────────────

def test_hyperloglog_from_db(db):
    assert isinstance(db.hyperloglog('test'), HyperLogLog)


def test_hyperloglog_repr(hll):
    r = repr(hll)
    assert 'HyperLogLog' in r and 'visitors' in r


# ── Basic add / count ─────────────────────────────────────────────────────────

def test_empty_count_is_zero(hll):
    assert hll.count() == 0


def test_single_element(hll):
    hll.add('element')
    assert hll.count() >= 1


def test_count_small_set(hll):
    for i in range(10):
        hll.add(f'item_{i}')
    # Should be close to 10 (±5 is generous)
    assert abs(hll.count() - 10) <= 5


def test_duplicates_not_double_counted(hll):
    for _ in range(100):
        hll.add('same_element')
    count = hll.count()
    assert count <= 5   # should be approx 1 (small error)


def test_count_accuracy_1k(db):
    """1000 unique elements, error should be < 25%."""
    hll = db.hyperloglog('large')
    for i in range(1000):
        hll.add(f'ip:{i}')
    count = hll.count()
    error = abs(count - 1000) / 1000
    assert error < 0.25, f'Error {error:.1%} too large for count={count}'


def test_count_accuracy_10k(db):
    """10000 unique elements, error should be < 25%."""
    hll = db.hyperloglog('xlarge')
    hll.add_many(f'user:{i}' for i in range(10000))
    count = hll.count()
    error = abs(count - 10000) / 10000
    assert error < 0.25, f'Error {error:.1%} too large'


# ── add_many ──────────────────────────────────────────────────────────────────

def test_add_many_basic(hll):
    updated = hll.add_many([f'x{i}' for i in range(100)])
    assert updated >= 1   # some registers updated
    assert abs(hll.count() - 100) <= 20


# ── merge ─────────────────────────────────────────────────────────────────────

def test_merge_union(db):
    """Merging two HLLs with distinct elements gives union cardinality."""
    a = db.hyperloglog('a')
    b = db.hyperloglog('b')
    a.add_many(f'a_{i}' for i in range(500))
    b.add_many(f'b_{i}' for i in range(500))
    a.merge(b)
    count = a.count()
    # Should be ~1000 (two distinct sets of 500)
    assert abs(count - 1000) / 1000 < 0.3


def test_merge_overlap(db):
    """Merging two HLLs with overlap is still correct."""
    a = db.hyperloglog('a')
    b = db.hyperloglog('b')
    a.add_many(f'item_{i}' for i in range(500))
    b.add_many(f'item_{i}' for i in range(250, 750))  # 250 overlap
    a.merge(b)
    count = a.count()
    # Should be ~750 unique (0-249 from a, 250-749 from union)
    assert abs(count - 750) / 750 < 0.3


def test_merge_returns_self(db):
    a = db.hyperloglog('a')
    b = db.hyperloglog('b')
    result = a.merge(b)
    assert result is a


# ── reset ─────────────────────────────────────────────────────────────────────

def test_reset(hll):
    hll.add_many(f'x{i}' for i in range(100))
    assert hll.count() > 0
    hll.reset()
    assert hll.count() == 0


# ── persistence ───────────────────────────────────────────────────────────────

def test_hyperloglog_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    hll  = db.hyperloglog('test')
    hll.add_many(f'item_{i}' for i in range(1000))
    count_before = hll.count()
    db.close()

    db2  = LSMTree(path, sync_writes=False)
    hll2 = db2.hyperloglog('test')
    count_after = hll2.count()
    assert count_before == count_after   # exact same estimate
    db2.close()
