# kicadstamp/internode_copper.py
"""
internode_copper.py — pure "copper between pads" unit extraction for the tree's
inter-node copper (plan_2026_09_12_internode_copper_core.md, stage Э1; design
techdocs/handoff/claude/design_2026_09_12_tree_internode_copper_reread.md §4,
§16).

A UNIT is a maximal chain of tracks/vias that STOPS AT A PAD: the traversal
follows copper joints (coincident endpoints, track-to-via joints) and a pad is a
TERMINATOR, never an edge. Two tracks meeting on the same pad are TWO different
units — merging them through the pad would collapse the whole net back into one
connected component and the strict classification below would die (this is the
main trap of the design, §4/R1: union-find is naturally inclined to do exactly
that).

ZONES ARE NOT PART OF THIS GRAPH. NEVER. (design §14, decision Р4.) A zone
connects by OVERLAP, not by ROUTING: a GND pour covering every cluster would
fuse all of them into one unit and the classification would fall apart. The
connectivity here is built from tracks and vias ONLY. Today a zone physically
cannot reach this code (domain Zone is a stub carrying just a name, and there is
no bulk zone read) — the rule is written down so that nobody "improves" this
later by feeding zones into the union-find, silently breaking the
classification.

Net names are NOT the criterion of the unit: the unit is defined by geometry.
The tracks/vias of one unit are nonetheless expected to carry ONE net; a
mismatch is reported as a warning (net_conflicts) instead of silently merging
or splitting — see the plan's Э1 trap list.

Classification (design §4 / plan Р1 — a STRICT RULE, not a threshold):

    all pads of the unit belong to nodes of this tree, and there are 2+ nodes
        -> INTERNODE — take it;
    all pads belong to ONE node
        -> CLUSTER — this is the cell's own copper, never take it;
    a pad outside the tree
        -> FOREIGN — someone else's connection, never take it;
    no pads at all (a chain of stitching vias)
        -> UNMOORED — never take it;
    a single pad and a dead end (a stub)
        -> STUB — never take it.

Qt-free and testable directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .domain.board import Footprint, Track, Via
from .domain.geometry import Vector2
from .i18n import _

logger = logging.getLogger(__name__)

__all__ = [
    "CopperUnit",
    "CopperVerdict",
    "PadRef",
    "classify_unit",
    "find_copper_units",
    "net_conflicts",
]


class CopperVerdict(str, Enum):
    """The classification of one CopperUnit (design §4 / plan Р1). Only
    INTERNODE is taken; every other verdict is a reason to leave the copper
    alone."""

    INTERNODE = "internode"
    CLUSTER = "cluster"
    FOREIGN = "foreign"
    UNMOORED = "unmoored"
    STUB = "stub"


@dataclass(frozen=True, order=True)
class PadRef:
    """A pad identity: the owning component's ref + the pad number (as a
    string, so a pad number read as int/float from KiCad still compares equal
    to a config-supplied '2'). This is the unit's IDENTITY: re-reads match the
    fresh unit to the stored record by the SET of pads, not by geometry — the
    geometry is exactly what is being re-read (design §11)."""

    ref: str
    pad: str


@dataclass
class CopperUnit:
    """One connected piece of track/via copper, moored to the pads it touches.

    tracks/vias are the whole copper of the unit (a track is never split — a
    T-branch off the middle of a segment is one physical piece of copper and
    stays one unit, design §16). pads is the sorted tuple of the pads the unit
    is moored to; it is the unit's identity for matching and for name
    generation."""

    tracks: list[Track] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    pads: tuple[PadRef, ...] = ()

    @property
    def net_names(self) -> set[str]:
        """The distinct non-empty net names carried by the unit's copper."""
        return {item.net_name for item in (*self.tracks, *self.vias)
                if item.net_name}

    @property
    def net_name(self) -> str | None:
        """The single net name of the unit, or None when the copper carries no
        net at all. A unit with more than one net is a data inconsistency —
        net_conflicts() reports it."""
        names = self.net_names
        if len(names) == 1:
            return next(iter(names))
        return None

    @property
    def pad_signature(self) -> frozenset[PadRef]:
        """The matching key of the unit: the SET of pads it connects (design
        §11 — 'the same unit' on a re-read)."""
        return frozenset(self.pads)

    def __len__(self) -> int:
        return len(self.tracks) + len(self.vias)


