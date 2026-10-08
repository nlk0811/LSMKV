"""
LSMKV Demo
===========
Walks through every public API call and prints results.

Run:
    python demo.py
"""

import os, shutil, sys
sys.path.insert(0, os.path.dirname(__file__))

from lsm_engine.lsm_tree import LSMTree

DB = '/tmp/lsmkv_demo'

def sep(title=''):
    print(f'\n{"─" * 50}')
    if title:
        print(f'  {title}')
        print(f'{"─" * 50}')

if __name__ == '__main__':
    if os.path.exists(DB):
        shutil.rmtree(DB)

    db = LSMTree(DB)

    sep('Put / Get')
    db.put('name',  'LSMKV')
    db.put('lang',  'Python')
    db.put('topic', 'Storage Engines')
    print(f'  name  → {db.get("name")}')
    print(f'  lang  → {db.get("lang")}')
    print(f'  topic → {db.get("topic")}')
    print(f'  missing → {db.get("missing")}')

    sep('Overwrite')
    db.put('lang', 'Python 3')
    print(f'  lang  → {db.get("lang")}')

    sep('Delete (tombstone)')
    db.delete('topic')
    print(f'  topic → {db.get("topic")}  (should be None)')

    sep('Range Scan')
    for c in 'abcdefghij':
        db.put(f'letter_{c}', c.upper())
    results = list(db.scan('letter_c', 'letter_g'))
    for k, v in results:
        print(f'  {k} → {v}')

    sep('Bulk load (5 000 keys)')
    for i in range(5_000):
        db.put(f'bulk_{i:06d}', f'value_{i}')
    print(f'  bulk_000042 → {db.get("bulk_000042")}')
    print(f'  bulk_004999 → {db.get("bulk_004999")}')

    sep('Stats')
    s = db.stats()
    print(f'  memtable  {s["memtable_bytes"]:,} bytes  /  {s["memtable_keys"]:,} keys')
    for lvl, info in s['levels'].items():
        print(f'  {lvl}  {info["files"]} file(s)  {info["bytes"]:,} bytes')

    sep('Close and reopen (persistence test)')
    db.close()
    db2 = LSMTree(DB)
    print(f'  name  → {db2.get("name")}   (read after reopen)')
    print(f'  topic → {db2.get("topic")}  (deleted key, should be None)')
    db2.close()

    shutil.rmtree(DB)
    print('\nDemo complete.\n')
