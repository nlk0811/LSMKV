"""
Tests for filter_scan(), reverse_scan(), and memory_usage().
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        d.put(f'item:{i:04d}', f'{{"score":{i},"active":{str(i%2==0).lower()}}}')
    yield d
    d.close()


# ── filter_scan ────────────────────────────────────────────────────────────────

def test_filter_scan_basic(db):
    results = list(db.filter_scan(lambda k, v: 'true' in v, 'item:', 'item;'))
    assert len(results) == 50   # even-indexed items
    assert all('true' in v for _, v in results)


def test_filter_scan_all_pass(db):
    results = list(db.filter_scan(lambda k, v: True, 'item:', 'item;'))
    assert len(results) == 100


def test_filter_scan_none_pass(db):
    results = list(db.filter_scan(lambda k, v: False, 'item:', 'item;'))
    assert results == []


def test_filter_scan_by_key(db):
    results = list(db.filter_scan(lambda k, v: k.endswith('0'), 'item:', 'item;'))
    assert all(k.endswith('0') for k, _ in results)


def test_filter_scan_unbounded(db):
    results = list(db.filter_scan(lambda k, v: 'true' in v))
    assert len(results) == 50


def test_filter_scan_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert list(db.filter_scan(lambda k, v: True)) == []
    db.close()


# ── reverse_scan ───────────────────────────────────────────────────────────────

def test_reverse_scan_order(db):
    results = list(db.reverse_scan('item:0010', 'item:0020'))
    keys = [k for k, _ in results]
    assert keys == sorted(keys, reverse=True)
    assert len(results) == 10


def test_reverse_scan_first_is_last_in_forward(db):
    forward = list(db.scan('item:0050', 'item:0060'))
    backward = list(db.reverse_scan('item:0050', 'item:0060'))
    assert [k for k, _ in forward] == list(reversed([k for k, _ in backward]))


def test_reverse_scan_same_data(db):
    forward = dict(db.scan('item:0000', 'item:0010'))
    backward = dict(db.reverse_scan('item:0000', 'item:0010'))
    assert forward == backward


def test_reverse_scan_unbounded(db):
    results = list(db.reverse_scan())
    assert len(results) == 100
    assert results[0][0] == 'item:0099'
    assert results[-1][0] == 'item:0000'


def test_reverse_scan_empty_range(db):
    results = list(db.reverse_scan('zzz0', 'zzz9'))
    assert results == []


def test_reverse_scan_with_flushed_data(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'k{i:06d}', f'v{i}')
    db.flush()
    results = list(db.reverse_scan('k004990', 'k005000'))
    assert len(results) == 10
    keys = [k for k, _ in results]
    assert keys == sorted(keys, reverse=True)
    db.close()


# ── memory_usage ──────────────────────────────────────────────────────────────

def test_memory_usage_returns_dict(db):
    mem = db.memory_usage()
    assert isinstance(mem, dict)
    assert 'total_estimated' in mem


def test_memory_usage_required_keys(db):
    mem = db.memory_usage()
    for key in ('memtable_bytes', 'imm_memtable_bytes', 'block_cache_bytes',
                'reader_cache_index_bytes', 'reader_cache_bloom_bytes',
                'level_key_index_bytes', 'total_estimated'):
        assert key in mem, f'Missing key: {key}'


def test_memory_usage_memtable_positive(db):
    mem = db.memory_usage()
    assert mem['memtable_bytes'] > 0


def test_memory_usage_total_is_sum(db):
    mem = db.memory_usage()
    components = {k: v for k, v in mem.items() if k != 'total_estimated'}
    assert mem['total_estimated'] == sum(components.values())


def test_memory_usage_after_flush(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5000):
        db.put(f'k{i:06d}', 'v' * 1000)
    before = db.memory_usage()

    db.flush()
    # Force cache population
    for i in range(100):
        db.get(f'k{i:06d}')
    after = db.memory_usage()

    # Block cache and reader cache should have grown after reads post-flush
    assert after['reader_cache_bloom_bytes'] >= before['reader_cache_bloom_bytes']
    db.close()
