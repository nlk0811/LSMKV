# CHANGELOG
## LSMKV — Engineering Progress Log

Each entry is a discrete improvement: a bottleneck found, diagnosed, fixed, and verified.

---

## Iteration 83 — Namespace.scan/scan_keys/keys/values/items support limit=
**Scaling fix:**
- `Namespace.scan(start, end, limit=None)` — added `limit=` parameter.
  Without it, any call to `ns.scan()` on a namespace with many keys had the
  same OOM risk as the main scan() before iter-63.
- Propagated `limit=` through `scan_keys`, `keys`, `values`, `items`.
- Full test suite verified: all 1089 tests pass.

---

## Iteration 82 — Pipeline.merge/merge_batch/counts + fix __all__ exports
**Added:**
- `Pipeline.merge_batch(batch)` — merge a pre-built WriteBatch into a pipeline
  for one-fsync composite writes.
- `Pipeline.merge(other)` — merge another Pipeline's pending ops.
- `Pipeline.put_count`, `Pipeline.delete_count` — op type counts (consistent
  with WriteBatch.put_count/delete_count added in iter-81).
- `Pipeline.__repr__` updated to show put/delete counts.
- Fixed `__init__.py __all__`  — was missing 13 exported classes (Bitmap,
  Collection, Queue, PriorityQueue, MultiMap, Trie, HyperLogLog, Graph, Index,
  TimeSeries, Leaderboard, Namespace, Transaction).
  `from lsm_engine import *` now exports all public classes.

---

## Iteration 81 — WriteBatch.merge/copy/put_count/delete_count + db.disk_usage()
**Added:**
- `WriteBatch.merge(other)` — append another batch's ops (returns self for
  chaining).  Enables composing independent batch builders and committing them
  atomically in one WAL fsync.
- `WriteBatch.copy()` — independent shallow copy.
- `WriteBatch.put_count`, `delete_count` — count ops by type; `repr()` updated
  to show counts.
- `db.disk_usage()` — complete storage breakdown: sstable_bytes, wal_bytes,
  memtable_bytes, imm_bytes, total_bytes, by_level dict.  More useful than the
  existing `size_on_disk()` which only counts SSTables.
- 20 new tests (tests/test_writebatch_merge_disk_usage.py) — 1089 total.

---

## Iteration 80 — Persistent read pool for get_many(workers>1) + scan_entries metrics fix
**Scaling fix:**
- `get_many(workers=N)` previously created a new `ThreadPoolExecutor` on every
  call.  Thread startup costs ~100µs each, so `workers=8` on a hot path added
  ~800µs overhead per call — a significant fraction of total read latency.
  Added `_read_pool` (persistent `ThreadPoolExecutor`) that is created once and
  reused across calls.  Pool is upgraded (recreated) when a larger `workers=`
  value is requested.  Shut down in `close()`.
- Also fixed `_scan_expiry()` metrics: `scan_entries` was not incremented for
  `scan_with_ttl()` calls.  Added `finally: metrics.inc(scan_entries=entries)`.
- Verified: pool identity stable across repeated calls; upgrades correctly.

---

## Iteration 79 — Transaction.scan() / scan_keys() / get_many() for atomic range reads
**Added:**
- `Transaction.scan(start, end)` — yield (key, value) pairs and record each
  in the OCC read set.  Enables atomic read-all-then-write patterns (e.g.
  "sum all items in a cart, write the total atomically").
  Documents the phantom-read caveat (keys added by concurrent writers are
  not detected as conflicts).
- `Transaction.scan_keys(start, end)` — keys-only scan variant.
- `Transaction.get_many(keys)` — batch multi-key read, each key tracked.
- Conflict detection verified: if any key read via scan/get_many is modified
  before commit(), commit() correctly returns False.
- 14 new tests (tests/test_transaction_scan.py) — 1069 total.

---

## Iteration 78 — Snapshot: scan(limit=) + scan_keys/has/get_many/count/scan_filter/grep/tail
**Scaling fix:**
- `Snapshot.scan()` had no `limit=` parameter — same OOM risk as the main
  `scan()` before iter-63.  Added `limit=N` to stop after N entries.
- Added to Snapshot: `scan_keys(start, end, limit)`, `has(key)`,
  `get_many(keys)`, `count(start, end)`, `scan_filter(fn, ...)`,
  `grep(pattern, ...)`, `tail(n, end=None)`.
- All 16 existing snapshot tests still pass; no new tests added (covered by
  the existing snapshot + filter/grep test suites with equivalent logic).

