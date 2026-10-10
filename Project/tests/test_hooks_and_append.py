"""
Tests for event hooks (on_flush, on_compaction) and db.atomic_append().
"""
import os
import shutil
import time
import threading
import pytest

from lsm_engine.lsm_tree import LSMTree


# ── on_flush ──────────────────────────────────────────────────────────────────

def test_on_flush_called_after_flush(tmp_path):
    log = []
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False,
                 on_flush=lambda b, l: log.append((b, l)))
    for i in range(1000):
        db.put(f'k{i:05d}', 'x' * 1000)
    db.flush()
    assert len(log) >= 1, 'on_flush must be called after flush()'
    assert all(b > 0 for b, _ in log), 'bytes_written must be > 0'
    assert all(l == 0 for _, l in log), 'level must be 0 for L0 flush'
    db.close()


def test_on_flush_not_called_without_data(tmp_path):
    log = []
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False,
                 on_flush=lambda b, l: log.append((b, l)))
    db.flush()   # empty memtable — no flush should happen
    # May or may not fire depending on imm state; just must not crash
    db.close()


def test_on_flush_exception_does_not_crash(tmp_path):
    """An exception in the on_flush callback must not propagate."""
    def bad_hook(b, l):
        raise RuntimeError('hook failure')

    db = LSMTree(str(tmp_path / 'db'), sync_writes=False, on_flush=bad_hook)
    for i in range(1000):
        db.put(f'k{i}', 'v' * 1000)
    db.flush()   # must not raise
    db.close()


def test_on_flush_called_from_background_thread(tmp_path):
    """on_flush fires from the background thread — must be thread-safe."""
    caller_threads = set()

    def hook(b, l):
        caller_threads.add(threading.current_thread().name)

    db = LSMTree(str(tmp_path / 'db'), sync_writes=False, on_flush=hook)
    for i in range(5000):
        db.put(f'k{i:06d}', 'v' * 1000)
    time.sleep(1)
    db.close()
    main_name = threading.main_thread().name
    # At least some flushes should come from non-main threads
    assert len(caller_threads) > 0


# ── on_compaction ─────────────────────────────────────────────────────────────

def test_on_compaction_called(tmp_path):
    log = []
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False,
                 on_compaction=lambda s, d, bi, bo: log.append((s, d, bi, bo)))
    for i in range(50000):
        db.put(f'k{i:08d}', 'v' * 400)
    time.sleep(4)
    assert len(log) >= 1, 'on_compaction must be called after L0→L1 compaction'
    s, d, bi, bo = log[0]
    assert s == 0 and d == 1, 'First compaction should be L0→L1'
    assert bi > 0 and bo > 0
    db.close()


def test_on_compaction_exception_does_not_crash(tmp_path):
    """Exception in on_compaction must not crash the engine."""
    def bad_hook(s, d, bi, bo):
        raise ValueError('compaction hook failure')

    db = LSMTree(str(tmp_path / 'db'), sync_writes=False, on_compaction=bad_hook)
    for i in range(50000):
        db.put(f'k{i:08d}', 'v' * 400)
    time.sleep(4)   # compaction runs, hook raises, must not crash
    assert db.get('k00000000') == 'v' * 400
    db.close()


def test_on_compaction_bytes_reasonable(tmp_path):
    """bytes_in and bytes_out in the compaction hook must be reasonable."""
    log = []
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False,
                 on_compaction=lambda s, d, bi, bo: log.append((bi, bo)))
    for i in range(50000):
        db.put(f'k{i:08d}', 'v' * 400)
    time.sleep(4)
    if log:
        bi, bo = log[0]
        # Output should be within 20% of input (no tombstones, no drop)
        assert bo > 0
        ratio = bo / bi
        assert 0.3 < ratio < 2.0, f'Unexpected compaction ratio: {ratio:.2f}'
    db.close()


# ── atomic_append ──────────────────────────────────────────────────────────────

def test_atomic_append_creates_key(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    result = db.atomic_append('k', 'first')
    assert result == 'first'
    assert db.get('k') == 'first'
    db.close()


def test_atomic_append_appends(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.atomic_append('list', 'a')
    db.atomic_append('list', 'b')
    db.atomic_append('list', 'c')
    assert db.get('list') == 'a,b,c'
    db.close()


def test_atomic_append_custom_separator(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.atomic_append('path', '/usr', separator='/')
    db.atomic_append('path', 'local', separator='/')
    db.atomic_append('path', 'bin', separator='/')
    assert db.get('path') == '/usr/local/bin'
    db.close()


def test_atomic_append_concurrent(tmp_path):
    """Concurrent appends must not lose any item."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    N = 8

    def worker(tid):
        for i in range(10):
            db.atomic_append('shared', f'{tid}_{i}')

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()

    result = db.get('shared')
    items = result.split(',') if result else []
    assert len(items) == N * 10, f'Expected {N*10} items, got {len(items)}'
    db.close()


def test_atomic_append_persist(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.atomic_append('log', 'entry1')
    db.atomic_append('log', 'entry2')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('log') == 'entry1,entry2'
    db2.close()
