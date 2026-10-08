"""
Tests for WriteBatch and LSMTree.write(batch).
"""
import os
import shutil
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.write_batch import WriteBatch


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'))
    yield d
    d.close()
    shutil.rmtree(str(tmp_path / 'db'), ignore_errors=True)


def test_batch_put_and_get(db):
    batch = WriteBatch()
    batch.put('key1', 'val1')
    batch.put('key2', 'val2')
    batch.put('key3', 'val3')
    db.write(batch)
    assert db.get('key1') == 'val1'
    assert db.get('key2') == 'val2'
    assert db.get('key3') == 'val3'


def test_batch_delete(db):
    db.put('to_delete', 'here')
    batch = WriteBatch()
    batch.delete('to_delete')
    db.write(batch)
    assert db.get('to_delete') is None


def test_batch_mixed(db):
    db.put('existing', 'old')
    batch = WriteBatch()
    batch.put('new_key', 'new_val')
    batch.delete('existing')
    batch.put('another', 'value')
    db.write(batch)
    assert db.get('new_key') == 'new_val'
    assert db.get('existing') is None
    assert db.get('another') == 'value'


def test_batch_overwrite(db):
    """When a batch puts the same key twice, the last value should win."""
    batch = WriteBatch()
    batch.put('key', 'first')
    batch.put('key', 'second')
    db.write(batch)
    assert db.get('key') == 'second'


def test_empty_batch(db):
    """Writing an empty batch should be a no-op with no exception."""
    batch = WriteBatch()
    db.write(batch)  # must not raise


def test_batch_chaining(db):
    """put/delete return self so calls can be chained."""
    batch = WriteBatch()
    batch.put('a', '1').put('b', '2').delete('a')
    db.write(batch)
    assert db.get('a') is None
    assert db.get('b') == '2'


def test_batch_persists_after_reopen(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path)
    batch = WriteBatch()
    for i in range(20):
        batch.put(f'key{i:03d}', f'val{i}')
    db.write(batch)
    db.close()

    db2 = LSMTree(path)
    for i in range(20):
        assert db2.get(f'key{i:03d}') == f'val{i}'
    db2.close()
    shutil.rmtree(path, ignore_errors=True)


def test_batch_len_and_byte_size():
    """len(batch) counts ops; byte_size sums key+value bytes."""
    batch = WriteBatch()
    batch.put('abc', 'xyz')       # 3 + 3 = 6
    batch.put('hello', 'world')   # 5 + 5 = 10
    batch.delete('gone')          # 4 + 0 = 4  (delete stores empty value)
    assert len(batch) == 3
    assert batch.byte_size == 20


def test_large_batch(db):
    """A batch of 1000 puts should all be readable after write."""
    batch = WriteBatch()
    for i in range(1000):
        batch.put(f'bigkey{i:06d}', f'bigval{i}')
    db.write(batch)
    assert db.get('bigkey000000') == 'bigval0'
    assert db.get('bigkey000500') == 'bigval500'
    assert db.get('bigkey000999') == 'bigval999'