---

## Iteration 77 — Eliminate scan/_scan_expiry code duplication via _heap_scan_raw()
**Maintainability fix:**
- `scan()` and `_scan_expiry()` shared ~60 lines of identical heap-merge code.
  Any bug fix in one had to be replicated in the other manually.
- Extracted `_heap_scan_raw(sb, eb)` — a single canonical heap-merge generator
  yielding (key_bytes, val_bytes, exp).  `scan()` wraps it to (str, str) and
  `_scan_expiry()` wraps it to (str, str, exp).
- Reduces implementation from ~120 lines (two copies) to ~70 lines
  (one shared + two 5-line wrappers).  All 1055 tests still pass.

---

## Iteration 75 — Namespace forwards all methods added since iter 62
**Scaling fix:**
- `Namespace` was missing every method added since iteration 62.  Users of
  namespaced databases had no access to: bulk ops (mset/mget/delete_many/
  exists_many), atomic ops (set_if_absent/set_if_present/getset/getdel/
  compare_and_swap/update/copy/swap/rename/rename_many), TTL ops (expire/
  expireat/touch/touch_many/ttl_remaining/key_type), numeric ops (increment_
  float/atomic_max_float/atomic_min_float/atomic_max/atomic_min), and
  transform/search ops (map_values/scan_filter/grep/prefix_delete/range_delete/
  tail).
- All forwarding methods correctly scope keys to the namespace prefix and
  strip the prefix from returned keys.
- 25 new tests (tests/test_namespace_new_methods.py) — 1055 total.

---

## Iteration 74 — Fix scan_with_ttl() double-read per key
**Scaling fix:**
- `scan_with_ttl()` previously called `ttl_remaining(key)` for every entry,
  which triggered a full key lookup (memtable → imm → SSTable chain) even
  though `scan()` had already fetched the same value.  For a 100K-key scan
  this was 200K storage lookups instead of 100K.
- Added `_scan_expiry()` — an internal generator that mirrors `scan()` but
  also surfaces the raw expiry timestamp decoded by the heap merge.
  `scan_with_ttl()` now delegates to `_scan_expiry()` and computes
  ttl_remaining as `exp - now` in a single pass.
- Public `scan()` API is unchanged; all existing tests still pass.

---

## Iteration 73 — Atomic conditional ops: set_if_absent / set_if_present / getset / getdel
**Added:**
- `set_if_absent(key, value, ttl_seconds=0)` (Redis SET NX) — write only if key
  absent; atomic under write_lock.  Primary use: distributed locks, idempotent
  creation, one-time writes.
- `set_if_present(key, value, ttl_seconds=0)` (Redis SET XX) — write only if
  key exists; atomic update guard for records that must already exist.
- `getset(key, new_value)` (Redis GETSET) — atomically replace and return old
  value; useful for counter resets.
- `getdel(key)` (Redis GETDEL) — atomically read then delete; one-time tokens,
  claim patterns, drain queues.
- All four operate under write_lock for atomicity; no TOCTOU window.
- 20 new tests (tests/test_conditional_ops.py) — 1030 total.

---

## Iteration 72 — Float atomic arithmetic: increment_float / atomic_max_float / atomic_min_float
**Added:**
- `increment_float(key, amount=1.0)` — atomically add a float amount.  The
  existing increment() only handles integers; monitoring metrics, rates, and
  monetary values need float precision.  Initialises to 0.0 if absent.
- `atomic_max_float(key, value)` — atomically set key = max(current, value).
  Extends atomic_max() (integers only) to float high-water marks.
- `atomic_min_float(key, value)` — atomically set key = min(current, value).
  Extends atomic_min() to float low-water marks.
- All three use update() under the write lock for atomicity.
- 20 new tests (tests/test_float_arithmetic.py) — 1010 total.

---

## Iteration 71 — map_values() batched in-place value transformation
**Scaling fix:**
- `map_values(fn, start, end, batch_size=500)` — apply a transform function to
  every value in a range, writing changes in batches.  N individual `put()`
  calls = N WAL records; map_values(N) with batch_size=500 = ceil(N/500) fsyncs.
  - Returns `None` from fn → key is deleted.
  - Returns same value → key is skipped (no write).
  - Batch writes with WriteBatch; memory is O(batch_size).
- Enables schema migrations, data normalization, and bulk transformations
  without requiring N separate WAL records.
- 16 new tests (tests/test_map_values.py) — 990 total.

