"""
Tests for TimeSeries (db.timeseries()) — persistent time-series store.
"""
import time
import pytest

from lsm_engine import LSMTree, TimeSeries


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def ts(db):
    s = db.timeseries('cpu')
    now = time.time()
    for i, val in enumerate([72.5, 85.0, 91.2, 68.0, 77.3]):
        s.add(val, timestamp=now - (5 - i) * 10)  # 10s apart
    return s, now


# ── Factory ───────────────────────────────────────────────────────────────────

def test_timeseries_from_db(db):
    assert isinstance(db.timeseries('test'), TimeSeries)


def test_timeseries_repr(ts):
    s, now = ts
    r = repr(s)
    assert 'TimeSeries' in r and 'cpu' in r


# ── add / count / len ─────────────────────────────────────────────────────────

def test_count_after_add(ts):
    s, now = ts
    assert s.count() == 5
    assert len(s) == 5


def test_add_uses_current_time(db):
    s = db.timeseries('t')
    t_before = time.time()
    s.add(42.0)
    t_after = time.time()
    pts = s.range(t_before - 1, t_after + 1)
    assert len(pts) == 1
    assert pts[0][1] == 42.0


def test_add_many(db):
    s = db.timeseries('t')
    t0 = time.time()
    s.add_many([(v, t0 + i) for i, v in enumerate([1.0, 2.0, 3.0])])
    assert s.count() == 3


def test_add_duplicate_timestamp_increments(db):
    """Same timestamp adds both points (µs bump)."""
    s  = db.timeseries('t')
    t0 = time.time()
    s.add(1.0, timestamp=t0)
    s.add(2.0, timestamp=t0)   # same µs → bumped
    assert s.count() == 2


# ── range ─────────────────────────────────────────────────────────────────────

def test_range_returns_points_in_order(ts):
    s, now = ts
    pts = s.range(now - 60, now + 1)
    timestamps = [t for t, _ in pts]
    assert timestamps == sorted(timestamps)


def test_range_inclusive_both_ends(db):
    s  = db.timeseries('t')
    t0 = time.time()
    s.add(1.0, timestamp=t0)
    s.add(2.0, timestamp=t0 + 5)
    s.add(3.0, timestamp=t0 + 10)
    pts = s.range(t0, t0 + 5)
    assert len(pts) == 2


def test_range_empty_range(ts):
    s, now = ts
    pts = s.range(now + 100, now + 200)
    assert pts == []


# ── latest ────────────────────────────────────────────────────────────────────

def test_latest_returns_most_recent(ts):
    s, now = ts
    latest = s.latest(2)
    assert len(latest) == 2
    assert latest[-1][1] == 77.3   # last added


def test_latest_one(ts):
    s, now = ts
    latest = s.latest(1)
    assert latest[0][1] == 77.3


def test_first(ts):
    s, now = ts
    pt = s.first()
    assert pt is not None
    assert pt[1] == 72.5   # first added


# ── aggregation ───────────────────────────────────────────────────────────────

def test_average(ts):
    s, now = ts
    avg = s.average(now - 60, now + 1)
    expected = sum([72.5, 85.0, 91.2, 68.0, 77.3]) / 5
    assert abs(avg - expected) < 0.01


def test_average_empty_returns_none(ts):
    s, now = ts
    assert s.average(now + 100, now + 200) is None


def test_sum(ts):
    s, now = ts
    total = s.sum(now - 60, now + 1)
    assert abs(total - sum([72.5, 85.0, 91.2, 68.0, 77.3])) < 0.01


def test_minimum_maximum(ts):
    s, now = ts
    assert s.minimum(now - 60, now + 1) == 68.0
    assert s.maximum(now - 60, now + 1) == 91.2


# ── delete / clear ────────────────────────────────────────────────────────────

def test_delete_before(ts):
    s, now = ts
    deleted = s.delete_before(now - 25)
    assert deleted >= 1
    assert s.count() == 5 - deleted


def test_clear(ts):
    s, now = ts
    s.clear()
    assert s.count() == 0


# ── persistence ───────────────────────────────────────────────────────────────

def test_timeseries_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    s    = db.timeseries('temp')
    t0   = time.time()
    s.add(22.5, timestamp=t0)
    s.add(23.1, timestamp=t0 + 1)
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    s2  = db2.timeseries('temp')
    assert s2.count() == 2
    assert s2.average(t0 - 1, t0 + 2) == pytest.approx((22.5 + 23.1) / 2, abs=0.01)
    db2.close()
