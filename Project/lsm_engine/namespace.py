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

    def scan(self, start: str = None, end: str = None
             ) -> Iterator[Tuple[str, str]]:
        """Yield (local_key, value) pairs within the namespace."""
        full_start = self._prefix + start if start else self._prefix
        full_end   = self._prefix + end   if end   else None
        plen = len(self._prefix)
        for key, val in self._db.scan(full_start, full_end):
            if not key.startswith(self._prefix):
                break   # left the namespace
            yield key[plen:], val

    def scan_keys(self, start: str = None, end: str = None) -> Iterator[str]:
        for key, _ in self.scan(start, end):
            yield key

    def keys(self, start: str = None, end: str = None) -> Iterator[str]:
        return self.scan_keys(start, end)

    def values(self, start: str = None, end: str = None) -> Iterator[str]:
        for _, val in self.scan(start, end):
            yield val

    def items(self, start: str = None, end: str = None
              ) -> Iterator[Tuple[str, str]]:
        return self.scan(start, end)

    # ── utility ───────────────────────────────────────────────────────────────

    def count(self, start: str = None, end: str = None) -> int:
        return sum(1 for _ in self.scan_keys(start, end))

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
