"""
Tests for read-only mode (read_only=True) and Cursor.seek_to_last().
"""
import os
import shutil
import pytest

from lsm_engine import LSMTree, ReadOnlyError, WriteBatch


@pytest.fixture
def populated(tmp_path):
    """DB with 100 flushed keys."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(100):
        db.put(f'k{i:04d}', f'v{i}')
    db.flush()
    db.close()
    return path


# ── Read-only reads work ───────────────────────────────────────────────────────

def test_ro_get(populated):
    db = LSMTree(populated, read_only=True)
    assert db.get('k0000') == 'v0'
    assert db.get('k0099') == 'v99'
    assert db.get('missing') is None
    db.close()


def test_ro_scan(populated):
    db = LSMTree(populated, read_only=True)
    results = list(db.scan('k0010', 'k0020'))
    assert len(results) == 10
    assert results[0] == ('k0010', 'v10')
    db.close()


def test_ro_prefix_scan(populated):
    db = LSMTree(populated, read_only=True)
    keys = [k for k, _ in db.prefix_scan('k001')]
    assert len(keys) == 10
    db.close()


def test_ro_scan_keys(populated):
    db = LSMTree(populated, read_only=True)
    keys = list(db.scan_keys('k0000', 'k0010'))
    assert len(keys) == 10
    db.close()


def test_ro_cursor(populated):
    db = LSMTree(populated, read_only=True)
    cur = db.cursor().seek('k0050')
    assert cur.valid() and cur.key() == 'k0050'
    db.close()


def test_ro_get_many(populated):
    db = LSMTree(populated, read_only=True)
    result = db.get_many(['k0000', 'k0099', 'nope'])
    assert result['k0000'] == 'v0'
    assert result['k0099'] == 'v99'
    assert result['nope'] is None
    db.close()


def test_ro_stats(populated):
    db = LSMTree(populated, read_only=True)
    s = db.stats()
    assert 'levels' in s
    db.close()


def test_ro_info_flag(populated):
    db = LSMTree(populated, read_only=True)
    assert db.info()['read_only'] is True
    db.close()


# ── Writes raise ReadOnlyError ─────────────────────────────────────────────────

def test_ro_put_raises(populated):
    db = LSMTree(populated, read_only=True)
    with pytest.raises(ReadOnlyError):
        db.put('new', 'val')
    db.close()


def test_ro_delete_raises(populated):
    db = LSMTree(populated, read_only=True)
    with pytest.raises(ReadOnlyError):
        db.delete('k0000')
    db.close()


def test_ro_write_raises(populated):
    db = LSMTree(populated, read_only=True)
    batch = WriteBatch().put('x', 'y')
    with pytest.raises(ReadOnlyError):
        db.write(batch)
    db.close()


def test_ro_flush_raises(populated):
    db = LSMTree(populated, read_only=True)
    with pytest.raises(ReadOnlyError):
        db.flush()
    db.close()


def test_ro_update_raises(populated):
    db = LSMTree(populated, read_only=True)
    with pytest.raises(ReadOnlyError):
        db.update('k0000', lambda v: v.upper())
    db.close()


def test_ro_compact_all_raises(populated):
    db = LSMTree(populated, read_only=True)
    with pytest.raises(ReadOnlyError):
        db.compact_all()
    db.close()


def test_ro_data_unchanged_after_reads(populated):
    """Read-only mode must not modify any files in the database directory."""
    import os
    before = {f: os.path.getmtime(os.path.join(populated, f))
              for f in os.listdir(populated)}
    db = LSMTree(populated, read_only=True)
    for i in range(100):
        db.get(f'k{i:04d}')
    list(db.scan())
    db.close()
    after = {f: os.path.getmtime(os.path.join(populated, f))
             for f in os.listdir(populated)}
    assert before == after, 'Read-only mode modified files'


# ── Cursor.seek_to_last() ──────────────────────────────────────────────────────

def test_seek_to_last_basic(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(20):
        db.put(f'key{i:04d}', f'val{i}')
    cur = db.cursor().seek_to_last()
    assert cur.valid()
    assert cur.key() == 'key0019'
    assert cur.value() == 'val19'
    db.close()


def test_seek_to_last_empty_db(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    cur = db.cursor().seek_to_last()
    assert not cur.valid()
    db.close()


def test_seek_to_last_next_is_invalid(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    for i in range(5):
        db.put(f'k{i}', 'v')
    cur = db.cursor().seek_to_last()
    assert cur.valid()
    cur.next()
    assert not cur.valid()
    db.close()


def test_seek_to_last_with_flushed_data(tmp_path):
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(5000):
        db.put(f'row{i:06d}', 'v')
    db.flush()
    cur = db.cursor().seek_to_last()
    assert cur.valid()
    assert cur.key() == 'row004999'
    db.close()
