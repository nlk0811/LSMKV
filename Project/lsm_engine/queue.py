"""
Queue — Persistent FIFO Queue
===============================
A persistent FIFO queue backed by the database.  Items survive process
restarts and are stored durably with CRC-checked WAL records.

Design
------
  head key  : __q:{name}:head  — last consumed sequence number (int)
  tail key  : __q:{name}:tail  — last produced sequence number (int)
  item keys : __q:{name}:item:{seq:015d}  — item data

push() atomically increments the tail to get a unique sequence number, then
stores the item.  Because the two operations are not in a single lock, there
is a brief window where tail > stored_items.  pop() handles this by retrying
if the next item key is not yet present.

pop() uses db.update() which holds the write lock for the entire bump+read,
making it fully atomic.

Concurrency:
  - Multiple concurrent pushers: correct (each gets a unique seq).
  - Multiple concurrent poppers: correct (head bump is atomic).
  - Mixed push/pop: correct.

Example
-------
    q = db.queue('jobs')
    q.push('task_1')
    q.push('task_2')
    while item := q.pop():
        process(item)
"""

import time
from typing import Iterator, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

_POP_RETRIES = 50   # retries if item key not yet stored (push in progress)
_POP_SLEEP   = 0.001


class Queue:
    """Persistent FIFO queue backed by a named set of database keys."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db       = db
        self._name     = name
        self._head_key = f'__q:{name}:head'
        self._tail_key = f'__q:{name}:tail'

    def _item_key(self, seq: int) -> str:
        return f'__q:{self._name}:item:{seq:015d}'

    # ── push ─────────────────────────────────────────────────────────────────

    def push(self, item: str) -> int:
        """Push item to the back.  Returns the new queue length.

        Uses atomic increment for the sequence number, so concurrent pushers
        each get a distinct sequence without transaction conflicts.
        """
        seq = self._db.increment(self._tail_key)
        self._db.put(self._item_key(seq), item)
        return len(self)

    def push_many(self, items) -> int:
        """Push multiple items.  Returns the new queue length."""
        for item in items:
            self.push(item)
        return len(self)

    # ── pop / peek ────────────────────────────────────────────────────────────

    def pop(self) -> Optional[str]:
        """Pop and return the front item, or None if the queue is empty.

        Uses db.update() to atomically bump the head pointer.
        Retries if a concurrent push has incremented tail but not yet stored
        the item (brief race window).
        """
        for _ in range(_POP_RETRIES):
            popped = [None]
            is_empty = [False]

            def _bump(head_str):
                head = int(head_str or '0')
                tail = int(self._db.get(self._tail_key) or '0')
                if head >= tail:
                    is_empty[0] = True
                    return head_str or '0'   # empty, no change
                next_seq = head + 1
                item = self._db.get(self._item_key(next_seq))
                if item is None:
                    return head_str or '0'   # push in progress, back off
                popped[0] = (item, next_seq)
                return str(next_seq)         # atomic head bump

            self._db.update(self._head_key, _bump)

            if is_empty[0]:
                return None   # truly empty
            if popped[0] is not None:
                item, seq = popped[0]
                self._db.delete(self._item_key(seq))   # cleanup
                return item
            # Item not yet written — push in progress; brief wait then retry
            time.sleep(_POP_SLEEP)

        return None   # gave up waiting (shouldn't happen in practice)

    def peek(self) -> Optional[str]:
        """Return the front item without removing it, or None if empty."""
        head = int(self._db.get(self._head_key) or '0')
        tail = int(self._db.get(self._tail_key) or '0')
        if head >= tail:
            return None
        return self._db.get(self._item_key(head + 1))

    # ── drain / clear ─────────────────────────────────────────────────────────

    def drain(self) -> List[str]:
        """Pop and return all current items in order."""
        items = []
        while True:
            item = self.pop()
            if item is None:
                break
            items.append(item)
        return items

    def clear(self):
        """Remove all items and reset the queue."""
        head = int(self._db.get(self._head_key) or '0')
        tail = int(self._db.get(self._tail_key) or '0')
        pipe = self._db.pipeline()
        for seq in range(head + 1, tail + 1):
            pipe.delete(self._item_key(seq))
        pipe.delete(self._head_key)
        pipe.delete(self._tail_key)
        pipe.execute()

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        head = int(self._db.get(self._head_key) or '0')
        tail = int(self._db.get(self._tail_key) or '0')
        return max(0, tail - head)

    def __bool__(self) -> bool:
        return len(self) > 0

    def __iter__(self) -> Iterator[str]:
        """Drain the queue (destructive)."""
        return iter(self.drain())

    def __repr__(self) -> str:
        return f'Queue({self._name!r}, len={len(self)})'
