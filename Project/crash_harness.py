"""
Crash Harness — Kill-9 Recovery Test
======================================
Spawns a writer subprocess, sends SIGKILL at a random point during the
write/flush cycle, then opens the database and verifies:

  1. No key that was fully written (acknowledged) is missing.
  2. No key has a corrupted value.
  3. The database opens without error.

This is the practical validation of the three crash-safety invariants
defined in docs/invariants.md.

Run:
    python crash_harness.py [--runs N] [--keys K]
"""

import argparse
import os
import random
import shutil
import signal
import subprocess
import sys
import time

DB_DIR = '/tmp/lsm_crash_test'

# ── Writer script (spawned in subprocess) ────────────────────────────────────

_WRITER = '''
import sys, os, time
sys.path.insert(0, {src!r})
from lsm_engine.lsm_tree import LSMTree

db  = LSMTree({db!r})
log = open({log!r}, 'w')

for i in range({n}):
    key = f'key_{{i:08d}}'
    db.put(key, f'val_{{i}}')
    log.write(key + '\\n')
    log.flush()

db.close()
log.close()
'''


def run_one(src_dir: str, n_keys: int, kill_after: int, run_id: int) -> dict:
    if os.path.exists(DB_DIR):
        shutil.rmtree(DB_DIR)
    os.makedirs(DB_DIR)

    log_path = f'/tmp/lsm_written_keys_{run_id}.txt'
    if os.path.exists(log_path):
        os.remove(log_path)

    script   = _WRITER.format(src=src_dir, db=DB_DIR, log=log_path, n=n_keys)
    script_p = '/tmp/_lsm_writer_proc.py'
    with open(script_p, 'w') as f:
        f.write(script)

    proc = subprocess.Popen([sys.executable, '-u', script_p],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    written = set()
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            line = proc.stdout.readline(timeout=0.05) if hasattr(proc.stdout, 'read') else None
        except Exception:
            line = None
        if os.path.exists(log_path):
            with open(log_path) as f:
                written = {l.strip() for l in f if l.strip()}
        if len(written) >= kill_after:
            break

    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()

    # Re-read the log one final time
    if os.path.exists(log_path):
        with open(log_path) as f:
            written = {l.strip() for l in f if l.strip()}

    # ── Recovery ──────────────────────────────────────────────────────────────
    sys.path.insert(0, src_dir)
    from lsm_engine.lsm_tree import LSMTree  # re-import in case it changed

    corrupt = []
    missing = []
    try:
        db = LSMTree(DB_DIR)
        for key in written:
            idx = int(key.split('_')[1])
            val = db.get(key)
            if val is None:
                missing.append(key)
            elif val != f'val_{idx}':
                corrupt.append((key, val))
        db.close()
    except Exception as e:
        return {'error': str(e), 'written': len(written)}

    return {
        'written' : len(written),
        'missing' : len(missing),
        'corrupt' : len(corrupt),
        'pass'    : len(missing) == 0 and len(corrupt) == 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=5,  help='number of crash runs')
    ap.add_argument('--keys', type=int, default=20_000, help='max keys per run')
    args = ap.parse_args()

    src = os.path.dirname(os.path.abspath(__file__))
    print('=' * 60)
    print('LSMKV Crash Recovery Harness')
    print('=' * 60)

    passed = 0
    for run in range(1, args.runs + 1):
        kill_at = random.randint(100, args.keys)
        print(f'\nRun {run}/{args.runs}  kill_after={kill_at:,} keys … ', end='', flush=True)
        result = run_one(src, args.keys, kill_at, run)
        if 'error' in result:
            print(f'ERROR: {result["error"]}')
        elif result['pass']:
            print(f'PASS  (written={result["written"]:,}  missing=0  corrupt=0)')
            passed += 1
        else:
            print(f'FAIL  written={result["written"]:,}  '
                  f'missing={result["missing"]}  corrupt={result["corrupt"]}')

    print(f'\nResult: {passed}/{args.runs} runs passed')
    shutil.rmtree(DB_DIR, ignore_errors=True)
    sys.exit(0 if passed == args.runs else 1)


if __name__ == '__main__':
    main()
