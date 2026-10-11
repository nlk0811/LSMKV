"""
Tests for Index (db.index()) — persistent full-text search index.
"""
import pytest
from lsm_engine import LSMTree, Index


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def idx(db):
    i = db.index('articles')
    i.add('doc:1', 'Python is a great language for data science')
    i.add('doc:2', 'Python and Java are popular programming languages')
    i.add('doc:3', 'Machine learning with Python and TensorFlow')
    i.add('doc:4', 'Go is great for systems programming')
    return i


# ── Factory ───────────────────────────────────────────────────────────────────

def test_index_from_db(db):
    assert isinstance(db.index('test'), Index)


def test_index_repr(idx):
    r = repr(idx)
    assert 'Index' in r and 'articles' in r


# ── add / get / count ─────────────────────────────────────────────────────────

def test_count(idx):
    assert idx.count() == 4


def test_len(idx):
    assert len(idx) == 4


def test_get_existing(idx):
    assert 'Python' in idx.get('doc:1')


def test_get_missing(idx):
    assert idx.get('nonexistent') is None


def test_contains(idx):
    assert 'doc:1' in idx
    assert 'ghost' not in idx


# ── search ────────────────────────────────────────────────────────────────────

def test_search_single_token(idx):
    results = idx.search('Python')
    assert 'doc:1' in results
    assert 'doc:2' in results
    assert 'doc:3' in results
    assert 'doc:4' not in results


def test_search_multi_token_ranks_by_relevance(idx):
    results = idx.search('Python language')
    # doc:1 has both 'python' and 'language'; should rank first or second
    assert 'doc:1' in results[:2]


def test_search_no_match(idx):
    results = idx.search('Rust async')
    assert results == []


def test_search_empty_query(idx):
    assert idx.search('') == []


def test_search_stop_words_only(idx):
    # 'is', 'a', 'for' are stop words — should return no results or empty
    results = idx.search('is a for')
    # Stop words filtered → no tokens → empty
    assert results == []


def test_search_top_n(idx):
    results = idx.search('Python', top_n=2)
    assert len(results) <= 2


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_existing(idx):
    assert idx.remove('doc:2') is True
    assert idx.count() == 3
    assert idx.search('Java') == []   # Java was only in doc:2


def test_remove_absent(idx):
    assert idx.remove('ghost') is False


def test_remove_clears_tokens(idx):
    idx.remove('doc:1')
    results = idx.search('science')
    assert 'doc:1' not in results


# ── update ────────────────────────────────────────────────────────────────────

def test_update_removes_old_tokens(idx):
    idx.update('doc:1', 'Go is fast and efficient')
    results = idx.search('Python')
    assert 'doc:1' not in results   # old Python token gone


def test_update_adds_new_tokens(idx):
    idx.update('doc:1', 'Go is fast and efficient')
    assert 'doc:1' in idx.search('Go fast')


# ── extra fields ─────────────────────────────────────────────────────────────

def test_add_with_extra_fields(db):
    idx = db.index('posts')
    idx.add('p:1', 'Introduction to machine learning',
            author='Alice', category='AI')
    assert 'p:1' in idx.search('Alice')
    assert 'p:1' in idx.search('machine learning AI')


# ── clear ────────────────────────────────────────────────────────────────────

def test_clear(idx):
    idx.clear()
    assert idx.count() == 0
    assert idx.search('Python') == []


# ── persistence ───────────────────────────────────────────────────────────────

def test_index_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    idx  = db.index('docs')
    idx.add('d:1', 'Hello world Python')
    idx.add('d:2', 'Java programming language')
    db.close()

    db2  = LSMTree(path, sync_writes=False)
    idx2 = db2.index('docs')
    assert idx2.count() == 2
    assert 'd:1' in idx2.search('Python')
    assert 'd:2' in idx2.search('Java')
    db2.close()
