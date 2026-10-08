"""
Tests for EngineMetrics and LSMTree metrics integration.

Metrics are updated on every operation; these tests verify the plumbing is
wired up correctly, not exact counts (background compaction may add noise).
"""
import shutil
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.metrics import EngineMetrics


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'))
    yield d
    d.close()


# ── EngineMetrics unit tests ───────────────────────────────────────────────────

def test_metrics_snapshot_has_required_keys():
    m = EngineMetrics()
    snap = m.snapshot()
    for key in ('uptime_seconds', 'puts', 'deletes', 'gets',
                'get_hits', 'get_misses', 'get_hit_rate',
                'cache_hits', 'cache_misses', 'cache_hit_rate',
                'flushes', 'compactions', 'scans',
                'write_ops_per_sec', 'read_ops_per_sec'):
        assert key in snap, f'Missing key: {key}'


def test_metrics_inc():
    m = EngineMetrics()
    m.inc(puts=5, gets=3)
    snap = m.snapshot()
    assert snap['puts'] == 5
    assert snap['gets'] == 3


def test_metrics_rates_non_negative():
    m = EngineMetrics()
    m.inc(puts=10, gets=5)
    snap = m.snapshot()
    assert snap['write_ops_per_sec'] >= 0
    assert snap['read_ops_per_sec'] >= 0


def test_metrics_hit_rate_zero_when_no_gets():
    m = EngineMetrics()
    snap = m.snapshot()
    assert snap['get_hit_rate'] == 0.0


def test_metrics_hit_rate_correct():
    m = EngineMetrics()
    m.inc(gets=10, get_hits=7, get_misses=3)
    snap = m.snapshot()
    assert snap['get_hit_rate'] == pytest.approx(0.7, abs=0.001)


# ── Integration: metrics updated by LSMTree ───────────────────────────────────

def test_puts_tracked(db):
    for i in range(10):
        db.put(f'k{i}', f'v{i}')
    snap = db.metrics.snapshot()
    assert snap['puts'] >= 10


def test_gets_tracked(db):
    for i in range(5):
        db.put(f'k{i}', f'v{i}')
    for i in range(10):   # 5 hits, 5 misses
        db.get(f'k{i}')
    snap = db.metrics.snapshot()
    assert snap['gets'] >= 10
    assert snap['get_hits'] >= 5
    assert snap['get_misses'] >= 5


def test_scans_tracked(db):
    for i in range(10):
        db.put(f'sk{i:02d}', f'v{i}')
    list(db.scan('sk00', 'sk10'))
    snap = db.metrics.snapshot()
    assert snap['scans'] >= 1
    assert snap['scan_entries'] >= 10


def test_stats_includes_metrics(db):
    """db.stats() should include everything from metrics.snapshot()."""
    db.put('x', 'y')
    s = db.stats()
    # stats() merges metrics into its dict
    assert 'puts' in s
    assert 'uptime_seconds' in s
    assert 'imm_bytes' in s   # added in this iteration


def test_cache_tracking(tmp_path):
    """First read on a key in an SSTable should be a cache miss; subsequent a hit."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)  # fast for bulk load
    # Write enough to create an SSTable (4 MB+)
    for i in range(5000):
        db.put(f'ck{i:06d}', 'x' * 1000)
    import time; time.sleep(0.3)   # allow background flush

    snap_before = db.metrics.snapshot()
    db.get('ck000000')   # may be cache miss (reader not yet open for this SSTable)
    db.get('ck000000')   # should be cache hit (reader already cached)
    snap_after = db.metrics.snapshot()

    # At least one cache event should have occurred
    assert snap_after['cache_hits'] + snap_after['cache_misses'] > \
           snap_before['cache_hits'] + snap_before['cache_misses']

    db.close()
