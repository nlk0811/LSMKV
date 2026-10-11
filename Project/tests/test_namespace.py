"""
Tests for Namespace (db.namespace()) — prefix-scoped database view.
"""
import os
import pytest

from lsm_engine import LSMTree, Namespace


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def ns(db):
    return db.namespace('ns:')


# ── Factory ───────────────────────────────────────────────────────────────────

def test_namespace_from_db(db):
    ns = db.namespace('user:')
    assert isinstance(ns, Namespace)
    assert ns.prefix == 'user:'


def test_namespace_auto_colon(db):
    ns = db.namespace('user')   # no trailing colon
    assert ns.prefix == 'user:'


def test_namespace_repr(db):
    ns = db.namespace('tenant:acme:')
    assert 'Namespace' in repr(ns) and 'tenant:acme:' in repr(ns)


# ── Basic CRUD ────────────────────────────────────────────────────────────────

def test_namespace_put_get(ns):
    ns.put('k', 'v')
    assert ns.get('k') == 'v'


def test_namespace_get_missing(ns):
    assert ns.get('absent') is None


def test_namespace_delete(ns):
    ns.put('k', 'v')
    ns.delete('k')
    assert ns.get('k') is None


def test_namespace_has(ns):
    ns.put('k', 'v')
    assert ns.has('k') is True
    assert ns.has('missing') is False


def test_namespace_keys_isolated(db, ns):
    """Namespace keys must not appear without the prefix from the raw db."""
    ns.put('local', 'val')
    assert db.get('local') is None          # not accessible without prefix
    assert db.get('ns:local') == 'val'      # stored with prefix


# ── Scan ─────────────────────────────────────────────────────────────────────

def test_namespace_scan_strips_prefix(ns):
    ns.put('a', '1')
    ns.put('b', '2')
    results = list(ns.scan())
    keys = [k for k, _ in results]
    assert 'a' in keys and 'b' in keys
    assert not any(k.startswith('ns:') for k in keys)


def test_namespace_scan_range(ns):
    for i in range(10):
        ns.put(f'k{i:04d}', f'v{i}')
    results = list(ns.scan('k0003', 'k0007'))
    assert len(results) == 4
    assert results[0][0] == 'k0003'


def test_namespace_scan_keys(ns):
    ns.put('a', '1')
    ns.put('b', '2')
    assert sorted(ns.scan_keys()) == ['a', 'b']


def test_namespace_values(ns):
    ns.put('a', '1')
    ns.put('b', '2')
    assert sorted(ns.values()) == ['1', '2']


def test_namespace_items(ns):
    ns.put('a', '1')
    results = list(ns.items())
    assert ('a', '1') in results


def test_namespace_does_not_see_other_namespaces(db):
    users   = db.namespace('user:')
    orders  = db.namespace('order:')
    users.put('alice', 'data')
    orders.put('001', 'book')
    assert list(users.scan()) == [('alice', 'data')]
    assert list(orders.scan()) == [('001', 'book')]


# ── Utility ───────────────────────────────────────────────────────────────────

def test_namespace_count(ns):
    for i in range(5):
        ns.put(f'k{i}', 'v')
    assert ns.count() == 5


def test_namespace_clear(ns, db):
    for i in range(5):
        ns.put(f'k{i}', 'v')
    db.put('global', 'keep')
    n = ns.clear()
    assert n == 5
    assert ns.count() == 0
    assert db.get('global') == 'keep'   # unaffected


def test_namespace_counter(ns):
    c = ns.counter('hits')
    c.incr()
    c.incr()
    assert c.value == 2


def test_namespace_increment(ns):
    assert ns.increment('score', 10) == 10
    assert ns.increment('score', 5) == 15


# ── TTL ───────────────────────────────────────────────────────────────────────

def test_namespace_put_with_ttl(ns):
    import time
    ns.put('k', 'v', ttl_seconds=0.05)
    assert ns.get('k') == 'v'
    time.sleep(0.1)
    assert ns.get('k') is None
