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

Group Commit
------------
When sync_writes=True (the default), records are batched in a background
flusher thread and a single fsync covers up to group_commit_max records.
This gives the same durability guarantee as per-write fsync while dramatically
increasing throughput under concurrent or burst write loads.

  sync_writes=True  (default) — group-commit fsync; each log_put/log_delete
      call blocks until its record is durable, but multiple callers share one
      fsync.  throughput: limited by fsync_latency / avg_batch_size.
  sync_writes=False — no fsync; OS decides when to flush.  Used in benchmarks
      to isolate engine overhead from I/O cost.  Not crash-safe.

Record wire format (big-endian):
  op_type  : 1  byte   (1=PUT, 2=DELETE)
  key_len  : 4  bytes
  val_len  : 4  bytes
  timestamp: 8  bytes  (float64)
  key      : key_len bytes
  value    : val_len bytes
  crc32    : 4  bytes  (CRC of everything above)
"""

import os
import struct
import threading
import time
import zlib
from enum import IntEnum
from typing import Iterator, List, Tuple

from ._utils import fsync_fd

_HDR_FMT  = '>BIId'
_HDR_SIZE = struct.calcsize(_HDR_FMT)   # 17 bytes
_CRC_SIZE = 4


class OpType(IntEnum):
    PUT    = 1
    DELETE = 2


class WAL:
    def __init__(self, path: str, sync_writes: bool = True,
                 group_commit_max: int = 64, group_commit_us: int = 200):
        self.path  = path
        self._sync = sync_writes
        self._f    = open(path, 'ab')
        self._file_lock = threading.Lock()   # serialises raw file writes

        if sync_writes:
            self._gc_lock     = threading.Lock()
            self._gc_cv       = threading.Condition(self._gc_lock)
            self._gc_pending: List[Tuple[bytes, threading.Event]] = []
            self._gc_max      = group_commit_max
            self._gc_wait     = group_commit_us / 1_000_000
            self._gc_shutdown = False
            self._gc_thread   = threading.Thread(
                target=self._gc_loop, daemon=True, name='wal-gc')
            self._gc_thread.start()

    # ── write ──────────────────────────────────────────────────────────────────

    def log_put(self, key: bytes, value: bytes):
        self._write(OpType.PUT, key, value)

    def log_delete(self, key: bytes):
        self._write(OpType.DELETE, key, b'')

    def log_batch(self, ops):
        """Write a sequence of (op_int, key, value) tuples with one fsync.

        More efficient than N separate log_put/log_delete calls when
        sync_writes=True because only one group-commit submission is made,
        so the batch competes for at most one fsync slot.
        """
        if not ops:
            return
        combined = b''.join(self._make_record(op, key, value) for op, key, value in ops)
        self._write_raw(combined)

    def _make_record(self, op, key: bytes, value: bytes) -> bytes:
        hdr     = struct.pack(_HDR_FMT, int(op), len(key), len(value), time.time())
        payload = hdr + key + value
        crc     = struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF)
        return payload + crc

    def _write(self, op: OpType, key: bytes, value: bytes):
        self._write_raw(self._make_record(op, key, value))

    def _write_raw(self, data: bytes):
        """Write raw bytes (one or more records) with appropriate durability."""
        if not self._sync:
            with self._file_lock:
                self._f.write(data)
                self._f.flush()
            return

        # Group-commit: submit and block until flushed
        with self._gc_lock:
            if self._gc_shutdown:
                # Fallback: write directly (engine is shutting down)
                with self._file_lock:
                    self._f.write(data)
                    self._f.flush()
                    fsync_fd(self._f.fileno())
                return
            done = threading.Event()
            self._gc_pending.append((data, done))
            self._gc_cv.notify()
        done.wait()

    # ── group-commit loop ──────────────────────────────────────────────────────

    def _gc_loop(self):
        """Background thread: drain pending records with a single fsync per batch."""
        while True:
            with self._gc_lock:
                if not self._gc_pending and not self._gc_shutdown:
                    self._gc_cv.wait(timeout=self._gc_wait)
                if not self._gc_pending:
                    if self._gc_shutdown:
                        return
                    continue
                # Take up to gc_max entries; leave the rest for the next loop
                batch = self._gc_pending[:self._gc_max]
                del self._gc_pending[:self._gc_max]

            with self._file_lock:
                for data, _ in batch:
                    self._f.write(data)
                self._f.flush()
                fsync_fd(self._f.fileno())

            for _, done in batch:
                done.set()

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
                    break
                expected = struct.unpack('>I', crc_b)[0]
                actual   = zlib.crc32(hdr_data + key + value) & 0xFFFFFFFF
                if expected != actual:
                    break
                yield OpType(op), key, value

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def truncate(self):
        """Discard all WAL contents.
        MUST only be called after the SSTable + MANIFEST are durable.
        All records in the WAL have already been waited-on by their callers
        (done.wait()), so _gc_pending is empty at this point.
        """
        with self._file_lock:
            self._f.close()
            with open(self.path, 'wb') as f:
                f.flush()
                fsync_fd(f.fileno())
            self._f = open(self.path, 'ab')

    def close(self):
        if self._sync:
            # Signal gc thread to stop and drain remaining pending
            with self._gc_lock:
                self._gc_shutdown = True
                self._gc_cv.notify_all()
            self._gc_thread.join(timeout=2)
            # Flush any stragglers (shouldn't happen normally — callers wait)
            with self._gc_lock:
                remaining = self._gc_pending[:]
                self._gc_pending.clear()
            if remaining:
                with self._file_lock:
                    for data, _ in remaining:
                        self._f.write(data)
                    self._f.flush()
                    fsync_fd(self._f.fileno())
                for _, done in remaining:
                    done.set()
        with self._file_lock:
            self._f.close()

    @property
    def size_bytes(self) -> int:
        return os.path.getsize(self.path) if os.path.exists(self.path) else 0