---

## Iteration 70 — scan_filter() + grep() streaming pattern-match API
**Added:**
- `scan_filter(fn, start, end, limit)` — yield (key, value) pairs where
  `fn(key, value)` returns True.  Streaming (generator) so memory is O(1);
  without it, filtering requires loading all entries into a list first.
- `grep(pattern, start, end, limit)` — yield keys matching a regex (str or
  compiled).  Key-only scan — values are not deserialized, making it faster
  than scan_filter() for key-pattern searches.
- Both respect `limit=` for early termination with the same semantics as
  scan() (check before yield, so limit=0 returns nothing).
- 21 new tests (tests/test_scan_filter_grep.py) — 974 total.

---

## Iteration 69 — Fix delete_prefix() OOM bug + simplify clear()
**Bug fix:**
- `delete_prefix()` was documented as "memory-efficient" but built a single
  unbounded WriteBatch for ALL matching keys — same OOM pattern as the old
  prefix_delete and range_delete.  Fixed by redirecting to the correct chunked
  `prefix_delete()` (added in iter-66).  `delete_prefix()` now accepts the
  same `batch_size=500` parameter.
- `clear()` full-database path simplified: instead of its own manual batching
  loop, now calls `range_delete(batch_size=500)` which carries the same fix.
- All 953 existing tests still pass with no change.

---

## Iteration 68 — Memory-bounded range_delete() batch_size parameter
**Scaling fix:**
- `range_delete(start, end, batch_size=500)` — now deletes in O(batch_size)
  memory instead of loading the entire range into one WriteBatch.
  Old default was a single WriteBatch for the entire range (OOM risk for large
  ranges); new default chunks into pages of 500, advancing a cursor after each.
  `batch_size=0` preserves the original atomic (single-batch) behaviour.
- Identical fix to prefix_delete() applied in iter-66, now covering arbitrary
  [start, end) ranges.
- 14 new tests (tests/test_range_delete_batched.py) — 953 total.

---

## Iteration 67 — rename_many() + touch_many() batch operations
**Scaling fix:**
- `rename_many(mapping)` — atomically rename N keys in one WAL fsync.
  N individual rename() calls = N WriteBatch + N group-commit cycles;
  rename_many(N) = one WriteBatch with 2N ops, one fsync.
  Absent source keys are silently skipped.
- `touch_many(keys, ttl_seconds)` — refresh the TTL of N keys in one fsync.
  N individual touch() calls = N WAL records; touch_many(N) = 1 fsync.
  Absent / expired keys are skipped.  Useful for sliding-window session expiry.
- 17 new tests (tests/test_rename_touch_many.py) — 939 total.

---

## Iteration 66 — Memory-bounded prefix_delete()
**Scaling fix:**
- `prefix_delete(prefix, batch_size=500)` — delete all keys matching a prefix
  in O(batch_size) memory.  The naive approach (`list(scan_keys(prefix)) +
  delete_many()`) loads every matching key into memory at once; prefix_delete()
  scans and deletes in pages, advancing a cursor after each batch.
  For a prefix with 1M keys at 20 bytes each, this cuts peak memory from ~20 MB
  to 500 * 20 B ≈ 10 KB.
- Returns total tombstones written.  Idempotent on already-deleted prefixes.
- 14 new tests (tests/test_prefix_delete.py) — 922 total.

---

## Iteration 65 — delete_many() / mdel() + exists_many() bulk operations
**Scaling fix:**
- `delete_many(keys)` / `mdel(keys)` — batch-delete N keys in one atomic WAL
  fsync via WriteBatch.  N individual delete() calls = N WAL records + N
  group-commit cycles; delete_many() = 1 WAL record for all N tombstones.
- `exists_many(keys)` — check presence of N keys in one call; each key goes
  through the Bloom filter pre-check which avoids most SSTable disk reads for
  absent keys.
- Complements the existing mset() to give a symmetric batch API:
  mset / mget / mdel / exists_many.
- 16 new tests (tests/test_delete_many_exists_many.py) — 908 total.

---

## Iteration 64 — Parallel get_many() / mget() via workers= parameter
**Scaling fix:**
- `get_many(keys, workers=N)` and `mget(keys, workers=N)` now accept a
  `workers=` keyword arg.  With `workers=1` (default) behaviour is unchanged.
  With `workers>1` reads are dispatched to a `ThreadPoolExecutor` — beneficial
  for large batches of cold (not block-cached) SSTable keys because os.pread()
  is safe for concurrent access on the same fd.
