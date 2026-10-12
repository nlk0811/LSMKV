"""
Tests for Trie (db.trie()) — persistent prefix tree for autocomplete.
"""
import pytest
from lsm_engine import LSMTree, Trie


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def t(db):
    trie = db.trie('words')
    trie.insert('apple', 'application', 'apply', 'apt',
                'banana', 'band', 'bandana', 'car')
    return trie


# ── Factory ───────────────────────────────────────────────────────────────────

def test_trie_from_db(db):
    assert isinstance(db.trie('test'), Trie)


def test_trie_repr(t):
    assert 'Trie' in repr(t)


# ── insert ────────────────────────────────────────────────────────────────────

def test_insert_returns_new_count(t):
    n = t.insert('cherry')
    assert n == 1


def test_insert_duplicate_not_counted(t):
    assert t.insert('apple') == 0


def test_insert_multiple(db):
    t = db.trie('t')
    assert t.insert('a', 'b', 'c') == 3


# ── contains ─────────────────────────────────────────────────────────────────

def test_contains_exact_word(t):
    assert t.contains('apple')


def test_contains_prefix_not_inserted(t):
    assert not t.contains('app')   # 'app' was never inserted


def test_contains_protocol(t):
    assert 'apple' in t
    assert 'mango' not in t


# ── search ────────────────────────────────────────────────────────────────────

def test_search_prefix(t):
    results = t.search('app')
    assert results == ['apple', 'application', 'apply']


def test_search_returns_sorted(t):
    results = t.search('ban')
    assert results == sorted(results)


def test_search_limit(t):
    results = t.search('app', limit=2)
    assert len(results) == 2
    assert results == ['apple', 'application']


def test_search_no_matches(t):
    assert t.search('xyz') == []


def test_search_all(t):
    results = t.search(limit=100)
    assert len(results) == 8
    assert results == sorted(results)


def test_search_exact_word_returned(t):
    results = t.search('apple')
    assert 'apple' in results


# ── starts_with ───────────────────────────────────────────────────────────────

def test_starts_with_true(t):
    assert t.starts_with('app')
    assert t.starts_with('ban')


def test_starts_with_false(t):
    assert not t.starts_with('xyz')


def test_starts_with_full_word(t):
    assert t.starts_with('apple')


# ── count_prefix / count ──────────────────────────────────────────────────────

def test_count_prefix(t):
    assert t.count_prefix('app') == 3
    assert t.count_prefix('ban') == 3   # banana, band, bandana
    assert t.count_prefix('xyz') == 0


def test_count(t):
    assert t.count() == 8
    assert len(t) == 8


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_existing(t):
    assert t.delete('apply') is True
    assert not t.contains('apply')
    assert t.count_prefix('app') == 2


def test_delete_absent(t):
    assert t.delete('mango') is False


def test_delete_does_not_affect_prefix(t):
    t.delete('band')
    assert t.contains('bandana')   # still there
    assert t.contains('banana')    # still there


# ── clear ─────────────────────────────────────────────────────────────────────

def test_clear(t):
    t.clear()
    assert len(t) == 0
    assert t.search('app') == []


# ── iteration ─────────────────────────────────────────────────────────────────

def test_iteration(t):
    words = list(t)
    assert words == sorted(words)
    assert 'apple' in words


# ── persistence ───────────────────────────────────────────────────────────────

def test_trie_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    t    = db.trie('dict')
    t.insert('hello', 'help', 'world')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    t2  = db2.trie('dict')
    assert t2.count() == 3
    assert t2.search('hel') == ['hello', 'help']
    db2.close()
