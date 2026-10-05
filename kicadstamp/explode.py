# kicadstamp/explode.py
"""The "Разнос" PLAN — read-only (2026-10-05, plan
``plan_2026_10_05_explode_r1_core.md`` §2, design
``design_2026_10_05_explode_cluster.md`` РЗ2/РЗ3/РЗ4).

`plan_explode` reads the live board and the two registries and answers, WITHOUT
writing anything: which FOREIGN cluster instances must move aside for the cell
instance to be read cleanly, which copper moves with each of them, and which
inter-cluster (``net_traces``) pieces the user may take along (the table with
the default "take" ticks). The execution and the journal live in
``kicadstamp/explode_journal.py``.

Reused owners (never a second copy):
  * the cell instance — ``kicadstamp/cell_instance.resolve_context_footprints``;
  * the grouping rule — ``selection_narrowing.group_selection``;
  * "whose copper is this" — ``selection_narrowing.is_own_key``;
  * live ``net_traces`` copper — ``net_trace_planner.find_live_copper``;
  * the pad's own area — ``geometry/pad_area.pad_area_of`` (never KiCad's box);
  * the joint epsilon and the disjoint-set primitive —
    ``geometry/cell_copper_connectivity`` (1e-3 mm) and
    ``geometry/union_find.UnionFind``.

Layers (Ответ Демону, variant b): a track joins a pad ONLY on a layer the pad
actually has (``Pad.copper_layers``; None = unknown = all). A via is through.
A track joins another track only on its own layer; a via joins any layer at its
point. The copper graph is CUT in the cell's pads — they classify a component,
they never union two pieces through themselves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .cell_instance import resolve_context_footprints
from .cluster_matching import cluster_prefix_match
from .config import net_trace_effective_name
from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .domain.board import Footprint, Via
from .domain.geometry import Box2, Vector2
from .geometry.copper_connect import (capsules_touch, disc_touches_capsule,
                                      discs_touch)
from .geometry.pad_area import pad_area_of
from .geometry.union_find import UnionFind
from .i18n import _
from .net_trace_planner import find_live_copper, net_trace_registry_key
from .placement.services.clone_role_resolver import resolve_footprint_by_role
from .registry import load_registry_entries, record_key_part
from .selection_narrowing import (
    FootprintInfo,
    cell_record_addresses,
    group_selection,
    is_own_key,
)
from .sheet_names import resolve_sheet_path_names
from .utils.layers import layer_to_str
from .utils.units import MM

__all__ = [
    "ExplodeError",
    "ExplodePlan",
    "Move",
    "MovedInstance",
    "NetTracePiece",
    "Pose",
    "plan_explode",
]

# Touches values that do NOT tick a table piece by default.
_UNTOUCHED = frozenset({"cell", "none"})


class ExplodeError(Exception):
    """A refusal the caller reports verbatim — never a silent empty plan."""


# ── poses ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pose:
    """One board item's ABSOLUTE pose (nm / degrees). Footprint: x, y, angle;
    via: x, y; track: start and end. ``layer`` is kept for the report only (the
    journal restores positions, not layers)."""

    kind: str
    x: int = 0
    y: int = 0
    angle: float = 0.0
    sx: int = 0
    sy: int = 0
    ex: int = 0
    ey: int = 0
    layer: Any = None

    @classmethod
    def of(cls, item: Any) -> "Pose":
        if isinstance(item, Footprint):
            return cls("footprint", x=item.position.x, y=item.position.y,
                       angle=item.angle_deg, layer=item.layer)
        if isinstance(item, Via):
            return cls("via", x=item.position.x, y=item.position.y)
        return cls("track", sx=item.start.x, sy=item.start.y,
                   ex=item.end.x, ey=item.end.y, layer=item.layer)

    def shifted(self, dx: int, dy: int) -> "Pose":
        return Pose(self.kind, x=self.x + dx, y=self.y + dy, angle=self.angle,
                    sx=self.sx + dx, sy=self.sy + dy,
                    ex=self.ex + dx, ey=self.ey + dy, layer=self.layer)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "x": self.x, "y": self.y,
                "angle": self.angle, "sx": self.sx, "sy": self.sy,
                "ex": self.ex, "ey": self.ey}

    @classmethod
    def from_dict(cls, raw: dict) -> "Pose":
        return cls(kind=raw["kind"], x=raw.get("x", 0), y=raw.get("y", 0),
                   angle=raw.get("angle", 0.0), sx=raw.get("sx", 0),
                   sy=raw.get("sy", 0), ex=raw.get("ex", 0),
                   ey=raw.get("ey", 0))


@dataclass(frozen=True)
class Move:
    """One item the plan will shift: uuid, kind, ref (footprints) and the
    absolute before/after poses."""

    uuid: str
    kind: str
    before: Pose
    after: Pose
    ref: Optional[str] = None


# ── plan value objects ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class MovedInstance:
    """A foreign cluster instance that leaves the cell's area WHOLE."""

    cluster: str
    sheet: Optional[str]
    refs: tuple
    footprints: tuple
    copper: tuple
    vector: tuple  # (dx_nm, dy_nm)

    @property
    def label(self) -> str:
        return _cluster_label(self.cluster, self.sheet)


