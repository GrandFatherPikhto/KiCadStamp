# kicadstamp/geometry/cell_copper_connectivity.py
"""Copper connectivity of one Cell's tracks+vias, computed purely from the
cell's OWN geometry (no adapter, no live board).

Two segments are CONNECTED when they share an endpoint coordinate within an
epsilon: track start/end and via offset, all in the cell's local along/across
frame. Grouping them into connected components is what lets us ask "which
(role, pad) tags share one physical copper node" — the basis for the
self-verifying `net_template_pad` invariant
(check_bridging_pad_hints_are_self_consistent, kicadstamp/validation.py) and
the reusable helper originally written for the fpga_flash bridging-pad
hypothesis tests (tests/test_fpga_flash_bridging_pads.py, H1-H6).

Moved out of that test file into core 2026-08-29 (plan
2026_08_29_bridging_pad_connectivity_guard.md §2.1) — the point where the
"reusable for future hintless bridging roles" note became a real second
consumer (the validation check), so it stopped being test-only code.

Pure math, no side effects: given a Cell, returns component membership only.
The consumers decide what a component's tag set means for their role/net
question.
"""
from dataclasses import dataclass

from ..config import Cell


@dataclass(frozen=True)
class CopperSegment:
    """One cell copper element (a track or via) with its role/pad tag and the
    endpoint coordinates that participate in connectivity."""

    kind: str  # 'track' | 'via'
    role: str | None  # net_from_role
    pad: str | None  # net_from_role_pad
    points: tuple[tuple[float, float], ...]


class _UnionFind:
    """Minimal union-find used by cell_copper_components."""

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


def _segments_from_cell(cell: Cell) -> list[CopperSegment]:
    """Every track + via of the cell as a CopperSegment (kind, role, pad,
    points) — the flat list cell_copper_components unions over."""
    segments: list[CopperSegment] = []
    for t in cell.tracks:
        segments.append(CopperSegment(
            "track", t.net_from_role, t.net_from_role_pad,
            ((t.start_along_mm, t.start_across_mm),
             (t.end_along_mm, t.end_across_mm)),
        ))
    for v in cell.vias:
        segments.append(CopperSegment(
            "via", v.net_from_role, v.net_from_role_pad,
            ((v.offset_along_mm, v.offset_across_mm),),
        ))
    return segments


def cell_copper_components(cell: Cell, eps: float = 1e-3) -> list[list[CopperSegment]]:
    """Connected components of a Cell's copper (all tracks + vias), union-find.

    Two segments are CONNECTED when they share an endpoint coordinate within
    `eps` mm: track start/end and via offset, all in the cell's local
    along/across frame. The extracted profile stores exact coordinates (e.g.
    (3.5875, -1.905) repeated to the 4th decimal), so the default eps=1e-3
    merges exact joints and nothing else.

    Returns a list of components; each component is a list of CopperSegment
    (kind, role, pad, points). Segments with no net_from_role are tagged
    (None, None). Reusable for ANY cell of any profile — this is what the
    bridging-pad checks build on."""
    segments = _segments_from_cell(cell)

    uf = _UnionFind(len(segments))
    # Bucket every endpoint coordinate onto the eps grid, then union all
    # segments whose endpoints land in the same bucket (exact joints merge).
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, seg in enumerate(segments):
        for (x, y) in seg.points:
            buckets.setdefault((round(x / eps), round(y / eps)), []).append(i)
    for bucket in buckets.values():
        for other in bucket[1:]:
            uf.union(bucket[0], other)

    groups: dict[int, list[CopperSegment]] = {}
    for i, seg in enumerate(segments):
        groups.setdefault(uf.find(i), []).append(seg)
    return list(groups.values())


def component_role_pads(component: list[CopperSegment]) -> set[tuple[str | None, str | None]]:
    """The distinct (role, pad) tags present in one copper component."""
    return {(seg.role, seg.pad) for seg in component}


def component_containing(components: list[list[CopperSegment]], role: str, pad: str):
    """The first copper component containing any segment tagged (role, pad),
    else None."""
    for comp in components:
        if any(seg.role == role and seg.pad == pad for seg in comp):
            return comp
    return None


def component_shared_point(component: list[CopperSegment], tag_a, tag_b
                           ) -> tuple[float, float]:
    """The exact (along, across) coordinate where the segments tagged `tag_a`
    (a (role, pad) pair) and the segments tagged `tag_b` of ONE component
    meet — the physical joint that makes them a single copper node. Used for
    the "why" of a bridging-pad conflict diagnostic. Asserts exactly one such
    shared point (two tags sharing a node meet at exactly the joint)."""
    pts_a = {pt for seg in component if (seg.role, seg.pad) == tag_a for pt in seg.points}
    pts_b = {pt for seg in component if (seg.role, seg.pad) == tag_b for pt in seg.points}
    common = pts_a & pts_b
    assert len(common) == 1, \
        f"expected exactly one shared point between {tag_a} and {tag_b}, got {sorted(common)}"
    return next(iter(common))
