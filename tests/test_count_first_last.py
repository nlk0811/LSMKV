"""
Tests for db.count_prefix(), is_empty(), first(), last().
"""
import pytest
from lsm_engine import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(10):
        d.put(f'user:{i:04d}', str(i))
    for i in range(5):
        d.put(f'sess:{i:04d}', f'tok{i}')
    yield d
    d.close()


# ── count_prefix ──────────────────────────────────────────────────────────────

def test_count_prefix_basic(db):
    assert db.count_prefix('user:') == 10


def test_count_prefix_other_prefix(db):
    assert db.count_prefix('sess:') == 5


def test_count_prefix_nonexistent(db):
    assert db.count_prefix('nope:') == 0


def test_count_prefix_after_delete(db):
    db.delete('user:0000')
    assert db.count_prefix('user:') == 9


def test_count_prefix_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.count_prefix('k:') == 0
    d.close()


# ── is_empty ──────────────────────────────────────────────────────────────────

def test_is_empty_false_for_nonempty(db):
    assert db.is_empty('user:', 'user;') is False


def test_is_empty_true_for_absent_prefix(db):
    assert db.is_empty('nope:', 'nope;') is True


def test_is_empty_false_globally(db):
    assert db.is_empty() is False


def test_is_empty_true_for_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.is_empty() is True
    d.close()


def test_is_empty_after_clear(db):
    db.prefix_delete('user:')
    db.prefix_delete('sess:')
    assert db.is_empty() is True


def test_is_empty_returns_bool(db):
    result = db.is_empty()
    assert isinstance(result, bool)


# ── first ─────────────────────────────────────────────────────────────────────

def test_first_returns_smallest_key(db):
    pair = db.first('user:', 'user;')
    assert pair == ('user:0000', '0')


def test_first_with_range(db):
    pair = db.first('user:0005', 'user;')
    assert pair == ('user:0005', '5')


def test_first_absent_range(db):
    assert db.first('zzz:', 'zzz;') is None


def test_first_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.first() is None
    d.close()


def test_first_returns_tuple(db):
    pair = db.first()
    assert len(pair) == 2
    assert isinstance(pair[0], str) and isinstance(pair[1], str)


def test_first_key_is_prefix_of_first_key(db):
    pair = db.first()
    # first key globally should be 'sess:0000' (s < u)
    assert pair[0] == 'sess:0000'


# ── last ──────────────────────────────────────────────────────────────────────

def test_last_returns_largest_key(db):
    pair = db.last('user:', 'user;')
    assert pair == ('user:0009', '9')


def test_last_with_range(db):
    pair = db.last('user:0003', 'user:0008')
    assert pair == ('user:0007', '7')


def test_last_absent_range(db):
    assert db.last('zzz:', 'zzz;') is None


def test_last_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.last() is None
    d.close()


def test_last_returns_tuple(db):
    pair = db.last()
    assert len(pair) == 2


def test_last_globally(db):
    pair = db.last()
    # last key globally is 'user:0009' (u > s)
    assert pair[0] == 'user:0009'
