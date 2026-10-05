# kicadstamp/explode_connectivity.py
"""Copper connectivity + pad classification for the "Разнос" plan (2026-10-05,
plan ``plan_2026_10_05_explode_r1_core.md``).

Split out of ``kicadstamp/explode.py`` (Р1в-0; rule Д8 — explode.py had passed
740 lines). The split changes NO behaviour.

A track is a CAPSULE (its segment grown by width/2), a via a DISC
(diameter/2, through). Two pieces of copper on a shared layer are connected when
their shapes OVERLAP (``kicadstamp/geometry/copper_connect``), with a 1 mm
broad-phase grid. Pads are CLASSIFIERS, never unioners. Everything here is
board-agnostic pure geometry over the DTOs; the caller decides LAYERS and which
footprints are the cell / a foreign instance.
"""
from __future__ import annotations

import math

from .domain.board import Via
from .geometry.copper_connect import (capsules_touch, disc_touches_capsule,
                                      discs_touch)
from .geometry.pad_area import pad_area_of
from .geometry.union_find import UnionFind
from .utils.units import MM

__all__ = [
    "pad_areas",
    "copper_extent",
    "copper_touch",
    "copper_classes",
    "cell_pads",
    "foreign_labels",
    "classify",
    "cell_pads_text",
]

# The broad-phase grid (Р1б-1): 1 mm buckets on the items' bounding boxes, so a
# board is never scanned pairwise in full.
_GRID_NM = 1_000_000


def pad_areas(adapter, class_fps: dict) -> list:
    """[(PadArea, label, copper_layers)] over every pad of every class.

    A CELL pad gets its OWN label ``cell:<ref>:<pad>`` (Р1а-1), so a component
    that touches TWO different cell pads is distinguishable from one that
    touches a single pad — the basis of the ``tee`` rule. A foreign pad keeps its
    ``<cluster>/<sheet>`` instance label."""
    out = []
    for label, fps in class_fps.items():
        for fp in fps:
            ref = getattr(fp, "ref", None) or getattr(fp, "uuid", "?")
            for pad in adapter.get_footprint_pads(fp):
                area = pad_area_of(pad)
                if area is None:
                    continue
                pad_label = (f"cell:{ref}:{getattr(pad, 'number', '?')}"
                             if label == "cell" else label)
                out.append((area, pad_label,
                            getattr(pad, "copper_layers", None)))
    return out


def copper_extent(item) -> tuple:
    """(bbox, half_extent_nm) of a copper item: a track's segment grown by
    width/2, a via's centre grown by diameter/2. bbox = (x1, y1, x2, y2) in nm."""
    if isinstance(item, Via):
        r = getattr(item, "diameter_mm", 0.0) / 2.0 * MM
        p = item.position
        return (p.x - r, p.y - r, p.x + r, p.y + r), r
    half = getattr(item, "width_mm", 0.0) * MM / 2.0
    x1, x2 = sorted((item.start.x, item.end.x))
    y1, y2 = sorted((item.start.y, item.end.y))
    return (x1 - half, y1 - half, x2 + half, y2 + half), half


def copper_touch(a, b, ha: float, hb: float) -> bool:
    """Do two copper items overlap, KiCad-style? A track is a capsule (half
    width ``ha``/``hb``), a via a disc (radius ``ha``/``hb``, through — no layer
    check). Two tracks connect only on the SAME layer. This sees ends AND a
    T-junction, and does not need two points to coincide (Р1б-1)."""
    a_via, b_via = isinstance(a, Via), isinstance(b, Via)
    if a_via and b_via:
        return discs_touch(a.position.x, a.position.y, ha,
                           b.position.x, b.position.y, hb)
    if a_via:
        return disc_touches_capsule(a.position.x, a.position.y, ha,
                                    b.start.x, b.start.y, b.end.x, b.end.y, hb * 2)
    if b_via:
        return disc_touches_capsule(b.position.x, b.position.y, hb,
                                    a.start.x, a.start.y, a.end.x, a.end.y, ha * 2)
    if a.layer != b.layer:
        return False
    return capsules_touch(a.start.x, a.start.y, a.end.x, a.end.y, ha * 2,
                          b.start.x, b.start.y, b.end.x, b.end.y, hb * 2)


