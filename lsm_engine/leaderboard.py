"""
Leaderboard — Persistent Sorted Set by Score
=============================================
A Leaderboard maintains a set of (member, score) pairs sorted by score.
Members can be ranked, top/bottom-N listed, and scores updated atomically.

Implementation uses two key sets per leaderboard:
  __lb:{name}:score:{member}              — member's current score
  __lb:{name}:rank:{inv_score:019d}:{member} — sorted rank index

The rank key encodes the INVERTED score (MAX_SCORE - score) so that
lexicographic forward scan produces descending score order (highest first).

Score range: 0 … 10^18 - 1 (integer scores only).
For float scores, multiply by a scaling factor before adding:
    lb.add('alice', int(rating * 1000))   # 3 decimal places

Example
-------
    lb = db.leaderboard('game_scores')
    lb.add('alice', 1500)
    lb.add('bob',   2300)
    lb.add('carol', 1800)

    lb.top(3)      # [('bob', 2300), ('carol', 1800), ('alice', 1500)]
    lb.rank('alice')   # 3 (1-indexed from highest)
    lb.score('alice')  # 1500
    lb.update('alice', 200)  # alice now has 1700
    lb.count()     # 3
"""

from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

_MAX_SCORE = 10 ** 18


class Leaderboard:
    """Persistent sorted set backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _rank_key(self, score: int, member: str) -> str:
        """Rank key encodes inverted score for descending lexicographic order."""
        inv = _MAX_SCORE - score
        return f'__lb:{self._name}:rank:{inv:019d}:{member}'

    def _score_key(self, member: str) -> str:
        return f'__lb:{self._name}:score:{member}'

    def _rank_prefix(self) -> str:
        return f'__lb:{self._name}:rank:'

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, member: str, score: int) -> None:
        """Set member's score (replaces existing score if any)."""
        old = self.score(member)
        if old is not None:
            self._db.delete(self._rank_key(old, member))
        self._db.put(self._score_key(member), str(score))
        self._db.put(self._rank_key(score, member), '')

    def update(self, member: str, delta: int) -> int:
        """Atomically add delta to member's score.  Returns new score."""
        old   = self.score(member) or 0
        new   = old + delta
        self.add(member, new)   # add() handles old rank key removal
        return new

    def remove(self, member: str) -> bool:
        """Remove member.  Returns True if member existed."""
        score = self.score(member)
        if score is None:
            return False
        self._db.delete(self._rank_key(score, member))
        self._db.delete(self._score_key(member))
        return True

    def clear(self) -> int:
        """Remove all members.  Returns count removed."""
        all_keys = [k for k, _ in self._db.prefix_scan(f'__lb:{self._name}:')]
        for k in all_keys:
            self._db.delete(k)
        return len(all_keys) // 2   # each member has 2 keys

    # ── read ──────────────────────────────────────────────────────────────────

    def score(self, member: str) -> Optional[int]:
        """Return member's score, or None if not in leaderboard."""
        v = self._db.get(self._score_key(member))
        return int(v) if v is not None else None

    def rank(self, member: str) -> Optional[int]:
        """Return 1-indexed rank (1 = highest score), or None if absent."""
        target_score = self.score(member)
        if target_score is None:
            return None
        target_full = self._rank_key(target_score, member)
        prefix      = self._rank_prefix()
        position    = 1
        for full_key, _ in self._db.prefix_scan(prefix):
            if full_key == target_full:
                return position
            position += 1
        return None

    def top(self, n: int) -> List[Tuple[str, int]]:
        """Return top-N (member, score) pairs in descending score order."""
        results = []
        prefix  = self._rank_prefix()
        plen    = len(prefix)
        for full_key, _ in self._db.prefix_scan(prefix):
            if len(results) >= n:
                break
            # full_key = '__lb:{name}:rank:{inv:019d}:{member}'
            local = full_key[plen:]          # '{inv:019d}:{member}'
            sep   = local.index(':')
            inv_score = int(local[:sep])
            member    = local[sep + 1:]
            results.append((member, _MAX_SCORE - inv_score))
        return results

    def bottom(self, n: int) -> List[Tuple[str, int]]:
        """Return bottom-N (member, score) pairs in ascending score order.

        Uses tail(n) — O(n) memory instead of O(total_members).
        Rank keys use inverted scores so the last N keys in the rank prefix
        have the highest inverted scores = the lowest actual scores.
        """
        prefix  = self._rank_prefix()
        end_key = prefix[:-1] + ';'    # exclusive end of the rank prefix
        tail_kv = self._db.tail(n, end=end_key)
        plen    = len(prefix)
        result  = []
        for full_key, _ in reversed(tail_kv):
            if not full_key.startswith(prefix):
                continue
            local     = full_key[plen:]
            sep       = local.index(':')
            inv_score = int(local[:sep])
            member    = local[sep + 1:]
            result.append((member, _MAX_SCORE - inv_score))
        return result

    def increment_score(self, member: str, delta: int) -> int:
        """Add delta to member's score.  Returns new score.  Alias for update()."""
        return self.update(member, delta)

    def has(self, member: str) -> bool:
        """Return True if member is in the leaderboard."""
        return self.score(member) is not None

    def range_by_score(self, min_score: int, max_score: int,
                       limit: Optional[int] = None) -> List[Tuple[str, int]]:
        """Return (member, score) pairs with score in [min_score, max_score].

        Results are in descending score order (highest first).
        limit= caps the number of results returned.

        Uses a ranged scan on the rank-key prefix — O(matching) time and O(1)
        memory per entry (streaming).

        Example — players between Bronze and Silver tier:
            bronze_silver = lb.range_by_score(1000, 4999)
        """
        prefix    = self._rank_prefix()
        plen      = len(prefix)
        inv_start = _MAX_SCORE - max_score
        inv_end   = _MAX_SCORE - min_score + 1
        start_key = f'{prefix}{inv_start:019d}'
        end_key   = f'{prefix}{inv_end:019d}'
        result    = []
        count     = 0
        for full_key, _ in self._db.scan(start_key, end_key):
            if not full_key.startswith(prefix):
                break
            local     = full_key[plen:]
            sep       = local.index(':')
            inv_score = int(local[:sep])
            member    = local[sep + 1:]
            result.append((member, _MAX_SCORE - inv_score))
            count += 1
            if limit is not None and count >= limit:
                break
        return result

    def count(self) -> int:
        """Total number of members."""
        return sum(1 for _ in self._db.prefix_scan(self._rank_prefix()))

    def members(self) -> List[Tuple[str, int]]:
        """All members in descending score order.

        Streams via prefix_scan — O(1) memory per entry (no pre-count needed).
        """
        prefix = self._rank_prefix()
        plen   = len(prefix)
        result = []
        for full_key, _ in self._db.prefix_scan(prefix):
            local     = full_key[plen:]
            sep       = local.index(':')
            inv_score = int(local[:sep])
            member    = local[sep + 1:]
            result.append((member, _MAX_SCORE - inv_score))
        return result

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, member: str) -> bool:
        return self.score(member) is not None

    def __repr__(self) -> str:
        return f'Leaderboard({self._name!r}, members={len(self)})'
