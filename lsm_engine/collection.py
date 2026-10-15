"""
Collection — Persistent Unordered Set of Unique Members
=========================================================
A persistent set backed by the database.  Members are stored as individual
keys; membership tests and insertions are O(log n); iteration is O(n).

Key format:
    __col:{name}:member:{value}  →  ""

Supports standard set operations (union, intersection, difference) which
return Python frozenset objects for zero-overhead in-memory composition.
Use add_from() to persist the result.

Example
-------
    fruits  = db.collection('fruits')
    veggies = db.collection('veggies')

    fruits.add('apple', 'banana', 'cherry')
    veggies.add('carrot', 'apple')    # apple in both

    'apple' in fruits         # True
    fruits.count()            # 3

    fruits.union(veggies)         # frozenset({'apple','banana','cherry','carrot'})
    fruits.intersection(veggies)  # frozenset({'apple'})
    fruits.difference(veggies)    # frozenset({'banana','cherry'})

    # Persist a result
    combined = db.collection('all_produce')
    combined.add_from(fruits.union(veggies))
"""

from typing import FrozenSet, Iterable, Iterator, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree


class Collection:
    """Persistent set of unique string members."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _key(self, member: str) -> str:
        return f'__col:{self._name}:member:{member}'

    def _prefix(self) -> str:
        return f'__col:{self._name}:member:'

    def _member_from_key(self, full_key: str) -> str:
        return full_key[len(self._prefix()):]

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, *members: str) -> int:
        """Add one or more members.  Returns count actually added (new ones)."""
        added = 0
        for m in members:
            if not self._db.has(self._key(m)):
                self._db.put(self._key(m), '')
                added += 1
        return added

    def add_from(self, iterable: Iterable[str]) -> int:
        """Add all members from an iterable.  Returns count added."""
        return self.add(*iterable)

    def remove(self, *members: str) -> int:
        """Remove one or more members.  Returns count actually removed."""
        removed = 0
        for m in members:
            if self._db.has(self._key(m)):
                self._db.delete(self._key(m))
                removed += 1
        return removed

    def discard(self, member: str) -> None:
        """Remove member if present; no-op if absent."""
        self._db.delete(self._key(member))

    def pop(self) -> Optional[str]:
        """Remove and return an arbitrary member, or None if empty."""
        k = self._db.first_key(self._prefix(), self._prefix()[:-1] + ';')
        if k is None:
            return None
        member = self._member_from_key(k)
        self._db.delete(k)
        return member

    def clear(self) -> int:
        """Remove all members.  Returns count removed.

        Uses prefix_delete() — O(batch_size) memory and ceil(N/500) WAL fsyncs
        instead of N individual WAL records.
        """
        return self._db.prefix_delete(self._prefix())

    # ── read ──────────────────────────────────────────────────────────────────

    def contains(self, member: str) -> bool:
        return self._db.has(self._key(member))

    def members(self) -> FrozenSet[str]:
        """Return all members as a frozenset."""
        plen = len(self._prefix())
        return frozenset(
            k[plen:]
            for k in self._db.scan_keys(self._prefix(), self._prefix()[:-1] + ';')
        )

    def count(self) -> int:
        return sum(1 for _ in self._db.scan_keys(
            self._prefix(), self._prefix()[:-1] + ';'))

    def __iter__(self) -> Iterator[str]:
        plen = len(self._prefix())
        for k in self._db.scan_keys(self._prefix(), self._prefix()[:-1] + ';'):
            yield k[plen:]

    # ── set operations ────────────────────────────────────────────────────────

    def union(self, other: 'Collection') -> FrozenSet[str]:
        """Return members in self OR other.  O(|self| + |other|) memory."""
        return self.members() | other.members()

    def intersection(self, other: 'Collection') -> FrozenSet[str]:
        """Return members in both self AND other.

        Streams self, checks other.contains() per entry via Bloom filter.
        O(|self|) time, O(|intersection|) memory — much cheaper than loading
        both full sets when the result is small.
        """
        return frozenset(m for m in self if other.contains(m))

    def difference(self, other: 'Collection') -> FrozenSet[str]:
        """Return members in self but NOT in other.

        Streams self, excludes members present in other.
        O(|self|) time, O(|difference|) memory.
        """
        return frozenset(m for m in self if not other.contains(m))

    def symmetric_difference(self, other: 'Collection') -> FrozenSet[str]:
        """Return members in exactly one of self/other."""
        return self.members() ^ other.members()

    def is_subset(self, other: 'Collection') -> bool:
        """Return True if every member of self is also in other.

        Streams self; stops at the first member absent from other.
        O(|self|) worst case but early-exits on first mismatch.
        """
        return all(other.contains(m) for m in self)

    def is_superset(self, other: 'Collection') -> bool:
        """Return True if every member of other is also in self."""
        return other.is_subset(self)

    def is_disjoint(self, other: 'Collection') -> bool:
        """Return True if self and other share no members.

        Streams self; stops at the first shared member (O(1) early exit).
        """
        return not any(other.contains(m) for m in self)

    # ── Python protocols ──────────────────────────────────────────────────────

    def __contains__(self, member: str) -> bool:
        return self.contains(member)

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return f'Collection({self._name!r}, members={len(self)})'
