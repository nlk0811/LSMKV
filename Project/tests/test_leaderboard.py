"""
Tests for Leaderboard (db.leaderboard()) — persistent sorted set by score.
"""
import pytest
from lsm_engine import LSMTree, Leaderboard


@pytest.fixture
def db(tmp_path):
    d = LSMTree(str(tmp_path / 'db'), sync_writes=False)
    yield d
    d.close()


@pytest.fixture
def lb(db):
    board = db.leaderboard('game')
    board.add('alice', 1500)
    board.add('bob',   2300)
    board.add('carol', 1800)
    board.add('dave',  900)
    return board


# ── Factory ───────────────────────────────────────────────────────────────────

def test_leaderboard_from_db(db):
    assert isinstance(db.leaderboard('test'), Leaderboard)


def test_leaderboard_repr(lb):
    assert 'Leaderboard' in repr(lb) and 'game' in repr(lb)


# ── score ─────────────────────────────────────────────────────────────────────

def test_score_known_member(lb):
    assert lb.score('alice') == 1500


def test_score_unknown_member(lb):
    assert lb.score('unknown') is None


# ── rank ──────────────────────────────────────────────────────────────────────

def test_rank_highest_is_one(lb):
    assert lb.rank('bob') == 1


def test_rank_ordering(lb):
    assert lb.rank('bob')   == 1
    assert lb.rank('carol') == 2
    assert lb.rank('alice') == 3
    assert lb.rank('dave')  == 4


def test_rank_absent_returns_none(lb):
    assert lb.rank('nonexistent') is None


# ── top ───────────────────────────────────────────────────────────────────────

def test_top_descending_order(lb):
    t = lb.top(3)
    assert t == [('bob', 2300), ('carol', 1800), ('alice', 1500)]


def test_top_all(lb):
    t = lb.top(10)
    assert len(t) == 4
    scores = [s for _, s in t]
    assert scores == sorted(scores, reverse=True)


def test_top_one(lb):
    assert lb.top(1) == [('bob', 2300)]


# ── bottom ────────────────────────────────────────────────────────────────────

def test_bottom_ascending_order(lb):
    b = lb.bottom(2)
    assert b[0][1] < b[1][1]   # ascending by score


def test_bottom_lowest(lb):
    assert lb.bottom(1) == [('dave', 900)]


# ── add / update / remove ─────────────────────────────────────────────────────

def test_add_new_member(lb, db):
    lb.add('eve', 3000)
    assert lb.rank('eve') == 1
    assert lb.score('eve') == 3000


def test_add_replaces_existing(lb):
    lb.add('alice', 5000)   # update by re-adding
    assert lb.score('alice') == 5000
    assert lb.rank('alice') == 1


def test_update_delta(lb):
    new = lb.update('dave', 500)
    assert new == 1400
    assert lb.score('dave') == 1400


def test_remove_existing(lb):
    assert lb.remove('carol') is True
    assert lb.score('carol') is None
    assert lb.count() == 3


def test_remove_absent(lb):
    assert lb.remove('ghost') is False


# ── count / contains ─────────────────────────────────────────────────────────

def test_count(lb):
    assert lb.count() == 4


def test_len(lb):
    assert len(lb) == 4


def test_contains(lb):
    assert 'alice' in lb
    assert 'ghost' not in lb


# ── clear ────────────────────────────────────────────────────────────────────

def test_clear(lb):
    lb.clear()
    assert lb.count() == 0
    assert lb.score('alice') is None


# ── persistence ───────────────────────────────────────────────────────────────

def test_leaderboard_persists(tmp_path):
    path = str(tmp_path / 'db')
    db   = LSMTree(path, sync_writes=False)
    lb   = db.leaderboard('scores')
    lb.add('alice', 100)
    lb.add('bob',   200)
    db.close()

    db2 = LSMTree(path, sync_writes=False)
    lb2 = db2.leaderboard('scores')
    assert lb2.top(1) == [('bob', 200)]
    assert lb2.score('alice') == 100
    db2.close()
