"""
Concurrent access tests.

Verifies that concurrent readers and writers don't corrupt data or deadlock.
All tests use threading rather than multiprocessing since the engine uses
Python threading primitives internally.
"""
import shutil
import threading
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.write_batch import WriteBatch


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'))
    yield d
    d.close()


def test_concurrent_readers(tmp_path):
    """8 threads reading from a pre-populated db should all succeed."""
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    for i in range(200):
        db.put(f'rkey{i:04d}', f'rval{i}')

    errors = []

    def reader():
        try:
            for i in range(0, 200, 4):
                val = db.get(f'rkey{i:04d}')
                assert val == f'rval{i}', f'rkey{i:04d}: got {val!r}'
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db.close()
    assert errors == [], f'Reader errors: {errors}'


def test_concurrent_writer_reader(tmp_path):
    """Writer and readers run concurrently; reads must return consistent values."""
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    # Pre-populate so readers have something to read immediately
    for i in range(100):
        db.put(f'ckey{i:04d}', f'init{i}')

    errors = []
    stop_event = threading.Event()

    def writer():
        try:
            for i in range(300):
                db.put(f'wkey{i:04d}', f'wval{i}')
        except Exception as e:
            errors.append(('writer', e))
        finally:
            stop_event.set()

    def reader():
        # Read pre-populated keys; they should always be present
        try:
            while not stop_event.is_set():
                for i in range(0, 100, 10):
                    val = db.get(f'ckey{i:04d}')
                    assert val == f'init{i}', f'ckey{i:04d}: expected init{i}, got {val!r}'
        except Exception as e:
            errors.append(('reader', e))

    w = threading.Thread(target=writer)
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    w.start()
    w.join()
    stop_event.set()
    for t in readers:
        t.join()

    db.close()
    assert errors == [], f'Concurrent errors: {errors}'


def test_concurrent_scans(tmp_path):
    """4 threads each performing range scans should not deadlock or error."""
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    for i in range(100):
        db.put(f'scan{i:04d}', f'v{i}')

    errors = []

    def scanner():
        try:
            for _ in range(5):
                results = list(db.scan('scan0000', 'scan0100'))
                assert len(results) == 100, f'Expected 100, got {len(results)}'
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=scanner) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    db.close()
    assert errors == [], f'Scan errors: {errors}'


def test_concurrent_write_batches(tmp_path):
    """4 threads each writing a non-overlapping WriteBatch; all 200 keys present at end."""
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    errors = []

    def batch_writer(thread_id):
        try:
            batch = WriteBatch()
            for j in range(50):
                batch.put(f't{thread_id}key{j:04d}', f't{thread_id}val{j}')
            db.write(batch)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=batch_writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f'Batch write errors: {errors}'
    for i in range(4):
        for j in range(50):
            val = db.get(f't{i}key{j:04d}')
            assert val == f't{i}val{j}', f't{i}key{j:04d}: got {val!r}'

    db.close()