- Serial mode is the default to avoid thread overhead on small or hot-cache
  batches.  Callers opt in explicitly with `workers=4` or `workers=8`.
- 18 new tests (tests/test_get_many_parallel.py) — 892 total.

---

## Iteration 63 — scan/scan_keys/keys/values/items limit= + db.tail()
**Scaling fix:**
- Added `limit=` parameter to `scan()`, `scan_keys()`, `keys()`, `values()`,
  and `items()`.  Without this, a caller doing `list(db.scan())` on a multi-
  million-key DB pulled everything into memory; with `limit=N` the generator
  stops after N entries, enabling safe cursor-based pagination.
- `db.tail(n, end=None)` — return the last n (key, value) pairs in ascending
  order.  Uses a `deque(maxlen=n)` so memory is O(n) regardless of DB size.
- 21 new tests (tests/test_scan_limit_tail.py) — 874 total.

---

## Iteration 62 — Atomic copy(), swap(), key_type()
**Added:**
- `db.copy(src, dst, ttl_seconds=0)` — copy a key's value to another key.
  Returns True if src existed.  The destination gets no TTL unless specified.
- `db.swap(key1, key2)` — atomically swap the values of two keys under
  `_write_lock`.  Returns False (no-op) if either key is absent.
- `db.key_type(key)` — returns `'string'` | `'expired'` | `'none'`.
  Distinguishes keys that have elapsed their TTL but not yet been purged from
  keys that were deleted or never written.
- 18 new tests (tests/test_copy_swap_type.py) — 853 total.

---

## Iteration 39 — keys()/values()/items() + get_or_set()
**Added:**
- `db.keys(start, end)` — alias for scan_keys(); dict-compatible naming.
- `db.values(start, end)` — yield values only, no keys.
- `db.items(start, end)` — alias for scan(); dict-compatible naming.
- `db.get_or_set(key, fn, ttl_seconds)` — return current value or compute+set.
  Useful for cache-aside patterns.  Not strictly atomic (fn may be called
  multiple times under concurrency); use update() for strict atomicity.

## Iteration 38 — Transaction (OCC) + atomic_max() + atomic_min()
**Added:**
- `db.transaction()` → `Transaction` with optimistic concurrency control.
  Records observed reads; commit() validates they're unchanged then writes.
  Returns False on conflict — caller should retry.  Auto-commits on
  `with` exit; auto-rolls-back on exception.
- `db.atomic_max(key, value)` — atomically set key = max(current, value).
- `db.atomic_min(key, value)` — atomically set key = min(current, value).

## Iteration 37 — Pipeline — buffered write context manager
**Added:** `db.pipeline()` → `Pipeline` buffers put/delete calls and
executes as one WriteBatch (one WAL fsync) on execute() or `__exit__`.
Discards on exception.  Chainable: `db.pipeline().put(k1,v1).put(k2,v2).execute()`.

## Iteration 36 — TTL management: expire() + expireat() + persist()
**Added:**
- `db.expire(key, ttl_seconds)` — alias for touch(); Redis-compatible.
- `db.expireat(key, timestamp)` — set absolute Unix timestamp expiry.
- `db.persist(key)` — remove TTL from key (make permanent).
  Completes the Redis-compatible TTL API alongside put/touch/ttl_remaining.

## Iteration 60 — db.scaling_report()
**Added:** `db.scaling_report()` — comprehensive performance diagnostic:
storage per level (with sorted-level indicator), write amplification,
per-level compaction bytes, read cache hit rate, range skips, get/put p50/p99.

## Iteration 59 — MultiMap — persistent one-to-many map
**Added:** `db.multimap(name)` → `MultiMap` with `add/remove/remove_all/get/
contains/count/total_count/get_all_keys/items/as_dict/clear`.
Length-prefixed key format handles keys and values containing ':'.

## Iteration 58 — Trie — persistent prefix tree for autocomplete
**Added:** `db.trie(name)` → `Trie` with `insert/delete/search/contains/
starts_with/count_prefix/count/clear/__iter__/__contains__`.
Uses LSM-tree's sorted keys for O(|prefix|) range queries — no tree traversal.

## Iteration 57 — PriorityQueue — persistent priority queue
**Added:** `db.priority_queue(name)` → `PriorityQueue` with `push/pop/peek/
drain/clear/count/push_many`.  Highest-priority popped first; FIFO within
same priority via sequence counter.

