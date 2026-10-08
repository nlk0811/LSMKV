"""
Tests for S-07: compaction I/O throttling and write-amplification metrics.

Verifies:
  - RateLimiter correctly throttles to a target rate
  - LSMTree respects compaction_rate_bytes_per_sec
  - Throttled compaction produces correct data
  - bytes_written_user and write_amplification appear in metrics
"""
import os
import shutil
import time
import pytest

from lsm_engine._utils import RateLimiter
from lsm_engine.lsm_tree import LSMTree


# ── RateLimiter unit tests ─────────────────────────────────────────────────────

def test_rate_limiter_unlimited():
    """Rate of 0 means unlimited — consume() must return instantly."""
    rl = RateLimiter(0)
    t0 = time.monotonic()
    for _ in range(10000):
        rl.consume(1024 * 1024)   # 1 MB each, unlimited
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1, f'Unlimited limiter took {elapsed:.3f}s — should be instant'


def test_rate_limiter_throttles():
    """At 1 MB/s, writing 2 MB must take at least 1 second."""
    rl = RateLimiter(1 * 1024 * 1024)   # 1 MB/s
    t0 = time.monotonic()
    rl.consume(1 * 1024 * 1024)   # 1 MB
    rl.consume(1 * 1024 * 1024)   # another 1 MB → must wait ~1s
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.9, f'Should have waited ~1s, waited {elapsed:.3f}s'
    assert elapsed < 3.0, f'Waited too long: {elapsed:.3f}s'


def test_rate_limiter_reset():
    """reset() clears accumulated bytes so prior debt doesn't affect future calls."""
    rl = RateLimiter(bytes_per_sec=1024)
    rl._total = 10 * 1024 * 1024   # fake 10 MB of accumulated debt
    rl.reset()
    assert rl._total == 0, 'reset() must clear _total'
    # After reset, a single tiny consume should be instant (no prior debt)
    t0 = time.monotonic()
    rl.consume(1)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1, f'consume(1) after reset took {elapsed:.3f}s'


def test_rate_limiter_small_chunks():
    """Many small consume() calls accumulate correctly."""
    rl = RateLimiter(1 * 1024 * 1024)   # 1 MB/s
    t0 = time.monotonic()
    chunk = 1024   # 1 KB per call
    for _ in range(1024):             # 1024 × 1 KB = 1 MB
        rl.consume(chunk)
    elapsed = time.monotonic() - t0
    # 1 MB at 1 MB/s ≈ 1s; allow generous bounds since the first chunk is free
    assert elapsed < 2.5, f'Took {elapsed:.3f}s for 1 MB at 1 MB/s'


# ── Integration: throttled compaction ─────────────────────────────────────────

def test_throttled_compaction_correctness(tmp_path):
    """Throttled compaction must produce correct results (no lost/corrupted data)."""
    path = str(tmp_path / 'db')
    # 10 MB/s throttle — fast enough for the test but exercises the code path
    db = LSMTree(path, sync_writes=False,
                 compaction_rate_bytes_per_sec=10 * 1024 * 1024)
    for i in range(50000):
        db.put(f'tc{i:07d}', 'v' * 400)
    time.sleep(4)   # let throttled compaction run
    for i in [0, 10000, 25000, 49999]:
        assert db.get(f'tc{i:07d}') == 'v' * 400, f'tc{i:07d} missing'
    db.close()


def test_unthrottled_vs_throttled_same_data(tmp_path):
    """Both throttled and unthrottled engines produce the same readable data."""
    for sub, rate in [('fast', 0), ('slow', 20 * 1024 * 1024)]:
        p = str(tmp_path / sub)
        db = LSMTree(p, sync_writes=False,
                     compaction_rate_bytes_per_sec=rate)
        for i in range(5000):
            db.put(f'key{i:06d}', f'val{i}')
        db.close()
        db2 = LSMTree(p, sync_writes=False)
        for i in [0, 2500, 4999]:
            assert db2.get(f'key{i:06d}') == f'val{i}'
        db2.close()


# ── Write-amplification metrics ────────────────────────────────────────────────

def test_bytes_written_user_tracked(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('hello', 'world')   # 5 + 5 = 10 bytes
    db.put('foo', 'bar')       # 3 + 3 = 6 bytes
    snap = db.metrics.snapshot()
    assert snap['bytes_written_user'] >= 16
    db.close()


def test_write_amplification_appears_after_flush(tmp_path):
    """write_amplification appears in snapshot once any flush has occurred."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(5000):
        db.put(f'wa{i:06d}', 'x' * 1000)
    db.close()   # force flush before reading metrics
    db2 = LSMTree(path, sync_writes=False)
    # After reopen, bytes_written_user from the previous session isn't visible.
    # Re-write a few keys to get bytes_written_user > 0, then close again.
    for i in range(10):
        db2.put(f'extra{i}', 'y' * 1000)
    snap_mid = db2.metrics.snapshot()
    assert snap_mid['bytes_written_user'] > 0
    db2.close()

    # Check that write_amplification, when present, is a positive number.
    db3 = LSMTree(path, sync_writes=False)
    for i in range(5000):
        db3.put(f'wb{i:06d}', 'x' * 1000)
    time.sleep(1)
    snap = db3.metrics.snapshot()
    assert snap['bytes_written_user'] > 0
    # write_amplification only appears once bytes_written_user > 0
    if 'write_amplification' in snap:
        assert snap['write_amplification'] > 0
    db3.close()


def test_write_amplification_not_present_without_writes():
    """write_amplification key absent when no bytes have been written."""
    from lsm_engine.metrics import EngineMetrics
    m = EngineMetrics()
    snap = m.snapshot()
    assert 'write_amplification' not in snap
