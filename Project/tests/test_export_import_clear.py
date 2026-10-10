"""
Tests for db.export(), db.import_(), and db.clear().
"""
import json
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree, ReadOnlyError


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(100):
        d.put(f'k{i:04d}', f'v{i}')
    yield d
    d.close()


# ── export ────────────────────────────────────────────────────────────────────

def test_export_creates_file(db, tmp_path):
    path = str(tmp_path / 'out.jsonl')
    db.export(path)
    assert os.path.exists(path)


def test_export_returns_count(db, tmp_path):
    n = db.export(str(tmp_path / 'out.jsonl'))
    assert n == 100


def test_export_jsonl_format(db, tmp_path):
    path = str(tmp_path / 'out.jsonl')
    db.export(path)
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) == 100
    for line in lines:
        obj = json.loads(line)
        assert 'k' in obj and 'v' in obj


def test_export_range(db, tmp_path):
    path = str(tmp_path / 'out.jsonl')
    n = db.export(path, start='k0010', end='k0020')
    assert n == 10


def test_export_sorted_order(db, tmp_path):
    path = str(tmp_path / 'out.jsonl')
    db.export(path)
    with open(path) as f:
        keys = [json.loads(l)['k'] for l in f if l.strip()]
    assert keys == sorted(keys)


def test_export_read_only_ok(tmp_path):
    """export() on a read-only database must work (reads only)."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    n = ro.export(str(tmp_path / 'out.jsonl'))
    assert n == 1
    ro.close()


# ── import_ ───────────────────────────────────────────────────────────────────

def test_import_roundtrip(db, tmp_path):
    export_path = str(tmp_path / 'out.jsonl')
    db.export(export_path)

    db2 = LSMTree(str(tmp_path / 'db2'), sync_writes=False)
    m = db2.import_(export_path)
    assert m == 100
    for i in [0, 50, 99]:
        assert db2.get(f'k{i:04d}') == f'v{i}'
    db2.close()


def test_import_returns_count(db, tmp_path):
    export_path = str(tmp_path / 'out.jsonl')
    db.export(export_path)
    db2 = LSMTree(str(tmp_path / 'db2'), sync_writes=False)
    m = db2.import_(export_path)
    assert m == 100
    db2.close()


def test_import_with_ttl(db, tmp_path):
    export_path = str(tmp_path / 'out.jsonl')
    db.export(export_path, start='k0000', end='k0010')
    db2 = LSMTree(str(tmp_path / 'db2'), sync_writes=False)
    db2.import_(export_path, ttl_seconds=0.05)
    assert db2.get('k0005') is not None   # before expiry
    time.sleep(0.1)
    assert db2.get('k0005') is None       # after expiry
    db2.close()


def test_import_raises_on_read_only(db, tmp_path):
    export_path = str(tmp_path / 'out.jsonl')
    db.export(export_path)
    path = str(tmp_path / 'db2')
    db_plain = LSMTree(path, sync_writes=False)
    db_plain.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.import_(export_path)
    ro.close()


def test_import_empty_file(tmp_path):
    path = str(tmp_path / 'empty.jsonl')
    open(path, 'w').close()
    db2 = LSMTree(str(tmp_path / 'db2'), sync_writes=False)
    m = db2.import_(path)
    assert m == 0
    db2.close()


# ── clear ─────────────────────────────────────────────────────────────────────

def test_clear_all(db):
    count = db.clear()
    assert count == 100
    assert list(db.scan()) == []


def test_clear_with_prefix(db):
    # 'k000' is a prefix of k0000–k0009 (10 keys)
    count = db.clear('k000')
    assert count == 10, f'Expected 10, got {count}'
    assert db.get('k0000') is None
    assert db.get('k0099') == 'v99'   # unaffected (prefix k009X not matching)


def test_clear_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    assert db.clear() == 0
    db.close()


def test_clear_raises_on_read_only(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('k', 'v')
    db.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.clear()
    ro.close()


def test_clear_persists(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(10):
        db.put(f'k{i}', 'v')
    db.clear()
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert list(db2.scan()) == []
    db2.close()