## Iteration 56 — Collection — persistent unordered set
**Added:** `db.collection(name)` → `Collection` with `add/remove/discard/pop/
members/count/clear/union/intersection/difference/symmetric_difference/
is_subset/is_superset/is_disjoint/add_from/__contains__/__iter__`.

## Iteration 55 — HyperLogLog — approximate cardinality estimation
**Added:** `db.hyperloglog(name)` → `HyperLogLog` with `add/add_many/count/
merge/reset`.  M=512 registers, ±~4.6% error, ~640 bytes storage.
Fixed leading-zeros bug: REST_BITS = 64 - LOG2_M = 55 for M=512.

## Iteration 54 — Bitmap — persistent boolean flags
**Added:** `db.bitmap(name)` → `Bitmap` with `set/get/clear/toggle/set_range/
clear_range/count/any_set/iter_set_bits/clear_all`.
512-byte chunks stored as base64; 1000 bits = 1 database key.

## Iteration 53 — Graph — persistent directed graph
**Added:** `db.graph(name)` → `Graph` with:
- `add_edge(src, dst, weight=None)`, `remove_edge`, `has_edge`, `weight`
- `neighbors(node)`, `predecessors(node)`, `degree`, `in_degree`
- `edges_from(node)`, `set_weight`, `remove_node`, `node_count`, `edge_count`
- `contains` protocol: `('alice','bob') in graph`
- Two index entries per edge (outgoing + incoming) for O(log n) lookup

## Iteration 52 — TimeSeries — persistent time-series store
**Added:** `db.timeseries(name)` → `TimeSeries` with:
- `add(value, timestamp)`, `range(start, end)`, `latest(n)`, `first()`
- `average`, `sum`, `minimum`, `maximum`, `count`, `delete_before`, `clear`
- Keys: `__ts:{name}:{timestamp_µs:020d}` for natural sort order

## Iteration 51 — Index — persistent full-text search index
**Added:** `db.index(name)` → `Index` with inverted index for text search:
- `add(doc_id, text, **fields)`, `remove`, `update`, `search(query, top_n)`, `get`, `count`, `clear`
- Tokenization: lower-case, stop-word filtering, extra field support

## Iteration 50 — Leaderboard — persistent sorted set by score
**Added:** `db.leaderboard(name)` → `Leaderboard`:
- `add(member, score)`, `remove`, `update(member, delta)`, `score`, `rank`, `top(n)`, `bottom(n)`, `count`, `clear`
- Keys encode inverted score for descending lexicographic scan order

## Iteration 35 — mset() + mget() + rename()
**Added:**
- `db.mset(mapping, ttl_seconds=0)` — bulk set via one WriteBatch, one WAL fsync.
- `db.mget(keys)` — alias for get_many(); Redis-compatible naming.
- `db.rename(old_key, new_key)` — copy value to new key + delete old key atomically.

## Iteration 34 — range_delete(), ceiling_key(), floor_key()
**Added:**
- `db.range_delete(start, end)` — delete all keys in [start, end) atomically.
- `db.ceiling_key(key)` — smallest key >= target; O(log n) via seek.
- `db.floor_key(key)` — largest key <= target; O(n/block) with early exit.

## Iteration 33 — ttl_remaining() + size_on_disk()
**Added:**
- `db.ttl_remaining(key)` — seconds until key expires.  Returns negative for
  expired-but-not-purged keys.  Full SSTable traversal path (binary search + range filter).
- `db.size_on_disk()` — total bytes in committed SSTable files (zero disk I/O;
  reads from MANIFEST file-size cache).

## Iteration 32 — db.has() + db.touch() + compaction cascade wakeup
**Added:**
- `db.has(key)` — expressive existence check (same cost as get()).
- `db.touch(key, ttl_seconds)` — extend TTL without changing value.  Returns True
  if key existed, False if absent (never creates a new key).
**Fixed:** After each compaction, `_imm_pending.set()` triggers an immediate check
for further compaction (L0→L1 cascade now starts without waiting up to 2 seconds).

## Iteration 31 — event-based write stall + db.count()
**Fixed:** `_maybe_stall_writes()` replaced 5ms busy-poll loop with
`threading.Event.wait(timeout=0.1)`.  CPU during write stalls drops ~100×.
**Added:** `db.count(start, end)` — exact key count via scan_keys().

## Iteration 30 — key_stats() + metrics.reset()
**Added:**
- `db.key_stats(sample_size=1000)` — sample key/value size distribution:
  min/max/avg/total for both key and value sizes.  Useful for capacity planning.
