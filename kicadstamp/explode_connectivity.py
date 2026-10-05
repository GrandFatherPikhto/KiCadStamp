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
    "CopperPiece",
    "pad_areas",
    "copper_extent",
    "copper_touch",
    "copper_classes",
    "cell_pad_areas",
    "build_cell_islands",
    "classify_copper",
    "copper_pieces",
    "item_half",
    "prune_dangling",
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
    uf = _union_items(items)
    extents = [copper_extent(it) for it in items]

    classifiers = _ClassifierGrid()
    for pad_area, label, layers in pads:
        classifiers.add_pad(pad_area, label, layers)
    own: list = [set() for _ in range(n)]
    for i, it in enumerate(items):
        half = extents[i][1]
        if isinstance(it, Via):
            for pad_area, label, _layers in classifiers.pads(extents[i][0]):
                if pad_area.contains(it.position, margin=half):
                    own[i].add(label)
        else:
            for pad_area, label, layers in classifiers.pads(extents[i][0]):
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


def _box_cells(box) -> list:
    """The 1 mm grid cells a box ``(x1, y1, x2, y2)`` (nm) spans.

    ONE owner of the broad-phase bucketing (Р1б-1): the copper-vs-copper union
    (``_union_items``) and the copper-vs-classifier pass (``_ClassifierGrid``)
    enumerate cells with it, so the two phases cannot drift apart."""
    cx0 = int(math.floor(box[0] / _GRID_NM))
    cx1 = int(math.floor(box[2] / _GRID_NM))
    cy0 = int(math.floor(box[1] / _GRID_NM))
    cy1 = int(math.floor(box[3] / _GRID_NM))
    return [(cx, cy) for cx in range(cx0, cx1 + 1)
            for cy in range(cy0, cy1 + 1)]


class _ClassifierGrid:
    """1 mm broad phase over the CLASSIFIERS (pads, and an island's own copper).

    The "Разнос" classifies copper against the pads of a few moving clusters; the
    "enclosed copper" selection classifies against EVERY pad on the board, and
    against one island per instance pad — a flat loop over those is millions of
    geometry tests (measured on a synthetic 1800-copper / 1500-pad board: 5.7 s,
    see ``kicadstamp/diagnostics/deepseek_probe_enclosed_copper_scaling_*``). So
    each classifier is bucketed by the AABB of its own area (``PadArea.bounds`` /
    ``copper_extent``), and a copper item queries only the cells its own box
    spans. The candidate set is a SUPERSET; ``_touches_pad`` / ``copper_touch``
    still decide every candidate exactly (the answer never changes)."""

    def __init__(self):
        self._pads: list = []
        self._coppers: list = []
        self._pad_buckets: dict = {}
        self._copper_buckets: dict = {}

    def add_pad(self, area, label, layers) -> None:
        index = len(self._pads)
        self._pads.append((area, label, layers))
        for cell in _box_cells(_bounds_tuple(area.bounds())):
            self._pad_buckets.setdefault(cell, []).append(index)

    def add_copper(self, item, label) -> None:
        box, half = copper_extent(item)
        index = len(self._coppers)
        self._coppers.append((item, half, label))
        for cell in _box_cells(box):
            self._copper_buckets.setdefault(cell, []).append(index)

    def pads(self, box) -> list:
        """The pads whose box shares a cell with ``box`` (superset)."""
        seen = self._hits(self._pad_buckets, box)
        return [self._pads[i] for i in seen]

    def coppers(self, box) -> list:
        """The island copper whose box shares a cell with ``box`` (superset)."""
        seen = self._hits(self._copper_buckets, box)
        return [self._coppers[i] for i in seen]

    @staticmethod
    def _hits(buckets, box) -> set:
        out: set = set()
        for cell in _box_cells(box):
            bucket = buckets.get(cell)
            if bucket:
                out.update(bucket)
        return out


def _bounds_tuple(box) -> tuple:
    """A ``Box2`` as the ``(x1, y1, x2, y2)`` tuple ``_box_cells`` wants."""
    return (box.pos.x, box.pos.y, box.pos.x + box.size.x,
            box.pos.y + box.size.y)


