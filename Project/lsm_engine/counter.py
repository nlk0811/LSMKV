"""
Counter — Persistent Integer Counter
=====================================
A Counter wraps a single integer key with convenient incr/decr/reset methods.
It is backed by the database (durable, survives restarts) and thread-safe.

Example
-------
    visits = db.counter('page:home:visits')
    visits.incr()           # +1
    visits.incr(5)          # +5
    visits.decr()           # -1
    print(int(visits))      # current value
    visits.reset()          # set back to 0

    # Context manager (closes nothing — just syntactic sugar)
    with db.counter('signups') as c:
        c.incr()
        print(c)            # Counter('signups'=1)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree


class Counter:
    """Persistent integer counter backed by a single database key."""

    def __init__(self, db: 'LSMTree', key: str):
        self._db  = db
        self._key = key

    # ── read ─────────────────────────────────────────────────────────────────

    @property
    def value(self) -> int:
        """Current value (0 if key is absent)."""
        return int(self._db.get(self._key) or '0')

    # ── write ─────────────────────────────────────────────────────────────────

    def incr(self, amount: int = 1) -> int:
        """Increment by amount (default 1).  Returns new value."""
        return self._db.increment(self._key, amount)

    def decr(self, amount: int = 1) -> int:
        """Decrement by amount (default 1).  Returns new value."""
        return self._db.increment(self._key, -amount)

    def reset(self, value: int = 0) -> 'Counter':
        """Set the counter to value (default 0).  Returns self for chaining."""
        self._db.put(self._key, str(value))
        return self

    def set(self, value: int) -> 'Counter':
        """Alias for reset(value)."""
        return self.reset(value)

    # ── Python protocols ──────────────────────────────────────────────────────

    def __int__(self) -> int:
        return self.value

    def __repr__(self) -> str:
        return f'Counter({self._key!r}={self.value})'

    def __enter__(self) -> 'Counter':
        return self

    def __exit__(self, *_) -> bool:
        return False
