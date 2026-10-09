"""
Sorted String Table (SSTable)
==============================
Immutable, sorted, on-disk key-value file produced by flushing the memtable
or by compaction.

File layout
-----------
  [ data blocks … ]
  [ index block   ]   — sparse index: first key of each block → (offset, size)
  [ bloom filter  ]   — serialised BloomFilter with CRC
  [ footer        ]   — 32-byte fixed record, always at EOF

Entry wire format (inside data blocks, big-endian):
  key_len : 4 bytes
  val_len : 4 bytes
  flags   : 1 byte   (0x01 = tombstone)
  key     : key_len bytes
  value   : val_len bytes  (empty when tombstone)

Footer:
  index_offset : 8 bytes (uint64)
  bloom_offset : 8 bytes (uint64)
  index_size   : 4 bytes (uint32)
  bloom_size   : 4 bytes (uint32)
  magic        : 8 bytes  b'SSTABLE1'
"""

import os
import struct
import zlib
from typing import Iterator, List, Optional, Tuple

from .bloom_filter import BloomFilter
from ._utils import fsync_fd

BLOCK_SIZE    = 4096
_ENTRY_FMT    = '>IIB'
_ENTRY_HDR    = struct.calcsize(_ENTRY_FMT)   # 9 bytes
_FOOTER_FMT   = '>QQII8s'
_FOOTER_SIZE  = struct.calcsize(_FOOTER_FMT)  # 32 bytes
_MAGIC        = b'SSTABLE1'


# ── Writer ─────────────────────────────────────────────────────────────────────

# Compression marker: two bytes that can never begin an uncompressed data block.
# Uncompressed entries start with key_len (uint32 big-endian); a first byte of
# 0xFF would mean key_len >= 4 GiB — physically impossible.
_COMPRESS_MARKER = b'\xff\x01'
_COMPRESS_LEVEL  = 6   # zlib level: good balance of speed and ratio


class SSTableWriter:
    def __init__(self, path: str, bloom_capacity: int = 10_000,
                 compression: str = 'none', bloom_fpr: float = 0.01):
        """
        compression: 'none' (default) or 'zlib'.
        When 'zlib', each data block is compressed before writing using
        _COMPRESS_MARKER + zlib.compress(block_bytes).  Reduces disk usage
        by 30-90% for structured data.  Old SSTables (compression='none')
        are still readable by the new reader without any migration.
        """
        self.path         = path
        self._compression = compression
        self._f     = open(path, 'wb')
        self._index: List[Tuple[bytes, int, int]] = []  # (first_key, file_off, file_size)
        self._bloom = BloomFilter(max(bloom_capacity, 100), bloom_fpr)
        self._buf   = bytearray()
        self._file_off        = 0    # actual byte position in the file
        self._block_file_off  = 0    # file offset where current block started
        self._block_first_key: Optional[bytes] = None

    def add(self, key: bytes, value: bytes, tombstone: bool = False):
        flags = 0x01 if tombstone else 0x00
        v     = b'' if tombstone else value
        entry = struct.pack(_ENTRY_FMT, len(key), len(v), flags) + key + v
        if self._block_first_key is None:
            self._block_first_key = key
        self._buf += entry
        self._bloom.add(key)
        if len(self._buf) >= BLOCK_SIZE:
            self._flush_block()

    def _flush_block(self):
        if not self._buf:
            return
        raw = bytes(self._buf)
        if self._compression == 'zlib':
            data = _COMPRESS_MARKER + zlib.compress(raw, _COMPRESS_LEVEL)
        else:
            data = raw
        self._index.append((self._block_first_key, self._block_file_off, len(data)))
        self._f.write(data)
        self._file_off       += len(data)
        self._block_file_off  = self._file_off
        self._block_first_key = None
        self._buf             = bytearray()

    def finish(self) -> int:
        """Finalise the file and return its size in bytes."""
        self._flush_block()

        # ── index block ──
        index_offset = self._file_off
        idx_body = struct.pack('>I', len(self._index))
        for (fk, off, sz) in self._index:
            idx_body += struct.pack('>I', len(fk)) + fk + struct.pack('>QI', off, sz)
        idx_crc  = struct.pack('>I', zlib.crc32(idx_body) & 0xFFFFFFFF)
        idx_data = idx_body + idx_crc
        self._f.write(idx_data)

        # ── bloom filter block ──
        bloom_offset = index_offset + len(idx_data)
        bloom_body   = self._bloom.serialize()
        bloom_crc    = struct.pack('>I', zlib.crc32(bloom_body) & 0xFFFFFFFF)
        bloom_data   = bloom_body + bloom_crc
        self._f.write(bloom_data)

        # ── footer ──
        footer = struct.pack(_FOOTER_FMT,
                             index_offset, bloom_offset,
                             len(idx_data), len(bloom_data),
                             _MAGIC)
        self._f.write(footer)
        self._f.flush()
        fsync_fd(self._f.fileno())
        self._f.close()
        return os.path.getsize(self.path)