def _normalize_pad_number(number: Any) -> str:
    """Pad numbers come back from kipy as str/int/float depending on the pad
    (domain Pad.number is kept as-is). Normalize to a plain string so that a
    float 2.0 and a config-supplied '2' name the same pad."""
    if isinstance(number, float) and number.is_integer():
        return str(int(number))
    return str(number)


def find_copper_units(
    adapter,
    raw_items: Iterable[Any],
    *,
    footprints: Iterable[Any] | None = None,
) -> tuple[list[CopperUnit], list[str]]:
    """Split the area's track/via copper into "copper between pads" units.

    `raw_items` is the area's copper (the domain Track/Via DTOs); the area's
    footprints are taken from `raw_items` itself when `footprints` is not given
    (the extract-selection callers keep everything in one list). EVERY pad of
    every area footprint is an anchor — not only the pads of the tree's own
    clusters: that is what makes a pad a boundary ("stop at the pad") rather
    than a filter on cluster membership.

    Returns (units, warnings). `warnings` carries the net-name conflicts
    (design Э1: a unit's copper must carry one net; a mismatch is a Log
    warning, not a silent merge). Units are returned in a deterministic order
    (the order their first copper item appears in `raw_items`).

    The adapter must provide pad geometry (`get_footprint_pads` +
    `get_bounding_boxes`): without real pad boxes there is no boundary at all,
    so a limited adapter is a programming error here, not a silent fallback.
    """
    # Local import (the same idiom tree_from_selection uses): template_selection
    # pulls kicad.adapter + role_narrowing; a module-level import would widen
    # this pure module's load graph for no benefit.
    from .template_selection import (
        _inflated_boxes,
        _point_in_box,
        _points_match,
    )

    items = list(raw_items)
    area_footprints = (list(footprints) if footprints is not None
                       else [i for i in items if isinstance(i, Footprint)])
    tracks = [i for i in items if isinstance(i, Track)]
    vias = [i for i in items if isinstance(i, Via)]
    if not tracks and not vias:
        return [], []

    if not (hasattr(adapter, "get_footprint_pads")
            and hasattr(adapter, "get_bounding_boxes")):
        raise ValueError(
            "find_copper_units needs an adapter with get_footprint_pads() and "
            "get_bounding_boxes() — the pad boundary cannot be derived without "
            "real pad geometry")

    # ── Every pad in the area is a terminator (the boundary of a unit) ─────
    pads: list[Any] = []
    pad_refs: list[PadRef] = []
    for fp in area_footprints:
        for pad in adapter.get_footprint_pads(fp):
            pads.append(pad)
            pad_refs.append(PadRef(ref=fp.ref,
                                   pad=_normalize_pad_number(pad.number)))
    pad_boxes = _inflated_boxes(adapter, pads) if pads else []

    def _point_on_pad(point: Vector2) -> bool:
        return any(box is not None and _point_in_box(point, box)
                   for box in pad_boxes)

    def _joint_point(a_start: Vector2, a_end: Vector2,
                     b_start: Vector2, b_end: Vector2) -> Vector2 | None:
        for p in (a_start, a_end):
            for q in (b_start, b_end):
                if _points_match(p, q):
                    return p
        return None

    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(tracks)):
        parent[("t", i)] = ("t", i)
    for i in range(len(vias)):
        parent[("v", i)] = ("v", i)

    # ── Copper joints. A pad is a TERMINATOR, not an edge: a joint that lands
    # exactly on a pad does NOT merge the two pieces — each is moored to that
    # pad on its own and stays a separate unit (design §4/§16, plan Э1 trap 1).
    # A T-branch whose joint sits off any pad is a genuine joint: one unit with
    # three or more pads (trap 2).
    for i, t in enumerate(tracks):
        for j in range(i + 1, len(tracks)):
            o = tracks[j]
            p = _joint_point(t.start, t.end, o.start, o.end)
            if p is not None and not _point_on_pad(p):
                union(("t", i), ("t", j))
    for i, t in enumerate(tracks):
        for j, v in enumerate(vias):
            p = _joint_point(t.start, t.end, v.position, v.position)
            if p is not None and not _point_on_pad(p):
                union(("t", i), ("v", j))

    # ── Moor every copper item to every pad box it touches. Mooring labels the
    # unit, it never unions two units (see above).
    unit_pads: dict[tuple[str, int], set[PadRef]] = {}
    for index, box in enumerate(pad_boxes):
        if box is None:
            continue
        ref = pad_refs[index]
        for i, t in enumerate(tracks):
            if _point_in_box(t.start, box) or _point_in_box(t.end, box):
                unit_pads.setdefault(find(("t", i)), set()).add(ref)
        for j, v in enumerate(vias):
            if _point_in_box(v.position, box):
                unit_pads.setdefault(find(("v", j)), set()).add(ref)

    # ── Assemble, preserving the raw_items order of the first copper item.
    order: list[tuple[str, int]] = []
    by_root: dict[tuple[str, int], CopperUnit] = {}
    for i, t in enumerate(tracks):
        root = find(("t", i))
        if root not in by_root:
            by_root[root] = CopperUnit()
            order.append(root)
        by_root[root].tracks.append(t)
    for j, v in enumerate(vias):
        root = find(("v", j))
        if root not in by_root:
            by_root[root] = CopperUnit()
            order.append(root)
        by_root[root].vias.append(v)

    units: list[CopperUnit] = []
    for root in order:
        unit = by_root[root]
        unit.pads = tuple(sorted(unit_pads.get(root, ())))
        units.append(unit)

    warnings: list[str] = []
    for unit in units:
        warnings.extend(net_conflicts(unit))
    return units, warnings


