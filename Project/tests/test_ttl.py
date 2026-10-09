"""
Tests for TTL (Time-To-Live) support.

Keys with a TTL return None from get() once expired, are skipped by scan(),
and are physically removed during deep compaction at the final level.
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree
from lsm_engine.write_batch import WriteBatch
from lsm_engine.ttl import encode, decode, is_expired


# ── ttl.py unit tests ──────────────────────────────────────────────────────────

def test_encode_decode_roundtrip():
    value  = b'hello world'
    expiry = time.time() + 100
    encoded = encode(value, expiry)
    actual, exp_out = decode(encoded)
    assert actual == value
    assert abs(exp_out - expiry) < 0.001


def test_decode_non_ttl_value():
    data = b'plain value'
    actual, exp = decode(data)
    assert actual == data
    assert exp is None


def test_is_expired_future():
    assert not is_expired(time.time() + 100)


def test_is_expired_past():
    assert is_expired(time.time() - 1)


def test_is_expired_none():
    assert not is_expired(None)


# ── LSMTree.put() with TTL ─────────────────────────────────────────────────────

def test_ttl_key_readable_before_expiry(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('k', 'v', ttl_seconds=10)
    assert db.get('k') == 'v'
    db.close()


def test_ttl_key_none_after_expiry(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('k', 'v', ttl_seconds=0.05)
    assert db.get('k') == 'v'
    time.sleep(0.1)
    assert db.get('k') is None
    db.close()


def test_no_ttl_key_unaffected(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('permanent', 'data')
    db.put('temporary', 'bye', ttl_seconds=0.05)
    time.sleep(0.1)
    assert db.get('permanent') == 'data'
    assert db.get('temporary') is None
    db.close()


def test_overwrite_with_ttl(tmp_path):
    """Overwriting an unexpired TTL key with a regular put clears the TTL."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('k', 'ttl_val', ttl_seconds=0.05)
    db.put('k', 'no_ttl')   # overwrite without TTL
    time.sleep(0.1)
    # The second put wins, no TTL — key should still be readable
    assert db.get('k') == 'no_ttl'
    db.close()


def test_ttl_key_in_sstable(tmp_path):
    """TTL check works for keys flushed to an SSTable."""
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    # Write enough to trigger a flush (~5 MB)
    for i in range(5000):
        db.put(f'bg{i:06d}', 'x' * 1000)
    db.put('ttl_key', 'expires', ttl_seconds=0.05)
    time.sleep(0.5)   # flush + TTL expiry
    assert db.get('ttl_key') is None
    # Regular keys still readable
    assert db.get('bg000000') == 'x' * 1000
    db.close()


def test_ttl_persist_unexpired(tmp_path):
    """Unexpired TTL key survives close/reopen."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    db.put('persistent_ttl', 'value', ttl_seconds=3600)   # 1 hour
    db.close()
    db2 = LSMTree(path, sync_writes=False)
    assert db2.get('persistent_ttl') == 'value'
    db2.close()


# ── scan() and prefix_scan() with TTL ─────────────────────────────────────────

def test_scan_skips_expired(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('sk:a', 'keep')
    db.put('sk:b', 'expire', ttl_seconds=0.05)
    db.put('sk:c', 'keep')
    time.sleep(0.1)
    keys = [k for k, _ in db.scan('sk:', 'sk;')]
    assert 'sk:a' in keys and 'sk:c' in keys
    assert 'sk:b' not in keys
    db.close()


def test_prefix_scan_skips_expired(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    db.put('ns:alice', 'ok')
    db.put('ns:bob',   'gone', ttl_seconds=0.05)
    time.sleep(0.1)
    result = dict(db.prefix_scan('ns:'))
    assert 'ns:alice' in result
    assert 'ns:bob' not in result
    db.close()


# ── WriteBatch TTL ─────────────────────────────────────────────────────────────

def test_write_batch_put_ttl(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    batch = WriteBatch()
    batch.put('permanent', 'yes')
    batch.put_ttl('temp', 'no', ttl_seconds=0.05)
    db.write(batch)
    time.sleep(0.1)
    assert db.get('permanent') == 'yes'
    assert db.get('temp') is None
    db.close()


def test_write_batch_put_ttl_chaining(tmp_path):
    db = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    batch = WriteBatch().put('a', '1').put_ttl('b', '2', ttl_seconds=10).put('c', '3')
    db.write(batch)
    assert db.get('a') == '1'
    assert db.get('b') == '2'   # still valid
    assert db.get('c') == '3'
    db.close()
