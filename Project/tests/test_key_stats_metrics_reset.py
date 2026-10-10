"""
Tests for db.key_stats() and EngineMetrics.reset().
"""
import time
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.metrics import EngineMetrics


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(200):
        d.put(f'key{i:05d}', 'v' * (i % 50 + 10))
    yield d
    d.close()


# ── key_stats ─────────────────────────────────────────────────────────────────

def test_key_stats_returns_dict(db):
    stats = db.key_stats(100)
    assert isinstance(stats, dict)
    assert 'sampled' in stats
    assert 'key_size' in stats
    assert 'val_size' in stats


def test_key_stats_sample_count(db):
    stats = db.key_stats(50)
    assert stats['sampled'] == 50


def test_key_stats_sample_all(db):
    stats = db.key_stats(10000)
    assert stats['sampled'] == 200   # only 200 keys in db


def test_key_stats_key_size_fields(db):
    stats = db.key_stats(100)
    ks = stats['key_size']
    assert all(k in ks for k in ('min', 'max', 'avg', 'total'))


def test_key_stats_min_leq_max(db):
    stats = db.key_stats(100)
    assert stats['key_size']['min'] <= stats['key_size']['max']
    assert stats['val_size']['min'] <= stats['val_size']['max']


def test_key_stats_avg_in_range(db):
    stats = db.key_stats(100)
    ks = stats['key_size']
    assert ks['min'] <= ks['avg'] <= ks['max']


def test_key_stats_total_consistent(db):
    stats = db.key_stats(100)
    ks = stats['key_size']
    assert abs(ks['total'] - ks['avg'] * stats['sampled']) < 0.01 * stats['sampled']


def test_key_stats_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    stats = db.key_stats()
    assert stats['sampled'] == 0
    assert stats['key_size'] == {}
    db.close()


def test_key_stats_uniform_keys(tmp_path):
    """All keys same length → min == max == avg."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(10):
        db.put(f'key{i:04d}', 'val')   # always 7 bytes ('key0000')
    stats = db.key_stats()
    ks = stats['key_size']
    assert ks['min'] == ks['max'] == ks['avg'] == 7
    db.close()


# ── EngineMetrics.reset ────────────────────────────────────────────────────────

def test_metrics_reset_clears_counts(db):
    db.get('key00000')
    db.put('new_k', 'new_v')
    db.metrics.reset()
    snap = db.metrics.snapshot()
    assert snap['puts'] == 0
    assert snap['gets'] == 0


def test_metrics_reset_restarts_uptime(db):
    time.sleep(0.05)
    db.metrics.reset()
    snap = db.metrics.snapshot()
    assert snap['uptime_seconds'] < 0.1


def test_metrics_reset_counts_new_ops(db):
    db.metrics.reset()
    for i in range(10):
        db.put(f'new{i}', 'v')
    for i in range(5):
        db.get(f'new{i}')
    snap = db.metrics.snapshot()
    assert snap['puts'] == 10
    assert snap['gets'] == 5


def test_metrics_reset_unit(tmp_path):
    m = EngineMetrics()
    m.inc(puts=100, gets=50, compactions=3)
    m.reset()
    snap = m.snapshot()
    assert snap['puts'] == 0
    assert snap['gets'] == 0
    assert snap['compactions'] == 0


def test_metrics_reset_latency_cleared(db):
    for i in range(10):
        db.get(f'key{i:05d}')
    db.metrics.reset()
    snap = db.metrics.snapshot()
    assert snap['get_count'] == 0
    assert snap['get_p50_us'] == 0.0


def test_metrics_reset_does_not_affect_subsequent_ops(db):
    db.metrics.reset()
    db.put('after_reset', 'val')
    result = db.get('after_reset')
    assert result == 'val'
    snap = db.metrics.snapshot()
    assert snap['puts'] >= 1
    assert snap['gets'] >= 1
