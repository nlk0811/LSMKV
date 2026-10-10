"""
Transaction — Optimistic Multi-Key Read-Write Transaction
==========================================================
A Transaction records which keys were read (the "read set") and buffers
writes in a WriteBatch.  On commit(), it verifies that all observed reads
still hold their expected values; if any key was modified since it was read,
commit() returns False and the caller should retry.

This is optimistic concurrency control (OCC), not serializable isolation.
The check-then-write sequence in commit() is NOT atomic — another writer
could theoretically sneak in between the validation loop and the db.write()
call.  For most business logic (counters, queues, rate-limiters) this is
sufficient.  For strict serializability, use db.compare_and_swap() or
db.update() instead.

Example — bank transfer with retry loop:
    MAX_RETRIES = 10
    for _ in range(MAX_RETRIES):
        with db.transaction() as txn:
            bal1 = int(txn.get('account:1') or 0)
            bal2 = int(txn.get('account:2') or 0)
            if bal1 < 100:
                break
            txn.put('account:1', str(bal1 - 100))
            txn.put('account:2', str(bal2 + 100))
        if txn.commit():
            break   # success
        # retry — conflict detected

Example — context manager (auto-commit, manual retry):
    with db.transaction() as txn:
        txn.put('k', txn.get('k') or 'default')
    # txn.commit() is called automatically on __exit__
    # Returns True on success, False on conflict
    # Check txn.committed to see whether the commit succeeded
"""

from .write_batch import WriteBatch


class Transaction:
    """Optimistic multi-key read-write transaction."""

    def __init__(self, db):
        self._db        = db
        self._reads:    dict = {}    # key → value observed
        self._batch:    WriteBatch = WriteBatch()
        self.committed: bool = False  # set by commit()

    # ── reads ─────────────────────────────────────────────────────────────────

    def get(self, key):
        """Read a key and record the observation for conflict detection.

        If the same key is read multiple times within one transaction, only
        the first observed value is tracked (subsequent reads are served from
        the database without re-recording).
        """
        val = self._db.get(key)
        if key not in self._reads:
            self._reads[key] = val
        return val

    def has(self, key) -> bool:
        """Check key existence (records the observation)."""
        return self.get(key) is not None

    # ── writes ────────────────────────────────────────────────────────────────

    def put(self, key, value, ttl_seconds: float = 0) -> 'Transaction':
        """Buffer a put.  Returns self for chaining."""
        if ttl_seconds > 0:
            self._batch.put_ttl(key, value, ttl_seconds)
        else:
            self._batch.put(key, value)
        return self

    def delete(self, key) -> 'Transaction':
        """Buffer a delete.  Returns self for chaining."""
        self._batch.delete(key)
        return self

    # ── commit / rollback ─────────────────────────────────────────────────────

    def commit(self) -> bool:
        """Validate reads and apply writes.

        Returns True on success, False if any observed read is now stale
        (conflict detected — caller should retry the entire transaction).
        """
        for key, expected in self._reads.items():
            if self._db.get(key) != expected:
                self.committed = False
                return False
        if len(self._batch) > 0:
            self._db.write(self._batch)
        self.committed = True
        return True

    def rollback(self):
        """Discard all buffered reads and writes without applying anything."""
        self._reads.clear()
        self._batch  = WriteBatch()
        self.committed = False

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        """Number of buffered write operations."""
        return len(self._batch)

    def __repr__(self) -> str:
        return (f'Transaction(reads={len(self._reads)}, '
                f'writes={len(self._batch)}, '
                f'committed={self.committed})')

    # ── context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> 'Transaction':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self.commit()   # auto-commit on clean exit
        else:
            self.rollback()
        return False   # do not suppress exceptions
