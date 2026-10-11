"""
Bitmap — Persistent Space-Efficient Boolean Flags
==================================================
A Bitmap stores up to 2^63 boolean flags (bits) indexed by integer.  It uses
approximately 1 byte per 8 flags, making it far more compact than individual
keys for tracking large populations.

Internally the bitmap is split into 4096-byte chunks stored as base64:
  __bm:{name}:{chunk_id}  →  base64_encoded_chunk

Use cases:
  • User feature flags:  seen_feature.set(user_id)
  • Read receipts:       read_receipts.set(message_id)
  • Attendance:          daily_users.set(user_id)

Example
-------
    seen = db.bitmap('seen_homepage')
    seen.set(42)          # user 42 has seen the homepage
    seen.get(42)          # True
    seen.get(43)          # False
    seen.count()          # 1 (number of set bits)
    seen.clear(42)        # unset
    seen.total_count()    # 0

    # Range operations
    announcements = db.bitmap('saw_announcement:2026-10')
    for user_id in range(1000):
        announcements.set(user_id)
    announcements.count()   # 1000
"""

import base64
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

_CHUNK_BYTES = 512   # 512 bytes = 4096 bits per chunk; balance between wasted space and chunk count
_BITS_PER_CHUNK = _CHUNK_BYTES * 8


class Bitmap:
    """Persistent space-efficient boolean bitmap backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _chunk_key(self, chunk_id: int) -> str:
        return f'__bm:{self._name}:{chunk_id:015d}'

    def _chunk_id_and_offset(self, bit: int):
        return bit // _BITS_PER_CHUNK, bit % _BITS_PER_CHUNK

    # ── chunk I/O ─────────────────────────────────────────────────────────────

    def _read_chunk(self, chunk_id: int) -> bytearray:
        raw = self._db.get(self._chunk_key(chunk_id))
        if raw is None:
            return bytearray(_CHUNK_BYTES)
        return bytearray(base64.b64decode(raw))

    def _write_chunk(self, chunk_id: int, chunk: bytearray) -> None:
        self._db.put(self._chunk_key(chunk_id),
                     base64.b64encode(bytes(chunk)).decode())

    # ── single bit ────────────────────────────────────────────────────────────

    def set(self, bit: int, value: bool = True) -> None:
        """Set or clear a single bit.  set(bit) sets; set(bit, False) clears."""
        chunk_id, offset = self._chunk_id_and_offset(bit)
        chunk = self._read_chunk(chunk_id)
        byte_i, bit_i = divmod(offset, 8)
        if value:
            chunk[byte_i] |=  (1 << bit_i)
        else:
            chunk[byte_i] &= ~(1 << bit_i) & 0xFF
        self._write_chunk(chunk_id, chunk)

    def clear(self, bit: int) -> None:
        """Clear a single bit (alias for set(bit, False))."""
        self.set(bit, False)

    def get(self, bit: int) -> bool:
        """Return True if bit is set, False otherwise."""
        chunk_id, offset = self._chunk_id_and_offset(bit)
        raw = self._db.get(self._chunk_key(chunk_id))
        if raw is None:
            return False
        chunk = base64.b64decode(raw)
        byte_i, bit_i = divmod(offset, 8)
        if byte_i >= len(chunk):
            return False
        return bool(chunk[byte_i] & (1 << bit_i))

    def toggle(self, bit: int) -> bool:
        """Toggle a bit and return the new value."""
        new_val = not self.get(bit)
        self.set(bit, new_val)
        return new_val

    # ── range / bulk ──────────────────────────────────────────────────────────

    def set_range(self, start: int, end: int, value: bool = True) -> int:
        """Set or clear bits in [start, end).  Returns count modified."""
        for bit in range(start, end):
            self.set(bit, value)
        return end - start

    def clear_range(self, start: int, end: int) -> int:
        """Clear all bits in [start, end).  Returns count cleared."""
        return self.set_range(start, end, False)

    # ── aggregation ───────────────────────────────────────────────────────────

    def count(self, start: int = None, end: int = None) -> int:
        """Count set bits.  If start/end given, count only in [start, end)."""
        if start is not None and end is not None:
            return sum(1 for b in range(start, end) if self.get(b))
        # Count all set bits across all stored chunks
        prefix = f'__bm:{self._name}:'
        total  = 0
        for _, v in self._db.scan(prefix, prefix[:-1] + ';'):
            chunk = base64.b64decode(v)
            total += bin(int.from_bytes(chunk, 'little')).count('1')
        return total

    def any_set(self, start: int = None, end: int = None) -> bool:
        """Return True if any bit is set."""
        return self.count(start, end) > 0

    def iter_set_bits(self, max_bit: int) -> Iterator[int]:
        """Yield indices of all set bits up to max_bit (exclusive)."""
        for bit in range(max_bit):
            if self.get(bit):
                yield bit

    # ── maintenance ───────────────────────────────────────────────────────────

    def clear_all(self) -> int:
        """Clear all bits.  Returns number of chunks deleted."""
        prefix = f'__bm:{self._name}:'
        keys   = [k for k in self._db.scan_keys(prefix, prefix[:-1] + ';')]
        for k in keys:
            self._db.delete(k)
        return len(keys)

    # ── inspection ────────────────────────────────────────────────────────────

    def __getitem__(self, bit: int) -> bool:
        return self.get(bit)

    def __setitem__(self, bit: int, value: bool) -> None:
        self.set(bit, value)

    def __repr__(self) -> str:
        return f'Bitmap({self._name!r})'
