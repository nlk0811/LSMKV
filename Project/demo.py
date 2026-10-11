"""
LSMKV Demo
===========
A complete walkthrough of every major API call and feature.

Run:
    cd Project
    python demo.py
"""

import os, shutil, sys, time
sys.path.insert(0, os.path.dirname(__file__))

from lsm_engine import LSMTree, WriteBatch, Cursor

DB = '/tmp/lsmkv_demo'

def sep(title=''):
    print(f'\n{"─" * 58}')
    if title:
        print(f'  {title}')
        print(f'{"─" * 58}')


if __name__ == '__main__':
    if os.path.exists(DB):
        shutil.rmtree(DB)

    db = LSMTree(DB, sync_writes=True)

    # ── 1. Basic operations ──────────────────────────────────────────────────
    sep('1. Put / Get / Delete')
    db.put('name',  'LSMKV')
    db.put('lang',  'Python')
    db.put('topic', 'Storage Engines')
    print(f'  name  → {db.get("name")}')
    print(f'  lang  → {db.get("lang")}')
    print(f'  missing → {db.get("missing")}')

    db.put('lang', 'Python 3')
    print(f'  lang (overwrite) → {db.get("lang")}')

    db.delete('topic')
    print(f'  topic (deleted)  → {db.get("topic")}')

    # ── 2. TTL — automatic key expiry ────────────────────────────────────────
    sep('2. TTL — keys expire automatically')
    db.put('session', 'user-token-abc', ttl_seconds=0.15)
    print(f'  session (before expiry): {db.get("session")}')
    time.sleep(0.2)
    print(f'  session (after expiry):  {db.get("session")}  ← None')

    # ── 3. WriteBatch — atomic multi-key writes ──────────────────────────────
    sep('3. WriteBatch — one WAL fsync for N operations')
    batch = WriteBatch()
    (batch
     .put('order:001', '{"item":"book","qty":1}')
     .put('order:002', '{"item":"pen","qty":3}')
     .put_ttl('promo:spring', 'SAVE20', ttl_seconds=3600)
     .delete('order:000'))
    db.write(batch)
    print(f'  order:001 → {db.get("order:001")}')
    print(f'  order:002 → {db.get("order:002")}')
    print(f'  promo:spring → {db.get("promo:spring")}')

    # ── 4. Range and prefix scans ────────────────────────────────────────────
    sep('4. Range scan and prefix scan')
    for c in 'abcdefghij':
        db.put(f'letter_{c}', c.upper())
    results = list(db.scan('letter_c', 'letter_g'))
    print(f'  scan(letter_c → letter_g): {[k for k,_ in results]}')

    for ns in ['user:alice', 'user:bob', 'user:carol', 'order:1']:
        db.put(ns, 'data')
    user_keys = [k for k, _ in db.prefix_scan('user:')]
    print(f'  prefix_scan(user:): {user_keys}')

    # ── 5. Cursor — stateful seek + pagination ───────────────────────────────
    sep('5. Cursor — seek + pagination')
    with db.cursor() as cur:
        cur.seek('letter_d')
        page = []
        while cur.valid() and len(page) < 4:
            page.append(f'{cur.key()}→{cur.value()}')
            cur.next()
    print(f'  page from letter_d: {page}')

    # ── 6. Atomic operations ─────────────────────────────────────────────────
    sep('6. Atomic ops — update / CAS / increment')
    db.put('counter', '0')
    for _ in range(5):
        db.increment('counter')
    print(f'  counter after 5 increments: {db.get("counter")}')

    db.put('status', 'pending')
    swapped = db.compare_and_swap('status', 'pending', 'active')
    print(f'  CAS pending→active: {swapped}  value: {db.get("status")}')
    failed  = db.compare_and_swap('status', 'pending', 'closed')
    print(f'  CAS pending→closed (should fail): {failed}')

    db.update('name', lambda v: v.upper() if v else 'UNKNOWN')
    print(f'  update(name, upper): {db.get("name")}')

    # ── 7. Bulk load + stats ──────────────────────────────────────────────────
    sep('7. Bulk load (5 000 keys) + stats')
    for i in range(5_000):
        db.put(f'bulk_{i:06d}', f'value_{i}')
    print(f'  bulk_000042 → {db.get("bulk_000042")}')
    print(f'  bulk_004999 → {db.get("bulk_004999")}')

    s = db.stats()
    print(f'  memtable  {s["memtable_bytes"]:,} B  /  {s["memtable_keys"]:,} keys')
    for lvl, info in s['levels'].items():
        print(f'  {lvl}  {info["files"]} file(s)  {info["bytes"]:,} B')

    # ── 8. iter_batches — streaming bulk export ───────────────────────────────
    sep('8. iter_batches — streaming export')
    total = sum(len(b) for b in db.iter_batches(batch_size=500, start='bulk_', end='bulk_z'))
    print(f'  iter_batches(500): {total} keys streamed')

    # ── 9. Multi-key get ─────────────────────────────────────────────────────
    sep('9. get_many — multi-key lookup')
    result = db.get_many(['name', 'lang', 'missing_key', 'counter'])
    for k, v in result.items():
        print(f'  {k} → {v}')

    # ── 10. Close and reopen (persistence test) ──────────────────────────────
    sep('10. Persistence — close and reopen')
    db.close()
    db2 = LSMTree(DB)
    print(f'  name  → {db2.get("name")}   (read after reopen)')
    print(f'  topic → {db2.get("topic")}  (deleted key, None)')
    print(f'  bulk_000042 → {db2.get("bulk_000042")}')

    # ── 11. Snapshot — point-in-time read ────────────────────────────────────
    sep('11. Snapshot — consistent read while writes continue')
    db2.flush()
    snap = db2.snapshot()
    db2.put('new_after_snap', 'invisible to snapshot')
    print(f'  snap.get(name) = {snap.get("name")}   ← original value')
    print(f'  snap.get(new_after_snap) = {snap.get("new_after_snap")}  ← not in snapshot')
    snap.close()

    # ── 12. Backup ───────────────────────────────────────────────────────────
    sep('12. Backup — consistent hot copy')
    backup_dir = DB + '_backup'
    info = db2.backup(backup_dir)
    print(f'  Backed up: {info["files"]} files, {info["bytes"]:,} bytes')
    db_restored = LSMTree(backup_dir)
    print(f'  Restored: name → {db_restored.get("name")}')
    db_restored.close()
    shutil.rmtree(backup_dir)

    # ── 13. stats_report ─────────────────────────────────────────────────────
    sep('13. stats_report — formatted dashboard')
    # Trigger some reads so latency is non-zero
    for i in range(0, 100, 10):
        db2.get(f'bulk_{i:06d}')
    print(db2.stats_report())

    # ── 14. info ─────────────────────────────────────────────────────────────
    sep('14. info — active configuration')
    cfg = db2.info()
    for k, v in cfg.items():
        if k != 'directory':
            print(f'  {k}: {v}')

    db2.close()

    # ── 15. Compression ──────────────────────────────────────────────────────
    sep('15. zlib compression — 68% smaller for structured data')
    db_comp = LSMTree(DB + '_comp', sync_writes=False, compression='zlib')
    template = '{{"id":{i},"name":"User{i}","email":"user{i}@example.com","status":"active"}}'
    for i in range(5000):
        db_comp.put(f'user:{i:06d}', template.format(i=i))
    db_comp.flush()
    s = db_comp.stats()
    comp_bytes = sum(v['bytes'] for v in s['levels'].values())
    print(f'  5 000 JSON records  →  {comp_bytes:,} bytes on disk (compressed)')
    print(f'  user:000042 → {db_comp.get("user:000042")}')
    db_comp.close()
    shutil.rmtree(DB + '_comp')

    # ── 16. Pipeline ──────────────────────────────────────────────────────────
    sep('16. Pipeline — bulk writes with one WAL fsync')
    db3 = LSMTree(DB + '_pipe', sync_writes=False)
    with db3.pipeline() as pipe:
        for i in range(100):
            pipe.put(f'batch:{i:04d}', f'val{i}')
    print(f'  100 keys via pipeline: {db3.count("batch:", None)} entries')
    db3.close()
    shutil.rmtree(DB + '_pipe')

    # ── 17. Transaction ───────────────────────────────────────────────────────
    sep('17. Transaction — optimistic multi-key atomic update')
    db4 = LSMTree(DB + '_txn', sync_writes=False)
    db4.put('account:1', '1000')
    db4.put('account:2', '500')
    for _ in range(3):
        with db4.transaction() as txn:
            a = int(txn.get('account:1') or 0)
            b = int(txn.get('account:2') or 0)
            if a >= 100:
                txn.put('account:1', str(a - 100))
                txn.put('account:2', str(b + 100))
        if txn.committed:
            break
    print(f'  account:1 → {db4.get("account:1")}  (was 1000, transferred 100)')
    print(f'  account:2 → {db4.get("account:2")}  (was 500, received 100)')
    db4.close()
    shutil.rmtree(DB + '_txn')

    # ── 18. Range and neighbor operations ─────────────────────────────────────
    sep('18. Range ops — delete_prefix, ceiling_key, floor_key, range_delete')
    db5 = LSMTree(DB + '_range', sync_writes=False)
    for c in 'abcdefghijklmnopqrstuvwxyz':
        db5.put(c, c.upper())
    print(f'  ceiling_key("m") = {db5.ceiling_key("m")}  (≥ m)')
    print(f'  floor_key("m") = {db5.floor_key("m")}    (≤ m)')
    print(f'  ceiling_key("m1") = {db5.ceiling_key("m1")}  (next after m)')
    n = db5.range_delete('r', 'v')
    print(f'  range_delete(r→v): removed {n} keys')
    db5.close()
    shutil.rmtree(DB + '_range')

    # ── 19. Atomic numeric ops ─────────────────────────────────────────────────
    sep('19. Atomic numeric ops — atomic_max, atomic_min')
    db6 = LSMTree(DB + '_nums', sync_writes=False)
    for score in [72, 91, 55, 88, 63]:
        db6.atomic_max('high_score', score)
        db6.atomic_min('low_score', score)
    print(f'  high_score = {db6.get("high_score")}  (max of 72,91,55,88,63)')
    print(f'  low_score  = {db6.get("low_score")}  (min of 72,91,55,88,63)')
    db6.close()
    shutil.rmtree(DB + '_nums')

    shutil.rmtree(DB)
    print('\nDemo complete.\n')
