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
from dataclasses import dataclass, field

from .domain.board import Via
from .geometry.copper_connect import (capsules_touch, disc_touches_capsule,
                                      discs_touch)
from .geometry.pad_area import pad_area_of
from .geometry.union_find import UnionFind
from .utils.units import MM

__all__ = [
    "CellIsland",
    "pad_areas",
    "copper_extent",
    "copper_touch",
    "copper_classes",
    "cell_pad_areas",
    "build_cell_islands",
    "classify_copper",
    "cell_pads",
    "foreign_labels",
    "classify",
    "cell_pads_text",
    "islands_text",
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


def cell_pad_areas(adapter, fps) -> list:
    """[(PadArea, copper_layers, name)] for the pads of ``fps`` (Р1в-1) — the
    cell instance's own pads, so ``build_cell_islands`` can join them to the
    cell's own copper. ``name`` is ``<ref>.<pad>`` for the tee warning."""
    out = []
    for fp in fps:
        ref = getattr(fp, "ref", None) or getattr(fp, "uuid", "?")
        for pad in adapter.get_footprint_pads(fp):
            area = pad_area_of(pad)
            if area is not None:
                out.append((area, getattr(pad, "copper_layers", None),
                            f"{ref}.{getattr(pad, 'number', '?')}"))
    return out


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


@dataclass
class CellIsland:
    """One connected island of the CELL's own copper + the pads it joins
    (Р1в-1). ``pads`` = [(PadArea, copper_layers, name)]; ``copper`` = the cell's
    own copper items in it. The label is what a foreign piece reports when it
    touches this island."""

    label: str
    pads: list = field(default_factory=list)
    copper: list = field(default_factory=list)


def _touches_pad(item, half: float, area, layers) -> bool:
    if isinstance(item, Via):
        return area.contains(item.position, margin=half)
    if layers is not None and item.layer not in layers:
        return False
    return area.segment_touches(item.start, item.end, margin=half)


def _island_key(island: "CellIsland") -> tuple:
    names = sorted(name for _a, _l, name in island.pads)
    uuids = sorted(str(getattr(it, "uuid", "")) for it in island.copper)
    return (names or ["~"], uuids)


def build_cell_islands(cell_pads, cell_own) -> list:
    """Islands of the CELL's own copper and pads (Р1в-1).

    ``cell_pads`` = [(PadArea, copper_layers, name)] of the cell instance's pads;
    ``cell_own`` = the copper items the registry recorded for THIS cell at THIS
    instance (``is_own_key``). Council of the cell's own elements: a pad and a
    copper item are joined when their shapes touch, copper items by ``copper_touch``.
    Returns a DETERMINISTIC list of CellIsland (``cell:0``, ``cell:1``, ...)."""
    items = list(cell_own)
    n = len(items)
    uf = UnionFind(n + len(cell_pads))
    ext = [copper_extent(it) for it in items]
    for i in range(n):
        for j in range(i + 1, n):
            if copper_touch(items[i], items[j], ext[i][1], ext[j][1]):
                uf.union(i, j)
    for pi, (area, layers, _name) in enumerate(cell_pads):
        node = n + pi
        for i, it in enumerate(items):
            if _touches_pad(it, ext[i][1], area, layers):
                uf.union(node, i)

    groups: dict = {}
    for pi, pad in enumerate(cell_pads):
        groups.setdefault(uf.find(n + pi), CellIsland("")).pads.append(pad)
    for i, it in enumerate(items):
        groups.setdefault(uf.find(i), CellIsland("")).copper.append(it)
    ordered = sorted(groups.values(), key=_island_key)
    return [CellIsland(label=f"cell:{i}", pads=g.pads, copper=g.copper)
            for i, g in enumerate(ordered)]


def _union_items(items) -> "UnionFind":
    """Union-find over copper items by shape: 1 mm broad-phase grid, then exact
    pair tests."""
    n = len(items)
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
    return uf


def classify_copper(tracks, vias, islands, foreign_pads,
                    skip_uuids=frozenset()) -> dict:
    """{uuid: set(labels)} for the copper the CLASSIFICATION graph runs on (Р1в-1).

    The CELL's own copper (``skip_uuids``) is NOT a node — it always stays put —
    but it and the cell's pads are CLASSIFIERS through their island: a piece that
    touches an island reports ``cell:<i>``. A foreign pad reports its
    ``<cluster>/<sheet>``. Components are the shape-connected ones (track-track
    same layer, via through)."""
    items = [it for it in list(tracks) + list(vias)
             if getattr(it, "uuid", None) not in skip_uuids]
    n = len(items)
    if n == 0:
        return {}
    uf = _union_items(items)
    extents = [copper_extent(it) for it in items]
    island_cu = [(island.label,
                  [(it, copper_extent(it)) for it in island.copper])
                 for island in islands]

    def _touches_island(item, half, island):
        for area, layers, _name in island.pads:
            if _touches_pad(item, half, area, layers):
                return True
        for other, other_ext in island_cu_by_label[island.label]:
            if copper_touch(item, other, half, other_ext[1]):
                return True
        return False

    island_cu_by_label = dict(island_cu)
    own: list = [set() for _ in range(n)]
    for i, it in enumerate(items):
        half = extents[i][1]
        for area, label, layers in foreign_pads:
            if _touches_pad(it, half, area, layers):
                own[i].add(label)
        for island in islands:
            if _touches_island(it, half, island):
                own[i].add(island.label)
    by_root: dict = {}
    for i in range(n):
        by_root.setdefault(uf.find(i), set()).update(own[i])
    out: dict = {}
    for i, it in enumerate(items):
        out[getattr(it, "uuid", None)] = set(by_root[uf.find(i)])
    return out


def cell_pads(classes: set) -> set:
    """The CELL-pad / cell-island labels of a component (``cell`` or ``cell:...``)."""
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


def islands_text(classes, item, islands) -> str:
    """The pad names of EACH cell island a component touches, islands joined by
    ``" | "`` (Р1в-1). The tee warning names the pads of BOTH islands, e.g.
    ``C130.1, C131.1 | R5.2``."""
    touched = cell_pads(classes.get(getattr(item, "uuid", None), set()))
    by_label = {island.label: island for island in islands}
    parts = []
    for label in sorted(touched):
        island = by_label.get(label)
        if island is None:
            continue
        names = sorted(name for _a, _l, name in island.pads)
        parts.append(", ".join(names) if names else label)
    return " | ".join(parts) if parts else "?"
