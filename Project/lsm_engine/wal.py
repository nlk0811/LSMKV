"""
Write-Ahead Log (WAL)
=====================
Every Put/Delete is appended here — with a CRC-checked record — before the
memtable is updated.  On a crash the WAL is replayed from the beginning to
restore the memtable to a consistent state.

Crash-safety Invariant 1 (WAL Completeness)
--------------------------------------------
  WAL.log_put / log_delete  ──►  memtable.put / delete

The WAL is truncated ONLY after the corresponding SSTable has been durably
written AND the MANIFEST has been atomically updated to reference it.
(See LSMTree._flush_memtable for enforcement.)

Record wire format (big-endian):
  op_type : 1  byte   (1=PUT, 2=DELETE)
  key_len : 4  bytes
  val_len : 4  bytes
  timestamp: 8 bytes  (float64)
  key     : key_len bytes
  value   : val_len bytes
  crc32   : 4  bytes  (CRC of everything above)
"""

import os
import struct
import time
import zlib
from enum import IntEnum
from typing import Iterator, Tuple

from ._utils import fsync_fd

_HDR_FMT  = '>BIId'
_HDR_SIZE = struct.calcsize(_HDR_FMT)   # 17 bytes
_CRC_SIZE = 4


class OpType(IntEnum):
    PUT    = 1
    DELETE = 2


class WAL:
    def __init__(self, path: str):
        self.path = path
        self._f   = open(path, 'ab')

    # ── write ──────────────────────────────────────────────────────────────────

    def log_put(self, key: bytes, value: bytes):
        self._write(OpType.PUT, key, value)

    def log_delete(self, key: bytes):
        self._write(OpType.DELETE, key, b'')

    def _write(self, op: OpType, key: bytes, value: bytes):
        hdr     = struct.pack(_HDR_FMT, int(op), len(key), len(value), time.time())
        payload = hdr + key + value
        crc     = struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF)
        self._f.write(payload + crc)
        self._f.flush()
        fsync_fd(self._f.fileno())

    # ── recovery ───────────────────────────────────────────────────────────────

    def replay(self) -> Iterator[Tuple[OpType, bytes, bytes]]:
        """Yield (op, key, value) for every valid record; stop at first bad one."""
        if not os.path.exists(self.path):
            return
        with open(self.path, 'rb') as f:
            while True:
                hdr_data = f.read(_HDR_SIZE)
                if len(hdr_data) < _HDR_SIZE:
                    break
                op, klen, vlen, _ts = struct.unpack(_HDR_FMT, hdr_data)
                key   = f.read(klen)
                value = f.read(vlen)
                crc_b = f.read(_CRC_SIZE)
                if len(key) < klen or len(value) < vlen or len(crc_b) < _CRC_SIZE:
                    break  # truncated record = crash point, stop here
                expected = struct.unpack('>I', crc_b)[0]
                actual   = zlib.crc32(hdr_data + key + value) & 0xFFFFFFFF
                if expected != actual:
                    break  # corrupted record, stop
                yield OpType(op), key, value

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def truncate(self):
        """
        Discard all WAL contents.
        MUST only be called after the SSTable + MANIFEST are durable.
        """
        self._f.close()
        with open(self.path, 'wb') as f:
            f.flush()
            fsync_fd(f.fileno())
        self._f = open(self.path, 'ab')

    def close(self):
        self._f.close()

    @property
    def size_bytes(self) -> int:
        return os.path.getsize(self.path) if os.path.exists(self.path) else 0