def net_conflicts(unit: CopperUnit) -> list[str]:
    """Log warnings for a unit whose copper carries more than one net name. The
    unit is defined by geometry, so this is reported rather than resolved —
    the plan's Э1 rule: a net-name mismatch must not silently merge or split
    copper."""
    names = unit.net_names
    if len(names) <= 1:
        return []
    return [_(
        "inter-node copper: one unit of copper (pads {pads}) carries several "
        "nets at once: {nets} — the unit is defined by geometry; check the "
        "routing").format(
            pads=", ".join(f"{p.ref}.{p.pad}" for p in unit.pads) or "-",
            nets=", ".join(sorted(names)))]


def classify_unit(unit: CopperUnit, node_by_ref: Mapping[str, Any]) -> CopperVerdict:
    """Classify one unit against the tree's nodes.

    `node_by_ref` maps a component ref to the tree NODE it belongs to (any
    hashable node identity — the caller's tree-node key). A ref absent from the
    mapping is a component outside this tree.

    The order matters and encodes the design table (§4 / Р1): a foreign pad
    outranks everything (the unit is somebody else's connection), then "no pad
    at all", then the node count, then the single-pad dead end.
    """
    if not unit.pads:
        return CopperVerdict.UNMOORED
    if any(pad.ref not in node_by_ref for pad in unit.pads):
        return CopperVerdict.FOREIGN
    nodes = {node_by_ref[pad.ref] for pad in unit.pads}
    if len(nodes) >= 2:
        return CopperVerdict.INTERNODE
    if len(unit.pads) == 1:
        # One pad, dead end — a stub of the cluster's own copper, not a link.
        return CopperVerdict.STUB
    return CopperVerdict.CLUSTER
