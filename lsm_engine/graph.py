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

from typing import Dict, Iterator, List, Optional, Tuple, TYPE_CHECKING

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

    def add_edges(self, edges) -> int:
        """Add multiple directed edges atomically in one WAL fsync.

        edges — iterable of (src, dst) or (src, dst, weight) tuples.
        N individual add_edge() calls = 2N WAL records; add_edges(N) = 1 fsync.

        Returns count of edges added.

        Example:
            graph.add_edges([
                ('alice', 'bob'),
                ('bob', 'carol', 1.5),   # with weight
                ('alice', 'carol'),
            ])
        """
        from .write_batch import WriteBatch as _WB
        batch = _WB()
        count = 0
        for edge in edges:
            if len(edge) == 2:
                src, dst = edge
                val = ''
            else:
                src, dst, w = edge
                val = str(w) if w is not None else ''
            batch.put(self._edge_key(src, dst), val)
            batch.put(self._pred_key(dst, src), '')
            count += 1
        if count:
            self._db.write(batch)
        return count

    def remove_edge(self, src: str, dst: str) -> bool:
        """Remove edge src→dst.  Returns True if it existed."""
        if not self.has_edge(src, dst):
            return False
        self._db.delete(self._edge_key(src, dst))
        self._db.delete(self._pred_key(dst, src))
        return True

    def remove_node(self, node: str) -> int:
        """Remove all edges to and from node.  Returns number of edges removed.

        Uses one prefix_delete per direction + one delete_many for the reverse
        index entries — avoids N individual WAL records for a hub node.
        """
        # Collect reverse-index cleanup keys before bulk deleting the forward index
        out_dsts = list(self.neighbors(node))
        in_srcs  = list(self.predecessors(node))

        # Delete outgoing forward edges in bulk
        self._db.prefix_delete(self._edge_prefix(node))
        # Delete incoming reverse pointers in bulk
        self._db.prefix_delete(self._pred_prefix(node))
        # Clean up cross-index entries
        self._db.delete_many([self._pred_key(dst, node) for dst in out_dsts])
        self._db.delete_many([self._edge_key(src, node) for src in in_srcs])

        return len(out_dsts) + len(in_srcs)

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
        n = self.edge_count()
        self._db.prefix_delete(f'__graph:{self._name}:')
        return n

    # ── traversal ─────────────────────────────────────────────────────────────

    def dfs(self, start: str,
            max_depth: Optional[int] = None) -> 'Iterator[str]':
        """Yield nodes reachable from start in depth-first order.

        start is yielded first.  Each node is visited at most once.
        Uses iterative DFS (explicit stack) to avoid Python recursion limits.

        max_depth — maximum hops from start (None = unlimited).

        Memory: O(visited) — proportional to nodes reached, not total graph.

        Example — find all transitive dependencies:
            deps = list(graph.dfs('package_a'))
        """
        visited: set = {start}
        stack = [(start, 0)]
        while stack:
            node, depth = stack.pop()
            yield node
            if max_depth is None or depth < max_depth:
                for neighbor in reversed(list(self.neighbors(node))):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append((neighbor, depth + 1))

    def bfs(self, start: str,
            max_depth: Optional[int] = None) -> 'Iterator[str]':
        """Yield nodes reachable from start in breadth-first order.

        start is yielded first.  Each node is visited at most once.

        max_depth — stop after this many hops from start (None = unlimited).
                    max_depth=1 yields start + its direct neighbors.

        Memory: O(visited) — proportional to nodes reached, not total graph size.

        Example — find all friends-of-friends:
            friends = list(graph.bfs('alice', max_depth=2))
        """
        from collections import deque as _deque
        visited: set = {start}
        queue = _deque([(start, 0)])
        while queue:
            node, depth = queue.popleft()
            yield node
            if max_depth is None or depth < max_depth:
                for neighbor in self.neighbors(node):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, depth + 1))

    def shortest_path(self, src: str,
                      dst: str) -> 'Optional[List[str]]':
        """Return the shortest directed path from src to dst as a list of nodes.

        Returns None if no path exists.  Returns [src] if src == dst.
        Uses BFS — finds the fewest-hop path.

        Memory: O(visited) — proportional to nodes explored before reaching dst.

        Example:
            path = graph.shortest_path('alice', 'carol')
            # ['alice', 'bob', 'carol'] — alice → bob → carol
        """
        from collections import deque as _deque
        if src == dst:
            return [src]
        prev: dict = {src: None}
        queue = _deque([src])
        while queue:
            node = queue.popleft()
            for neighbor in self.neighbors(node):
                if neighbor not in prev:
                    prev[neighbor] = node
                    if neighbor == dst:
                        path = []
                        cur: Optional[str] = dst
                        while cur is not None:
                            path.append(cur)
                            cur = prev[cur]
                        return list(reversed(path))
                    queue.append(neighbor)
        return None

    # ── inspection ────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return self.edge_count()

    def __contains__(self, edge: tuple) -> bool:
        """Support `('alice', 'bob') in graph`."""
        return self.has_edge(*edge)

    def __repr__(self) -> str:
        return f'Graph({self._name!r}, edges={self.edge_count()})'
