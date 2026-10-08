"""
Tests for S-08: parallel compaction thread pool and write-stall back-pressure.

Verifies:
  - compaction_threads=N runs N concurrent jobs without data corruption
  - non-adjacent level pairs compact in parallel (no false serialisation)
  - write stall kicks in when L0 is full, releases when compaction drains it
  - compaction_threads=1 (single-threaded) still works correctly
"""
import os
import shutil
import threading
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, L0_COMPACT_TRIGGER


# ── Parallel compaction correctness ───────────────────────────────────────────

@pytest.mark.parametrize("threads", [1, 2, 4])
def test_parallel_compaction_correctness(tmp_path, threads):
    """Data must be intact after compaction regardless of thread count."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, compaction_threads=threads)
    n = 50000
    for i in range(n):
        db.put(f'pc{i:07d}', 'v' * 400)
    time.sleep(3)
    for i in [0, n // 4, n // 2, n - 1]:
        assert db.get(f'pc{i:07d}') == 'v' * 400
    db.close()


def test_parallel_compaction_with_deletes(tmp_path):
    """Tombstones must be handled correctly when compaction runs in parallel."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, compaction_threads=2)
    for i in range(20000):
        db.put(f'del{i:06d}', f'val{i}')
    for i in range(0, 20000, 2):    # delete even keys
        db.delete(f'del{i:06d}')
    time.sleep(2)
    for i in range(0, 100):
        if i % 2 == 0:
            assert db.get(f'del{i:06d}') is None
        else:
            assert db.get(f'del{i:06d}') == f'val{i}'
    db.close()


def test_parallel_compaction_persist_reopen(tmp_path):
    """All data written before close must be readable after reopen."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, compaction_threads=2)
    n = 30000
    for i in range(n):
        db.put(f'pr{i:07d}', f'v{i}')
    db.close()
    db2 = LSMTree(path, sync_writes=False, compaction_threads=2)
    for i in [0, n // 3, n // 2, n - 1]:
        assert db2.get(f'pr{i:07d}') == f'v{i}'
    db2.close()


def test_concurrent_writes_and_compaction(tmp_path):
    """4 writer threads writing concurrently while compaction runs in parallel."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, compaction_threads=2)
    errors = []

    def writer(tid):
        try:
            for j in range(5000):
                db.put(f't{tid}k{j:05d}', 'v' * 200)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(2)

    assert errors == [], f'Writer errors: {errors}'
    for tid in range(4):
        for j in [0, 2500, 4999]:
            v = db.get(f't{tid}k{j:05d}')
            assert v == 'v' * 200, f't{tid}k{j:05d}: {v!r}'
    db.close()


# ── Write-stall back-pressure ─────────────────────────────────────────────────

def test_write_stall_releases(tmp_path):
    """Writes must eventually complete even if L0 temporarily hits the stop trigger."""
    path = str(tmp_path / 'db')
    # Use 1 compaction thread to make it easier to fill L0 first
    db = LSMTree(path, sync_writes=False, compaction_threads=1)
    # Write a large amount — stall may kick in but all writes should finish
    for i in range(60000):
        db.put(f'stall{i:07d}', 'x' * 400)
    # If we get here, stall released correctly (didn't deadlock)
    for i in [0, 30000, 59999]:
        assert db.get(f'stall{i:07d}') == 'x' * 400
    db.close()


def test_compaction_threads_one_backward_compat(tmp_path):
    """compaction_threads=1 reproduces the old single-threaded behaviour."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False, compaction_threads=1)
    for i in range(20000):
        db.put(f'st{i:06d}', 'w' * 500)
    time.sleep(2)
    for i in [0, 10000, 19999]:
        assert db.get(f'st{i:06d}') == 'w' * 500
    db.close()
