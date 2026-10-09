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
from typing import Iterator, List, Optional, Tuple

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
        """Submit a PUT record and block until durable (backward-compatible)."""
        done = self._submit(self._make_record(OpType.PUT, key, value))
        if done:
            done.wait()

    def log_delete(self, key: bytes):
        done = self._submit(self._make_record(OpType.DELETE, key, b''))
        if done:
            done.wait()

    def log_batch(self, ops):
        """Submit all ops as one combined record and block until durable."""
        done = self._submit_batch(ops)
        if done:
            done.wait()

    # ── non-blocking submit methods (return Event; caller waits outside lock) ───

    def submit_put(self, key: bytes, value: bytes) -> Optional[threading.Event]:
        """Queue a PUT record for group-commit.  Returns an Event the caller must
        wait on (outside any locks) before returning to the user.  Returns None
        when sync_writes=False (no waiting needed).

        This is the key to efficient concurrent group-commit: by releasing the
        write-lock before calling event.wait(), multiple writers can overlap
        their WAL submissions, letting the gc-thread batch them in one fsync
        instead of one fsync per writer.
        """
        return self._submit(self._make_record(OpType.PUT, key, value))

    def submit_delete(self, key: bytes) -> Optional[threading.Event]:
        return self._submit(self._make_record(OpType.DELETE, key, b''))

    def submit_batch(self, ops) -> Optional[threading.Event]:
        return self._submit_batch(ops)

    # ── internals ──────────────────────────────────────────────────────────────

    def _make_record(self, op, key: bytes, value: bytes) -> bytes:
        hdr     = struct.pack(_HDR_FMT, int(op), len(key), len(value), time.time())
        payload = hdr + key + value
        crc     = struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF)
        return payload + crc

    def _submit(self, data: bytes) -> Optional[threading.Event]:
        """Queue data for group commit.  Returns an Event (or None for async mode).
        The caller is responsible for calling event.wait() to ensure durability.
        """
        if not self._sync:
            with self._file_lock:
                self._f.write(data)
                self._f.flush()
            return None
        with self._gc_lock:
            if self._gc_shutdown:
                with self._file_lock:
                    self._f.write(data)
                    self._f.flush()
                    fsync_fd(self._f.fileno())
                done = threading.Event()
                done.set()
                return done
            done = threading.Event()
            self._gc_pending.append((data, done))
            self._gc_cv.notify()
        return done

    def _submit_batch(self, ops) -> Optional[threading.Event]:
        if not ops:
            return None
        combined = b''.join(self._make_record(op, key, value) for op, key, value in ops)
        return self._submit(combined)

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

        Any records still pending in _gc_pending at truncation time have
        already been flushed to an SSTable (the flush that triggered the
        truncation included them).  We signal their done events so callers
        outside the write-lock unblock correctly.
        """
        if self._sync:
            with self._gc_lock:
                pending, self._gc_pending[:] = self._gc_pending[:], []
            for _, done in pending:
                done.set()   # data is in SSTable — caller can safely return
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
