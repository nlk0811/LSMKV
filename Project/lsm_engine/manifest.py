"""
MANIFEST
=========
Tracks which SSTable files exist at each level.

Crash-safety Invariant 2 (Compaction Atomicity)
------------------------------------------------
The MANIFEST update is the commit point for a compaction:
  1. New SSTable written and fsync'd.
  2. MANIFEST atomically updated (tmp → fsync → rename → fsync dir).
  3. Old input files deleted.

If the process crashes between steps 1 and 2, the new SSTable file exists
but is not referenced by the MANIFEST → it is cleaned up on the next start.
If the process crashes between steps 2 and 3, the old files still exist
but are no longer referenced → also cleaned up on the next start.

Crash-safety Invariant 3 (WAL Truncation Safety)
-------------------------------------------------
The WAL is truncated only AFTER add_l0() commits the flushed SSTable to the
MANIFEST (enforced in LSMTree._flush_memtable).
"""

import json
import os
from typing import Dict, List

from ._utils import fsync_fd, fsync_dir

MAX_LEVELS = 7


class Manifest:
    def __init__(self, directory: str):
        self.dir  = directory
        self.path = os.path.join(directory, 'MANIFEST.json')
        self._levels: List[List[str]] = [[] for _ in range(MAX_LEVELS)]
        self._load()

    # ── persistence ────────────────────────────────────────────────────────────

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            data = json.load(f)
        for i, files in enumerate(data.get('levels', [])):
            if i < MAX_LEVELS:
                self._levels[i] = [fp for fp in files if os.path.exists(fp)]

    def save(self):
        """Atomic write: write tmp → fsync → rename → fsync directory."""
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'levels': self._levels}, f, indent=2)
            f.flush()
            fsync_fd(f.fileno())
        os.rename(tmp, self.path)
        fsync_dir(self.dir)

    # ── mutations (each calls save()) ─────────────────────────────────────────

    def add_l0(self, path: str):
        """Register a newly flushed L0 SSTable. Call BEFORE WAL truncation."""
        self._levels[0].append(path)
        self.save()

    def apply_compaction(
        self,
        inputs:  Dict[int, List[str]],   # {level: [paths to remove]}
        outputs: Dict[int, List[str]],   # {level: [paths to add]}
    ):
        """
        Commit a compaction: atomically swap out old files for new ones.
        This is the single MANIFEST write that makes the compaction durable.
        """
        for lvl, paths in inputs.items():
            for p in paths:
                try:
                    self._levels[lvl].remove(p)
                except ValueError:
                    pass
        for lvl, paths in outputs.items():
            self._levels[lvl].extend(paths)
        self.save()

    # ── read ───────────────────────────────────────────────────────────────────

    @property
    def levels(self) -> List[List[str]]:
        return self._levels

    def all_files(self) -> List[str]:
        return [f for lvl in self._levels for f in lvl]

    def level_size(self, lvl: int) -> int:
        return sum(os.path.getsize(f) for f in self._levels[lvl] if os.path.exists(f))
