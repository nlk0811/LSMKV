"""
Pipeline — Buffered Write Context Manager
==========================================
A Pipeline collects put/delete operations and applies them all at once in
a single WriteBatch (one WAL fsync) when the context exits or execute() is
called.

The ergonomic benefit over WriteBatch directly:
  - Chainable method calls without constructing a batch manually.
  - Auto-executes on context manager exit (no forget-to-call-write() bugs).
  - Supports pipelines that span multiple code paths before final commit.

Example
-------
    # Auto-commit on exit — all ops in one WAL fsync
    with db.pipeline() as pipe:
        pipe.put('user:1', '{"name":"Alice"}')
        pipe.put('user:2', '{"name":"Bob"}')
        pipe.delete('user:0')

    # Manual execute
    pipe = db.pipeline()
    for row in data:
        pipe.put(row.key, row.value)
    n = pipe.execute()   # n = number of ops applied
    print(f'Applied {n} operations')

    # Chaining
    (db.pipeline()
       .put('k1', 'v1')
       .put_ttl('sess', token, ttl_seconds=3600)
       .delete('old')
       .execute())

Pipelines do NOT support read operations (get, scan, etc.).  For atomic
read-modify-write, use db.update(), db.compare_and_swap(), or db.increment().
"""

from .write_batch import WriteBatch


class Pipeline:
    """Buffered write pipeline that flushes as a single atomic WriteBatch."""

    def __init__(self, db):
        self._db    = db
        self._batch = WriteBatch()

    # ── buffering ─────────────────────────────────────────────────────────────

    def put(self, key, value, ttl_seconds: float = 0) -> 'Pipeline':
        """Buffer a put.  Returns self for chaining."""
        if ttl_seconds > 0:
            self._batch.put_ttl(key, value, ttl_seconds)
        else:
            self._batch.put(key, value)
        return self

    def put_ttl(self, key, value, ttl_seconds: float) -> 'Pipeline':
        """Buffer a put with TTL.  Returns self for chaining."""
        self._batch.put_ttl(key, value, ttl_seconds)
        return self

    def delete(self, key) -> 'Pipeline':
        """Buffer a delete.  Returns self for chaining."""
        self._batch.delete(key)
        return self

    # ── execution ─────────────────────────────────────────────────────────────

    def execute(self) -> int:
        """Apply all buffered operations atomically.  Returns count applied.

        The pipeline is reset after execute() so it can be reused.
        """
        n = len(self._batch)
        if n > 0:
            self._db.write(self._batch)
            self._batch = WriteBatch()
        return n

    def discard(self) -> 'Pipeline':
        """Discard all buffered operations without applying them."""
        self._batch = WriteBatch()
        return self

    def merge_batch(self, batch: WriteBatch) -> 'Pipeline':
        """Merge a pre-built WriteBatch into this pipeline.

        Useful for combining reusable batch builders with an in-flight pipeline:
            pipe = db.pipeline()
            pipe.put('counter', '0')
            pipe.merge_batch(pre_built_user_batch)  # add pre-built ops
            pipe.execute()   # one fsync for everything
        """
        self._batch.merge(batch)
        return self

    def merge(self, other: 'Pipeline') -> 'Pipeline':
        """Merge another Pipeline's pending ops into this one.  Returns self."""
        self._batch.merge(other._batch)
        return self

    # ── inspection ────────────────────────────────────────────────────────────

    @property
    def put_count(self) -> int:
        """Number of staged put operations (including put_ttl)."""
        return self._batch.put_count

    @property
    def delete_count(self) -> int:
        """Number of staged delete operations."""
        return self._batch.delete_count

    def __len__(self) -> int:
        return len(self._batch)

    def __repr__(self) -> str:
        return (f'Pipeline({len(self._batch)} pending ops: '
                f'{self.put_count} puts, {self.delete_count} deletes)')

    # ── context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> 'Pipeline':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self.execute()   # auto-commit on clean exit
        else:
            self.discard()   # discard on exception (don't apply partial work)
        return False   # do not suppress exceptions
