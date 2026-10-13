"""
Tests for range_delete() batch_size parameter (memory-bounded chunked deletion).
"""
import pytest
from lsm_engine import LSMTree


N = 50


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(N):
        d.put(f'k:{i:04d}', f'v{i}')
    yield d
    d.close()


# ── batch_size default (500) ──────────────────────────────────────────────────

def test_range_delete_default_removes_all_in_range(db):
    n = db.range_delete('k:0010', 'k:0030')
    assert n == 20


def test_range_delete_default_leaves_outside(db):
    db.range_delete('k:0010', 'k:0030')
    assert db.get('k:0009') is not None
    assert db.get('k:0030') is not None


# ── batch_size=1 (extreme case) ───────────────────────────────────────────────

def test_range_delete_batch_1_correct_count(db):
    n = db.range_delete('k:0000', 'k:0010', batch_size=1)
    assert n == 10


def test_range_delete_batch_1_all_removed(db):
    db.range_delete('k:0000', 'k:0010', batch_size=1)
    for i in range(10):
        assert db.get(f'k:{i:04d}') is None


def test_range_delete_batch_1_outside_intact(db):
    db.range_delete('k:0000', 'k:0010', batch_size=1)
    assert db.get('k:0010') is not None


# ── various batch sizes ───────────────────────────────────────────────────────

@pytest.mark.parametrize('batch_size', [1, 3, 7, 10, 25, 1000])
def test_range_delete_same_result_all_batch_sizes(db, batch_size, tmp_path):
    path = str(tmp_path / f'db_{batch_size}')
    d = LSMTree(path, sync_writes=False)
    for i in range(N):
        d.put(f'k:{i:04d}', f'v{i}')
    n = d.range_delete('k:0005', 'k:0035', batch_size=batch_size)
    assert n == 30
    for i in range(5, 35):
        assert d.get(f'k:{i:04d}') is None
    for i in range(5):
        assert d.get(f'k:{i:04d}') is not None
    for i in range(35, N):
        assert d.get(f'k:{i:04d}') is not None
    d.close()


# ── batch_size=0 legacy mode ──────────────────────────────────────────────────

def test_range_delete_batch_0_legacy_still_works(db):
    n = db.range_delete('k:0020', 'k:0030', batch_size=0)
    assert n == 10
    for i in range(20, 30):
        assert db.get(f'k:{i:04d}') is None


# ── edge cases ────────────────────────────────────────────────────────────────

def test_range_delete_empty_range_batched(db):
    assert db.range_delete('zzz', 'zzz9', batch_size=10) == 0


def test_range_delete_batched_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    for i in range(20):
        d.put(f'r:{i:04d}', str(i))
    d.range_delete('r:0005', 'r:0015', batch_size=3)
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    for i in range(5, 15):
        assert d2.get(f'r:{i:04d}') is None
    for i in range(5):
        assert d2.get(f'r:{i:04d}') is not None
    d2.close()
