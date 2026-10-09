"""
K-Way Merge Compaction
=======================
Merges N sorted SSTables into one output SSTable using a min-heap.

Algorithm
---------
  1. Create one scan-iterator per input SSTable, ordered newest → oldest.
  2. Prime a min-heap with the first entry from each iterator.
     Heap key: (entry_key, reader_sequence).
     reader_sequence 0 = newest, so for equal keys the newest version is
     popped first.
  3. Pop the minimum.  Advance that reader and push its next entry.
  4. If the current key equals the previous yielded key → skip (dedup).
  5. If the entry is a tombstone AND we are at the deepest compaction level
     (no older data below) → drop it entirely.  Otherwise emit it.

Complexity
----------
  Time:  O(N log K)   N = total entries, K = number of input files
  Space: O(K)         heap holds at most one entry per file
"""

import heapq
import os
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple

from .sstable import SSTableReader, SSTableWriter, _ENTRY_HDR
from .ttl     import decode as _ttl_decode, is_expired as _ttl_expired


@dataclass(order=True)
class _E:
    key:   bytes
    seq:   int               # lower = newer/higher priority
    val:   bytes = field(compare=False, default=b'')
    tomb:  bool  = field(compare=False, default=False)
    it:    Any   = field(compare=False, default=None)


def kway_merge(
    readers: List[SSTableReader],
    start:   Optional[bytes] = None,
    end:     Optional[bytes] = None,
    drop_tombstones: bool    = False,
) -> Iterator[Tuple[bytes, bytes, bool]]:
    """
    Merge readers (newest first) into a sorted, deduplicated stream.
    Yields (key, value, is_tombstone).
    If drop_tombstones is True, tombstone entries are suppressed entirely
    (used during full compaction to the deepest level).
    """
    heap: list = []
    iters = [r.scan(start, end) for r in readers]

    for seq, it in enumerate(iters):
        try:
            k, v, d = next(it)
            heapq.heappush(heap, _E(k, seq, v, d, it))
        except StopIteration:
            pass

    last_key: Optional[bytes] = None
    while heap:
        e = heapq.heappop(heap)
        try:
            k, v, d = next(e.it)
            heapq.heappush(heap, _E(k, e.seq, v, d, e.it))
        except StopIteration:
            pass

        if e.key == last_key:
            continue        # older version of an already-seen key
        last_key = e.key

        if drop_tombstones and e.tomb:
            continue        # safe to drop tombstones at deepest level

        if drop_tombstones and not e.tomb:
            _, exp = _ttl_decode(e.val)
            if _ttl_expired(exp):
                continue    # drop expired TTL entries at deepest level

        yield e.key, e.val, e.tomb


def compact(
    input_paths:     List[str],
    output_path:     str,
    drop_tombstones: bool = False,
    rate_limiter = None,
    compaction_filter = None,
    dst_level: int = 0,
    compression: str = 'none',
) -> int:
    """Merge input_paths (newest first) into output_path.

    rate_limiter: optional RateLimiter — throttle write bandwidth per-job.
    compaction_filter: optional callable(key: bytes, value: bytes, level: int)
        → bytes | None.  Called for each live (non-tombstone) entry.  Return
        the (possibly modified) value to keep it, or None to drop the entry.
        Useful for: purging a key prefix, migrating value formats, custom TTL.
    dst_level: the destination level, passed to compaction_filter.

    Returns the number of entries written.
    """
    readers = [SSTableReader(p) for p in input_paths if os.path.exists(p)]
    if not readers:
        return 0

    est_capacity = max(sum(len(r._index) * 100 for r in readers), 100)
    writer  = SSTableWriter(output_path, bloom_capacity=est_capacity, compression=compression)
    written = 0
    pending_bytes = 0
    BATCH = 256

    for key, value, tombstone in kway_merge(readers, drop_tombstones=drop_tombstones):
        if compaction_filter is not None and not tombstone:
            new_val = compaction_filter(key, value, dst_level)
            if new_val is None:
                continue   # filter dropped this entry
            value = new_val
        writer.add(key, value, tombstone=tombstone)
        written += 1
        if rate_limiter is not None:
            pending_bytes += _ENTRY_HDR + len(key) + len(value)
            if written % BATCH == 0:
                rate_limiter.consume(pending_bytes)
                pending_bytes = 0

    if rate_limiter is not None and pending_bytes:
        rate_limiter.consume(pending_bytes)

    if written > 0:
        writer.finish()
    else:
        writer._f.close()
        os.remove(output_path)

    for r in readers:
        r.close()

    return written