def copper_classes(tracks, vias, pads) -> dict:
    """{uuid: set(pad classes)} for every copper item (tracks + vias).

    Connectivity is by SHAPE, the way KiCad sees it (Р1б-1). Broad phase over
    1 mm buckets, then exact distance per candidate pair. Pads are CLASSIFIERS,
    never unioners. Keyed by UUID because the adapter hands out COPIES: two reads
    of the same item are equal by uuid, not by identity."""
    items = list(tracks) + list(vias)
    n = len(items)
    out: dict = {getattr(it, "uuid", None): set() for it in items}
    if n == 0:
        return out
    uf = UnionFind(n)
    extents = [copper_extent(it) for it in items]

    grid: dict = {}
    for i, (box, _h) in enumerate(extents):
        for cx in range(int(math.floor(box[0] / _GRID_NM)),
                        int(math.floor(box[2] / _GRID_NM)) + 1):
            for cy in range(int(math.floor(box[1] / _GRID_NM)),
                            int(math.floor(box[3] / _GRID_NM)) + 1):
                grid.setdefault((cx, cy), []).append(i)
    seen: set = set()
    for bucket in grid.values():
        for ii in range(len(bucket)):
            for jj in range(ii + 1, len(bucket)):
                i, j = bucket[ii], bucket[jj]
                key = (i, j) if i < j else (j, i)
                if key in seen:
                    continue
                seen.add(key)
                if copper_touch(items[i], items[j],
                                extents[i][1], extents[j][1]):
                    uf.union(i, j)

    own: list = [set() for _ in range(n)]
    for i, it in enumerate(items):
        half = extents[i][1]
        if isinstance(it, Via):
            for pad_area, label, _layers in pads:
                if pad_area.contains(it.position, margin=half):
                    own[i].add(label)
        else:
            for pad_area, label, layers in pads:
                if layers is not None and it.layer not in layers:
                    continue
                if pad_area.segment_touches(it.start, it.end, margin=half):
                    own[i].add(label)
    by_root: dict = {}
    for i in range(n):
        by_root.setdefault(uf.find(i), set()).update(own[i])
    for i, it in enumerate(items):
        out[getattr(it, "uuid", None)] = set(by_root[uf.find(i)])
    return out


def cell_pads(classes: set) -> set:
    """The CELL-pad labels of a component (``cell`` or ``cell:<ref>:<pad>``)."""
    return {c for c in classes if c == "cell" or c.startswith("cell:")}


def foreign_labels(classes: set) -> set:
    """The foreign-instance labels (``<cluster>/<sheet>``) of a component."""
    return {c for c in classes
            if not (c == "cell" or c.startswith("cell:"))}


def classify(classes: set) -> str:
    """cell / <cluster>/<sheet> / tee / multi / none (Р1а-1).

    An ordinary inter-cluster track runs from ONE cell pad to ONE foreign pad
    (D23 -> PIF): that is the FOREIGN label, NOT ``tee``. ``tee`` is the DANGEROUS
    case the plan names: a component joining TWO DIFFERENT cell pads (the cell's
    inner piece) AND a foreign pad — the inner piece would leave with the foreign
    cluster. ``multi`` — several foreign instances without the tee condition —
    is its own label so ``tee`` means one thing."""
    cell = cell_pads(classes)
    foreign = foreign_labels(classes)
    if not cell and not foreign:
        return "none"
    if not foreign:
        return "cell"
    if len(cell) >= 2:
        return "tee"
    if len(foreign) >= 2:
        return "multi"
    return next(iter(foreign))


def cell_pads_text(classes, item) -> str:
    """The names of the cell pads a component touches, for the tee warning."""
    labels = sorted(cell_pads(classes.get(getattr(item, "uuid", None), set())))
    names = [".".join(label.split(":")[1:]) for label in labels]
    return " — ".join(names) if names else "?"