def _union_items(items) -> "UnionFind":
    """Union-find over copper items by shape: 1 mm broad-phase grid, then exact
    pair tests."""
    n = len(items)
    uf = UnionFind(n)
    extents = [copper_extent(it) for it in items]
    grid: dict = {}
    for i, (box, _h) in enumerate(extents):
        for cell in _box_cells(box):
            grid.setdefault(cell, []).append(i)
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


@dataclass
class CopperPiece:
    """One shape-connected piece of copper and the labels it touches.

    ``items`` are the track/via objects of the piece; ``classes`` is the UNION of
    the island / foreign-pad labels every item in it touches (pads classify, they
    never union — see ``classify_copper``)."""

    items: list = field(default_factory=list)
    classes: set = field(default_factory=set)


def _classify_graph(items, islands, foreign_pads):
    """The shared shape-graph and the per-item labels over ``items``.

    Returns ``(uf, own)``: ``uf`` is the union-find over the copper (built by
    ``_union_items``), ``own[i]`` the set of island / foreign-pad labels item ``i``
    touches. ONE owner for BOTH ``classify_copper`` and ``copper_pieces`` — a
    second pass over the same grid would be a copy (map.md §2.1)."""
    uf = _union_items(items)
    extents = [copper_extent(it) for it in items]

    classifiers = _ClassifierGrid()
    for area, label, layers in foreign_pads:
        classifiers.add_pad(area, label, layers)
    for island in islands:
        for area, layers, _name in island.pads:
            classifiers.add_pad(area, island.label, layers)
        for other in island.copper:
            classifiers.add_copper(other, island.label)
    own: list = [set() for _ in range(len(items))]
    for i, it in enumerate(items):
        half = extents[i][1]
        box = extents[i][0]
        for area, label, layers in classifiers.pads(box):
            if _touches_pad(it, half, area, layers):
                own[i].add(label)
        for other, other_half, label in classifiers.coppers(box):
            if copper_touch(it, other, half, other_half):
                own[i].add(label)
    return uf, own


def copper_pieces(tracks, vias, islands, foreign_pads) -> list:
    """EVERY shape-connected piece of copper with the labels it touches.

    Same graph as ``classify_copper`` (one owner: ``_classify_graph``), but the
    pieces come back as OBJECTS instead of a ``{uuid: set}`` map — a caller whose
    question is "which whole pieces touch the cell and no foreign pad" cannot
    answer it from a per-uuid map without re-grouping."""
    items = list(tracks) + list(vias)
    uf, own = _classify_graph(items, islands, foreign_pads)
    grouped: dict = {}
    for i, it in enumerate(items):
        piece = grouped.setdefault(uf.find(i), CopperPiece())
        piece.items.append(it)
        piece.classes.update(own[i])
    return list(grouped.values())


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
    if not items:
        return {}
    uf, own = _classify_graph(items, islands, foreign_pads)
    by_root: dict = {}
    for i in range(len(items)):
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


# ── pruning the branches that HANG off a taken piece (С1-2, 2026-10-05) ──────

def item_half(item) -> float:
    """Half the copper ITEM's own width in nm (a track's ``width/2``, a via's
    radius ``diameter/2``) — the ONE place the pruning takes it from."""
    if isinstance(item, Via):
        return getattr(item, "diameter_mm", 0.0) / 2.0 * MM
    return getattr(item, "width_mm", 0.0) * MM / 2.0


def _point_touches_item(point, half, item) -> bool:
    """A track END (a point, i.e. a degenerate capsule of width 0) touches a
    copper item: a via is a DISC (through), a track a CAPSULE. The caller checks
    the layer for the track-track case. Primitive: ``geometry/copper_connect``."""
    if isinstance(item, Via):
        return disc_touches_capsule(item.position.x, item.position.y,
                                    item_half(item), point.x, point.y,
                                    point.x, point.y, 0.0)
    return capsules_touch(point.x, point.y, point.x, point.y, 0.0,
                          item.start.x, item.start.y, item.end.x, item.end.y,
                          item_half(item) * 2.0)


