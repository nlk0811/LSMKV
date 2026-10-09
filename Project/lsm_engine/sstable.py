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
_ENTRY_HDR    = struct.calcsize(_ENTRY_FMT)   # 9 bytes (key_len, val_len, flags)
_FOOTER_FMT   = '>QQII8s'
_FOOTER_SIZE  = struct.calcsize(_FOOTER_FMT)  # 32 bytes
_MAGIC        = b'SSTABLE1'

# ── Prefix-compressed entry format ────────────────────────────────────────────
# Used when SSTableWriter(prefix_compression=True).  Adjacent entries in the same
# block share a common key prefix that is stored only once per block restart point.
#
#   shared_prefix_len : 2 bytes (uint16)  — bytes shared with previous key
#   key_suffix_len    : 2 bytes (uint16)  — remaining key bytes
#   val_len           : 4 bytes (uint32)
#   flags             : 1 byte            (0x01 = tombstone)
#   key_suffix        : key_suffix_len bytes
#   value             : val_len bytes
#
# Block markers (first 2 bytes of the stored block):
#   \xff\x01  — zlib-compressed,  old entry format  (existing)
#   \xff\x02  — uncompressed,     prefix-compressed format  (new)
#   \xff\x03  — zlib-compressed,  prefix-compressed format  (new)
#   no marker — uncompressed,     old entry format  (backward compat)
_PC_FMT     = '>HHIB'               # shared(H), suffix_len(H), val_len(I), flags(B)
_PC_HDR     = struct.calcsize(_PC_FMT)   # 9 bytes (same overhead as old format)
_PC_MARKER  = b'\xff\x02'
_PCZ_MARKER = b'\xff\x03'


# ── Writer ─────────────────────────────────────────────────────────────────────

# Compression marker: two bytes that can never begin an uncompressed data block.
# Uncompressed entries start with key_len (uint32 big-endian); a first byte of
# 0xFF would mean key_len >= 4 GiB — physically impossible.
_COMPRESS_MARKER = b'\xff\x01'
_COMPRESS_LEVEL  = 6   # zlib level: good balance of speed and ratio