@dataclass(frozen=True)
class NetTracePiece:
    """One row of the inter-cluster copper table: a single live piece of a
    ``net_traces`` record, its classification and the "take" tick."""

    record: str
    kind: str          # "via" | "track"
    net: str
    layer: str
    length_mm: float
    uuid: str
    touches: str       # "cell" | "<cluster>/<sheet>" | "none" | "tee"
    ticked: bool
    item: Any = field(compare=False, repr=False)
    vector: tuple = (0, 0)


@dataclass
class ExplodePlan:
    """Everything the tab (Р2) and the CLI (Р1) show. Read-only: nothing here
    has touched the board."""

    cell_name: str
    cluster: str
    sheet: Optional[str]
    instance: tuple
    area: Box2
    instances: tuple
    table: tuple
    moves: tuple
    warnings: tuple = ()
    config_path: str = ""
    margin_mm: float = 5.0
    gap_mm: float = 5.0

    @property
    def label(self) -> str:
        return _cluster_label(self.cluster, self.sheet)


# ── small geometry helpers ──────────────────────────────────────────────────

def _cluster_label(cluster: Any, sheet: Any) -> str:
    return f"{cluster}/{sheet}" if sheet else str(cluster)


def _box_map(adapter, items) -> dict:
    """{uuid: Box2|None} from ONE ``get_bounding_boxes`` call (plan §2)."""
    items = list(items)
    boxes = adapter.get_bounding_boxes(items) if items else []
    return {getattr(it, "uuid", None): boxes[i] if i < len(boxes) else None
            for i, it in enumerate(items)}


def _boxes_overlap(a: Box2, b: Box2) -> bool:
    return (a.pos.x <= b.pos.x + b.size.x and b.pos.x <= a.pos.x + a.size.x
            and a.pos.y <= b.pos.y + b.size.y and b.pos.y <= a.pos.y + a.size.y)


def _union_boxes(boxes) -> Optional[Box2]:
    real = [b for b in boxes if b is not None]
    if not real:
        return None
    x1 = min(b.pos.x for b in real)
    y1 = min(b.pos.y for b in real)
    x2 = max(b.pos.x + b.size.x for b in real)
    y2 = max(b.pos.y + b.size.y for b in real)
    return Box2(pos=Vector2(x1, y1), size=Vector2(x2 - x1, y2 - y1))


def _inflate(box: Box2, margin_nm: int) -> Box2:
    return Box2(pos=Vector2(box.pos.x - margin_nm, box.pos.y - margin_nm),
                size=Vector2(box.size.x + 2 * margin_nm,
                             box.size.y + 2 * margin_nm))


def _box_center(box: Box2) -> tuple:
    return box.pos.x + box.size.x // 2, box.pos.y + box.size.y // 2


def _offset_to_leave(area: Box2, box: Box2, ux: float, uy: float,
                     gap_nm: int) -> tuple:
    """The rigid offset (dx, dy) that takes ``box`` out of ``area`` along the
    unit direction (ux, uy), plus the gap. A zero-length ray is +X."""
    if ux == 0.0 and uy == 0.0:
        ux = 1.0
    if not _boxes_overlap(box, area):
        dx, dy = 0.0, 0.0
    else:
        ts = []
        if ux > 0.0:
            ts.append((area.pos.x + area.size.x - box.pos.x) / ux)
        elif ux < 0.0:
            ts.append((area.pos.x - (box.pos.x + box.size.x)) / ux)
        if uy > 0.0:
            ts.append((area.pos.y + area.size.y - box.pos.y) / uy)
        elif uy < 0.0:
            ts.append((area.pos.y - (box.pos.y + box.size.y)) / uy)
        t = max(0.0, min(ts)) if ts else 0.0
        dx, dy = t * ux, t * uy
    dx += gap_nm * ux
    dy += gap_nm * uy
    return int(round(dx)), int(round(dy))


