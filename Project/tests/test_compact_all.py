"""
Tests for compact_all(), wait_for_compaction(), iter_batches(), and
the updated stats_report() with latency percentiles.
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def loaded_db(tmp_path):
    """DB with ~25 MB of data (triggers multiple flushes and L0→L1 compaction)."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50000):
        db.put(f'key{i:07d}', 'v' * 400)
    time.sleep(3)
    yield db
    db.close()


# ── wait_for_compaction() ──────────────────────────────────────────────────────

def test_wait_returns_true_when_no_compaction_running(loaded_db):
    """Immediately after background compaction settles, wait should return True."""
    result = loaded_db.wait_for_compaction(timeout_seconds=10)
    assert result is True


def test_wait_returns_false_on_timeout(tmp_path):
    """wait with a zero timeout should return False (nothing can finish instantly)."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    # Submit a fake long-running future
    import concurrent.futures, threading
    ready = threading.Event()
    def slow_job():
        ready.wait(5)   # block for up to 5s
    fut = db._compaction_executor.submit(slow_job)
    with db._compaction_fut_lock:
        db._compaction_futures[99] = fut
    result = db.wait_for_compaction(timeout_seconds=0)
    assert result is False
    ready.set()
    fut.result(timeout=6)
    db.close()


# ── compact_all() ──────────────────────────────────────────────────────────────

def test_compact_all_returns_dict(loaded_db):
    info = loaded_db.compact_all(timeout_seconds=60)
    assert isinstance(info, dict)
    assert 'compactions_run' in info
    assert 'elapsed_seconds' in info


def test_compact_all_data_intact(loaded_db):
    """All keys must be readable after compact_all()."""
    loaded_db.compact_all(timeout_seconds=60)
    for i in [0, 25000, 49999]:
        assert loaded_db.get(f'key{i:07d}') == 'v' * 400


def test_compact_all_elapsed_is_positive(loaded_db):
    info = loaded_db.compact_all()
    assert info['elapsed_seconds'] >= 0


def test_compact_all_empty_db(tmp_path):
    """compact_all on an empty database must not crash."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    info = db.compact_all()
    assert info['compactions_run'] >= 0
    db.close()


def test_compact_all_then_get(tmp_path):
    """After compact_all(), keys written before must still be readable."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(50000):
        db.put(f'ca{i:06d}', f'v{i}')
    db.compact_all()
    for i in [0, 25000, 49999]:
        assert db.get(f'ca{i:06d}') == f'v{i}'
    db.close()


# ── iter_batches() ─────────────────────────────────────────────────────────────

def test_iter_batches_all_keys(tmp_path):
    """iter_batches must yield all keys exactly once."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    n = 250
    for i in range(n):
        db.put(f'ib{i:04d}', f'v{i}')
    total = sum(len(b) for b in db.iter_batches(batch_size=50))
    assert total == n
    db.close()


def test_iter_batches_size(tmp_path):
    """All batches except possibly the last must be exactly batch_size."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(105):
        db.put(f'bs{i:04d}', 'v')
    batches = list(db.iter_batches(batch_size=20))
    for b in batches[:-1]:
        assert len(b) == 20
    assert len(batches[-1]) <= 20
    db.close()


def test_iter_batches_sorted_order(tmp_path):
    """Keys within batches and across batches must be in sorted order."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        db.put(f'ord{i:04d}', 'v')
    all_keys = [k for batch in db.iter_batches(batch_size=30) for k, _ in batch]
    assert all_keys == sorted(all_keys)
    db.close()


def test_iter_batches_range(tmp_path):
    """iter_batches respects start/end bounds."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        db.put(f'rng{i:04d}', 'v')
    keys = [k for batch in db.iter_batches(batch_size=10, start='rng0050', end='rng0060')
            for k, _ in batch]
    assert len(keys) == 10
    assert keys[0] == 'rng0050' and keys[-1] == 'rng0059'
    db.close()


def test_iter_batches_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert list(db.iter_batches()) == []
    db.close()


# ── stats_report() with latency ───────────────────────────────────────────────

def test_stats_report_includes_latency(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        db.put(f'k{i}', 'v')
    for i in range(50):
        db.get(f'k{i}')
    report = db.stats_report()
    assert 'p50' in report and 'p99' in report, f'No latency in: {report[:200]}'
    assert 'µs' in report
    db.close()


def test_stats_report_format(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('k', 'v')
    report = db.stats_report()
    assert report.startswith('┌')
    assert report.endswith('┘')
    assert 'memtable' in report
    assert 'puts' in report
    db.close()
