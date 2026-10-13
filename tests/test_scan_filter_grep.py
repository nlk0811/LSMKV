"""
Tests for db.scan_filter() and db.grep() streaming pattern-match methods.
"""
import re
import pytest
from lsm_engine import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(20):
        d.put(f'user:{i:04d}', 'active' if i % 2 == 0 else 'inactive')
    for i in range(10):
        d.put(f'sess:{i:04d}', f'token_{i}')
    yield d
    d.close()


# ── scan_filter ───────────────────────────────────────────────────────────────

def test_scan_filter_by_value(db):
    inactive = list(db.scan_filter(lambda k, v: v == 'inactive'))
    assert len(inactive) == 10
    assert all(v == 'inactive' for _, v in inactive)


def test_scan_filter_by_key(db):
    result = list(db.scan_filter(lambda k, v: k.startswith('user:')))
    assert len(result) == 20


def test_scan_filter_range(db):
    result = list(db.scan_filter(
        lambda k, v: True, start='user:0005', end='user:0015'))
    assert len(result) == 10
    assert all('user:0005' <= k < 'user:0015' for k, _ in result)


def test_scan_filter_empty_result(db):
    result = list(db.scan_filter(lambda k, v: v == 'nope'))
    assert result == []


def test_scan_filter_limit(db):
    result = list(db.scan_filter(lambda k, v: True, limit=5))
    assert len(result) == 5


def test_scan_filter_limit_zero(db):
    result = list(db.scan_filter(lambda k, v: True, limit=0))
    assert result == []


def test_scan_filter_limit_larger_than_matches(db):
    result = list(db.scan_filter(lambda k, v: v == 'active', limit=1000))
    assert len(result) == 10


def test_scan_filter_yields_key_value_pairs(db):
    pairs = list(db.scan_filter(lambda k, v: True, limit=3))
    for item in pairs:
        assert len(item) == 2
        assert isinstance(item[0], str) and isinstance(item[1], str)


def test_scan_filter_both_conditions(db):
    result = list(db.scan_filter(
        lambda k, v: k.startswith('user:') and v == 'active'))
    assert all(v == 'active' for _, v in result)
    assert all(k.startswith('user:') for k, _ in result)


def test_scan_filter_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert list(d.scan_filter(lambda k, v: True)) == []
    d.close()


def test_scan_filter_is_generator(db):
    gen = db.scan_filter(lambda k, v: True)
    import types
    assert isinstance(gen, types.GeneratorType)


# ── grep ──────────────────────────────────────────────────────────────────────

def test_grep_string_pattern(db):
    keys = list(db.grep(r'user:'))
    assert len(keys) == 20
    assert all(k.startswith('user:') for k in keys)


def test_grep_compiled_pattern(db):
    pat = re.compile(r'^sess:\d{4}$')
    keys = list(db.grep(pat))
    assert len(keys) == 10
    assert all(re.match(pat, k) for k in keys)


def test_grep_range_scoped(db):
    keys = list(db.grep(r'\d{4}', start='user:', end='user;'))
    assert len(keys) == 20
    assert all(k.startswith('user:') for k in keys)


def test_grep_no_match(db):
    assert list(db.grep(r'admin:')) == []


def test_grep_limit(db):
    keys = list(db.grep(r'user:', limit=3))
    assert len(keys) == 3


def test_grep_limit_zero(db):
    assert list(db.grep(r'user:', limit=0)) == []


def test_grep_limit_larger_than_matches(db):
    keys = list(db.grep(r'sess:', limit=1000))
    assert len(keys) == 10


def test_grep_yields_strings(db):
    keys = list(db.grep(r'user:', limit=2))
    assert all(isinstance(k, str) for k in keys)


def test_grep_is_generator(db):
    import types
    assert isinstance(db.grep(r'user:'), types.GeneratorType)


def test_grep_specific_suffix_pattern(db):
    pat = re.compile(r'user:00[02468]')
    keys = list(db.grep(pat, 'user:', 'user;'))
    assert all(re.search(pat, k) for k in keys)
