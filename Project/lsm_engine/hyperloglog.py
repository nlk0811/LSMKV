"""
HyperLogLog — Approximate Cardinality Estimation
==================================================
Estimates the number of unique elements in a set using ~300 bytes of storage,
regardless of how many elements are added.  Error rate: ±~4.6% typical for M=512 (σ = 1.04/√M ≈ 4.6%).

Algorithm: HyperLogLog (Flajolet et al. 2007)
  - Hash each element to 64 bits
  - Split hash into bucket index (8 bits) and remaining 56 bits
  - Track max leading-zero-count per bucket (256 buckets × 1 byte = 256 bytes)
  - Estimate cardinality via harmonic mean of 2^(max_lz) per bucket

Storage: one database key per HyperLogLog:
  __hll:{name}  →  base64(256 bytes, one byte per register)

Example
-------
    uv = db.hyperloglog('unique_visitors')
    for ip in request_ips:
        uv.add(ip)
    print(f'~{uv.count():,} unique visitors')  # ≈ accurate within 1.6%

    # Merge multiple HLLs (e.g., per-hour → daily)
    daily = db.hyperloglog('daily')
    for hour in range(24):
        daily.merge(db.hyperloglog(f'hour:{hour}'))
    daily.count()   # estimate for the day
"""

import base64
import hashlib
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree

# M = 256 registers (2^8); alpha_M correction constant
_M           = 512
_LOG2_M      = 9        # log2(512)
_REST_BITS   = 64 - _LOG2_M  # remaining bits after bucket index = 55
_ALPHA_M     = 0.7213 / (1 + 1.079 / _M)   # bias correction


def _hash64(item: str) -> int:
    """64-bit hash of item (using first 8 bytes of SHA-256)."""
    h = hashlib.sha256(item.encode() if isinstance(item, str) else item).digest()
    return int.from_bytes(h[:8], 'big')


def _count_leading_zeros(value: int, max_bits: int = 56) -> int:
    """Count leading zero bits in `value` (treating it as max_bits wide)."""
    if value == 0:
        return max_bits + 1
    return min(max_bits - value.bit_length() + 1, max_bits)


class HyperLogLog:
    """Approximate cardinality estimator using ~300 bytes of storage."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name
        self._key  = f'__hll:{name}'

    # ── register I/O ──────────────────────────────────────────────────────────

    def _read_registers(self) -> bytearray:
        raw = self._db.get(self._key)
        if raw is None:
            return bytearray(_M)
        return bytearray(base64.b64decode(raw))

    def _write_registers(self, regs: bytearray) -> None:
        self._db.put(self._key, base64.b64encode(bytes(regs)).decode())

    # ── add ───────────────────────────────────────────────────────────────────

    def add(self, item) -> bool:
        """Add an element.  Returns True if the register was updated (new max)."""
        h       = _hash64(str(item))
        bucket  = h & (_M - 1)                          # low _LOG2_M bits
        rest    = h >> _LOG2_M                           # remaining _REST_BITS bits
        lz      = _count_leading_zeros(rest, _REST_BITS) # ρ(s) = leading zeros + 1

        regs = self._read_registers()
        if lz > regs[bucket]:
            regs[bucket] = lz
            self._write_registers(regs)
            return True
        return False

    def add_many(self, items) -> int:
        """Add multiple items.  Returns count of register updates."""
        regs    = self._read_registers()
        updated = 0
        for item in items:
            h      = _hash64(str(item))
            bucket = h & (_M - 1)
            rest   = h >> _LOG2_M
            lz     = _count_leading_zeros(rest, _REST_BITS)
            if lz > regs[bucket]:
                regs[bucket] = lz
                updated += 1
        if updated:
            self._write_registers(regs)
        return updated

    # ── count ─────────────────────────────────────────────────────────────────

    def count(self) -> int:
        """Return estimated cardinality (±~1.6% error for M=256)."""
        regs = self._read_registers()
        Z    = sum(2 ** (-r) for r in regs)
        raw  = _ALPHA_M * (_M ** 2) / Z

        # Small range correction (linear counting)
        zeros = regs.count(0)
        if raw <= 2.5 * _M and zeros > 0:
            raw = _M * math.log(_M / zeros)

        # Large range correction (hash collision avoidance)
        two_pow_bits = 2 ** _REST_BITS
        if raw > two_pow_bits / 30:
            raw = -two_pow_bits * math.log(1 - raw / two_pow_bits)

        return max(0, round(raw))

    # ── merge ─────────────────────────────────────────────────────────────────

    def merge(self, other: 'HyperLogLog') -> 'HyperLogLog':
        """Merge another HyperLogLog into this one (union).  Returns self."""
        my_regs    = self._read_registers()
        other_regs = other._read_registers()
        for i in range(_M):
            if other_regs[i] > my_regs[i]:
                my_regs[i] = other_regs[i]
        self._write_registers(my_regs)
        return self

    # ── reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all registers (reset to empty)."""
        self._db.delete(self._key)

    # ── inspection ────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f'HyperLogLog({self._name!r}, ~{self.count():,})'
