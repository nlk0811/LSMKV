"""
Namespace — Prefix-Scoped Database View
=========================================
A Namespace is a lightweight wrapper that automatically prepends a prefix to
every key operation.  All standard read and write methods work exactly as on
the underlying LSMTree, but keys appear without the prefix from the caller's
perspective.

Use cases:
  • Multi-tenant isolation:  ns = db.namespace('tenant:acme:')
  • Logical partitioning:    users = db.namespace('user:')
  • Testing:                 test_ns = db.namespace('test:')  →  test_ns.clear()

Example
-------
    users = db.namespace('user:')

    users.put('alice', '{"email":"alice@example.com"}')
    users.get('alice')          # '{"email":"alice@example.com"}'
    users.get('missing')        # None

    # Internally stored as 'user:alice', 'user:bob'
    # scan() strips the prefix from yielded keys
    for uid, data in users.scan():
        print(uid, data)        # 'alice', '{...}'

    users.count()               # number of users
    users.clear()               # delete all user:* keys
"""

from typing import Iterator, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree
    from .write_batch import WriteBatch


def _prefix_end(prefix: bytes) -> Optional[bytes]:
    ba = bytearray(prefix)
    for i in range(len(ba) - 1, -1, -1):
        if ba[i] < 0xFF:
            ba[i] += 1
            return bytes(ba[:i + 1])
    return None


