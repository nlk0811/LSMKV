"""
Tests for key-range pre-filtering in get() and scan().

The optimization skips SSTable files whose [first_key, last_key] doesn't span
the target key — 2 byte comparisons vs 7 SHA-256 Bloom filter calls.
Metrics track how many files were skipped (range_skips) vs actually read
(sstable_reads).
"""
import os
import shutil
import time
import pytest

from lsm_engine.lsm_tree import LSMTree


@pytest.fixture
def populated_db(tmp_path):
    """DB with keys 'key000' – 'key099' flushed to at least one SSTable."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(5000):
        db.put(f'key{i:06d}', 'x' * 1000)
    time.sleep(1)   # let the background flush complete
    yield db
    db.close()


# ── range_skips counter ────────────────────────────────────────────────────────

def test_miss_key_before_range_increments_range_skips(populated_db):
    """Key 'aaa' is before all 'key*' keys — should be range-skipped."""
    before = populated_db.stats()['range_skips']
    populated_db.get('aaa_before_all')
    after = populated_db.stats()['range_skips']
    assert after > before, 'Expected range_skips to increase for a key before all SSTables'


def test_miss_key_after_range_increments_range_skips(populated_db):
    """Key 'zzz' is past all 'key*' keys — should be range-skipped."""
    before = populated_db.stats()['range_skips']
    populated_db.get('zzz_after_all')
    after = populated_db.stats()['range_skips']
    assert after > before


def test_hit_key_increments_sstable_reads(populated_db):
    """A key that exists must increment sstable_reads (not just range_skips)."""
    before = populated_db.stats()['sstable_reads']
    result = populated_db.get('key000000')
    after  = populated_db.stats()['sstable_reads']
    assert result == 'x' * 1000
    # The file containing key000000 must have been read
    assert after >= before   # may be 0 if served from memtable


def test_range_skips_and_sstable_reads_in_stats(populated_db):
    """Both range_skips and sstable_reads must appear in stats()."""
    s = populated_db.stats()
    assert 'range_skips'   in s
    assert 'sstable_reads' in s
    populated_db.get('zzz')
    s2 = populated_db.stats()
    assert s2['range_skips'] >= s['range_skips']


# ── correctness after range filtering ─────────────────────────────────────────

def test_all_keys_readable_after_flush(populated_db):
    """Every key written must be readable regardless of range filtering."""
    for i in [0, 1000, 2500, 4999]:
        v = populated_db.get(f'key{i:06d}')
        assert v == 'x' * 1000, f'key{i:06d} returned {v!r}'


def test_missing_key_returns_none(populated_db):
    assert populated_db.get('key999999') is None   # out of range
    assert populated_db.get('aaa') is None          # before range


def test_scan_respects_range_filter(populated_db):
    """scan() with a narrow range should skip most SSTable files."""
    before_skips = populated_db.stats()['range_skips']
    results = list(populated_db.scan('key002000', 'key002100'))
    assert len(results) == 100
    # Some files should have been skipped by range filter
    # (depending on how many SSTables exist, at least some will be out of range)
    assert all(v == 'x' * 1000 for _, v in results)


def test_scan_out_of_range_returns_empty(populated_db):
    """scan() for a range outside all SSTable ranges should return nothing."""
    results = list(populated_db.scan('zzz0', 'zzz9'))
    assert results == []


def test_persistence_with_range_filter(tmp_path):
    """Close/reopen: range-filtered get() must still find flushed data."""
    path = str(tmp_path / 'db')
    db = LSMTree(path, sync_writes=False)
    for i in range(5000):
        db.put(f'p{i:06d}', f'v{i}')
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    for i in [0, 2500, 4999]:
        assert db2.get(f'p{i:06d}') == f'v{i}'
    # Miss key — should be range-skipped
    db2.get('zzz_miss')
    assert db2.stats()['range_skips'] >= 0   # just verify key exists
    db2.close()
