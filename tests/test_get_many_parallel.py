"""
Tests for get_many() / mget() with workers= parameter.
Covers serial (workers=1) and parallel (workers>1) modes.
"""
import pytest
from lsm_engine import LSMTree


N = 60


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(N):
        d.put(f'user:{i:04d}', f'value{i}')
    yield d
    d.close()


# ── get_many serial (default) ─────────────────────────────────────────────────

def test_get_many_all_present(db):
    keys = [f'user:{i:04d}' for i in range(10)]
    r = db.get_many(keys)
    assert len(r) == 10
    assert all(r[k] is not None for k in keys)


def test_get_many_values_correct(db):
    r = db.get_many(['user:0007', 'user:0042'])
    assert r['user:0007'] == 'value7'
    assert r['user:0042'] == 'value42'


def test_get_many_missing_keys_none(db):
    r = db.get_many(['absent:1', 'user:0001', 'absent:2'])
    assert r['absent:1'] is None
    assert r['absent:2'] is None
    assert r['user:0001'] == 'value1'


def test_get_many_empty_input(db):
    assert db.get_many([]) == {}


def test_get_many_all_missing(db):
    r = db.get_many(['x', 'y', 'z'])
    assert all(v is None for v in r.values())


def test_get_many_preserves_all_keys(db):
    keys = ['user:0001', 'absent', 'user:0002']
    r = db.get_many(keys)
    assert set(r.keys()) == set(keys)


# ── get_many parallel ─────────────────────────────────────────────────────────

def test_get_many_parallel_values_match_serial(db):
    keys = [f'user:{i:04d}' for i in range(N)]
    serial = db.get_many(keys)
    parallel = db.get_many(keys, workers=8)
    assert serial == parallel


def test_get_many_workers_2(db):
    keys = [f'user:{i:04d}' for i in range(20)]
    r = db.get_many(keys, workers=2)
    assert len(r) == 20
    assert r['user:0019'] == 'value19'


def test_get_many_workers_equals_batch_size(db):
    keys = [f'user:{i:04d}' for i in range(4)]
    r = db.get_many(keys, workers=4)
    assert len(r) == 4


def test_get_many_workers_larger_than_batch(db):
    keys = [f'user:{i:04d}' for i in range(3)]
    r = db.get_many(keys, workers=16)
    assert len(r) == 3


def test_get_many_parallel_with_missing(db):
    keys = ['user:0010', 'absent:a', 'user:0020', 'absent:b']
    r = db.get_many(keys, workers=4)
    assert r['user:0010'] == 'value10'
    assert r['absent:a'] is None


def test_get_many_parallel_empty(db):
    assert db.get_many([], workers=4) == {}


# ── mget alias ────────────────────────────────────────────────────────────────

def test_mget_serial(db):
    r = db.mget(['user:0000', 'user:0001'])
    assert r['user:0000'] == 'value0'


def test_mget_parallel(db):
    keys = [f'user:{i:04d}' for i in range(30)]
    r = db.mget(keys, workers=4)
    assert len(r) == 30
    serial = db.get_many(keys)
    assert r == serial


# ── cross-workers correctness ─────────────────────────────────────────────────

@pytest.mark.parametrize('workers', [1, 2, 4, 8])
def test_get_many_same_result_all_worker_counts(db, workers):
    keys = [f'user:{i:04d}' for i in range(N)]
    expected = {k: db.get(k) for k in keys}
    r = db.get_many(keys, workers=workers)
    assert r == expected