# ── Reader ─────────────────────────────────────────────────────────────────────

class SSTableReader:
    def __init__(self, path: str, block_cache=None, _template=None):
        """Open an SSTable for reading.

        _template: optional SSTableReader whose already-loaded index and Bloom
            filter are borrowed instead of re-reading them from disk.  Used by
            LSMTree.scan() to avoid 3 redundant disk seeks per SSTable when the
            metadata is already resident in the reader cache.  The template's
            file descriptor is NOT shared — a fresh fd is opened — so concurrent
            scans and compaction eviction are both safe.
        """
        self.path         = path
        self._f           = open(path, 'rb')
        self._block_cache = block_cache
        self._rlock       = __import__('threading').Lock()  # pread fallback
        if _template is not None:
            # Borrow metadata from the cached reader — no disk I/O required.
            self._index = _template._index
            self._bloom = _template._bloom
            if hasattr(_template, '_last_key_cache'):
                self._last_key_cache = _template._last_key_cache
        else:
            self._load_footer()
            self._load_index()
            self._load_bloom()

    # ── init helpers ───────────────────────────────────────────────────────────

    def _load_footer(self):
        self._f.seek(-_FOOTER_SIZE, 2)
        raw = self._f.read(_FOOTER_SIZE)
        self._idx_off, self._bloom_off, self._idx_sz, self._bloom_sz, magic = \
            struct.unpack(_FOOTER_FMT, raw)
        if magic != _MAGIC:
            raise ValueError(f'Bad SSTable magic in {self.path}: {magic!r}')

    def _load_index(self):
        self._f.seek(self._idx_off)
        raw      = self._f.read(self._idx_sz)
        body, crc_b = raw[:-4], raw[-4:]
        if (zlib.crc32(body) & 0xFFFFFFFF) != struct.unpack('>I', crc_b)[0]:
            raise ValueError(f'Index CRC mismatch in {self.path}')
        pos   = 0
        count = struct.unpack('>I', body[pos:pos+4])[0]; pos += 4
        self._index: List[Tuple[bytes, int, int]] = []
        for _ in range(count):
            klen          = struct.unpack('>I', body[pos:pos+4])[0]; pos += 4
            key           = body[pos:pos+klen];                       pos += klen
            off, sz       = struct.unpack('>QI', body[pos:pos+12]);   pos += 12
            self._index.append((key, off, sz))

    def _load_bloom(self):
        self._f.seek(self._bloom_off)
        raw      = self._f.read(self._bloom_sz)
        body, crc_b = raw[:-4], raw[-4:]
        if (zlib.crc32(body) & 0xFFFFFFFF) != struct.unpack('>I', crc_b)[0]:
            raise ValueError(f'Bloom CRC mismatch in {self.path}')
        self._bloom = BloomFilter.deserialize(body)

    # ── lookup ─────────────────────────────────────────────────────────────────

    def may_contain(self, key: bytes) -> bool:
        return self._bloom.may_contain(key)

    def get(self, key: bytes) -> Optional[Tuple[bytes, bool]]:
        """Return (value, is_tombstone) or None if not found."""
        if not self._bloom.may_contain(key) or not self._index:
            return None
        idx = self._block_idx(key)
        _, off, sz = self._index[idx]
        return self._search_block(off, sz, key)

    def scan(self,
             start: Optional[bytes] = None,
             end:   Optional[bytes] = None
             ) -> Iterator[Tuple[bytes, bytes, bool]]:
        """Yield (key, value, is_tombstone) in sorted order within [start, end)."""
        si = self._block_idx(start) if start else 0
        for fk, off, sz in self._index[si:]:
            if end and fk >= end:
                break
            data, pos = self._read_block(off, sz), 0
            while pos < len(data):
                if pos + _ENTRY_HDR > len(data):
                    break
                klen, vlen, flags = struct.unpack(_ENTRY_FMT, data[pos:pos+_ENTRY_HDR])
                pos  += _ENTRY_HDR
                key   = data[pos:pos+klen];  pos += klen
                value = data[pos:pos+vlen];  pos += vlen
                if start and key < start:
                    continue
                if end and key >= end:
                    return
                yield key, value, bool(flags & 0x01)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _block_idx(self, key: bytes) -> int:
        """Binary search: last block whose first_key <= key."""
        lo, hi, result = 0, len(self._index) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._index[mid][0] <= key:
                result = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    def _read_block(self, off: int, sz: int) -> bytes:
        """Read a data block, serving from the block cache when available.

        Uses os.pread() for an atomic positional read (no seek+read race).
        Transparently decompresses zlib-compressed blocks — the block cache
        stores the decompressed result so subsequent hits pay no CPU cost.

        Backward-compatible: blocks without the _COMPRESS_MARKER are returned
        verbatim, so SSTables written by any prior version are still readable.
        """
        if self._block_cache is not None:
            key  = (self.path, off)
            data = self._block_cache.get(key)
            if data is not None:
                return data
        raw = self._pread(off, sz)
        if raw[:2] == _COMPRESS_MARKER:
            raw = zlib.decompress(raw[2:])
        if self._block_cache is not None:
            self._block_cache.put((self.path, off), raw)
        return raw

    def _pread(self, off: int, sz: int) -> bytes:
        """Atomic positional read: os.pread where available, seek+read elsewhere."""
        try:
            return os.pread(self._f.fileno(), sz, off)
        except AttributeError:
            # Windows — fall back to seek+read under a per-instance lock
            with self._rlock:
                self._f.seek(off)
                return self._f.read(sz)

    def _search_block(self, off: int, sz: int, target: bytes) -> Optional[Tuple[bytes, bool]]:
        data, pos = self._read_block(off, sz), 0
        while pos < len(data):
            if pos + _ENTRY_HDR > len(data):
                break
            klen, vlen, flags = struct.unpack(_ENTRY_FMT, data[pos:pos+_ENTRY_HDR])
            pos  += _ENTRY_HDR
            key   = data[pos:pos+klen]; pos += klen
            value = data[pos:pos+vlen]; pos += vlen
            if key == target:
                return (value, bool(flags & 0x01))
        return None

    @property
    def first_key(self) -> Optional[bytes]:
        return self._index[0][0] if self._index else None

    @property
    def last_key(self) -> Optional[bytes]:
        """Last key in this SSTable, derived from the final data block.
        Result is cached on first call so repeated lookups are free.
        Used by compaction to determine which files overlap a given key range.
        """
        if not self._index:
            return None
        cached = getattr(self, '_last_key_cache', None)
        if cached is not None:
            return cached
        _, off, sz = self._index[-1]
        try:
            data, pos, last = self._read_block(off, sz), 0, None
            while pos < len(data):
                if pos + _ENTRY_HDR > len(data):
                    break
                klen, vlen, _ = struct.unpack(_ENTRY_FMT, data[pos:pos+_ENTRY_HDR])
                pos += _ENTRY_HDR
                last  = data[pos:pos+klen]
                pos  += klen + vlen
            self._last_key_cache = last
            return last
        except Exception:
            return None

    def close(self):
        self._f.close()
