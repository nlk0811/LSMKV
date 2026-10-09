"""
Tests for:
  - Group-commit WAL batching under concurrent writes
  - db.validate() integrity checker
"""
import os
import shutil
import threading
import time
import pytest

from lsm_engine.lsm_tree import LSMTree


# ── Group-commit concurrent writes ────────────────────────────────────────────

def test_concurrent_writes_no_data_loss(tmp_path):
    """N concurrent writers: all written keys must be readable at the end."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=True)
    N_THREADS = 8
    N_EACH    = 100
    errors    = []

    def writer(tid):
        try:
            for i in range(N_EACH):
                db.put(f't{tid}k{i:04d}', f'v{tid}_{i}')
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(N_THREADS)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == [], f'Write errors: {errors}'
    for tid in range(N_THREADS):
        for i in [0, N_EACH // 2, N_EACH - 1]:
            v = db.get(f't{tid}k{i:04d}')
            assert v == f'v{tid}_{i}', f't{tid}k{i:04d}: got {v!r}'
    db.close()


def test_concurrent_writes_faster_than_sequential(tmp_path):
    """Concurrent sync writes must achieve better total throughput than sequential."""
    N_THREADS = 4
    N_EACH    = 100

    # Sequential
    db_seq = LSMTree(str(tmp_path / 'seq'), sync_writes=True)
    t0 = time.perf_counter()
    for i in range(N_THREADS * N_EACH):
        db_seq.put(f'sk{i:06d}', 'v')
    seq_time = time.perf_counter() - t0
    db_seq.close()

    # Concurrent
    db_con = LSMTree(str(tmp_path / 'con'), sync_writes=True)
    def worker(tid):
        for i in range(N_EACH):
            db_con.put(f'ck{tid}_{i:04d}', 'v')

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    con_time = time.perf_counter() - t0
    db_con.close()

    # Concurrent should be at least 1.5× faster (group-commit batching)
    speedup = seq_time / con_time
    assert speedup >= 1.5, (
        f'Expected concurrent to be at least 1.5× faster than sequential; '
        f'got {speedup:.2f}× (seq={seq_time:.2f}s, con={con_time:.2f}s)'
    )


def test_group_commit_correctness_persist(tmp_path):
    """Data written by concurrent writers must survive close/reopen."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=True)
    N = 4

    def writer(tid):
        for i in range(50):
            db.put(f'gc{tid}_{i:04d}', f'val{tid}_{i}')

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    for tid in range(N):
        for i in [0, 25, 49]:
            assert db2.get(f'gc{tid}_{i:04d}') == f'val{tid}_{i}'
    db2.close()


# ── db.validate() ─────────────────────────────────────────────────────────────

def test_validate_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    result = db.validate()
    assert result['ok'] is True
    assert result['issues'] == []
    db.close()


def test_validate_after_flush(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(1000):
        db.put(f'k{i:05d}', 'v')
    db.flush()
    result = db.validate()
    assert result['ok'] is True
    db.close()


def test_validate_detects_missing_file(tmp_path):
    """validate() must report a missing SSTable file."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(5000):
        db.put(f'k{i:06d}', 'x' * 1000)
    db.flush()

    # Find and remove an SSTable file from under the engine
    import json
    manifest_data = json.load(open(os.path.join(path, 'MANIFEST.json')))
    sst_files = [f for lvl in manifest_data['levels'] for f in lvl]
    if sst_files:
        os.remove(sst_files[0])   # delete a file without telling the manifest

    result = db.validate()
    assert result['ok'] is False
    assert any('missing' in issue for issue in result['issues'])
    db.close()


def test_validate_returns_dict(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    result = db.validate()
    assert isinstance(result, dict)
    assert 'ok' in result
    assert 'issues' in result
    assert isinstance(result['issues'], list)
    db.close()


def test_validate_concurrent_safe(tmp_path):
    """validate() must not crash or deadlock when called during active writes."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(1000):
        db.put(f'k{i:05d}', 'v')

    results = []
    def validate_worker():
        results.append(db.validate())

    threads = [threading.Thread(target=validate_worker) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert all(r['ok'] for r in results)
    db.close()
