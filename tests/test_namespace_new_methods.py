"""
Tests for Namespace methods added in iterations 62-74.
Verifies prefix isolation for each new forwarded method.
"""
import time
import pytest
from lsm_engine import LSMTree


@pytest.fixture
def ns(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    n = db.namespace('u')
    for i in range(10):
        n.put(f'k{i:02d}', str(i))
    yield n, db
    db.close()


# ── bulk read / write ─────────────────────────────────────────────────────────

def test_mset_prefixes_keys(ns):
    n, db = ns
    n.mset({'a': '1', 'b': '2'})
    assert db.get('u:a') == '1' and db.get('u:b') == '2'


def test_mget_strips_prefix(ns):
    n, db = ns
    r = n.mget(['k00', 'k01', 'missing'])
    assert r['k00'] == '0' and r['missing'] is None
    assert all(not k.startswith('u:') for k in r)


def test_get_many_same_as_mget(ns):
    n, db = ns
    assert n.get_many(['k00', 'k01']) == n.mget(['k00', 'k01'])


def test_delete_many_scoped(ns):
    n, db = ns
    n.put('x', 'v'); n.put('y', 'v')
    n.delete_many(['x', 'y'])
    assert n.get('x') is None and n.get('y') is None


def test_exists_many_scoped(ns):
    n, db = ns
    flags = n.exists_many(['k00', 'absent'])
    assert flags['k00'] is True and flags['absent'] is False


# ── atomic / conditional ops ──────────────────────────────────────────────────

def test_set_if_absent_scoped(ns):
    n, db = ns
    assert n.set_if_absent('new', 'val') is True
    assert db.get('u:new') == 'val'
    assert n.set_if_absent('new', 'other') is False


def test_set_if_present_scoped(ns):
    n, db = ns
    n.put('p', 'old')
    assert n.set_if_present('p', 'new') is True
    assert n.get('p') == 'new'
    assert n.set_if_present('absent', 'v') is False


def test_getset_scoped(ns):
    n, db = ns
    old = n.getset('k00', '999')
    assert old == '0' and n.get('k00') == '999'


def test_getdel_scoped(ns):
    n, db = ns
    n.put('tok', 'abc')
    val = n.getdel('tok')
    assert val == 'abc' and n.get('tok') is None


def test_copy_within_namespace(ns):
    n, db = ns
    n.copy('k00', 'k00_copy')
    assert n.get('k00_copy') == '0'
    assert db.get('u:k00_copy') == '0'


def test_swap_within_namespace(ns):
    n, db = ns
    n.put('s1', 'A'); n.put('s2', 'B')
    assert n.swap('s1', 's2') is True
    assert n.get('s1') == 'B' and n.get('s2') == 'A'


def test_rename_within_namespace(ns):
    n, db = ns
    n.put('old_key', 'v')
    n.rename('old_key', 'new_key')
    assert n.get('new_key') == 'v' and n.get('old_key') is None
    assert db.get('u:new_key') == 'v'


# ── TTL ops ───────────────────────────────────────────────────────────────────

def test_expire_scoped(ns):
    n, db = ns
    n.put('sess', 'v')
    n.expire('sess', 0.05)
    time.sleep(0.1)
    assert n.get('sess') is None


def test_touch_scoped(ns):
    n, db = ns
    n.put('sess', 'v', ttl_seconds=0.05)
    n.touch('sess', 60)
    time.sleep(0.1)
    assert n.get('sess') == 'v'   # touch refreshed TTL


def test_touch_many_scoped(ns):
    n, db = ns
    n.put('a', 'v', ttl_seconds=0.05)
    n.put('b', 'v', ttl_seconds=0.05)
    n.touch_many(['a', 'b'], ttl_seconds=60)
    time.sleep(0.1)
    assert n.get('a') == 'v' and n.get('b') == 'v'


def test_key_type_scoped(ns):
    n, db = ns
    n.put('alive', 'v')
    assert n.key_type('alive') == 'string'
    assert n.key_type('absent') == 'none'


# ── numeric ops ───────────────────────────────────────────────────────────────

def test_increment_float_scoped(ns):
    n, db = ns
    n.increment_float('score', 1.5)
    n.increment_float('score', 2.5)
    assert abs(float(n.get('score')) - 4.0) < 1e-9
    assert db.get('u:score') is not None


def test_atomic_max_scoped(ns):
    n, db = ns
    n.put('m', '5')
    n.atomic_max('m', 10)
    assert n.get('m') == '10'


def test_atomic_max_float_scoped(ns):
    n, db = ns
    n.put('peak', '3.0')
    v = n.atomic_max_float('peak', 5.5)
    assert abs(v - 5.5) < 1e-9


# ── bulk transform / search ───────────────────────────────────────────────────

def test_map_values_scoped(ns):
    n, db = ns
    n.put('a', '5'); n.put('b', '10')
    n.map_values(lambda k, v: str(int(v) * 2), 'a', 'c')
    assert n.get('a') == '10' and n.get('b') == '20'


def test_scan_filter_strips_prefix(ns):
    n, db = ns
    results = list(n.scan_filter(lambda k, v: True, limit=3))
    assert all(not k.startswith('u:') for k, _ in results)


def test_grep_strips_prefix(ns):
    n, db = ns
    keys = list(n.grep(r'^k0'))
    assert all(not k.startswith('u:') for k in keys)
    assert all(k.startswith('k0') for k in keys)


# ── prefix / range delete ─────────────────────────────────────────────────────

def test_prefix_delete_scoped(ns):
    n, db = ns
    for i in range(5):
        n.put(f'tmp:{i}', 'v')
    n.prefix_delete('tmp:')
    assert n.get('tmp:0') is None
    assert n.get('k00') is not None  # other keys unaffected


def test_range_delete_scoped(ns):
    n, db = ns
    n.range_delete('k02', 'k05')
    assert n.get('k02') is None and n.get('k05') is not None


# ── tail ──────────────────────────────────────────────────────────────────────

def test_tail_strips_prefix(ns):
    n, db = ns
    last3 = n.tail(3)
    assert len(last3) == 3
    assert all(not k.startswith('u:') for k, _ in last3)
