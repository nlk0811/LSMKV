"""
Tests for Pipeline (db.pipeline()) — buffered write context manager.
"""
import time
import pytest

from lsm_engine import LSMTree, Pipeline
from lsm_engine.lsm_tree import ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── Basic pipeline ────────────────────────────────────────────────────────────

def test_pipeline_context_manager(db):
    with db.pipeline() as p:
        p.put('k1', 'v1')
        p.put('k2', 'v2')
        p.delete('k0')
    assert db.get('k1') == 'v1'
    assert db.get('k2') == 'v2'
    assert db.get('k0') is None


def test_pipeline_execute(db):
    p = db.pipeline()
    p.put('a', '1').put('b', '2')
    n = p.execute()
    assert n == 2
    assert db.get('a') == '1' and db.get('b') == '2'


def test_pipeline_execute_returns_count(db):
    p = db.pipeline()
    for i in range(10):
        p.put(f'k{i}', f'v{i}')
    assert p.execute() == 10


def test_pipeline_empty_execute(db):
    assert db.pipeline().execute() == 0


def test_pipeline_reset_after_execute(db):
    p = db.pipeline()
    p.put('k', 'v')
    p.execute()
    assert len(p) == 0
    p.put('k2', 'v2')
    p.execute()
    assert db.get('k2') == 'v2'


def test_pipeline_len(db):
    p = db.pipeline()
    assert len(p) == 0
    p.put('k', 'v')
    assert len(p) == 1
    p.delete('x')
    assert len(p) == 2
    p.execute()
    assert len(p) == 0


def test_pipeline_repr(db):
    p = db.pipeline()
    p.put('k', 'v')
    assert 'Pipeline' in repr(p)
    assert '1' in repr(p)


# ── Chaining ──────────────────────────────────────────────────────────────────

def test_pipeline_chaining(db):
    n = (db.pipeline()
         .put('a', '1')
         .put('b', '2')
         .delete('missing')
         .execute())
    assert n == 3
    assert db.get('a') == '1'


def test_pipeline_put_ttl(db):
    with db.pipeline() as p:
        p.put_ttl('sess', 'token', ttl_seconds=0.05)
    assert db.get('sess') == 'token'
    time.sleep(0.1)
    assert db.get('sess') is None


def test_pipeline_put_with_ttl_param(db):
    with db.pipeline() as p:
        p.put('k', 'v', ttl_seconds=0.05)
    assert db.get('k') == 'v'
    time.sleep(0.1)
    assert db.get('k') is None


# ── Atomicity ─────────────────────────────────────────────────────────────────

def test_pipeline_all_or_nothing_on_exception(db):
    """Pipeline must not apply partial ops when an exception is raised."""
    try:
        with db.pipeline() as p:
            p.put('safe_key', 'value')
            raise RuntimeError('abort!')
    except RuntimeError:
        pass
    assert db.get('safe_key') is None, 'Partial ops must not be applied'


def test_pipeline_discard(db):
    p = db.pipeline()
    p.put('k', 'v')
    p.discard()
    p.execute()   # execute empty batch
    assert db.get('k') is None


# ── Persistence ───────────────────────────────────────────────────────────────

def test_pipeline_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    with db.pipeline() as p:
        for i in range(20):
            p.put(f'k{i:04d}', f'v{i}')
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    for i in [0, 10, 19]:
        assert db2.get(f'k{i:04d}') == f'v{i}'
    db2.close()


# ── db.pipeline() factory ────────────────────────────────────────────────────

def test_pipeline_from_db(db):
    p = db.pipeline()
    assert isinstance(p, Pipeline)


def test_pipeline_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.close()
    ro = LSMTree(path, read_only=True)
    p = ro.pipeline()
    p.put('k', 'v')
    with pytest.raises(ReadOnlyError):
        p.execute()
    ro.close()
