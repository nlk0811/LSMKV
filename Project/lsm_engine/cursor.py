"""
Cursor — Stateful Forward Iterator
====================================
A stateful cursor over the database that supports seek, next, valid/key/value
inspection, and the standard Python iterator protocol.

Use `db.cursor()` to create one.  Cursors are lightweight: they wrap the
existing `scan()` generator and add seek + context-manager support.

Example
-------
    # Paginate in chunks of 100
    with db.cursor() as cur:
        cur.seek('user:0000')
        page = []
        while cur.valid() and len(page) < 100:
            page.append((cur.key(), cur.value()))
            cur.next()

    # Standard iterator protocol
    for key, value in db.cursor().seek_to_first():
        print(key, value)

    # Seek to a position, read a few keys, stop
    cur = db.cursor()
    cur.seek('order:500')
    if cur.valid():
        print(cur.key(), cur.value())

Thread safety: a Cursor must not be shared between threads.  Create one
cursor per thread.
"""

from typing import Iterator, Optional, Tuple


class Cursor:
    """Stateful forward iterator over LSMTree key-value pairs.

    The cursor wraps the underlying scan() generator.  It is forward-only:
    there is no prev() or seek_backward().  Use seek() to reposition.
    """

    def __init__(self, db):
        self._db      = db
        self._gen     = None
        self._current: Optional[Tuple[str, str]] = None

    # ── positioning ────────────────────────────────────────────────────────────

    def seek(self, key) -> 'Cursor':
        """Position the cursor at the first key >= `key`.

        Returns self so calls can be chained:
            for k, v in db.cursor().seek('prefix:'):
                ...
        """
        self._gen     = self._db.scan(key)
        self._advance()
        return self

    def seek_to_first(self) -> 'Cursor':
        """Position the cursor at the first key in the database."""
        self._gen = self._db.scan()
        self._advance()
        return self

    def seek_prefix(self, prefix) -> 'Cursor':
        """Position the cursor at the first key that starts with prefix."""
        return self.seek(prefix)

    # ── inspection ─────────────────────────────────────────────────────────────

    def valid(self) -> bool:
        """Return True if the cursor points to a live entry."""
        return self._current is not None

    def key(self) -> Optional[str]:
        """Current key, or None if the cursor is exhausted."""
        return self._current[0] if self._current else None

    def value(self) -> Optional[str]:
        """Current value, or None if the cursor is exhausted."""
        return self._current[1] if self._current else None

    def item(self) -> Optional[Tuple[str, str]]:
        """Current (key, value) pair, or None if exhausted."""
        return self._current

    # ── advancement ────────────────────────────────────────────────────────────

    def next(self) -> 'Cursor':
        """Advance to the next key.  Returns self for chaining."""
        self._advance()
        return self

    def _advance(self):
        try:
            self._current = next(self._gen)
        except (StopIteration, TypeError):
            self._current = None

    # ── Python protocols ───────────────────────────────────────────────────────

    def __iter__(self) -> Iterator[Tuple[str, str]]:
        """Yield (key, value) pairs from the current position onward."""
        while self.valid():
            yield self._current
            self._advance()

    def __next__(self) -> Tuple[str, str]:
        if not self.valid():
            raise StopIteration
        item = self._current
        self._advance()
        return item

    def __enter__(self) -> 'Cursor':
        return self

    def __exit__(self, *_):
        self.close()

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def close(self):
        """Release the underlying generator. Called automatically by __exit__."""
        self._gen     = None
        self._current = None

    def __repr__(self) -> str:
        if self.valid():
            return f'Cursor(key={self.key()!r})'
        return 'Cursor(exhausted)'
