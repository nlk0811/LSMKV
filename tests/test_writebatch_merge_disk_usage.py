"""
Tests for WriteBatch.merge/copy/put_count/delete_count and db.disk_usage().
"""
import pytest
from lsm_engine import LSMTree
from lsm_engine.write_batch import WriteBatch


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(20):
        d.put(f'k:{i:04d}', str(i))
    yield d
    d.close()


# ── WriteBatch.merge ──────────────────────────────────────────────────────────

def test_merge_combines_ops():
    b1 = WriteBatch(); b1.put('a', '1').put('b', '2')
    b2 = WriteBatch(); b2.put('c', '3').delete('b')
    b1.merge(b2)
    assert len(b1) == 4


def test_merge_does_not_modify_other():
    b1 = WriteBatch(); b1.put('x', 'v')
    b2 = WriteBatch(); b2.put('y', 'v')
    b1.merge(b2)
    assert len(b2) == 1   # b2 unchanged


def test_merge_returns_self():
    b1 = WriteBatch()
    result = b1.merge(WriteBatch())
    assert result is b1


def test_merge_empty_into_batch():
    b = WriteBatch(); b.put('a', '1')
    b.merge(WriteBatch())
    assert len(b) == 1


def test_merge_batch_into_empty():
    b1 = WriteBatch()
    b2 = WriteBatch(); b2.put('a', '1').delete('b')
    b1.merge(b2)
    assert len(b1) == 2


def test_merge_applied_atomically(db):
    b1 = WriteBatch(); b1.put('new_a', 'A')
    b2 = WriteBatch(); b2.put('new_b', 'B').delete('k:0000')
    b1.merge(b2)
    db.write(b1)
    assert db.get('new_a') == 'A'
    assert db.get('new_b') == 'B'
    assert db.get('k:0000') is None


def test_merge_chaining():
    b1 = WriteBatch(); b1.put('a', '1')
    b2 = WriteBatch(); b2.put('b', '2')
    b3 = WriteBatch(); b3.put('c', '3')
    b1.merge(b2).merge(b3)
    assert len(b1) == 3


# ── WriteBatch.copy ───────────────────────────────────────────────────────────

def test_copy_has_same_len():
    b = WriteBatch(); b.put('a', '1').delete('b')
    assert len(b.copy()) == 2


def test_copy_is_independent():
    b = WriteBatch(); b.put('a', '1')
    c = b.copy()
    c.put('extra', 'v')
    assert len(b) == 1  # original unchanged


def test_copy_applied_correctly(db):
    b = WriteBatch(); b.put('copy_test', 'v')
    c = b.copy()
    db.write(c)
    assert db.get('copy_test') == 'v'


# ── put_count / delete_count ──────────────────────────────────────────────────

def test_put_count():
    b = WriteBatch()
    b.put('a', '1').put('b', '2').delete('c')
    assert b.put_count == 2


def test_delete_count():
    b = WriteBatch()
    b.put('a', '1').delete('b').delete('c')
    assert b.delete_count == 2


def test_put_count_includes_put_ttl():
    b = WriteBatch()
    b.put_ttl('a', 'v', ttl_seconds=60)
    assert b.put_count == 1


def test_counts_sum_to_len():
    b = WriteBatch()
    b.put('a', '1').put('b', '2').delete('c').delete('d')
    assert b.put_count + b.delete_count == len(b)


# ── db.disk_usage ─────────────────────────────────────────────────────────────

def test_disk_usage_has_expected_keys(db):
    usage = db.disk_usage()
    for key in ('sstable_bytes', 'wal_bytes', 'memtable_bytes',
                'imm_bytes', 'total_bytes', 'by_level'):
        assert key in usage


def test_disk_usage_total_is_sum(db):
    u = db.disk_usage()
    expected = (u['sstable_bytes'] + u['wal_bytes'] +
                u['memtable_bytes'] + u['imm_bytes'])
    assert u['total_bytes'] == expected


def test_disk_usage_wal_positive(db):
    assert db.disk_usage()['wal_bytes'] > 0


def test_disk_usage_memtable_positive(db):
    assert db.disk_usage()['memtable_bytes'] > 0


def test_disk_usage_sstable_after_flush(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(1000):
        d.put(f'k:{i}', str(i))
    d.flush()
    usage = d.disk_usage()
    assert usage['sstable_bytes'] > 0
    d.close()


def test_disk_usage_by_level_dict(db):
    db.flush()
    usage = db.disk_usage()
    assert isinstance(usage['by_level'], dict)
