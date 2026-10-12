"""
Tests for scan/scan_keys/keys/values/items limit= parameter and db.tail().
"""
import pytest
from lsm_engine import LSMTree

N = 30


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(N):
        d.put(f'key:{i:04d}', f'val{i}')
    yield d
    d.close()


# ── scan with limit ───────────────────────────────────────────────────────────

def test_scan_limit_stops_early(db):
    pairs = list(db.scan(limit=5))
    assert len(pairs) == 5


def test_scan_limit_returns_first_n_sorted(db):
    pairs = list(db.scan(limit=3))
    keys = [k for k, _ in pairs]
    assert keys == sorted(keys)
    assert keys[0] == 'key:0000'


def test_scan_limit_none_returns_all(db):
    pairs = list(db.scan(limit=None))
    assert len(pairs) == N


def test_scan_limit_larger_than_total(db):
    pairs = list(db.scan(limit=N + 100))
    assert len(pairs) == N


def test_scan_limit_zero(db):
    pairs = list(db.scan(limit=0))
    assert pairs == []


def test_scan_limit_with_range(db):
    pairs = list(db.scan('key:0005', 'key:0015', limit=3))
    assert len(pairs) == 3
    assert pairs[0][0] == 'key:0005'


def test_scan_limit_range_smaller_than_limit(db):
    pairs = list(db.scan('key:0025', 'key:0030', limit=100))
    assert len(pairs) == 5


# ── scan_keys with limit ──────────────────────────────────────────────────────

def test_scan_keys_limit(db):
    keys = list(db.scan_keys(limit=7))
    assert len(keys) == 7
    assert keys == sorted(keys)


# ── keys / values / items with limit ─────────────────────────────────────────

def test_keys_limit(db):
    ks = list(db.keys(limit=4))
    assert len(ks) == 4


def test_values_limit(db):
    vs = list(db.values(limit=6))
    assert len(vs) == 6


def test_items_limit(db):
    its = list(db.items(limit=8))
    assert len(its) == 8


# ── tail ──────────────────────────────────────────────────────────────────────

def test_tail_returns_last_n(db):
    last = db.tail(3)
    assert len(last) == 3
    keys = [k for k, _ in last]
    assert keys[-1] == f'key:{N-1:04d}'


def test_tail_result_ascending(db):
    last5 = db.tail(5)
    keys = [k for k, _ in last5]
    assert keys == sorted(keys)


def test_tail_n_larger_than_total(db):
    all_items = db.tail(N + 50)
    assert len(all_items) == N


def test_tail_zero(db):
    assert db.tail(0) == []


def test_tail_negative(db):
    assert db.tail(-1) == []


def test_tail_one(db):
    last1 = db.tail(1)
    assert len(last1) == 1
    assert last1[0][0] == f'key:{N-1:04d}'


def test_tail_with_end_bound(db):
    last5 = db.tail(5, end='key:0010')
    assert len(last5) == 5
    assert all(k < 'key:0010' for k, _ in last5)
    keys = [k for k, _ in last5]
    assert keys == sorted(keys)
    assert keys[-1] == 'key:0009'


def test_tail_with_end_bound_smaller_than_n(db):
    result = db.tail(20, end='key:0003')
    assert len(result) == 3  # only keys 0000-0002 exist before 0003


def test_tail_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.tail(5) == []
    d.close()


def test_tail_persists_across_reopen(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(10):
        db.put(f'k:{i:03d}', str(i))
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    last = db2.tail(3)
    assert len(last) == 3
    assert last[-1][0] == 'k:009'
    db2.close()
