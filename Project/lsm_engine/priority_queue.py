"""
PriorityQueue — Persistent Min/Max Priority Queue
==================================================
Items are stored with an integer priority.  pop() always returns the
highest-priority item.  Items with equal priority are returned in FIFO
order (earliest pushed first).

Key format:
    __pq:{name}:item:{inv_priority:019d}:{seq:015d}  →  item_value
    __pq:{name}:seq                                  →  sequence counter

The inverted priority (MAX_PRIORITY - priority) gives highest priority
first in lexicographic forward scan.  The sequence counter within the same
priority gives FIFO ordering.

Example
-------
    tasks = db.priority_queue('jobs')
    tasks.push('low_task',    priority=1)
    tasks.push('urgent_task', priority=10)
    tasks.push('normal_task', priority=5)

    tasks.pop()   # → ('urgent_task', 10)
    tasks.pop()   # → ('normal_task', 5)
    tasks.pop()   # → ('low_task', 1)

    # FIFO tiebreaking for equal priority
    tasks.push('first_high',  priority=5)
    tasks.push('second_high', priority=5)
    tasks.pop()   # → ('first_high', 5)
    tasks.pop()   # → ('second_high', 5)
"""

from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

_MAX_PRI = 10 ** 18   # priority range: 0 … 10^18 - 1


class PriorityQueue:
    """Persistent priority queue backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db      = db
        self._name    = name
        self._seq_key = f'__pq:{name}:seq'

    # ── key helpers ───────────────────────────────────────────────────────────

    def _item_prefix(self) -> str:
        return f'__pq:{self._name}:item:'

    def _item_key(self, priority: int, seq: int) -> str:
        inv = _MAX_PRI - priority
        return f'__pq:{self._name}:item:{inv:019d}:{seq:015d}'

    def _parse_key(self, full_key: str) -> Tuple[int, int]:
        """Return (priority, seq) from a full item key."""
        local   = full_key[len(self._item_prefix()):]
        inv_pri = int(local[:19])
        seq     = int(local[20:])
        return _MAX_PRI - inv_pri, seq

    # ── push ─────────────────────────────────────────────────────────────────

    def push(self, item: str, priority: int = 0) -> int:
        """Push item with given priority.  Returns its sequence number.

        Higher priority = popped first.
        Equal priority = FIFO (earlier push = popped first).
        """
        seq = self._db.increment(self._seq_key)
        self._db.put(self._item_key(priority, seq), item)
        return seq

    def push_many(self, items_with_priorities) -> List[int]:
        """Push multiple (item, priority) tuples.  Returns list of seq numbers."""
        return [self.push(item, pri) for item, pri in items_with_priorities]

    # ── pop / peek ────────────────────────────────────────────────────────────

    def pop(self) -> Tuple[Optional[str], Optional[int]]:
        """Remove and return (item, priority) or (None, None) if empty."""
        prefix = self._item_prefix()
        key    = self._db.first_key(prefix, prefix[:-1] + ';')
        if key is None:
            return None, None
        item     = self._db.get(key)
        priority, _ = self._parse_key(key)
        self._db.delete(key)
        return item, priority

    def peek(self) -> Tuple[Optional[str], Optional[int]]:
        """Return (item, priority) without removing, or (None, None) if empty."""
        prefix = self._item_prefix()
        key    = self._db.first_key(prefix, prefix[:-1] + ';')
        if key is None:
            return None, None
        item     = self._db.get(key)
        priority, _ = self._parse_key(key)
        return item, priority

    # ── inspection ────────────────────────────────────────────────────────────

    def count(self) -> int:
        prefix = self._item_prefix()
        return sum(1 for _ in self._db.scan_keys(prefix, prefix[:-1] + ';'))

    def drain(self) -> List[Tuple[str, int]]:
        """Pop and return all items in priority order."""
        items = []
        while True:
            item, pri = self.pop()
            if item is None:
                break
            items.append((item, pri))
        return items

    def clear(self) -> int:
        """Remove all items.  Returns count removed."""
        prefix = self._item_prefix()
        keys   = list(self._db.scan_keys(prefix, prefix[:-1] + ';'))
        for k in keys:
            self._db.delete(k)
        self._db.delete(self._seq_key)
        return len(keys)

    def __len__(self) -> int:
        return self.count()

    def __bool__(self) -> bool:
        return len(self) > 0

    def __repr__(self) -> str:
        return f'PriorityQueue({self._name!r}, items={len(self)})'
