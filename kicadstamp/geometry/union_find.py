# kicadstamp/geometry/union_find.py
"""Minimal union-find — the shared disjoint-set primitive.

Extracted from ``geometry/cell_copper_connectivity.py`` on 2026-10-05 (plan
``plan_2026_10_05_explode_r1_core.md`` §2) so the explode connectivity can
REUSE it instead of carrying a second copy of the same few lines. Behaviour is
unchanged: path halving in ``find`` and union by rank in ``union``.
"""
from __future__ import annotations

__all__ = ["UnionFind"]


class UnionFind:
    """Disjoint-set forest over ``n`` elements (0..n-1)."""

    def __init__(self, n: int):
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
