"""
Tests for increment_float(), atomic_max_float(), atomic_min_float().
"""
import pytest
from lsm_engine import LSMTree
from lsm_engine.lsm_tree import ReadOnlyError

EPS = 1e-10


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


# ── increment_float ───────────────────────────────────────────────────────────

def test_increment_float_basic(db):
    v = db.increment_float('k', 1.5)
    assert abs(v - 1.5) < EPS


def test_increment_float_absent_initialises_zero(db):
    v = db.increment_float('k')   # default amount=1.0
    assert v == 1.0


def test_increment_float_accumulates(db):
    db.increment_float('k', 1.5)
    v = db.increment_float('k', 2.3)
    assert abs(v - 3.8) < EPS


def test_increment_float_negative(db):
    db.increment_float('bal', 10.0)
    v = db.increment_float('bal', -3.5)
    assert abs(v - 6.5) < EPS


def test_increment_float_returns_float(db):
    assert isinstance(db.increment_float('k', 1.0), float)


def test_increment_float_with_integer_stored(db):
    db.put('k', '5')    # integer stored as string
    v = db.increment_float('k', 2.5)
    assert abs(v - 7.5) < EPS


def test_increment_float_default_amount(db):
    v = db.increment_float('k')
    assert v == 1.0


def test_increment_float_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', '1.0')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.increment_float('k', 1.0)
    ro.close()


def test_increment_float_persists(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.increment_float('rate', 3.14)
    d.close()
    d2 = LSMTree(path, sync_writes=False)
    assert abs(d2.increment_float('rate', 0.0) - 3.14) < EPS
    d2.close()


# ── atomic_max_float ──────────────────────────────────────────────────────────

def test_atomic_max_float_update(db):
    db.put('peak', '5.5')
    v = db.atomic_max_float('peak', 7.2)
    assert abs(v - 7.2) < EPS
    assert abs(float(db.get('peak')) - 7.2) < EPS


def test_atomic_max_float_no_change(db):
    db.put('peak', '5.5')
    v = db.atomic_max_float('peak', 3.0)
    assert abs(v - 5.5) < EPS


def test_atomic_max_float_absent(db):
    v = db.atomic_max_float('peak', 4.5)
    assert abs(v - 4.5) < EPS


def test_atomic_max_float_returns_float(db):
    assert isinstance(db.atomic_max_float('k', 1.0), float)


def test_atomic_max_float_equal_value(db):
    db.put('k', '3.0')
    v = db.atomic_max_float('k', 3.0)
    assert abs(v - 3.0) < EPS


def test_atomic_max_float_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', '1.0')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.atomic_max_float('k', 2.0)
    ro.close()


# ── atomic_min_float ──────────────────────────────────────────────────────────

def test_atomic_min_float_update(db):
    db.put('floor', '5.0')
    v = db.atomic_min_float('floor', 2.5)
    assert abs(v - 2.5) < EPS


def test_atomic_min_float_no_change(db):
    db.put('floor', '2.0')
    v = db.atomic_min_float('floor', 5.0)
    assert abs(v - 2.0) < EPS


def test_atomic_min_float_absent(db):
    v = db.atomic_min_float('floor', 9.9)
    assert abs(v - 9.9) < EPS


def test_atomic_min_float_returns_float(db):
    assert isinstance(db.atomic_min_float('k', 1.0), float)


def test_atomic_min_float_raises_read_only(tmp_path):
    path = str(tmp_path / 'db')
    d = LSMTree(path, sync_writes=False)
    d.put('k', '5.0')
    d.close()
    ro = LSMTree(path, read_only=True)
    with pytest.raises(ReadOnlyError):
        ro.atomic_min_float('k', 3.0)
    ro.close()
