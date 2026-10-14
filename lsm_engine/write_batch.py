"""
WriteBatch
==========
Collects multiple put/delete operations and applies them atomically via
LSMTree.write(batch).  Using a batch instead of individual put() calls gives:
  - One WAL fsync for the entire batch (group-commit friendly)
  - One lock acquisition for all memtable inserts
  - Lower per-key overhead at high write rates

Example
-------
    batch = WriteBatch()
    batch.put('user:1', '{"name": "Alice"}')
    batch.put('user:2', '{"name": "Bob"}')
    batch.delete('user:0')
    db.write(batch)
"""
from enum import IntEnum
from typing import Iterator, List, Tuple


class _OpType(IntEnum):
    PUT    = 1
    DELETE = 2


class WriteBatch:
    def __init__(self):
        self._ops: List[Tuple[_OpType, bytes, bytes]] = []

    # ── builder methods ────────────────────────────────────────────────────────

    def put(self, key, value) -> 'WriteBatch':
        """Stage a put. Returns self for chaining."""
        self._ops.append((_OpType.PUT, _to_bytes(key), _to_bytes(value)))
        return self

    def put_ttl(self, key, value, ttl_seconds: float) -> 'WriteBatch':
        """Stage a put with a TTL.  The key expires ttl_seconds from now.

        Equivalent to put() but the value is TTL-encoded so that get() and
        scan() return None/skip the entry once the TTL has elapsed.
        """
        import time
        from .ttl import encode as _ttl_encode
        expires_at = time.time() + ttl_seconds
        encoded    = _ttl_encode(_to_bytes(value), expires_at)
        self._ops.append((_OpType.PUT, _to_bytes(key), encoded))
        return self

    def delete(self, key) -> 'WriteBatch':
        """Stage a delete (tombstone). Returns self for chaining."""
        self._ops.append((_OpType.DELETE, _to_bytes(key), b''))
        return self

    def merge(self, other: 'WriteBatch') -> 'WriteBatch':
        """Append all ops from other into this batch.  Returns self for chaining.

        Useful for composing independent batch builders into one atomic write:
            batch1 = build_user_batch(user_data)
            batch2 = build_order_batch(order_data)
            batch1.merge(batch2)   # one WAL fsync for everything
            db.write(batch1)
        """
        self._ops.extend(other._ops)
        return self

    def copy(self) -> 'WriteBatch':
        """Return a new WriteBatch with the same operations (shallow copy)."""
        new = WriteBatch()
        new._ops = list(self._ops)
        return new

    def clear(self) -> 'WriteBatch':
        """Remove all staged operations."""
        self._ops.clear()
        return self

    # ── inspection ─────────────────────────────────────────────────────────────

    @property
    def put_count(self) -> int:
        """Number of staged put operations (including put_ttl)."""
        return sum(1 for op, _, _ in self._ops if int(op) == _OpType.PUT)

    @property
    def delete_count(self) -> int:
        """Number of staged delete operations."""
        return sum(1 for op, _, _ in self._ops if int(op) == _OpType.DELETE)

    def __len__(self) -> int:
        return len(self._ops)

    def __iter__(self) -> Iterator[Tuple[_OpType, bytes, bytes]]:
        return iter(self._ops)

    def __repr__(self) -> str:
        return (f'WriteBatch({len(self._ops)} ops: '
                f'{self.put_count} puts, {self.delete_count} deletes)')

    @property
    def byte_size(self) -> int:
        """Approximate byte size of all keys + values in the batch."""
        return sum(len(k) + len(v) for _, k, v in self._ops)


def _to_bytes(x) -> bytes:
    return x.encode() if isinstance(x, str) else bytes(x)
