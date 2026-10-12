"""
Trie — Persistent Prefix Tree for Autocomplete
===============================================
A Trie stores a set of strings and supports O(|prefix|) prefix queries.
Under the hood it leverages the LSM tree's lexicographic sorted-key ordering:
each word is stored as a single database key, and `prefix_scan` finds all
matching words in one range query — no tree traversal required.

Key format:
    __trie:{name}:{word}  →  ""

Example
-------
    words = db.trie('dictionary')

    words.insert('apple', 'application', 'apply', 'apt', 'banana')

    words.search('app', limit=3)     # ['apple', 'application', 'apply']
    words.starts_with('ap')          # True
    words.contains('apple')          # True
    words.contains('app')            # False (not an inserted word)
    words.count()                    # 5

    words.delete('apply')
    words.count_prefix('app')        # 2 (apple, application)
"""

from typing import Iterator, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree


class Trie:
    """Persistent prefix tree backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _key(self, word: str) -> str:
        return f'__trie:{self._name}:{word}'

    def _prefix(self) -> str:
        return f'__trie:{self._name}:'

    def _word_from_key(self, full_key: str) -> str:
        return full_key[len(self._prefix()):]

    # ── write ─────────────────────────────────────────────────────────────────

    def insert(self, *words: str) -> int:
        """Insert one or more words.  Returns count of new insertions."""
        inserted = 0
        for w in words:
            if not self._db.has(self._key(w)):
                self._db.put(self._key(w), '')
                inserted += 1
        return inserted

    def delete(self, word: str) -> bool:
        """Remove a word.  Returns True if it existed."""
        if not self._db.has(self._key(word)):
            return False
        self._db.delete(self._key(word))
        return True

    def clear(self) -> int:
        """Remove all words.  Returns count removed."""
        keys = [k for k in self._db.scan_keys(
            self._prefix(), self._prefix()[:-1] + ';')]
        for k in keys:
            self._db.delete(k)
        return len(keys)

    # ── search ────────────────────────────────────────────────────────────────

    def search(self, prefix: str = '', limit: int = 10) -> List[str]:
        """Return up to limit words that start with prefix, sorted.

        pass prefix='' to list all words.
        """
        scan_start = self._key(prefix) if prefix else self._prefix()
        # end: all keys starting with prefix end before (prefix+1 byte)
        if prefix:
            scan_end = self._key(prefix[:-1] + chr(ord(prefix[-1]) + 1)) \
                if prefix[-1] < '\U0010ffff' else None
        else:
            scan_end = self._prefix()[:-1] + ';'
        results = []
        for key in self._db.scan_keys(scan_start, scan_end):
            if not key.startswith(self._key(prefix)):
                break
            results.append(self._word_from_key(key))
            if len(results) >= limit:
                break
        return results

    def contains(self, word: str) -> bool:
        """Return True if the exact word was inserted."""
        return self._db.has(self._key(word))

    def starts_with(self, prefix: str) -> bool:
        """Return True if any inserted word starts with prefix."""
        return bool(self.search(prefix, limit=1))

    def count_prefix(self, prefix: str) -> int:
        """Count words starting with prefix."""
        return len(self.search(prefix, limit=10**9))

    def count(self) -> int:
        """Total number of inserted words."""
        return sum(1 for _ in self._db.scan_keys(
            self._prefix(), self._prefix()[:-1] + ';'))

    def __iter__(self) -> Iterator[str]:
        """Iterate all words in sorted order."""
        for key in self._db.scan_keys(self._prefix(), self._prefix()[:-1] + ';'):
            yield self._word_from_key(key)

    def __contains__(self, word: str) -> bool:
        return self.contains(word)

    def __len__(self) -> int:
        return self.count()

    def __repr__(self) -> str:
        return f'Trie({self._name!r}, words={len(self)})'
