"""
TTL (Time-To-Live) Encoding
============================
Keys with a TTL have their expiry time encoded into the stored value bytes
using a 12-byte prefix, without any change to the WAL or SSTable wire formats.

Wire layout of a TTL-encoded value:
    _MARKER   : 4 bytes   b'\\x00\\xfe\\xef\\x00'  (sentinel)
    expires_at: 8 bytes   big-endian float64 (Unix timestamp)
    actual    : remaining bytes  (the real value)

A regular value that happens to start with the 4-byte sentinel is astronomically
unlikely in practice and is safe for a reference implementation.  Production
systems would use a dedicated format flag instead.

On read:
  - decode() strips the header and returns (actual_value, expires_at)
  - is_expired(expires_at) returns True when time.time() > expires_at
  - get() and scan() in LSMTree treat expired entries as invisible tombstones
  - compaction drops expired entries at the deepest level (same as tombstones)
"""

import struct
import time
from typing import Optional, Tuple

_MARKER   = b'\x00\xfe\xef\x00'    # 4-byte sentinel
_EXPIRES  = '>d'                    # big-endian float64
_HDR_SIZE = len(_MARKER) + struct.calcsize(_EXPIRES)   # 12 bytes


def encode(value: bytes, expires_at: float) -> bytes:
    """Prepend the TTL header to value."""
    return _MARKER + struct.pack(_EXPIRES, expires_at) + value


def decode(data: bytes) -> Tuple[bytes, Optional[float]]:
    """Strip the TTL header from data if present.

    Returns (actual_value, expires_at) where expires_at is None for
    non-TTL values.
    """
    if data and len(data) >= _HDR_SIZE and data[:4] == _MARKER:
        expires_at = struct.unpack(_EXPIRES, data[4:12])[0]
        return data[12:], expires_at
    return data, None


def is_expired(expires_at: Optional[float]) -> bool:
    """Return True if the entry has passed its expiry time."""
    return expires_at is not None and time.time() >= expires_at
