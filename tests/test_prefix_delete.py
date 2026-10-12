"""
Tests for db.prefix_delete() — memory-bounded bulk deletion by key prefix.
"""
import pytest
from lsm_engine import LSMTree
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(40):
        d.put(f'session:{i:04d}', f'tok{i}')
    for i in range(20):
        d.put(f'user:{i:04d}', f'u{i}')
    yield d
    d.close()


# ── basic deletion ────────────────────────────────────────────────────────────

def test_prefix_delete_removes_all(db):
    n = db.prefix_delete('session:')
    assert n == 40
    remaining = list(db.scan_keys('session:', 'session;'))
    assert remaining == []


def test_prefix_delete_leaves_other_prefixes(db):
    db.prefix_delete('session:')
    users = list(db.scan_keys('user:', 'user;'))
    assert len(users) == 20


def test_prefix_delete_returns_count(db):
    assert db.prefix_delete('session:') == 40


def test_prefix_delete_nonexistent_prefix(db):
    assert db.prefix_delete('nope:') == 0


def test_prefix_delete_empty_db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert d.prefix_delete('k:') == 0
    d.close()


# ── batching ──────────────────────────────────────────────────────────────────

def test_prefix_delete_batch_size_1(db):
    n = db.prefix_delete('session:', batch_size=1)
    assert n == 40
    assert list(db.scan_keys('session:', 'session;')) == []


def test_prefix_delete_batch_size_3(db):
    n = db.prefix_delete('session:', batch_size=3)
    assert n == 40
    assert list(db.scan_keys('session:', 'session;')) == []


def test_prefix_delete_batch_size_larger_than_total(db):
    n = db.prefix_delete('session:', batch_size=10000)
    assert n == 40
    assert list(db.scan_keys('session:', 'session;')) == []


def test_prefix_delete_exact_multiple(db):
    # 40 keys, batch_size=10 → exactly 4 batches, no partial last batch
    n = db.prefix_delete('session:', batch_size=10)
    assert n == 40
    assert list(db.scan_keys('session:', 'session;')) == []


# ── correctness under various data arrangements ───────────────────────────────

def test_prefix_delete_single_key(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    d.put('only:one', 'val')
    n = d.prefix_delete('only:')
    assert n == 1
    assert d.get('only:one') is None
    d.close()


def test_prefix_delete_does_not_affect_superprefix(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    d.put('se', 'short')
    d.put('session:001', 'tok')
    d.prefix_delete('session:')
    assert d.get('se') == 'short'   # 'se' does not start with 'session:'
    d.close()


def test_prefix_delete_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    for i in range(10):
        d.put(f'del:{i}', str(i))
        d.put(f'keep:{i}', str(i))
    d.prefix_delete('del:')
    d.close()

    d2 = LSMTree(path, sync_writes=False)
    assert list(d2.scan_keys('del:', 'del;')) == []
    assert len(list(d2.scan_keys('keep:', 'keep;'))) == 10
    d2.close()


def test_prefix_delete_idempotent(db):
    db.prefix_delete('session:')
    n = db.prefix_delete('session:')  # already gone
    assert n == 0


# ── read-only guard ───────────────────────────────────────────────────────────

def test_prefix_delete_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k:1', 'v')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.prefix_delete('k:')
    ro.close()
