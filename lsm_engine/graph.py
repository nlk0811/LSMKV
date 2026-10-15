"""
Graph — Persistent Directed Graph
==================================
A persistent directed graph backed by the database.  Edges are stored as two
index entries each (outgoing + incoming) for O(log n) lookup and O(degree)
neighbor listing.

Key format:
  __graph:{name}:edge:{src}:{dst}  →  weight_str (outgoing index)
  __graph:{name}:pred:{dst}:{src}  →  ""          (incoming index)

Weights: optional float stored as string.  Pass weight=None for unweighted.

Example
-------
    g = db.graph('social')

    g.add_edge('alice', 'bob')
    g.add_edge('alice', 'carol')
    g.add_edge('bob',   'carol')

    g.neighbors('alice')       # ['bob', 'carol']  (outgoing)
    g.predecessors('carol')    # ['alice', 'bob']  (incoming)
    g.has_edge('alice', 'bob') # True
    g.degree('alice')          # 2 (out-degree)
    g.in_degree('carol')       # 2

    g.remove_edge('alice', 'bob')
    g.remove_node('carol')     # removes all edges to/from carol
"""

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .lsm_tree import LSMTree


class Graph:
    """Persistent directed graph backed by the database."""

    def __init__(self, db: 'LSMTree', name: str):
        self._db   = db
        self._name = name

    # ── key helpers ───────────────────────────────────────────────────────────

    def _edge_key(self, src: str, dst: str) -> str:
        return f'__graph:{self._name}:edge:{src}:{dst}'

    def _pred_key(self, dst: str, src: str) -> str:
        return f'__graph:{self._name}:pred:{dst}:{src}'

    def _edge_prefix(self, src: str) -> str:
        return f'__graph:{self._name}:edge:{src}:'

    def _pred_prefix(self, dst: str) -> str:
        return f'__graph:{self._name}:pred:{dst}:'

    # ── write ─────────────────────────────────────────────────────────────────

    def add_edge(self, src: str, dst: str, weight: float = None) -> None:
        """Add a directed edge from src to dst, optionally with a weight."""
        val = str(weight) if weight is not None else ''
        self._db.put(self._edge_key(src, dst), val)
        self._db.put(self._pred_key(dst, src), '')

    def remove_edge(self, src: str, dst: str) -> bool:
        """Remove edge src→dst.  Returns True if it existed."""
        if not self.has_edge(src, dst):
            return False
        self._db.delete(self._edge_key(src, dst))
        self._db.delete(self._pred_key(dst, src))
        return True

    def remove_node(self, node: str) -> int:
        """Remove all edges to and from node.  Returns number of edges removed."""
        count = 0
        # Remove outgoing edges
        for dst in self.neighbors(node):
            self._db.delete(self._edge_key(node, dst))
            self._db.delete(self._pred_key(dst, node))
            count += 1
        # Remove incoming edges
        for src in self.predecessors(node):
            self._db.delete(self._edge_key(src, node))
            self._db.delete(self._pred_key(node, src))
            count += 1
        return count

    def set_weight(self, src: str, dst: str, weight: float) -> bool:
        """Update the weight of an existing edge.  Returns False if absent."""
        if not self.has_edge(src, dst):
            return False
        self._db.put(self._edge_key(src, dst), str(weight))
        return True

    # ── read ──────────────────────────────────────────────────────────────────

    def has_edge(self, src: str, dst: str) -> bool:
        return self._db.has(self._edge_key(src, dst))

    def weight(self, src: str, dst: str) -> Optional[float]:
        """Return edge weight, or None if edge absent or unweighted."""
        v = self._db.get(self._edge_key(src, dst))
        if v is None:
            return None
        return float(v) if v else None

    def neighbors(self, node: str) -> List[str]:
        """Return outgoing neighbors of node."""
        prefix = self._edge_prefix(node)
        plen   = len(prefix)
        return [k[plen:] for k in self._db.scan_keys(prefix, prefix[:-1] + ';')]

    def predecessors(self, node: str) -> List[str]:
        """Return nodes with edges pointing TO node."""
        prefix = self._pred_prefix(node)
        plen   = len(prefix)
        return [k[plen:] for k in self._db.scan_keys(prefix, prefix[:-1] + ';')]

    def edges_from(self, node: str) -> List[Tuple[str, Optional[float]]]:
        """Return [(dst, weight)] for all outgoing edges from node."""
        prefix = self._edge_prefix(node)
        plen   = len(prefix)
        return [
            (k[plen:], float(v) if v else None)
            for k, v in self._db.scan(prefix, prefix[:-1] + ';')
        ]

    def degree(self, node: str) -> int:
        """Out-degree of node.

        Uses count_prefix() — O(n_neighbors) scan but no list materialisation.
        """
        prefix = self._edge_prefix(node)
        return self._db.count_prefix(prefix)

    def in_degree(self, node: str) -> int:
        """In-degree of node.

        Uses count_prefix() — O(n_predecessors) scan but no list materialisation.
        """
        prefix = self._pred_prefix(node)
        return self._db.count_prefix(prefix)

    def node_count(self) -> int:
        """Approximate node count (counts unique src nodes with at least one edge)."""
        seen  = set()
        prefix = f'__graph:{self._name}:edge:'
        plen   = len(prefix)
        for k in self._db.scan_keys(prefix, prefix[:-1] + ';'):
            src = k[plen:].split(':')[0]
            seen.add(src)
        return len(seen)

    def edge_count(self) -> int:
        """Total number of directed edges."""
        prefix = f'__graph:{self._name}:edge:'
        return sum(1 for _ in self._db.scan_keys(prefix, prefix[:-1] + ';'))

    def clear(self) -> int:
        """Remove all edges.  Returns count removed."""
        all_keys = [k for k in self._db.scan_keys(
            f'__graph:{self._name}:', f'__graph:{self._name};')]
        for k in all_keys:
            self._db.delete(k)
        return len(all_keys) // 2

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self.edge_count()

    def __contains__(self, edge: tuple) -> bool:
        """Support `('alice', 'bob') in graph`."""
        return self.has_edge(*edge)

    def __repr__(self) -> str:
        return f'Graph({self._name!r}, edges={self.edge_count()})'
