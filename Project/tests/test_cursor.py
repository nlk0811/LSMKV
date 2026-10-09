"""
Tests for Cursor, scan_keys(), delete_prefix(), and stats_report().
"""
import os
import shutil
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.cursor import Cursor


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(20):
        d.put(f'key{i:03d}', f'val{i}')
    yield d
    d.close()


# ── Cursor.seek() ──────────────────────────────────────────────────────────────

def test_seek_exact(db):
    cur = db.cursor().seek('key005')
    assert cur.valid()
    assert cur.key() == 'key005'
    assert cur.value() == 'val5'


def test_seek_between_keys(db):
    """Seek to a key that doesn't exist → lands on next larger key."""
    cur = db.cursor().seek('key005z')
    assert cur.valid()
    assert cur.key() == 'key006'


def test_seek_past_end(db):
    cur = db.cursor().seek('zzz')
    assert not cur.valid()


def test_seek_to_first(db):
    cur = db.cursor().seek_to_first()
    assert cur.valid()
    assert cur.key() == 'key000'


# ── Cursor.next() ─────────────────────────────────────────────────────────────

def test_next_advances(db):
    cur = db.cursor().seek('key010')
    cur.next()
    assert cur.key() == 'key011'
    cur.next()
    assert cur.key() == 'key012'


def test_next_at_end_is_invalid(db):
    cur = db.cursor().seek('key019')
    assert cur.valid()
    cur.next()
    assert not cur.valid()


def test_next_on_invalid_is_safe(db):
    cur = db.cursor().seek('zzz')
    assert not cur.valid()
    cur.next()   # must not raise
    assert not cur.valid()


# ── Iterator protocol ─────────────────────────────────────────────────────────

def test_cursor_as_iterator(db):
    result = list(db.cursor().seek('key015'))
    keys = [k for k, _ in result]
    assert keys == ['key015', 'key016', 'key017', 'key018', 'key019']


def test_cursor_for_loop(db):
    count = 0
    for k, v in db.cursor().seek('key010'):
        assert v == f'val{int(k[-3:])}'
        count += 1
    assert count == 10


def test_cursor_context_manager(db):
    result = []
    with db.cursor() as cur:
        cur.seek('key003')
        while cur.valid() and len(result) < 3:
            result.append(cur.key())
            cur.next()
    assert result == ['key003', 'key004', 'key005']


def test_cursor_item_returns_tuple(db):
    cur = db.cursor().seek('key007')
    assert cur.item() == ('key007', 'val7')


def test_cursor_prefix_seek(db):
    """seek_prefix is an alias for seek — lands at the first key with that prefix."""
    keys = [k for k, _ in db.cursor().seek_prefix('key01')]
    assert keys == ['key010', 'key011', 'key012', 'key013', 'key014',
                    'key015', 'key016', 'key017', 'key018', 'key019']


# ── scan_keys() ───────────────────────────────────────────────────────────────

def test_scan_keys_range(db):
    keys = list(db.scan_keys('key005', 'key010'))
    assert keys == ['key005', 'key006', 'key007', 'key008', 'key009']


def test_scan_keys_unbounded(db):
    keys = list(db.scan_keys())
    assert len(keys) == 20
    assert keys[0] == 'key000' and keys[-1] == 'key019'


# ── delete_prefix() ───────────────────────────────────────────────────────────

def test_delete_prefix_removes_keys(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5):
        db.put(f'ns:key{i}', f'v{i}')
    db.put('other:key', 'keep')
    n = db.delete_prefix('ns:')
    assert n == 5
    for i in range(5):
        assert db.get(f'ns:key{i}') is None
    assert db.get('other:key') == 'keep'
    db.close()


def test_delete_prefix_empty_returns_zero(db):
    n = db.delete_prefix('nonexistent:')
    assert n == 0


def test_delete_prefix_all(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(10):
        db.put(f'item:{i}', 'v')
    n = db.delete_prefix('item:')
    assert n == 10
    assert list(db.prefix_scan('item:')) == []
    db.close()


# ── stats_report() ────────────────────────────────────────────────────────────

def test_stats_report_is_string(db):
    report = db.stats_report()
    assert isinstance(report, str)
    assert len(report) > 0


def test_stats_report_contains_sections(db):
    report = db.stats_report()
    assert 'memtable' in report
    assert 'wal' in report
    assert 'puts' in report
