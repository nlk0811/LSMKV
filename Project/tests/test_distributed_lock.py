"""
Tests for db.lock(), db.unlock(), and db.extend_lock().
"""
import time
import threading
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── lock ──────────────────────────────────────────────────────────────────────

def test_lock_acquires_free_lock(db):
    assert db.lock('k', 'owner') is True
    assert db.get('k') == 'owner'


def test_lock_rejects_if_held(db):
    db.lock('k', 'owner1')
    assert db.lock('k', 'owner2') is False


def test_lock_ttl_expiry_allows_reacquire(db):
    db.lock('k', 'old', ttl_seconds=0.05)
    time.sleep(0.1)
    assert db.lock('k', 'new') is True
    assert db.get('k') == 'new'


def test_lock_concurrent_one_wins(db):
    """Under concurrency exactly one thread must win the lock."""
    wins = []

    def worker(i):
        if db.lock('contest', f'w{i}', ttl_seconds=30):
            wins.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(wins) == 1, f'Expected 1 winner, got {len(wins)}'


def test_lock_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.lock('k', 'owner')
    ro.close()


# ── unlock ────────────────────────────────────────────────────────────────────

def test_unlock_releases_own_lock(db):
    db.lock('k', 'owner', ttl_seconds=100)
    assert db.unlock('k', 'owner') is True
    assert db.get('k') is None


def test_unlock_wrong_owner_fails(db):
    db.lock('k', 'alice')
    assert db.unlock('k', 'bob') is False
    assert db.get('k') == 'alice'   # alice still holds it


def test_unlock_free_lock_returns_false(db):
    assert db.unlock('k', 'owner') is False


def test_unlock_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.lock('k', 'owner', ttl_seconds=100)
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.unlock('k', 'owner')
    ro.close()


# ── extend_lock ───────────────────────────────────────────────────────────────

def test_extend_lock_renews_ttl(db):
    db.lock('k', 'owner', ttl_seconds=0.05)
    db.extend_lock('k', 'owner', ttl_seconds=10)
    time.sleep(0.1)
    assert db.has('k'), 'extended lock must survive original TTL'


def test_extend_lock_wrong_owner_fails(db):
    db.lock('k', 'alice', ttl_seconds=100)
    assert db.extend_lock('k', 'bob', ttl_seconds=300) is False
    # Alice's TTL unchanged


def test_extend_lock_absent_key_returns_false(db):
    assert db.extend_lock('absent', 'owner', ttl_seconds=100) is False


def test_lock_unlock_cycle(db):
    """Repeated lock/unlock cycles must all succeed."""
    for i in range(5):
        assert db.lock('k', f'worker-{i}') is True
        assert db.unlock('k', f'worker-{i}') is True
    assert db.get('k') is None