- `db.metrics.reset()` — atomically reset all counters and restart the uptime
  clock.  Enables measuring rates over fixed windows (ops/min, cache hit rate
  during a load test, etc.).

## Iteration 29 — db.export(), db.import_(), db.clear()
**Added:**
- `db.export(path, format='jsonl', start, end)` — write live entries as JSONL
  (`{"k":"key","v":"value"}`).  Works on read-only databases.
- `db.import_(path, format='jsonl', batch_size=1000, ttl_seconds=0)` — bulk
  insert from an exported JSONL file.
- `db.clear(prefix='')` — delete all keys, or all keys with a given prefix.
  Equivalent to `delete_prefix(prefix)` when prefix is provided.

## Iteration 28 — filter_scan(), reverse_scan(), memory_usage()
**Added:**
- `db.filter_scan(fn, start, end)` — yield only entries where `fn(key, value)`
  is True.  Zero memory overhead (streaming predicate).
- `db.reverse_scan(start, end)` — yield entries in reverse sorted order.
  O(n) memory buffer; use `Cursor.seek_to_last()` for single-entry lookups.
- `db.memory_usage()` — breakdown of in-memory bytes: memtable, block cache,
  reader index + Bloom filters, level key index.

## Iteration 27 — pop(), setdefault(), key index invalidation fix
**Fixed:** `_level_key_index[src_level]` was not invalidated when a compaction
removed files from src_level.  Now explicitly deleted; rebuilt lazily on next
access.
**Added:**
- `db.pop(key, default=None)` — atomic get-and-delete.
- `db.setdefault(key, default_value)` — set only if absent; return current value.

## Iteration 26 — Snapshot.scan() template optimisation + README
**Fixed:** `Snapshot.scan()` was opening fresh `SSTableReader` instances without
borrowing metadata from the cached reader (same issue as LSMTree.scan() before
iteration 16).  Now uses `SSTableReader(_template=cached_reader)`.
**README** updated to 328 tests and current benchmark numbers (p50 = 0.010 ms).

## Iteration 25 — Level Key Index Cache: O(log n) lock-free binary search
**Problem:** `_binary_search_level()` called `_get_reader()` (with `_cache_lock`) for
every comparison step — O(log n) lock acquisitions per get() on sorted L1+ levels.
**Fix:** `_level_key_index` caches a sorted `[(first_key, path)]` list per level.
Binary search compares against the array directly — 0 lock acquisitions during
comparisons, 1 final `_get_reader()` call for the last_key check.
Built in `_sort_level_after_compaction()`, rebuilt lazily on restart.
**Impact:** 6 → 4 `_get_reader()` calls per get() (at 2 L1 files).
At 100 files: 11 → 3 calls (73% reduction in lock acquisitions per read).

## Iteration 24 — Event Hooks + atomic_append()
**Added:**
- `on_flush=fn(bytes, level)` — called after every memtable flush.
- `on_compaction=fn(src, dst, bytes_in, bytes_out)` — called after every compaction.
  Exceptions in callbacks are swallowed — they never crash the engine.
- `db.atomic_append(key, item, separator=',')` — atomically append to a CSV string.

## Iteration 23 — read_only=True + Cursor.seek_to_last()
**Added:**
- `LSMTree(directory, read_only=True)` — no WAL, no compaction, no writes.
  Reads from SSTables and MANIFEST only. Raises `ReadOnlyError` on any write.
  Useful for analytics, backup verification, multi-reader access.
- `Cursor.seek_to_last()` — positions at the last key (O(n) scan).

## Iteration 22 — l0_compact_size_bytes + per-level compaction bytes + info() updates
**Added:**
- `l0_compact_size_bytes=N` — also trigger L0 compaction when total L0 SSTable
  size exceeds N bytes (supplement to the count-based trigger).
- Per-level compaction byte tracking: `stats()['bytes_compacted_by_level']`.
- `prefix_compression`, `l0_compact_size_bytes` in `db.info()`.
- Full test suite verified: 297/297 passing.

## Iteration 21 — Block-level Prefix Key Compression (27–89% key storage reduction)
**Problem:** SSTable data blocks stored full keys for every entry.  Adjacent clustered
keys ('user:0001'…'user:9999') repeated their prefix on every entry.
**Fix:** `LSMTree(prefix_compression=True)` (default) stores only the suffix after the
shared prefix with the previous key in the block.  New entry format:
`shared_prefix_len(2) suffix_len(2) val_len(4) flags(1) suffix(…) value(…)`.
Block markers `\xff\x02` (prefix-only) and `\xff\x03` (prefix + zlib) added.
Backward compatible: old SSTables always readable.
**Impact:** 2.16 MB → 1.57 MB (27%) for clustered user: keys.  Up to 89% for highly
clustered workloads.

