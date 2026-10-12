"""
Tests for Collection (db.collection()) — persistent unordered set.
"""
import pytest
from lsm_engine import LSMTree, Collection


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def fruits(db):
    c = db.collection('fruits')
    c.add('apple', 'banana', 'cherry')
    return c


# ── Factory ───────────────────────────────────────────────────────────────────

def test_collection_from_db(db):
    assert isinstance(db.collection('test'), Collection)


def test_collection_repr(fruits):
    r = repr(fruits)
    assert 'Collection' in r and 'fruits' in r


# ── add ───────────────────────────────────────────────────────────────────────

def test_add_returns_new_count(fruits):
    n = fruits.add('date')
    assert n == 1


def test_add_duplicate_not_counted(fruits):
    n = fruits.add('apple')   # already exists
    assert n == 0


def test_add_multiple(fruits):
    n = fruits.add('date', 'elderberry')
    assert n == 2


def test_add_from(fruits):
    n = fruits.add_from(['fig', 'grape'])
    assert n == 2


# ── contains / count ──────────────────────────────────────────────────────────

def test_contains_protocol(fruits):
    assert 'apple' in fruits
    assert 'mango' not in fruits


def test_count(fruits):
    assert fruits.count() == 3
    assert len(fruits) == 3


# ── members / iteration ───────────────────────────────────────────────────────

def test_members_returns_frozenset(fruits):
    m = fruits.members()
    assert isinstance(m, frozenset)
    assert m == frozenset({'apple', 'banana', 'cherry'})


def test_iteration(fruits):
    items = set(fruits)
    assert items == {'apple', 'banana', 'cherry'}


# ── remove / discard / pop ────────────────────────────────────────────────────

def test_remove_existing(fruits):
    assert fruits.remove('apple') == 1
    assert 'apple' not in fruits
    assert fruits.count() == 2


def test_remove_absent(fruits):
    assert fruits.remove('mango') == 0


def test_discard_existing(fruits):
    fruits.discard('apple')
    assert 'apple' not in fruits


def test_discard_absent_no_error(fruits):
    fruits.discard('nonexistent')   # must not raise


def test_pop_returns_member(fruits):
    m = fruits.pop()
    assert m is not None
    assert m not in fruits
    assert fruits.count() == 2


def test_pop_empty_returns_none(db):
    c = db.collection('empty')
    assert c.pop() is None


def test_clear(fruits):
    fruits.clear()
    assert fruits.count() == 0


# ── set operations ────────────────────────────────────────────────────────────

def test_union(db, fruits):
    veggies = db.collection('veggies')
    veggies.add('carrot', 'apple')
    result = fruits.union(veggies)
    assert result == frozenset({'apple', 'banana', 'cherry', 'carrot'})


def test_intersection(db, fruits):
    veggies = db.collection('veggies')
    veggies.add('carrot', 'apple')
    assert fruits.intersection(veggies) == frozenset({'apple'})


def test_difference(db, fruits):
    veggies = db.collection('veggies')
    veggies.add('carrot', 'apple')
    assert fruits.difference(veggies) == frozenset({'banana', 'cherry'})


def test_symmetric_difference(db, fruits):
    veggies = db.collection('veggies')
    veggies.add('carrot', 'apple')
    sd = fruits.symmetric_difference(veggies)
    assert sd == frozenset({'banana', 'cherry', 'carrot'})


def test_is_subset(db, fruits):
    sub = db.collection('sub')
    sub.add('apple')
    assert sub.is_subset(fruits)
    assert not fruits.is_subset(sub)


def test_is_superset(db, fruits):
    sub = db.collection('sub')
    sub.add('apple')
    assert fruits.is_superset(sub)


def test_is_disjoint(db, fruits):
    other = db.collection('other')
    other.add('mango', 'pear')
    assert fruits.is_disjoint(other)
    other.add('apple')
    assert not fruits.is_disjoint(other)


# ── add_from with operation result ────────────────────────────────────────────

def test_persist_union(db, fruits):
    """Union result can be persisted in a new collection."""
    veggies = db.collection('veggies')
    veggies.add('carrot')
    combined = db.collection('combined')
    combined.add_from(fruits.union(veggies))
    assert 'apple' in combined and 'carrot' in combined


# ── persistence ───────────────────────────────────────────────────────────────

def test_collection_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    c    = db.collection('tags')
    c.add('python', 'database', 'lsm')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    c2  = db2.collection('tags')
    assert c2.count() == 3
    assert 'python' in c2
    db2.close()