def _point_touches_pad(point, half, layer, area, layers) -> bool:
    """A point touches an instance pad — ``PadArea.contains(point, margin=half)``,
    by the pad's layer exactly like ``_touches_pad`` (a via passes ``layer=None``:
    it is through)."""
    if layers is not None and layer not in layers:
        return False
    return area.contains(point, margin=half)


def _end_touches(point, half, item, layer, alive, cell_pads) -> bool:
    """Does a track END touch anything of the piece, or an instance pad?"""
    for other in alive:
        if other is item:
            continue
        if isinstance(other, Via):
            if _point_touches_item(point, half, other):
                return True
        elif other.layer == layer and _point_touches_item(point, half, other):
            return True
    for area, layers, _name in cell_pads:
        if _point_touches_pad(point, half, layer, area, layers):
            return True
    return False


def _track_dangles(track, alive, cell_pads) -> bool:
    """A track HANGS when at least one of its ENDS touches neither another
    remaining element of the piece nor an instance pad."""
    half = item_half(track)
    return not (
        _end_touches(track.start, half, track, track.layer, alive, cell_pads)
        and _end_touches(track.end, half, track, track.layer, alive, cell_pads))


def _via_dangles(via, alive, cell_pads) -> bool:
    """A via HANGS when it touches at most ONE other remaining element of the
    piece and no instance pad."""
    half = item_half(via)
    for area, _layers, _name in cell_pads:
        if area.contains(via.position, margin=half):
            return False
    neighbours = 0
    for other in alive:
        if other is via:
            continue
        if isinstance(other, Via):
            if discs_touch(via.position.x, via.position.y, half,
                           other.position.x, other.position.y,
                           item_half(other)):
                neighbours += 1
        elif disc_touches_capsule(via.position.x, via.position.y, half,
                                  other.start.x, other.start.y, other.end.x,
                                  other.end.y, item_half(other) * 2.0):
            neighbours += 1
    return neighbours <= 1


def prune_dangling(items, cell_pads) -> tuple:
    """Trim the branches that HANG off a TAKEN piece (С1-2 of
    ``plan_2026_10_05_select_enclosed_copper``; Denis 2026-10-05: «у меня там
    есть висячие дорожки… а они ну никак не относятся к "закрытой меди"»).

    Iterative leaf pruning to a fixed point: a TRACK with an END touching neither
    another remaining element nor an instance pad; a VIA touching at most one
    remaining element and no instance pad. Remove, repeat; what remains is taken.
    Touch is by SHAPE through ``copper_connect`` / ``PadArea`` ONLY, never by
    point coincidence.

    ``cell_pads`` = [(PadArea, copper_layers, name)] of the instance's pads.
    Returns ``(kept_items, removed_count)``.

    A taken piece is NEVER emptied, and the REASON is a REAL connection (С2-3): a
    track can join two pads by its BODY, not by an end — it runs from pad A
    THROUGH pad B and sticks out a little beyond it, so its far end is free
    ("dangling") although the piece is genuinely connected A↔B. Losing that
    connection is worse than selecting a tiny protrusion, so an emptied piece
    comes back WHOLE (a pure dead-end — reachable only under an explicit
    ``min_cell_pads=1`` — also returns whole). A pad-free RING hung by one track
    is NOT eaten either: every one of its own elements touches two neighbours
    inside the ring, so there is no leaf to start from (a rarity, described here,
    not special-cased)."""
    alive = list(items)
    removed = 0
    changed = True
    while changed:
        changed = False
        surviving = []
        for it in alive:
            dangling = (_via_dangles(it, alive, cell_pads)
                        if isinstance(it, Via)
                        else _track_dangles(it, alive, cell_pads))
            if dangling:
                removed += 1
                changed = True
            else:
                surviving.append(it)
        alive = surviving
    if items and not alive:
        return list(items), 0
    return alive, removed