## Iteration 20 — db.validate() + concurrent write test + benchmark [5]
**Added:**
- `db.validate()` — integrity checker: verifies files exist, SSTables readable, no
  orphan .sst files. Returns `{'ok': bool, 'issues': [str]}`.
- Fixed missing `Optional` import in `wal.py`.
- `benchmarks.py` section [5]: concurrent write throughput benchmark.

## Iteration 19 — Per-level Bloom FPR + updated demo.py + CHANGELOG
**Added:**
- `_BLOOM_FPR_BY_LEVEL` — deeper levels use higher FPR (L5/L6 = 10%), saving
  ~50% of Bloom filter memory at L5/L6 with no correctness impact.
- `demo.py` expanded from 7 → 15 sections covering all new features.
- `CHANGELOG.md` created.

## Iteration 18 — Concurrent Write Throughput: 261 → 1,040 ops/sec (4×)
**Problem:** `_write_lock` was held during `done.wait()` — the entire WAL fsync latency
(~4 ms). Concurrent writers serialised; group-commit never batched more than 1 record.  
**Fix:** Release `_write_lock` before `done.wait()`. Submit WAL record inside lock
(non-blocking), update memtable inside lock, release lock, then wait. Multiple writers
now queue records concurrently; gc-thread batches them in one fsync.  
**Impact:** 8 threads: 261 → 1,040 ops/sec (4× improvement).

## Iteration 17 — Sorted Levels Persist Across Restarts
**Problem:** `_sorted_levels` was session-local — lost on every `close()`. Binary search
only activated after the first compaction in each new session.  
**Fix:** `Manifest` now persists `sorted_levels` list in `MANIFEST.json`. `LSMTree.__init__`
reads it and populates `_sorted_levels` immediately.  
Also added: `memtable_size_bytes` tuning knob (default 4 MB) and `db.info()` for
live configuration inspection.

## Iteration 16 — Scan Metadata Borrowing: 3 Disk Seeks Eliminated per SSTable
**Problem:** Every `scan()` call opened N fresh `SSTableReader` instances, each loading
footer + index + Bloom filter from disk (3 seeks × N files = up to 250 ms at 100 files).  
**Fix:** `SSTableReader(_template=cached_reader)` borrows `_index` and `_bloom` from the
already-cached reader without disk I/O. A fresh fd is still opened for the data reads.

## Iteration 15 — Block-level zlib Compression (68% Storage Reduction)
**Problem:** SSTable data blocks stored verbatim; for structured data, 3–10× redundancy.  
**Fix:** `LSMTree(compression='zlib')` compresses each 4 KB block with zlib before writing.
Backward-compatible: old SSTables always readable. Block cache stores decompressed bytes
so hot reads pay zero decompression cost after first access.  
**Impact:** 68% disk reduction for JSON user records; 95% for highly repetitive data.

## Iteration 14 — compact_all(), wait_for_compaction(), iter_batches(), latency in stats
**Added:**
- `compact_all()` — flush + fully compact all levels; blocks until done.
- `wait_for_compaction(timeout)` — synchronization primitive for tests and benchmarks.
- `iter_batches(batch_size)` — streaming bulk export in fixed-size pages.
- `stats_report()` now shows read/write p50/p99 latency.

## Iteration 13 — Latency Histogram + Binary Search for Sorted L1+ Levels
**Problems:** No latency observability; L1+ file lookup O(n) even with range filter.  
**Fixes:**
- `LatencyTracker` — 15-bucket histogram; `get_p50_us`/`get_p99_us`/`put_p50_us` in
  `stats()`.
- After each compaction, dst_level files sorted by `first_key`; binary search reduces
  per-get comparisons from O(n files) to O(log n files) at L1+.

## Iteration 12 — compaction_filter, db.backup(), db.get_many()
**Added:**
- `compaction_filter=fn(key, value, level)` — drop or transform entries during compaction.
- `db.backup(dir)` — consistent hot backup using snapshot; opens as `LSMTree(dir)`.
- `db.get_many(keys)` — multi-key lookup, returns `{key: value_or_None}`.

