"""
Tests for Transaction.scan(), scan_keys(), and get_many().
"""
import pytest
from lsm_engine import LSMTree


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(10):
        d.put(f'item:{i:04d}', str(i * 10))
    yield d
    d.close()


# ── scan ──────────────────────────────────────────────────────────────────────

def test_txn_scan_yields_correct_pairs(db):
    with db.transaction() as txn:
        items = list(txn.scan('item:', 'item;'))
    assert len(items) == 10
    assert all(len(p) == 2 for p in items)


def test_txn_scan_records_reads(db):
    txn = db.transaction()
    list(txn.scan('item:0000', 'item:0005'))
    assert 'item:0000' in txn._reads
    assert 'item:0004' in txn._reads


def test_txn_scan_commit_success(db):
    for _ in range(5):
        with db.transaction() as txn:
            items = list(txn.scan('item:', 'item;'))
            total = sum(int(v) for _, v in items)
            txn.put('total', str(total))
        if txn.committed:
            break
    assert db.get('total') == str(sum(i * 10 for i in range(10)))


def test_txn_scan_conflict_detected(db):
    txn = db.transaction()
    list(txn.scan('item:', 'item;'))          # reads item:0000 = '0'
    db.put('item:0000', '999')                 # concurrent modification
    assert txn.commit() is False               # conflict!


def test_txn_scan_conflict_on_partial_range(db):
    txn = db.transaction()
    list(txn.scan('item:0005', 'item;'))       # reads item:0005..item:0009
    db.put('item:0007', '999')                 # modifies a key in the read range
    assert txn.commit() is False


def test_txn_scan_no_conflict_outside_range(db):
    txn = db.transaction()
    list(txn.scan('item:0005', 'item;'))       # reads item:0005..item:0009
    db.put('item:0003', '999')                 # outside read range
    txn.put('marker', 'x')
    assert txn.commit() is True                # no conflict


def test_txn_scan_range_scoped(db):
    with db.transaction() as txn:
        items = list(txn.scan('item:0003', 'item:0007'))
    assert len(items) == 4
    assert items[0][0] == 'item:0003'


def test_txn_scan_empty_result(db):
    with db.transaction() as txn:
        items = list(txn.scan('zzz:', 'zzz;'))
    assert items == []


# ── scan_keys ─────────────────────────────────────────────────────────────────

def test_txn_scan_keys_only(db):
    with db.transaction() as txn:
        keys = list(txn.scan_keys('item:', 'item;'))
    assert len(keys) == 10
    assert all(isinstance(k, str) for k in keys)


def test_txn_scan_keys_records_reads(db):
    txn = db.transaction()
    list(txn.scan_keys('item:0000', 'item:0005'))
    assert 'item:0000' in txn._reads


# ── get_many ──────────────────────────────────────────────────────────────────

def test_txn_get_many_returns_dict(db):
    with db.transaction() as txn:
        vals = txn.get_many(['item:0000', 'item:0001', 'absent'])
    assert vals['item:0000'] == '0'
    assert vals['item:0001'] == '10'
    assert vals['absent'] is None


def test_txn_get_many_records_reads(db):
    txn = db.transaction()
    txn.get_many(['item:0000', 'item:0001'])
    assert 'item:0000' in txn._reads
    assert 'item:0001' in txn._reads


def test_txn_get_many_conflict_detected(db):
    txn = db.transaction()
    txn.get_many(['item:0000', 'item:0001'])
    db.put('item:0001', '999')
    assert txn.commit() is False


def test_txn_get_many_no_conflict(db):
    txn = db.transaction()
    txn.get_many(['item:0000', 'item:0001'])
    txn.put('out', 'val')
    assert txn.commit() is True
