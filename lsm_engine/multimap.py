"""
MultiMap — Persistent One-to-Many Key-Value Map
================================================
A MultiMap stores multiple values per key.  Each (key, value) pair is stored
as a separate database entry, enabling O(log n) per-value operations and
O(count) iteration over all values for a key.

Key format:
    __mm:{name}:{key_len:06d}:{key}:{value}  →  ""

The length-prefixed key format ensures that value strings containing ':' do
not corrupt the key-scan prefix.

Use cases:
  • Inverted indexes:  tag → article IDs
  • Multi-select categories:  product → tag list
  • Adjacency lists:  user → follower IDs (complement to Graph)
  • Reverse lookups:  add a second MultiMap in the other direction

Example
-------
    tags = db.multimap('article_tags')
    tags.add('python',   'article:1')
    tags.add('python',   'article:2')
    tags.add('database', 'article:1')

    tags.get('python')                # ['article:1', 'article:2']
    tags.contains('python', 'article:1')  # True
    tags.count('python')              # 2

    tags.remove('python', 'article:1')
    tags.get_all_keys()               # ['database', 'python']
    tags.remove_all('python')         # removes all values for 'python'
"""

from typing import Dict, Iterator, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree


class MultiMap:
    """Persistent one-to-many key-value map."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _entry_key(self, key: str, value: str) -> str:
        return f'__mm:{self._name}:{len(key):06d}:{key}:{value}'

    def _entry_prefix(self, key: str) -> str:
        """Prefix for all entries belonging to key."""
        return f'__mm:{self._name}:{len(key):06d}:{key}:'

    def _global_prefix(self) -> str:
        return f'__mm:{self._name}:'

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, key: str, value: str) -> bool:
        """Add a (key, value) pair.  Returns True if it was new."""
        k = self._entry_key(key, value)
        if self._db.has(k):
            return False
        self._db.put(k, '')
        return True

    def add_many(self, key: str, *values: str) -> int:
        """Add multiple values for a key.  Returns count newly added."""
        return sum(1 for v in values if self.add(key, v))

    def remove(self, key: str, value: str) -> bool:
        """Remove a (key, value) pair.  Returns True if it existed."""
        k = self._entry_key(key, value)
        if not self._db.has(k):
            return False
        self._db.delete(k)
        return True

    def remove_all(self, key: str) -> int:
        """Remove all values for key.  Returns count removed."""
        prefix = self._entry_prefix(key)
        return self._db.prefix_delete(prefix)

    def clear(self) -> int:
        """Remove all (key, value) pairs.  Returns count removed."""
        gp = self._global_prefix()
        return self._db.prefix_delete(gp)

    # ── read ──────────────────────────────────────────────────────────────────

    def get(self, key: str) -> List[str]:
        """Return all values for key, sorted."""
        prefix = self._entry_prefix(key)
        plen   = len(prefix)
        return [k[plen:] for k in self._db.scan_keys(prefix, prefix[:-1] + ';')]

    def contains(self, key: str, value: str) -> bool:
        """Return True if (key, value) pair exists."""
        return self._db.has(self._entry_key(key, value))

    def count(self, key: str) -> int:
        """Count values for a specific key."""
        prefix = self._entry_prefix(key)
        return sum(1 for _ in self._db.scan_keys(prefix, prefix[:-1] + ';'))

    def total_count(self) -> int:
        """Total number of (key, value) pairs."""
        gp = self._global_prefix()
        return sum(1 for _ in self._db.scan_keys(gp, gp[:-1] + ';'))

    def get_all_keys(self) -> List[str]:
        """Return all unique keys that have at least one value."""
        gp   = self._global_prefix()
        gplen = len(gp)
        seen = set()
        keys = []
        for full_key in self._db.scan_keys(gp, gp[:-1] + ';'):
            local    = full_key[gplen:]        # '{key_len:06d}:{key}:{value}'
            key_len  = int(local[:6])
            key      = local[7:7 + key_len]
            if key not in seen:
                seen.add(key)
                keys.append(key)
        return sorted(keys)

    def items(self) -> Iterator[Tuple[str, str]]:
        """Yield all (key, value) pairs."""
        gp    = self._global_prefix()
        gplen = len(gp)
        for full_key in self._db.scan_keys(gp, gp[:-1] + ';'):
            local   = full_key[gplen:]
            key_len = int(local[:6])
            key     = local[7:7 + key_len]
            value   = local[7 + key_len + 1:]
            yield key, value

    def as_dict(self) -> Dict[str, List[str]]:
        """Return all data as {key: [values]} dict."""
        result: Dict[str, List[str]] = {}
        for key, value in self.items():
            result.setdefault(key, []).append(value)
        return result

    # ── inspection ────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f'MultiMap({self._name!r}, pairs={self.total_count()})'