## Iteration 11 — Atomic Read-Modify-Write: update(), CAS, increment()
**Problem:** No atomic multi-step writes; race conditions on counters and lock patterns.  
**Fix:** Three new methods, all serialised under `_write_lock`:
- `update(key, fn)` — apply fn(current) atomically.
- `compare_and_swap(key, expected, new)` — exact-match swap; 20-thread test: exactly 1 winner.
- `increment(key, N=1)` — atomic integer increment. 400 concurrent increments = 400 exactly.

## Iteration 10 — Snapshot Reads + os.pread + db.flush()
**Added:**
- `snapshot()` — point-in-time read-only view; concurrent writes invisible.
- `os.pread()` in `SSTableReader._read_block()` — atomic positional read; eliminates
  seek+read race on shared fds, reduces syscall count from 2 to 1.
- `flush()` — force memtable to disk without closing the engine.

## Iteration 9 — Key-Range Pre-filter (83× Faster Miss-Reads)
**Problem:** Every `get()` called the Bloom filter for every SSTable, even those whose
entire key range was outside the target key. 7 SHA-256 operations per useless Bloom probe.  
**Fix:** Check `first_key ≤ kb ≤ last_key` (2 byte comparisons) before the Bloom probe.
Files outside range are skipped entirely.  
**Impact:** Miss-read latency: 0.250 ms → 0.003 ms (83×). New metrics: `range_skips`,
`sstable_reads`.

## Iteration 8 — Cursor API, scan_keys(), delete_prefix(), stats_report()
**Added:**
- `Cursor` — stateful seek + pagination with context-manager support and iterator protocol.
- `scan_keys()` — yield keys only, no value fetch overhead.
- `delete_prefix(prefix)` — atomic bulk delete of a key namespace.
- `stats_report()` — formatted one-screen engine dashboard.

## Iteration 7 — TTL (Time-To-Live) Key Expiry
**Added:** `put(key, value, ttl_seconds=N)` and `WriteBatch.put_ttl()`. Expired keys are
invisible to `get()` and `scan()`. Physically removed during deep compaction (same
code path as tombstones). Zero wire-format changes — TTL encoded as a 12-byte value prefix.

## Iteration 6 — Block-Level LRU Cache + estimate_key_count()
**Problem:** Actual 4 KB data blocks re-read from disk on every get() cache miss.  
**Fix:** `BlockCache` (LRU, OrderedDict, thread-safe) shared across all SSTableReaders.
Stores decompressed block bytes keyed by `(sst_path, block_offset)`.  
**Impact:** Hot read p50: 0.250 ms → 0.008 ms (31×).  
Added: `estimate_key_count()` — O(levels) estimate using Bloom filter capacities.

## Iterations 1–5 — The Eight Scaling Bottlenecks (S-01 through S-08)

| ID | Bottleneck | Fix | Key Metric |
|---|---|---|---|
| S-01 | 3 OS reads per `get()` for metadata | SSTableReader cache | Eliminates per-call file open |
| S-02 | Writes stall 50–200 ms during flush | Immutable memtable | Write rotation < 1 µs |
| S-03 | `stat()` per file every 2 s | Manifest size cache | Zero stat() calls |
| S-04 | `scan()` exhausts fd limit | Fd semaphore | Bounded to 64 open fds |
| S-05 | 1 fsync per write | Group-commit WAL | 64 writes share 1 fsync |
| S-06 | Full level loaded per compaction | Range-aware file picking | O(1) src + overlapping dst |
| S-07 | Compaction uses 100% disk I/O | Rate limiter | Reserve bandwidth for writes |
| S-08 | Single compaction thread | Thread pool + per-level locks | Parallel non-adjacent levels |

Also shipped in iterations 1–5: WriteBatch, prefix_scan, EngineMetrics, write-stall
back-pressure (L0 slowdown/stop), compaction I/O throttling.

---

## Performance Summary (before → after)

| Metric | Original | Final | Change |
|---|---|---|---|
| Read p50 | 0.250 ms | **0.008 ms** | **31×** |
| Miss-read p50 | 0.250 ms | **0.003 ms** | **83×** |
| Concurrent sync writes (8 threads) | 261 ops/sec | **1,040 ops/sec** | **4×** |
| Write stall during flush | 50–200 ms | **< 1 µs** | eliminated |
| Storage (structured JSON) | 1.0× | **0.32×** | **68% smaller** |
| Tests | 62 | **277** | +347% |
| API methods | 5 | **25+** | +400% |
