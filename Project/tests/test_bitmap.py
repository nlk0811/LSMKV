"""
Tests for Bitmap (db.bitmap()) — persistent space-efficient boolean flags.
"""
import pytest
from lsm_engine import LSMTree, Bitmap


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def bm(db):
    return db.bitmap('flags')


# ── Factory ───────────────────────────────────────────────────────────────────

def test_bitmap_from_db(db):
    assert isinstance(db.bitmap('test'), Bitmap)


def test_bitmap_repr(bm):
    assert 'Bitmap' in repr(bm)


# ── set / get / clear ─────────────────────────────────────────────────────────

def test_set_and_get(bm):
    bm.set(0)
    assert bm.get(0) is True


def test_get_unset_returns_false(bm):
    assert bm.get(999) is False


def test_clear_single_bit(bm):
    bm.set(5)
    bm.clear(5)
    assert bm.get(5) is False


def test_set_false_clears(bm):
    bm.set(10)
    bm.set(10, False)
    assert bm.get(10) is False


def test_toggle(bm):
    assert bm.get(7) is False
    bm.toggle(7)
    assert bm.get(7) is True
    bm.toggle(7)
    assert bm.get(7) is False


def test_item_protocol(bm):
    bm[42] = True
    assert bm[42] is True
    bm[42] = False
    assert bm[42] is False


# ── large bit indices ─────────────────────────────────────────────────────────

def test_large_bit_index(bm):
    bm.set(100_000)
    assert bm.get(100_000) is True
    assert bm.get(99_999) is False


def test_cross_chunk_boundary(bm):
    # _CHUNK_BYTES=512 → 4096 bits per chunk; test around boundary
    bm.set(4095)
    bm.set(4096)
    bm.set(4097)
    assert bm.get(4095)
    assert bm.get(4096)
    assert bm.get(4097)
    assert not bm.get(4094)


# ── count ─────────────────────────────────────────────────────────────────────

def test_count_all(bm):
    bm.set(0); bm.set(10); bm.set(100)
    assert bm.count() == 3


def test_count_range(bm):
    for i in range(10):
        bm.set(i)
    assert bm.count(0, 5) == 5
    assert bm.count(5, 10) == 5


def test_count_zero_initially(bm):
    assert bm.count() == 0


def test_any_set(bm):
    assert not bm.any_set()
    bm.set(3)
    assert bm.any_set()


# ── range operations ──────────────────────────────────────────────────────────

def test_set_range(bm):
    n = bm.set_range(10, 20)
    assert n == 10
    for i in range(10, 20):
        assert bm.get(i)
    assert not bm.get(9)
    assert not bm.get(20)


def test_clear_range(bm):
    bm.set_range(0, 10)
    bm.clear_range(3, 7)
    for i in range(3, 7):
        assert not bm.get(i)
    assert bm.get(2)
    assert bm.get(7)


# ── iter_set_bits ─────────────────────────────────────────────────────────────

def test_iter_set_bits(bm):
    bm.set(3); bm.set(7); bm.set(15)
    bits = list(bm.iter_set_bits(20))
    assert bits == [3, 7, 15]


def test_iter_set_bits_empty(bm):
    assert list(bm.iter_set_bits(100)) == []


# ── clear_all ────────────────────────────────────────────────────────────────

def test_clear_all(bm):
    bm.set_range(0, 100)
    bm.clear_all()
    assert bm.count() == 0
    assert not bm.get(50)


# ── persistence ───────────────────────────────────────────────────────────────

def test_bitmap_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    bm   = db.bitmap('seen')
    bm.set(42)
    bm.set(1000)
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    bm2 = db2.bitmap('seen')
    assert bm2.get(42)
    assert bm2.get(1000)
    assert not bm2.get(41)
    assert bm2.count() == 2
    db2.close()


# ── storage efficiency ────────────────────────────────────────────────────────

def test_compact_storage(db):
    """Setting 1000 bits should use far fewer keys than 1000 individual keys."""
    bm = db.bitmap('dense')
    for i in range(1000):
        bm.set(i)
    # 1000 bits = ceil(1000/4096) = 1 chunk = 1 key
    chunk_count = sum(1 for _ in db.scan_keys('__bm:dense:', '__bm:dense;'))
    assert chunk_count == 1, f'Expected 1 chunk for 1000 bits, got {chunk_count}'
    assert bm.count() == 1000