class Namespace:
    """A prefix-scoped view of an LSMTree database."""

    def __init__(self, db: 'LSMTree', prefix: str):
        self._db     = db
        # Normalise: ensure exactly one separator character at the end
        self._prefix = prefix if prefix.endswith(':') else (prefix + ':') if prefix else ''

    # ── key helpers ───────────────────────────────────────────────────────────

    def _full(self, key: str) -> str:
        """Convert a namespace-local key to a full db key."""
        return self._prefix + key

    def _local(self, full_key: str) -> str:
        """Strip the namespace prefix from a full db key."""
        return full_key[len(self._prefix):]

    # ── basic operations ──────────────────────────────────────────────────────

    def put(self, key: str, value, ttl_seconds: float = 0) -> None:
        self._db.put(self._full(key), value, ttl_seconds=ttl_seconds)

    def get(self, key: str) -> Optional[str]:
        return self._db.get(self._full(key))

    def delete(self, key: str) -> None:
        self._db.delete(self._full(key))

    def has(self, key: str) -> bool:
        return self._db.has(self._full(key))

    # ── scans (keys appear without prefix) ───────────────────────────────────

    def scan(self, start: str = None, end: str = None,
             limit: Optional[int] = None) -> Iterator[Tuple[str, str]]:
        """Yield (local_key, value) pairs within the namespace.

        limit — stop after yielding this many entries (same semantics as
                LSMTree.scan(limit=)).
        """
        full_start = self._prefix + start if start else self._prefix
        full_end   = self._prefix + end   if end   else None
        plen = len(self._prefix)
        count = 0
        for key, val in self._db.scan(full_start, full_end):
            if not key.startswith(self._prefix):
                break   # left the namespace
            if limit is not None and count >= limit:
                break
            count += 1
            yield key[plen:], val

    def scan_keys(self, start: str = None, end: str = None,
                  limit: Optional[int] = None) -> Iterator[str]:
        for key, _ in self.scan(start, end, limit=limit):
            yield key

    def keys(self, start: str = None, end: str = None,
             limit: Optional[int] = None) -> Iterator[str]:
        return self.scan_keys(start, end, limit=limit)

    def values(self, start: str = None, end: str = None,
               limit: Optional[int] = None) -> Iterator[str]:
        for _, val in self.scan(start, end, limit=limit):
            yield val

    def items(self, start: str = None, end: str = None,
              limit: Optional[int] = None) -> Iterator[Tuple[str, str]]:
        return self.scan(start, end, limit=limit)

    # ── utility ───────────────────────────────────────────────────────────────

    def count(self, start: str = None, end: str = None) -> int:
        return sum(1 for _ in self.scan_keys(start, end))

    def count_prefix(self, prefix: str) -> int:
        """Count keys in this namespace whose local key starts with prefix."""
        return self._db.count_prefix(self._prefix + prefix)

    def is_empty(self, start: str = None, end: str = None) -> bool:
        """Return True if no live keys exist in the given namespace range."""
        return next(self.scan_keys(start, end, limit=1), None) is None

    def first(self, start: str = None, end: str = None) -> Optional[Tuple[str, str]]:
        """Return first (local_key, value) in range, or None."""
        return next(self.scan(start, end, limit=1), None)

    def last(self, start: str = None, end: str = None) -> Optional[Tuple[str, str]]:
        """Return last (local_key, value) in range, or None.  O(n) scan."""
        result = None
        for pair in self.scan(start, end):
            result = pair
        return result

    def clear(self) -> int:
        """Delete all keys in this namespace.  Returns count deleted."""
        return self._db.delete_prefix(self._prefix)

    def export(self, path: str, format: str = 'jsonl') -> int:
        """Export the namespace contents (strips prefix from keys)."""
        import json
        count = 0
        with open(path, 'w') as f:
            for key, val in self.scan():
                f.write(json.dumps({'k': key, 'v': val}) + '\n')
                count += 1
        return count

    # ── bulk read / write / delete ────────────────────────────────────────────

    def mset(self, mapping: dict, ttl_seconds: float = 0) -> None:
        self._db.mset({self._full(k): v for k, v in mapping.items()},
                      ttl_seconds=ttl_seconds)

    def mget(self, keys, *, workers: int = 1):
        full_keys = [self._full(k) for k in keys]
        result = self._db.mget(full_keys, workers=workers)
        return {self._local(fk): v for fk, v in result.items()}

    def get_many(self, keys, *, workers: int = 1):
        return self.mget(keys, workers=workers)

    def delete_many(self, keys) -> int:
        return self._db.delete_many([self._full(k) for k in keys])

    def mdel(self, keys) -> int:
        return self.delete_many(keys)

    def exists_many(self, keys) -> dict:
        full = {self._full(k): k for k in keys}
        result = self._db.exists_many(full.keys())
        return {full[fk]: v for fk, v in result.items()}

    # ── TTL operations ────────────────────────────────────────────────────────

    def expire(self, key: str, ttl_seconds: float) -> bool:
        return self._db.expire(self._full(key), ttl_seconds)

    def expireat(self, key: str, timestamp: float) -> bool:
        return self._db.expireat(self._full(key), timestamp)

    def touch(self, key: str, ttl_seconds: float) -> bool:
        return self._db.touch(self._full(key), ttl_seconds)

    def touch_many(self, keys, ttl_seconds: float) -> int:
        return self._db.touch_many([self._full(k) for k in keys], ttl_seconds)

    def ttl_remaining(self, key: str):
        return self._db.ttl_remaining(self._full(key))

    def key_type(self, key: str) -> str:
        return self._db.key_type(self._full(key))

    # ── atomic / conditional ops ──────────────────────────────────────────────

    def compare_and_swap(self, key: str, expected, new_value) -> bool:
        return self._db.compare_and_swap(self._full(key), expected, new_value)

    def update(self, key: str, fn, ttl_seconds: float = 0):
        return self._db.update(self._full(key), fn, ttl_seconds=ttl_seconds)

    def set_if_absent(self, key: str, value, ttl_seconds: float = 0) -> bool:
        return self._db.set_if_absent(self._full(key), value,
                                      ttl_seconds=ttl_seconds)

    def set_if_present(self, key: str, value, ttl_seconds: float = 0) -> bool:
        return self._db.set_if_present(self._full(key), value,
                                       ttl_seconds=ttl_seconds)

    def getset(self, key: str, new_value):
        return self._db.getset(self._full(key), new_value)

    def getdel(self, key: str):
        return self._db.getdel(self._full(key))

    def copy(self, src_key: str, dst_key: str, ttl_seconds: float = 0) -> bool:
        return self._db.copy(self._full(src_key), self._full(dst_key),
                             ttl_seconds=ttl_seconds)

    def swap(self, key1: str, key2: str) -> bool:
        return self._db.swap(self._full(key1), self._full(key2))

    def rename(self, old_key: str, new_key: str) -> bool:
        return self._db.rename(self._full(old_key), self._full(new_key))

    def rename_many(self, mapping: dict) -> int:
        return self._db.rename_many(
            {self._full(src): self._full(dst) for src, dst in mapping.items()})

    # ── numeric ops ───────────────────────────────────────────────────────────

    def increment_float(self, key: str, amount: float = 1.0) -> float:
        return self._db.increment_float(self._full(key), amount)

    def atomic_max(self, key: str, value: int) -> int:
        return self._db.atomic_max(self._full(key), value)

    def atomic_min(self, key: str, value: int) -> int:
        return self._db.atomic_min(self._full(key), value)

    def atomic_max_float(self, key: str, value: float) -> float:
        return self._db.atomic_max_float(self._full(key), value)

    def atomic_min_float(self, key: str, value: float) -> float:
        return self._db.atomic_min_float(self._full(key), value)

    # ── range / prefix delete ─────────────────────────────────────────────────

    def prefix_delete(self, prefix: str = '', batch_size: int = 500) -> int:
        """Delete all keys in this namespace (optionally matching sub-prefix)."""
        return self._db.prefix_delete(self._prefix + prefix,
                                      batch_size=batch_size)

    def delete_prefix(self, prefix: str = '', batch_size: int = 500) -> int:
        return self.prefix_delete(prefix, batch_size=batch_size)

    def range_delete(self, start: str = None, end: str = None,
                     batch_size: int = 500) -> int:
        fs = self._prefix + start if start else self._prefix
        fe = self._prefix + end   if end   else None
        return self._db.range_delete(fs, fe, batch_size=batch_size)

    # ── bulk transform / search ───────────────────────────────────────────────

    def map_values(self, fn, start: str = None, end: str = None,
                   batch_size: int = 500) -> int:
        fs = self._prefix + start if start else self._prefix
        fe = self._prefix + end   if end   else None
        plen = len(self._prefix)
        return self._db.map_values(
            lambda k, v: fn(k[plen:], v), fs, fe, batch_size=batch_size)

    def scan_filter(self, fn, start: str = None, end: str = None,
                    limit=None):
        fs = self._prefix + start if start else self._prefix
        fe = self._prefix + end   if end   else None
        plen = len(self._prefix)
        for key, val in self._db.scan_filter(
                lambda k, v: fn(k[plen:], v), fs, fe, limit=limit):
            if not key.startswith(self._prefix):
                break
            yield key[plen:], val

    def grep(self, pattern, start: str = None, end: str = None,
             limit=None):
        import re as _re
        if isinstance(pattern, str):
            pattern = _re.compile(pattern)
        fs = self._prefix + start if start else self._prefix
        fe = self._prefix + end   if end   else None
        plen = len(self._prefix)
        count = 0
        for key in self._db.scan_keys(fs, fe):
            if not key.startswith(self._prefix):
                break
            local = key[plen:]
            if pattern.search(local):
                if limit is not None and count >= limit:
                    break
                count += 1
                yield local

    # ── pagination / tail ─────────────────────────────────────────────────────

    def tail(self, n: int, end: str = None):
        fe = self._prefix + end if end else None
        plen = len(self._prefix)
        raw = self._db.tail(n, end=fe)
        return [(k[plen:], v) for k, v in raw
                if k.startswith(self._prefix)]

    # ── counter convenience ───────────────────────────────────────────────────

    def counter(self, key: str):
        """Return a Counter for a namespaced key."""
        return self._db.counter(self._full(key))

    def increment(self, key: str, amount: int = 1) -> int:
        return self._db.increment(self._full(key), amount)

    # ── inspection ────────────────────────────────────────────────────────────

    @property
    def prefix(self) -> str:
        return self._prefix

    def __repr__(self) -> str:
        return f'Namespace({self._prefix!r})'