class SSTableWriter:
    def __init__(self, path: str, bloom_capacity: int = 10_000,
                 compression: str = 'none', bloom_fpr: float = 0.01,
                 prefix_compression: bool = True):
        """
        compression: 'none' or 'zlib' (block-level zlib compression).
        prefix_compression: store only the key suffix for adjacent keys sharing
            a common prefix within a block.  Reduces key storage by up to 89%
            for clustered keys (e.g. 'user:0001' … 'user:9999').
            Default True.  Old SSTables (False) are always readable.
        bloom_fpr: target false-positive rate for the per-file Bloom filter.
        """
        self.path               = path
        self._compression       = compression
        self._prefix_compression = prefix_compression
        self._f     = open(path, 'wb')
        self._index: List[Tuple[bytes, int, int]] = []
        self._bloom = BloomFilter(max(bloom_capacity, 100), bloom_fpr)
        self._buf   = bytearray()
        self._file_off        = 0
        self._block_file_off  = 0
        self._block_first_key: Optional[bytes] = None
        self._block_last_key:  bytes           = b''   # for prefix delta encoding

    def add(self, key: bytes, value: bytes, tombstone: bool = False):
        flags = 0x01 if tombstone else 0x00
        v     = b'' if tombstone else value
        if self._prefix_compression:
            # Compute shared prefix length with the previous key in this block
            shared = 0
            mn = min(len(key), len(self._block_last_key))
            while shared < mn and key[shared] == self._block_last_key[shared]:
                shared += 1
            suffix = key[shared:]
            entry = struct.pack(_PC_FMT, shared, len(suffix), len(v), flags) + suffix + v
            self._block_last_key = key
        else:
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
        if self._prefix_compression and self._compression == 'zlib':
            data = _PCZ_MARKER + zlib.compress(raw, _COMPRESS_LEVEL)
        elif self._prefix_compression:
            data = _PC_MARKER + raw
        elif self._compression == 'zlib':
            data = _COMPRESS_MARKER + zlib.compress(raw, _COMPRESS_LEVEL)
        else:
            data = raw
        self._index.append((self._block_first_key, self._block_file_off, len(data)))
        self._f.write(data)
        self._file_off       += len(data)
        self._block_file_off  = self._file_off
        self._block_first_key = None
        self._block_last_key  = b''   # reset at each block boundary
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
        data, is_pc = self._read_block(off, sz)
        return self._search_pc_block(data, key) if is_pc else self._search_block(data, key)

    def scan(self,
             start: Optional[bytes] = None,
             end:   Optional[bytes] = None
             ) -> Iterator[Tuple[bytes, bytes, bool]]:
        """Yield (key, value, is_tombstone) in sorted order within [start, end)."""
        si = self._block_idx(start) if start else 0
        for fk, off, sz in self._index[si:]:
            if end and fk >= end:
                break
            data, is_pc = self._read_block(off, sz)
            if is_pc:
                yield from self._scan_pc_block(data, start, end)
            else:
                pos, last_key_in_block = 0, b''
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

    def _read_block(self, off: int, sz: int) -> Tuple[bytes, bool]:
        """Read a data block, returning (block_bytes, is_prefix_compressed).

        block_bytes: decoded payload (zlib-decompressed if needed, marker stripped).
        is_prefix_compressed: True if entries use the prefix-compressed format.

        Uses os.pread() for atomic positional reads.  The block cache stores
        the decoded payload (minus marker, after zlib) so hot reads are free.
        """
        if self._block_cache is not None:
            key  = (self.path, off)
            cached = self._block_cache.get(key)
            if cached is not None:
                # Cache stores (bytes, is_pc) as a 2-tuple.
                if isinstance(cached, tuple):
                    return cached
                return cached, False   # legacy cache entry (plain bytes)
        raw   = self._pread(off, sz)
        is_pc = False
        if raw[:2] == _PCZ_MARKER:             # prefix-compressed + zlib
            raw   = zlib.decompress(raw[2:])
            is_pc = True
        elif raw[:2] == _PC_MARKER:            # prefix-compressed, no zlib
            raw   = raw[2:]
            is_pc = True
        elif raw[:2] == _COMPRESS_MARKER:      # zlib only, old entry format
            raw   = zlib.decompress(raw[2:])
        # else: old entry format, no compression
        result = (raw, is_pc)
        if self._block_cache is not None:
            self._block_cache.put((self.path, off), result)
        return result

    def _pread(self, off: int, sz: int) -> bytes:
        """Atomic positional read: os.pread where available, seek+read elsewhere."""
        try:
            return os.pread(self._f.fileno(), sz, off)
        except AttributeError:
            # Windows — fall back to seek+read under a per-instance lock
            with self._rlock:
                self._f.seek(off)
                return self._f.read(sz)

    def _search_block(self, data: bytes, target: bytes) -> Optional[Tuple[bytes, bool]]:
        """Linear scan of an old-format (non-prefix-compressed) block."""
        pos = 0
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

    def _search_pc_block(self, data: bytes, target: bytes) -> Optional[Tuple[bytes, bool]]:
        """Linear scan of a prefix-compressed block for target key."""
        pos, last_key = 0, b''
        while pos < len(data):
            if pos + _PC_HDR > len(data):
                break
            shared, suf_len, vlen, flags = struct.unpack(_PC_FMT, data[pos:pos+_PC_HDR])
            pos += _PC_HDR
            suffix = data[pos:pos+suf_len]; pos += suf_len
            value  = data[pos:pos+vlen];   pos += vlen
            key    = last_key[:shared] + suffix
            last_key = key
            if key == target:
                return (value, bool(flags & 0x01))
        return None

    def _scan_pc_block(self, data: bytes,
                       start: Optional[bytes], end: Optional[bytes]
                       ) -> Iterator[Tuple[bytes, bytes, bool]]:
        """Iterate a prefix-compressed block, yielding entries in [start, end)."""
        pos, last_key = 0, b''
        while pos < len(data):
            if pos + _PC_HDR > len(data):
                break
            shared, suf_len, vlen, flags = struct.unpack(_PC_FMT, data[pos:pos+_PC_HDR])
            pos += _PC_HDR
            suffix = data[pos:pos+suf_len]; pos += suf_len
            value  = data[pos:pos+vlen];   pos += vlen
            key    = last_key[:shared] + suffix
            last_key = key
            if start and key < start:
                continue
            if end and key >= end:
                return
            yield key, value, bool(flags & 0x01)

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
            data, is_pc = self._read_block(off, sz)
            last = None
            if is_pc:
                last_key_pc = b''
                pos = 0
                while pos < len(data):
                    if pos + _PC_HDR > len(data):
                        break
                    shared, suf_len, vlen, _ = struct.unpack(_PC_FMT, data[pos:pos+_PC_HDR])
                    pos += _PC_HDR
                    suffix = data[pos:pos+suf_len]; pos += suf_len + vlen
                    last = last_key_pc[:shared] + suffix
                    last_key_pc = last
            else:
                pos = 0
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