def _ray(origin: tuple, target: tuple) -> tuple:
    """The unit direction from ``origin`` to ``target``; (1, 0) when equal."""
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    norm = math.hypot(dx, dy)
    if norm == 0.0:
        return 1.0, 0.0
    return dx / norm, dy / norm


# ── copper connectivity ─────────────────────────────────────────────────────

def _pad_areas(adapter, class_fps: dict) -> list:
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


# The broad-phase grid for the connectivity (Р1б-1): 1 mm buckets on the items'
# bounding boxes, so a board is never scanned pairwise in full.
_GRID_NM = 1_000_000


def _copper_extent(item) -> tuple:
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


def _copper_touch(a, b, ha: float, hb: float) -> bool:
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


def _copper_classes(tracks, vias, pads) -> dict:
    """{uuid: set(pad classes)} for every copper item (tracks + vias).

    Connectivity is by SHAPE, the way KiCad sees it (Р1б-1): a track is a capsule
    (width), a via a disc (diameter, through). Broad phase over 1 mm buckets, then
    exact distance per candidate pair. Pads are CLASSIFIERS, never unioners — the
    graph is cut in the cell's pads. Keyed by UUID because the adapter hands out
    COPIES: two reads of the same item are equal by uuid, not by identity."""
    items = list(tracks) + list(vias)
    n = len(items)
    out: dict = {getattr(it, "uuid", None): set() for it in items}
    if n == 0:
        return out
    uf = UnionFind(n)
    extents = [_copper_extent(it) for it in items]

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
                if _copper_touch(items[i], items[j],
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


def _cell_pads(classes: set) -> set:
    """The CELL-pad labels of a component (``cell`` or ``cell:<ref>:<pad>``)."""
    return {c for c in classes if c == "cell" or c.startswith("cell:")}


def _foreign_labels(classes: set) -> set:
    """The foreign-instance labels (``<cluster>/<sheet>``) of a component."""
    return {c for c in classes
            if not (c == "cell" or c.startswith("cell:"))}


def _classify(classes: set) -> str:
    """cell / <cluster>/<sheet> / tee / multi / none (Р1а-1).

    An ordinary inter-cluster track runs from ONE cell pad to ONE foreign pad
    (D23 -> PIF): that is the FOREIGN label, NOT ``tee``. ``tee`` is the DANGEROUS
    case the plan names: a component joining TWO DIFFERENT cell pads (the cell's
    inner piece) AND a foreign pad — the inner piece would leave with the foreign
    cluster. ``multi`` — several foreign instances without the tee condition —
    is its own label so ``tee`` means one thing."""
    cell = _cell_pads(classes)
    foreign = _foreign_labels(classes)
    if not cell and not foreign:
        return "none"
    if not foreign:
        return "cell"
    if len(cell) >= 2:
        return "tee"
    if len(foreign) >= 2:
        return "multi"
    return next(iter(foreign))


def _cell_pads_text(classes, item) -> str:
    """The names of the cell pads a component touches, for the tee warning."""
    labels = sorted(_cell_pads(classes.get(getattr(item, "uuid", None), set())))
    names = [".".join(label.split(":")[1:]) for label in labels]
    return " — ".join(names) if names else "?"


# ── the plan ────────────────────────────────────────────────────────────────

def _info(adapter, fp, sheet_names) -> FootprintInfo:
    return FootprintInfo(
        item=fp,
        role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
        cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
        sheet=tuple(resolve_sheet_path_names(fp, sheet_names) or ()))


def _nt_rows(cfg, adapter, sheet_names, instance_refs, classes, via_entries,
             track_entries, tick_overrides, cell_address) -> list:
    """The inter-cluster table pieces (a flat tuple of rows), grouped by record.

    Р1а-4: a record is read ONLY when it can touch the cell — its anchor ADDRESS
    (sheet/cluster) matches the cell instance, or (no address stored) its anchor
    resolves onto the instance. Everything else (the other channels) is skipped
    BEFORE find_live_copper, whose plan_net_traces otherwise logs half a dozen
    INFO lines per record. Its pieces come from the product's own
    ``find_live_copper`` (never a second calculation)."""

    class _Reg:
        def __init__(self, entries):
            self.entries = entries

    vreg, treg = _Reg(via_entries), _Reg(track_entries)
    out = []
    for nt in getattr(cfg, "net_traces", ()) or ():
        if not _record_touches_cell(adapter, nt, sheet_names, instance_refs,
                                    cell_address, via_entries, track_entries,
                                    classes):
            continue
        name = str(net_trace_effective_name(nt))
        try:
            live = find_live_copper(adapter, nt, via_registry=vreg,
                                    track_registry=treg, sheet_names=sheet_names)
        except Exception:  # noqa: BLE001 — a read must never crash the plan
            continue
        pieces = [p.live for p in getattr(live, "pieces", ()) if p.live]
        if not pieces:
            continue
        for item in pieces:
            touches = _classify(
                classes.get(getattr(item, "uuid", None), set()))
            uuid = str(getattr(item, "uuid", ""))
            ticked = touches not in _UNTOUCHED
            if uuid in (tick_overrides or {}):
                ticked = bool(tick_overrides[uuid])
            out.append(NetTracePiece(
                record=name,
                kind="via" if isinstance(item, Via) else "track",
                net=str(getattr(item, "net_name", "") or ""),
                layer=(_("(via)") if isinstance(item, Via)
                       else layer_to_str(item.layer)),
                length_mm=_length_mm(item),
                uuid=uuid, touches=touches, ticked=ticked, item=item))
    return out


def _anchor_on_instance(adapter, nt, sheet_names, instance_refs) -> bool:
    if not getattr(nt, "anchor_role", None):
        return False
    try:
        anchor = resolve_footprint_by_role(
            adapter, nt.anchor_role, getattr(nt, "anchor_sheet", None),
            getattr(nt, "anchor_cluster", None), sheet_names,
            label=_("net_traces anchor"))
    except Exception:  # noqa: BLE001 — an unresolvable anchor is a normal answer
        return False
    return getattr(anchor, "uuid", None) in instance_refs


def _record_touches_cell(adapter, nt, sheet_names, instance_refs, cell_address,
                         via_entries, track_entries, classes) -> bool:
    """Р1б-3: read a net_traces record when it CAN touch the cell — its anchor
    SHEET equals the cell sheet, or its anchor CLUSTER matches (a cross-sheet
    record the FPGA case needs), or (no address stored) its anchor resolves onto
    the instance, OR one of its REGISTRY pieces sits in a component touching a
    cell pad. Only the last needs no anchor resolve at all (the registry + the
    already-computed classes)."""
    a_sheet = getattr(nt, "anchor_sheet", None)
    a_cluster = getattr(nt, "anchor_cluster", None)
    c_cluster, c_sheet = cell_address
    if a_sheet is not None and c_sheet is not None and a_sheet == c_sheet:
        return True
    if a_cluster is not None and c_cluster is not None and cluster_prefix_match(
            str(c_cluster), str(a_cluster)):
        return True
    if a_sheet is None and a_cluster is None and _anchor_on_instance(
            adapter, nt, sheet_names, instance_refs):
        return True
    return _record_has_cell_piece(nt, via_entries, track_entries, classes)


def _record_has_cell_piece(nt, via_entries, track_entries, classes) -> bool:
    """True when a uuid the REGISTRY wrote for this record (no anchor resolve)
    belongs to a component that touches a cell pad."""
    for kind, entries in (("vias", via_entries), ("tracks", track_entries)):
        for i in range(len(getattr(nt, kind, ()) or ())):
            entry = (entries or {}).get(net_trace_registry_key(nt, i))
            uuid = getattr(entry, "uuid", None)
            if uuid is not None and _cell_pads(classes.get(uuid, set())):
                return True
    return False


def _length_mm(item) -> float:
    if isinstance(item, Via):
        return 0.0
    return math.hypot(item.end.x - item.start.x,
                      item.end.y - item.start.y) / MM


def cell_identity(cell_name: str, cell) -> str:
    """THE cell's registry identity (a registry key's TEMPLATE part) — the SAME
    value the redraw writes: ``record_key_part(cell_name, cell.uuid)``
    (clone_position_calculator / manual_position_calculator build every cell key
    with it). ONE builder, so the plan and the redraw can never disagree."""
    return record_key_part(cell_name, getattr(cell, "uuid", None))


def _cell_pairs_for_instance(cfg, inst_key) -> list:
    """[(cell_identity, addresses)] for every CELL the config places on the
    foreign instance `inst_key` = (cluster, sheet).

    Р1а-3: the first element is the CELL identity (the key's TEMPLATE part), NOT
    the identity of the record (entity/clone) — ``cell_record_addresses`` VALUES
    are addresses, and ``is_own_key`` compares the key's template part against
    the CELL identity. Passing the record identity made ``is_own_key`` always
    False, so NO foreign-instance copper was ever found. The instance is chosen
    by ADDRESS (any record of the cell stands on `inst_key`); one pair per cell."""
    out = []
    for cell_name, cell in (getattr(cfg, "cells", {}) or {}).items():
        addresses = cell_record_addresses(cfg, cell_name)
        if any(_address_is(addr, inst_key) for addr in addresses.values()):
            out.append((cell_identity(cell_name, cell), addresses))
    return out


def _address_is(address, inst_key) -> bool:
    r_cluster, r_sheet = address
    c_cluster, c_sheet = inst_key
    if r_cluster and c_cluster and not cluster_prefix_match(str(c_cluster),
                                                            str(r_cluster)):
        return False
    if r_sheet and c_sheet and r_sheet != c_sheet:
        return False
    return True


def _append_move(moves: list, item, dx: int, dy: int) -> None:
    uuid = getattr(item, "uuid", None)
    if uuid is None:
        return
    before = Pose.of(item)
    moves.append(Move(uuid=str(uuid), kind=before.kind,
                      ref=getattr(item, "ref", None), before=before,
                      after=before.shifted(dx, dy)))


def _component_foreign_labels(classes, item, moving_labels) -> list:
    return [c for c in
            _foreign_labels(classes.get(getattr(item, "uuid", None), set()))
            if c in moving_labels]


def plan_explode(adapter, cfg, config_path: str, cell_name: str, cluster: str,
                 sheet, sheet_names, *, margin_mm: float = 5.0,
                 gap_mm: float = 5.0, tick_overrides: Optional[dict] = None
                 ) -> ExplodePlan:
    """Read the board + registries and build the explode plan. Writes NOTHING."""
    margin_nm = int(round(margin_mm * MM))
    gap_nm = int(round(gap_mm * MM))
    sheet_names = dict(sheet_names or {})
    board_fps = list(adapter.get_footprints())
    tracks = list(adapter.get_tracks())
    vias = list(adapter.get_vias())

    instance = resolve_context_footprints(
        adapter, board_fps, cluster, sheet, sheet_names)
    if not instance:
        raise ExplodeError(_(
            "no instance of {cell} on the board for {where}").format(
                cell=cell_name, where=_cluster_label(cluster, sheet)))
    instance_refs = frozenset(getattr(f, "ref", None) for f in instance)
    instance_uuids = {getattr(f, "uuid", None) for f in instance}

    fp_boxes = _box_map(adapter, board_fps)

    infos = [_info(adapter, fp, sheet_names) for fp in board_fps]
    groups = group_selection(infos, getattr(cfg, "entities", ()) or ())
    cell_keys = {key for key, members in groups.items()
                 if any(getattr(m.item, "uuid", None) in instance_uuids
                        for m in members)}

    area = _union_boxes([fp_boxes.get(u) for u in instance_uuids])
    if area is None:
        raise ExplodeError(_(
            "the instance of {cell} has no measurable frame").format(
                cell=cell_name))
    area = _inflate(area, margin_nm)
    area_center = _box_center(area)

    # ── foreign instances that touch the area (they leave WHOLE) ────────────
    moving_groups = []  # ((cluster, sheet), [fps], box)
    for key, members in groups.items():
        if key in cell_keys:
            continue
        fps = [m.item for m in members]
        boxes = [fp_boxes.get(getattr(f, "uuid", None)) for f in fps]
        if not any(b is not None and _boxes_overlap(b, area) for b in boxes):
            continue
        moving_groups.append((key, fps, _union_boxes(boxes)))

    # ── registry: which copper belongs to a moving foreign instance ─────────
    via_entries, track_entries, owner = load_registry_entries(config_path, cfg)
    foreign_own = [(key, frozenset(getattr(f, "ref", None) for f in fps),
                    _cell_pairs_for_instance(cfg, key))
                   for (key, fps, _box) in moving_groups]

    def _foreign_owner(key_str: str):
        for (inst_key, refs, pairs) in foreign_own:
            for identity, addresses in pairs:
                if is_own_key(key_str, identity, addresses, inst_key, refs):
                    return inst_key
        return None

    moving_copper: dict = {key: [] for (key, _f, _b) in moving_groups}
    unregistered = []
    for item in list(tracks) + list(vias):
        uuid = getattr(item, "uuid", None)
        key_str = owner.get(uuid)
        if key_str is None:
            unregistered.append(item)
            continue
        inst_key = _foreign_owner(key_str)
        if inst_key is not None:
            moving_copper[inst_key].append(item)

    # ── pad classifiers (cell + every foreign instance) ─────────────────────
    class_fps: dict = {"cell": list(instance)}
    for key, fps, _box in moving_groups:
        class_fps[_cluster_label(*key)] = fps
    pads = _pad_areas(adapter, class_fps)

    classes = _copper_classes(tracks, vias, pads)

    # ── unregistered copper whose whole component touches ONE moving instance
    moving_labels = {_cluster_label(*k): k for (k, _f, _b) in moving_groups}
    for item in unregistered:
        cl = classes.get(getattr(item, "uuid", None), set())
        if _cell_pads(cl) or len(_foreign_labels(cl)) != 1:
            continue
        label = next(iter(_foreign_labels(cl)))
        if label in moving_labels:
            moving_copper[moving_labels[label]].append(item)

    # ── positions: vectors, moved instances, moves ──────────────────────────
    instances = []
    moves = []
    warnings = []
    for (key, fps, box) in moving_groups:
        ux, uy = _ray(area_center, _box_center(box))
        dx, dy = _offset_to_leave(area, box, ux, uy, gap_nm)
        copper = tuple(moving_copper.get(key, ()))
        instances.append(MovedInstance(
            cluster=key[0], sheet=key[1],
            refs=tuple(sorted(r for r in (getattr(f, "ref", None) for f in fps)
                              if r)),
            footprints=tuple(fps), copper=copper, vector=(dx, dy)))
        for item in list(fps) + list(copper):
            _append_move(moves, item, dx, dy)

    # ── the inter-cluster table + its ticked movers ─────────────────────────
    table = _nt_rows(cfg, adapter, sheet_names, instance_refs, classes,
                     via_entries, track_entries, tick_overrides or {},
                     (cluster, sheet))
    vector_by_label = {inst.label: inst.vector for inst in instances}
    final_table = []
    for piece in table:
        vector = (0, 0)
        if piece.touches == "tee":
            warnings.append(_(
                "T-branch: the cell's inner part ({pads}) leaves with the "
                "foreign cluster").format(
                    pads=_cell_pads_text(classes, piece.item)))
        elif piece.touches == "multi":
            warnings.append(_(
                "piece {uuid} touches several foreign clusters — it moves with "
                "the first; check by hand").format(uuid=piece.uuid))
        if piece.ticked:
            if piece.touches in vector_by_label:
                vector = vector_by_label[piece.touches]
            elif piece.touches in ("tee", "multi"):
                foreign = _component_foreign_labels(
                    classes, piece.item, moving_labels)
                vector = (vector_by_label.get(foreign[0], (0, 0))
                          if foreign else (0, 0))
            else:  # "cell"/"none": its own ray from its own frame (tick = leaves)
                box = _box_map(adapter, [piece.item]).get(piece.uuid)
                if box is not None:
                    ux, uy = _ray(area_center, _box_center(box))
                    vector = _offset_to_leave(area, box, ux, uy, gap_nm)
            if vector != (0, 0):
                _append_move(moves, piece.item, *vector)
        final_table.append(NetTracePiece(
            record=piece.record, kind=piece.kind, net=piece.net,
            layer=piece.layer, length_mm=piece.length_mm, uuid=piece.uuid,
            touches=piece.touches, ticked=piece.ticked, item=piece.item,
            vector=vector))

    return ExplodePlan(
        cell_name=cell_name, cluster=cluster, sheet=sheet,
        instance=tuple(instance), area=area, instances=tuple(instances),
        table=tuple(final_table), moves=tuple(moves), warnings=tuple(warnings),
        config_path=str(config_path), margin_mm=margin_mm, gap_mm=gap_mm)
