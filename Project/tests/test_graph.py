"""
Tests for Graph (db.graph()) — persistent directed graph.
"""
import pytest
from lsm_engine import LSMTree, Graph


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def g(db):
    graph = db.graph('social')
    graph.add_edge('alice', 'bob')
    graph.add_edge('alice', 'carol')
    graph.add_edge('bob',   'carol')
    graph.add_edge('carol', 'dave')
    return graph


# ── Factory ───────────────────────────────────────────────────────────────────

def test_graph_from_db(db):
    assert isinstance(db.graph('test'), Graph)


def test_graph_repr(g):
    r = repr(g)
    assert 'Graph' in r and 'social' in r


# ── has_edge ──────────────────────────────────────────────────────────────────

def test_has_edge_existing(g):
    assert g.has_edge('alice', 'bob') is True


def test_has_edge_reverse(g):
    assert g.has_edge('bob', 'alice') is False


def test_has_edge_absent(g):
    assert g.has_edge('alice', 'dave') is False


def test_contains_protocol(g):
    assert ('alice', 'bob') in g
    assert ('bob', 'alice') not in g


# ── neighbors / predecessors ─────────────────────────────────────────────────

def test_neighbors(g):
    assert sorted(g.neighbors('alice')) == ['bob', 'carol']


def test_predecessors(g):
    assert sorted(g.predecessors('carol')) == ['alice', 'bob']


def test_neighbors_empty(g):
    assert g.neighbors('dave') == []


def test_predecessors_empty(g):
    assert g.predecessors('alice') == []


# ── degree ────────────────────────────────────────────────────────────────────

def test_out_degree(g):
    assert g.degree('alice') == 2
    assert g.degree('carol') == 1
    assert g.degree('dave')  == 0


def test_in_degree(g):
    assert g.in_degree('carol') == 2
    assert g.in_degree('alice') == 0


# ── weights ───────────────────────────────────────────────────────────────────

def test_add_edge_with_weight(db):
    g = db.graph('w')
    g.add_edge('a', 'b', weight=0.75)
    assert g.weight('a', 'b') == pytest.approx(0.75)


def test_unweighted_edge_returns_none(g):
    assert g.weight('alice', 'bob') is None


def test_set_weight(db):
    g = db.graph('w')
    g.add_edge('a', 'b')
    g.set_weight('a', 'b', 2.5)
    assert g.weight('a', 'b') == pytest.approx(2.5)


def test_set_weight_absent_returns_false(db):
    g = db.graph('w')
    assert g.set_weight('a', 'b', 1.0) is False


def test_edges_from_with_weights(db):
    g = db.graph('w')
    g.add_edge('a', 'b', weight=1.0)
    g.add_edge('a', 'c', weight=2.0)
    edges = sorted(g.edges_from('a'))
    assert edges == [('b', 1.0), ('c', 2.0)]


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_edge(g):
    assert g.remove_edge('alice', 'bob') is True
    assert not g.has_edge('alice', 'bob')
    assert 'alice' not in g.predecessors('bob')


def test_remove_edge_absent(g):
    assert g.remove_edge('dave', 'alice') is False


def test_remove_node_clears_edges(g):
    n = g.remove_node('alice')
    assert n == 2   # alice→bob (removed earlier above if chained, else 2)
    assert not g.has_edge('alice', 'bob')
    assert not g.has_edge('alice', 'carol')
    assert g.has_edge('bob', 'carol')   # unaffected


# ── counts ────────────────────────────────────────────────────────────────────

def test_edge_count(g):
    assert g.edge_count() == 4


def test_len(g):
    assert len(g) == 4


# ── clear ────────────────────────────────────────────────────────────────────

def test_clear(g):
    g.clear()
    assert g.edge_count() == 0
    assert g.neighbors('alice') == []


# ── persistence ───────────────────────────────────────────────────────────────

def test_graph_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    g    = db.graph('g')
    g.add_edge('x', 'y')
    g.add_edge('y', 'z')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    g2  = db2.graph('g')
    assert g2.has_edge('x', 'y')
    assert g2.has_edge('y', 'z')
    assert g2.neighbors('x') == ['y']
    db2.close()
