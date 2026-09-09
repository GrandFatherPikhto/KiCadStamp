# gui/docks/cell_anchor_view.py
"""Cell-anchor editor — a Config right-QView page + context-menu action (Phase C
of plan_2026_09_09_cell_anchor_v2_declarative_and_board_overlay.md).

The Cell's full form (Components/Vias/Tracks/Nested — CellDock in its dialog)
is deliberately untouched by this plan; this widget edits ONLY the anchor.
It replaces the old "compute the number at save time" mechanism with the v2
declarative model (§0.1): the anchor is a REFERENCE, resolved at apply time
(Phase A — resolve_pad_mount). Two source shapes, two tabs:

  - Component: writes anchor_role (+ optional anchor_pad), popping anchor_xy
    so the live pad-role resolution (Phase A) actually kicks in (GUARD 1).
    Role-only = the component's stored centre offset (offline, no board);
    Role+Pad = resolved at apply time from a LIVE instance of that role's pad.
  - Marker: the user places a draggable marker circle (and the cell's bbox
    rectangle) as REAL KiCad graphics on a user layer (gui/board_overlay.py),
    drags the marker with KiCad's own tools, then "Read position" converts the
    dragged world point into the cell's own bbox frame and writes anchor_xy,
    clearing anchor_role/anchor_pad.

Working context (Sheet optional / Cluster) — the first (Component) tab hosts
the Sheet/Cluster pickers (Denis, 2026-09-09: "У нас должны быть выбраны в
первом табе Лист(если он нужен)/Кластер. Тогда живой инстанс будет
работать"). A Cell is abstract and may be placed several times (channels), so
the Marker tab's world<->cell-local mapping (read_clone_origin_live,
_world_pos_to_cell_local_offset — reused, NOT reimplemented) needs to know
WHICH placed instance is meant: the view finds the placement of this cell
whose own cluster matches the working Cluster (optionally narrowed by Sheet)
among the UNION of the top-level clone_placements and the placements
materialized from the entity trees (C.5.1 — a cell may be placed only as an
entity, e.g. pif_3v3_vdd -> entity pif_3v3_vdd_mcu). The lookup runs on the
WORKER thread (_resolve_context_then), because materializing the entity half
reads the trees' anchors live from the board. Without such a placed instance
the Marker buttons explain what to do (place the cell first); they never
guess.

Entry read/write uses the SAME path CellDock/Placer use
(find_dict_entry_file + read_data / merge_write — the pair that Phase B will
move here from placer.py; original placer copies stay untouched until B).

All overlay IPC runs on a worker thread via gui/worker.start_long_op (no
synchronous adapter calls on the UI thread). No background selection-polling
thread (§0.9): read-from-selection is an explicit button. Live board is needed
ONLY for that button and for the Marker tab's live frame — hand-picking
Role/Pad and saving works with connection.board = None (proven by test).

The overlay layer / stroke / marker geometry are read from gui_state.json
(Settings > "Board overlay") through gui.board_overlay's accessors — the
board_overlay module constants are only the DEFAULTS (Phase D). No colour is
set anywhere — graphics take their LAYER's colour (§0.6).
"""
import logging
from pathlib import Path
from typing import Any, Optional

from kipy.board_types import Pad as KipyPad

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from kicadstamp.cell_geometry_refresh import cell_content_bbox
from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config import (
    clone_placement_effective_name,
    load_config,
)
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.i18n import _
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.utils.units import MM

from .. import board_overlay, settings
from ..cell_edit_context import (
    cluster_present_on_board,
    remember_cell_edit_context,
    remembered_cell_edit_context,
)
from ..worker import start_long_op
from ._common import (
    ERROR_STYLE as _ERROR_STYLE,
    SUCCESS_STYLE as _SUCCESS_STYLE,
    WARN_STYLE as _WARN_STYLE,
    configure_searchable,
    merge_write,
    read_data,
    set_combo_items,
    show_message,
)
from .live_position import (
    _world_pos_to_cell_local_offset,
    read_clone_origin_live,
)
from .rename import find_dict_entry_file

logger = logging.getLogger(__name__)

# C.5.1 — a worker's "no result" marker: the cell has NO placed instance in
# the working Cluster/Sheet (neither a top-level clone_placement nor an entity
# placement materialized from the trees). Resolution runs on the worker thread
# (materializing the entities reads the live tree anchors), so the sentinel is
# returned instead of a value and the UI turns it into the "place the cell
# first" hint — never a crash. `None` is NOT usable here: it is a legitimate
# worker result ("no geometry" / "marker gone").
_NO_CLONE = object()

# The gui_state.json key holding the currently-drawn overlay uuids, scoped by
# the root config and the cell. Owned by gui.board_overlay (Phase D — the
# whole-map helpers persisted_overlay_uuids/clear_persisted_overlay and the
# exit sweep live there); this alias keeps the existing per-cell call sites.
_OVERLAY_STATE_KEY = board_overlay.OVERLAY_STATE_KEY


# ────────────────────────────────────────────────────────────────────────────
# Phase-D shutdown cleanup (D.2) — remove EVERY persisted overlay shape.
# ────────────────────────────────────────────────────────────────────────────

def _wait_long_op_done(controller, timeout_s: float) -> bool:
    """Spin a nested event loop until the long op finishes or `timeout_s`
    passes; returns True when an event loop was actually spun. The shutdown
    path must never race the worker thread (the op runs on it via
    start_long_op), so quitting waits for the removal to land."""
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return False
    loop = QEventLoop()
    controller.finished.connect(loop.quit)
    controller.failed.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(int(timeout_s * 1000))
    loop.exec()
    timer.stop()
    return True


def cleanup_all_overlays_sync(connection, timeout_s: float = 5.0) -> None:
    """GUI-shutdown cleanup of EVERY persisted overlay shape (Phase D D.2) —
    the by-uuid fast path across all roots/cells of cell_anchor_overlay. The
    removal IPC runs on the worker thread via start_long_op (never a
    synchronous adapter call on the UI thread); this function then waits for
    it with a BOUNDED nested event loop, so quitting never races the worker
    and a dead socket never stalls shutdown for more than timeout_s.

    No-op (persisted map left for a later whole-layer sweep) when there is
    nothing persisted, no live board, another long op is already on the
    shared kipy socket, or the removal did not finish within timeout_s."""
    uuids = board_overlay.persisted_overlay_uuids()
    if not uuids:
        return
    board = getattr(connection, "board", None)
    adapter = getattr(board, "adapter", None) if board is not None else None
    if adapter is None:
        return
    if getattr(connection, "long_op_active", False):
        return  # never interleave on the shared kipy REQ socket
    controller = start_long_op(
        connection, [], board_overlay.remove_overlay,
        lambda _ok: None, lambda _message: None, adapter, uuids)
    waited = _wait_long_op_done(controller, timeout_s)
    # Clear the persisted map only once the removal has genuinely finished (a
    # timeout while the op is still on the socket means the shapes may still
    # be there — leave the state for a later whole-layer sweep).
    if waited and not getattr(connection, "long_op_active", False):
        board_overlay.clear_persisted_overlay()


# ────────────────────────────────────────────────────────────────────────────
# Pure selection helpers (no Qt — unit-testable without a QApplication)
# ────────────────────────────────────────────────────────────────────────────

def _pad_uuid(pad: Any) -> Optional[str]:
    """The uuid of a pad object (domain Pad DTO or a raw kipy pad)."""
    k = getattr(pad, "_kipy", None)
    if k is not None and getattr(k, "id", None) is not None:
        return str(k.id.value)
    raw_id = getattr(pad, "id", None)
    if raw_id is not None:
        return str(getattr(raw_id, "value", raw_id))
    return None


def find_pad_owner(adapter, footprints, pad_uuid: str):
    """The footprint owning the pad with this uuid — a Pad carries NO reference
    to its footprint (§0.2); the owner is found by matching the pad uuid
    against every footprint's cached pads (get_footprint_pads reads the cached
    definition, not the API — ~2 ms over 325 footprints)."""
    for fp in footprints or []:
        for pad in adapter.get_footprint_pads(fp):
            if _pad_uuid(pad) == pad_uuid:
                return fp
    return None


def read_anchor_source(adapter, items, cell_roles, cell_name: str,
                       label: str = "cell anchor") -> dict:
    """Read the current board selection and classify it into the Component
    tab's three cases (§0.2 / C.2):

      - a pad is selected -> its owner footprint is found by uuid scan; the
        footprint's Role + Cluster and the pad's number are returned;
      - a footprint is selected -> Role + Cluster, pad left unset;
      - only a Via/Track (or non-content graphics) -> kind 'via'/'other' (NOT
        an error — the caller tells the user this is the Marker tab's case,
        never "nothing selected").

    Fatal (ValidationError, never a silent guess — the project's convention):
      * nothing selected;
      * the selection spans SEVERAL different Clusters (C.3 — never "take the
        first"); the message enumerates them;
      * several different roles among the selected content;
      * a pad whose owner footprint is not on the board;
      * the inferred role is not one of this cell's own components.

    Returns {kind: 'pad'|'footprint'|'via'|'other', role, pad, cluster}.
    `cell_roles` may be [] (unknown cell) — role-vs-cell validation is skipped.
    """
    items = list(items or [])
    if not items:
        raise ValidationError(format_fatal_error(
            _("{label}: nothing is selected on the board — select one component "
              "(or one of its pads) of cell {cell!r}").format(
                  label=label, cell=cell_name),
            [_("click a component (or one of its pads) of this cell in the PCB "
               "editor, then press the button again")]))

    content: list = []
    pad_numbers: dict = {}

    for item in items:
        if isinstance(item, KipyPad):
            pad_uuid = _pad_uuid(item)
            owner = find_pad_owner(adapter, adapter.get_footprints(), pad_uuid)
            if owner is None:
                raise ValidationError(format_fatal_error(
                    _("{label}: the selected pad {pad!r} has no footprint "
                      "owner on the live board").format(label=label,
                                                        pad=item.number),
                    [_("the board changed since it was loaded — press Refresh "
                       "and select the pad again")]))
            if owner not in content:
                content.append(owner)
            pad_numbers.setdefault(owner.uuid, str(item.number))
        elif isinstance(item, Footprint):
            if item not in content:
                content.append(item)

    if not content:
        has_via_track = any(isinstance(i, (Via, Track)) for i in items)
        return {"kind": "via" if has_via_track else "other",
                "role": None, "pad": None, "cluster": None}

    # Distinct Clusters over the selected content footprints.
    clusters: dict = {}
    for fp in content:
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
        if cluster:
            clusters.setdefault(cluster, []).append(fp.ref)
    if len(clusters) > 1:
        listing = ", ".join(
            "{c!r} ({refs})".format(c=cluster, refs=", ".join(refs))
            for cluster, refs in sorted(clusters.items()))
        raise ValidationError(format_fatal_error(
            _("{label}: the selection spans several clusters — {listing}")
            .format(label=label, listing=listing),
            [_("an anchor belongs to ONE placed instance of the cell; select "
               "components of a single cluster only, then press the button "
               "again")]))
    cluster = next(iter(clusters), None)

    roles: dict = {}
    for fp in content:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME) or ""
        if role:
            roles.setdefault(role, []).append(fp.ref)

    if len(content) == 1:
        fp = content[0]
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        pad = pad_numbers.get(fp.uuid)
    elif pad_numbers:
        # Several content footprints but a pad picked one of them — that owner
        # is the intended anchor component.
        owner = next(fp for fp in content if fp.uuid in pad_numbers)
        role = adapter.get_field_value(owner, ROLE_FIELD_NAME)
        pad = pad_numbers[owner.uuid]
    else:
        if len(roles) > 1:
            listing = ", ".join(
                "{r!r} ({refs})".format(r=role, refs=", ".join(refs))
                for role, refs in sorted(roles.items()))
            raise ValidationError(format_fatal_error(
                _("{label}: the selection contains several roles — {listing}")
                .format(label=label, listing=listing),
                [_("select exactly ONE component (or its pad) of this cell, "
                   "then press the button again")]))
        role = next(iter(roles), None)
        pad = None

    if not role:
        raise ValidationError(format_fatal_error(
            _("{label}: the selected footprint has no Role field").format(label=label),
            [_("tag the component with a Role (Tools -> tree -> Tag selected) "
               "or pick another component")]))
    if cell_roles and role not in cell_roles:
        raise ValidationError(format_fatal_error(
            _("{label}: role {role!r} is not a component of cell {cell!r}")
            .format(label=label, role=role, cell=cell_name),
            [_("the cell anchor must name one of this cell's own components — "
               "select a component of cell {cell!r}").format(cell=cell_name)]))
    return {"kind": "pad" if pad is not None else "footprint",
            "role": role, "pad": pad, "cluster": cluster}


def roles_for_cluster(adapter, cell_roles, cluster: str) -> list:
    """The cell roles that are actually present among the live footprints of
    `cluster` (cluster_prefix_match against the Cluster field), sorted. Falls
    back to the FULL cell_roles when the board/adapter is unavailable or
    nothing of this cell is on the cluster — the narrowing is a HINT (C.3),
    never a hard filter that hides a valid role."""
    if not cell_roles or not cluster:
        return sorted(cell_roles)
    if adapter is None:
        return sorted(cell_roles)
    present: set = set()
    try:
        for fp in adapter.get_footprints():
            fp_cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
            if cluster_prefix_match(fp_cluster, cluster):
                role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
                if role in cell_roles:
                    present.add(role)
    except Exception:  # noqa: BLE001 — narrowing is best-effort
        return sorted(cell_roles)
    return sorted(present) if present else sorted(cell_roles)


def _matches_clone_context(cp, cell_name: str, cluster: str, sheet: str) -> bool:
    """True when ONE placed instance (a top-level ClonePlacement or a
    materialized entity clone — C.5.1) is a placement of `cell_name` for the
    working Cluster (+ optional Sheet). A placement matches by its OWN cluster
    field (cluster_prefix_match — the same convention as role_narrowing) and,
    when Sheet is given and the placement carries one, by exact sheet."""
    if getattr(cp, "cell", None) != cell_name:
        return False
    cp_cluster = getattr(cp, "cluster", None) or ""
    if cluster and not cluster_prefix_match(cp_cluster, cluster):
        return False
    if sheet and getattr(cp, "sheet", None):
        if cp.sheet != sheet:
            return False
    return True


def _placement_key(cp):
    """Identity of ONE physical placement across representations. Two
    candidates are the SAME placement — NOT an ambiguity — when they share
    (cell, effective name, cluster, sheet). A placed cell may legitimately
    exist BOTH as a top-level clone_placement and as a tree entity node
    (e.g. after a migration); the union in resolve_clone_context_live must
    not count such a duplicate twice."""
    return (getattr(cp, "cell", None),
            clone_placement_effective_name(cp),
            getattr(cp, "cluster", None) or "",
            getattr(cp, "sheet", None))


def context_clone_candidates(cfg, cell_name: str, cluster: str,
                             sheet: str) -> list:
    """Top-level ClonePlacements of this cell matching the working Cluster
    (+ optional Sheet) — the OFFLINE (board-free) candidate list. The Marker
    tab's full lookup also covers placements materialized from entity trees —
    see resolve_clone_context_live (C.5.1)."""
    return [cp for cp in getattr(cfg, "clone_placements", []) or []
            if _matches_clone_context(cp, cell_name, cluster, sheet)]


def _single_candidate(candidates, cell_name: str, cluster: str):
    """None when nothing matches; the sole candidate when exactly one; fatal
    when several DISTINCT placements match (never "take the first" — the C.3 /
    C.5.1 "several matched -> fatal with enumeration" rule, shared by the
    offline and the live resolvers)."""
    if not candidates:
        return None
    if len(candidates) > 1:
        names = ", ".join(sorted(
            clone_placement_effective_name(c) for c in candidates))
        raise ValidationError(format_fatal_error(
            _("cell {cell!r}: several clone placements match cluster {cluster!r} "
              "— {names}").format(cell=cell_name, cluster=cluster, names=names),
            [_("pick a more specific Cluster/Sheet, or edit the anchor while "
               "exactly one placement of this cell is in context")]))
    return candidates[0]


def resolve_clone_context(cfg, cell_name: str, cluster: str, sheet: str):
    """The ONE top-level clone placement of this cell for the working
    Cluster/Sheet — None when nothing matches; fatal when several match
    (never "take the first"). The OFFLINE (board-free) twin; the live lookup
    that also covers entity placements is resolve_clone_context_live."""
    return _single_candidate(
        context_clone_candidates(cfg, cell_name, cluster, sheet),
        cell_name, cluster)


def resolve_clone_context_live(adapter, cfg, cell_name: str, cluster: str,
                               sheet: str, sheet_names):
    """The ONE placement of this cell for the working Cluster/Sheet among the
    UNION of the top-level clone_placements and the entity placements
    materialized from cfg.trees (C.5.1). A cell may be placed ONLY as an
    entity — e.g. pif_3v3_vdd is placed as entity pif_3v3_vdd_mcu (cluster
    PIF_3V3_VDD, sheet MCU), not as its own clone_placement; the
    top-level-only scan (resolve_clone_context) found nothing for it and the
    Marker tab dead-ended on 23 of 24 cells. None when nothing matches; fatal
    when several DISTINCT placements match (never "take the first").

    Runs on the WORKER thread (the Marker tab's dispatch): materialize_entity_placements
    resolves the trees' anchors LIVE from the board, so this must never be
    called on the UI thread. With no entities/trees the union degrades to the
    top-level clones and the materialization is a cheap empty pass."""
    candidates: list = []
    keys: set = set()
    for cp in getattr(cfg, "clone_placements", []) or []:
        if _matches_clone_context(cp, cell_name, cluster, sheet):
            keys.add(_placement_key(cp))
            candidates.append(cp)
    for cp in materialize_entity_placements(adapter, cfg, sheet_names=sheet_names):
        if not _matches_clone_context(cp, cell_name, cluster, sheet):
            continue
        key = _placement_key(cp)
        if key in keys:
            continue            # the SAME placement, already in the union
        keys.add(key)
        candidates.append(cp)
    return _single_candidate(candidates, cell_name, cluster)


# ────────────────────────────────────────────────────────────────────────────
# Overlay worker functions (run on the worker thread via start_long_op —
# pure IPC/file work, NO widget access). The overlay layer is resolved from
# the LIVE board inside the worker (never on the UI thread).
# ────────────────────────────────────────────────────────────────────────────

def _resolve_context_then(adapter, cfg, cell_name: str, cluster: str,
                          sheet: str, sheet_names, fn, *extra):
    """Resolve the ONE placed instance of this cell for the working
    Cluster/Sheet (top-level clone_placement + entity placements materialized
    from the trees — C.5.1) ON THE WORKER THREAD, then run
    `fn(adapter, cfg, clone, cell_name, sheet_names, *extra)`.

    This is where the placement lookup lives (not on the UI thread): the
    union's entity half is materialize_entity_placements, which resolves the
    trees' anchors LIVE from the board. Returns _NO_CLONE (never raises) when
    the cell has no placed instance in the working context — the caller turns
    it into the "place the cell first" hint; a several-matches ambiguity is a
    ValidationError raised here and routed to the op's failure handler."""
    clone = resolve_clone_context_live(
        adapter, cfg, cell_name, cluster, sheet, sheet_names)
    if clone is None:
        return _NO_CLONE
    return fn(adapter, cfg, clone, cell_name, sheet_names, *extra)


def _resolve_layer(adapter, layer_name: str):
    """Live layer enum for the overlay display name — shared fatal when the
    layer is not enabled on this board (see
    board_overlay.require_overlay_layer; the same helper the whole-layer
    sweep uses, so the two surface one message)."""
    return board_overlay.require_overlay_layer(adapter, layer_name)


def _cell_entry_mount_offset(entry: dict) -> tuple[float, float]:
    """The cell entry's CURRENT mount A in its stored frame (anchor_xy, else
    anchor_role's centre offset, else (0,0)) — the dict twin of
    cell_mount_offset (copied here verbatim; the placer.py original stays
    until Phase B moves it)."""
    xy = entry.get("anchor_xy")
    if xy is not None:
        return (float(xy[0]), float(xy[1]))
    role = entry.get("anchor_role")
    if role:
        for c in entry.get("components", []) or []:
            if c.get("role") == role:
                return (float(c.get("offset_along_mm", 0.0)),
                        float(c.get("offset_across_mm", 0.0)))
    return (0.0, 0.0)


def _cell_to_entry(cell) -> dict:
    """A typed Cell -> its dict entry shape (enough for bbox/mount helpers) —
    the overlay helpers stay on one dict path regardless of whether a typed or
    a raw entry is in hand."""
    if cell is None:
        return {}
    return {
        "anchor_xy": list(cell.anchor_xy) if cell.anchor_xy is not None else None,
        "anchor_role": cell.anchor_role,
        "components": [
            {"role": c.role,
             "offset_along_mm": c.offset_along_mm,
             "offset_across_mm": c.offset_across_mm}
            for c in cell.components],
        "vias": [{"offset_along_mm": v.offset_along_mm,
                  "offset_across_mm": v.offset_across_mm} for v in cell.vias],
        "tracks": [{"start_along_mm": t.start_along_mm,
                    "start_across_mm": t.start_across_mm,
                    "end_along_mm": t.end_along_mm,
                    "end_across_mm": t.end_across_mm} for t in cell.tracks],
        "clone_placements": [
            {"xy": list(cp.xy) if getattr(cp, "xy", None) is not None else None}
            for cp in cell.clone_placements],
    }


def _cell_point_to_world_mm(origin: Vector2, rotation_deg: float, mirror: bool,
                            ax_mm: float, ay_mm: float,
                            along_mm: float, across_mm: float) -> tuple[float, float]:
    """A cell bbox-frame point (along, across) -> world mm, through the SAME
    mapping apply_clone_geometry uses for content: world = origin + R(o - A),
    mirrored about the vertical axis through the placement origin. `origin` is
    the live mount's world position (nm) and A its bbox-frame point — both
    come from a resolved placed instance."""
    rotated = rotate_local_offset(along_mm - ax_mm, across_mm - ay_mm, rotation_deg)
    px = origin.x + rotated.x
    py = origin.y + rotated.y
    if mirror:
        px = 2 * origin.x - px
    return (px / MM, py / MM)


def overlay_world_bbox_mm(entry: dict, origin: Vector2, rotation_deg: float,
                          mirror: bool) -> tuple[float, float, float, float] | None:
    """The cell bbox's WORLD, axis-aligned bounding box (x1, y1, x2, y2 in mm)
    for the live instance frame (origin/rotation/mirror) — the four stored-bbox
    corners mapped through _cell_point_to_world_mm, then min/max'd. KiCad's
    BoardRectangle is axis-aligned, so a rotated cell is shown as the bounding
    box of its bbox corners. None when the cell entry has no geometry."""
    bbox = cell_content_bbox(entry)
    if bbox is None:
        return None
    min_along, max_along, min_across, max_across = bbox
    ax, ay = _cell_entry_mount_offset(entry)
    xs, ys = [], []
    for along, across in ((min_along, min_across), (max_along, min_across),
                          (max_along, max_across), (min_along, max_across)):
        x_mm, y_mm = _cell_point_to_world_mm(
            origin, rotation_deg, mirror, ax, ay, along, across)
        xs.append(x_mm)
        ys.append(y_mm)
    return (min(xs), min(ys), max(xs), max(ys))


def _draw_bbox_worker(adapter, cfg, clone, cell_name, sheet_names,
                      layer_name) -> Optional[str]:
    """Draw the cell's bbox rectangle around the placed instance of this
    clone — returns the created rectangle's uuid. Resolves the live frame via
    read_clone_origin_live (the same mount read as "Read current position")."""
    layer = _resolve_layer(adapter, layer_name)
    read = read_clone_origin_live(adapter, cfg, clone, sheet_names)
    cell = cfg.cells.get(cell_name)
    box = overlay_world_bbox_mm(_cell_to_entry(cell), read.position,
                                read.rotation_deg,
                                bool(getattr(clone, "mirror", False)))
    if box is None:
        return None
    x1, y1, x2, y2 = box
    # Phase D: the stroke width comes from the Settings "Board overlay" page
    # (board_overlay module constants are only the DEFAULTS).
    return board_overlay.draw_bbox(adapter, layer, x1, y1, x2, y2,
                                   board_overlay.overlay_bbox_stroke_mm())


def _place_marker_worker(adapter, cfg, clone, cell_name, sheet_names,
                         layer_name) -> Optional[str]:
    """Draw the draggable marker circle at the cell's CURRENT anchor (the
    mount's world position) or, when the cell has no anchor, at the centre of
    its bbox — returns the marker's uuid."""
    layer = _resolve_layer(adapter, layer_name)
    read = read_clone_origin_live(adapter, cfg, clone, sheet_names)
    cell = cfg.cells.get(cell_name)
    entry = _cell_to_entry(cell)
    ax, ay = _cell_entry_mount_offset(entry)
    if ax or ay:
        x_mm, y_mm = read.position.x / MM, read.position.y / MM
    else:
        bbox = cell_content_bbox(entry)
        centre = ((bbox[0] + bbox[1]) / 2.0, (bbox[2] + bbox[3]) / 2.0) \
            if bbox else (0.0, 0.0)
        x_mm, y_mm = _cell_point_to_world_mm(
            read.position, read.rotation_deg, bool(getattr(clone, "mirror", False)),
            0.0, 0.0, centre[0], centre[1])
    # Phase D: radius/stroke come from the Settings "Board overlay" page
    # (board_overlay module constants are only the DEFAULTS).
    return board_overlay.draw_marker(adapter, layer, x_mm, y_mm,
                                     board_overlay.overlay_marker_radius_mm(),
                                     board_overlay.overlay_marker_stroke_mm())


def _read_marker_worker(adapter, cfg, clone, cell_name, sheet_names,
                        marker_uuid) -> Optional[tuple[float, float]]:
    """Read the (user-dragged) marker's world position and convert it into the
    cell's own bbox-frame anchor (anchor_xy) — the world->local inversion
    reuses _world_pos_to_cell_local_offset (live_position.py, NOT a second
    implementation); the result is the ABSOLUTE bbox offset (the current mount
    + the offset relative to it), exactly what the old placer Point flow
    computed. None when the marker is no longer on the board."""
    pos = board_overlay.read_marker(adapter, marker_uuid)
    if pos is None:
        return None
    world = Vector2.from_xy(int(round(pos[0] * MM)), int(round(pos[1] * MM)))
    rel = _world_pos_to_cell_local_offset(
        adapter, cfg, clone, sheet_names, world,
        bool(getattr(clone, "mirror", False)))
    cell = cfg.cells.get(cell_name)
    if cell is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r} not found in config").format(cell=cell_name),
            [_("reload the config and try again")]))
    a0, a1 = cell_mount_offset(cell)
    return (round(a0 + rel[0], 9), round(a1 + rel[1], 9))


# ────────────────────────────────────────────────────────────────────────────
# The widget
# ────────────────────────────────────────────────────────────────────────────

class CellAnchorView(QWidget):
    """The cell-anchor editor — a Config right-QView page (added via
    config_tree_dock.add_right_page) and opened from the Config tree's Cells
    context menu ("Cell anchor..." next to "Edit cell...")."""

    saved = pyqtSignal()

    def __init__(self, main_window, connection=None, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._connection = connection
        self._cell_name: Optional[str] = None
        self._file_path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        self._active_op = None
        # Persisted overlay uuids (survive an app restart, see _OVERLAY_STATE_KEY).
        self._marker_uuid: Optional[str] = None
        self._bbox_uuid: Optional[str] = None

        self._build_ui()
        self._reload_form()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._title = QLabel()
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # ── Tab 1 — Component (also hosts the working Sheet/Cluster context) ──
        comp_page = QWidget()
        comp_layout = QVBoxLayout(comp_page)
        comp_layout.setContentsMargins(4, 4, 4, 4)

        ctx_box = QGroupBox(_("Working context (Sheet/Cluster of the placed "
                              "instance)"))
        ctx_form = QFormLayout(ctx_box)
        self._cluster_combo = QComboBox()
        configure_searchable(self._cluster_combo)
        self._cluster_combo.setPlaceholderText(_("Cluster of the placed cell"))
        ctx_form.addRow(_("Cluster:"), self._cluster_combo)
        self._sheet_combo = QComboBox()
        configure_searchable(self._sheet_combo)
        self._sheet_combo.setPlaceholderText(_("Sheet (optional)"))
        ctx_form.addRow(_("Sheet:"), self._sheet_combo)
        ctx_note = QLabel(_("The Marker tab maps through the placed instance "
                            "of this cell on the chosen Cluster — the live "
                            "board is used only then and for “Read from "
                            "selection”."))
        ctx_note.setWordWrap(True)
        ctx_form.addRow(ctx_note)
        comp_layout.addWidget(ctx_box)

        comp_box = QGroupBox(_("Component anchor"))
        comp_form = QFormLayout(comp_box)
        self._role_combo = QComboBox()
        self._role_combo.setPlaceholderText(_("pick this cell's component role"))
        comp_form.addRow(_("Role:"), self._role_combo)
        self._pad_edit = QLineEdit()
        self._pad_edit.setPlaceholderText(_("pad number (optional)"))
        comp_form.addRow(_("Pad:"), self._pad_edit)
        row = QHBoxLayout()
        self._read_selection_button = QPushButton(_("Read from selection"))
        self._read_selection_button.setToolTip(
            _("Fill Role (+ Pad) and Cluster from ONE selected component or "
              "pad of this cell on the live board."))
        self._read_selection_button.clicked.connect(self._on_read_from_selection)
        row.addWidget(self._read_selection_button)
        self._set_anchor_button = QPushButton(_("Set as anchor"))
        self._set_anchor_button.clicked.connect(self._on_set_component_anchor)
        row.addWidget(self._set_anchor_button)
        self._clear_anchor_button = QPushButton(_("Clear anchor"))
        self._clear_anchor_button.clicked.connect(self._on_clear_anchor)
        row.addWidget(self._clear_anchor_button)
        comp_form.addRow(row)
        comp_note = QLabel(_("Without a pad the anchor is the component's "
                             "stored centre (offline). With a pad the pad's "
                             "point is resolved at Apply from a live instance "
                             "of this role — anchor_xy is removed so that "
                             "live resolution actually runs."))
        comp_note.setWordWrap(True)
        comp_form.addRow(comp_note)
        comp_layout.addWidget(comp_box)
        comp_layout.addStretch(1)
        self._tabs.addTab(comp_page, _("Component"))

        # ── Tab 2 — Marker ────────────────────────────────────────────────
        marker_page = QWidget()
        marker_layout = QVBoxLayout(marker_page)
        marker_layout.setContentsMargins(4, 4, 4, 4)

        marker_box = QGroupBox(_("Marker"))
        marker_form = QFormLayout(marker_box)
        layer_note = QLabel()
        layer_note.setWordWrap(True)
        self._overlay_layer_note = layer_note
        marker_form.addRow(layer_note)
        self._refresh_overlay_layer_note()
        m_row = QHBoxLayout()
        self._place_marker_button = QPushButton(_("Place marker"))
        self._place_marker_button.clicked.connect(self._on_place_marker)
        m_row.addWidget(self._place_marker_button)
        self._read_marker_button = QPushButton(_("Read position"))
        self._read_marker_button.clicked.connect(self._on_read_marker)
        m_row.addWidget(self._read_marker_button)
        self._remove_marker_button = QPushButton(_("Remove marker"))
        self._remove_marker_button.clicked.connect(self._on_remove_marker)
        m_row.addWidget(self._remove_marker_button)
        marker_form.addRow(m_row)
        b_row = QHBoxLayout()
        self._show_bbox_button = QPushButton(_("Show bbox"))
        self._show_bbox_button.clicked.connect(self._on_show_bbox)
        b_row.addWidget(self._show_bbox_button)
        self._hide_bbox_button = QPushButton(_("Hide bbox"))
        self._hide_bbox_button.clicked.connect(self._on_hide_bbox)
        b_row.addWidget(self._hide_bbox_button)
        marker_form.addRow(b_row)
        # Phase D (D.2) — the explicit "по кнопке" cleanup: removes BOTH the
        # marker and the bbox of this cell from the board (by their uuids).
        r_row = QHBoxLayout()
        self._remove_overlay_button = QPushButton(_("Remove overlay"))
        self._remove_overlay_button.setToolTip(
            _("Removes this cell's drawn marker and bbox from the board."))
        self._remove_overlay_button.clicked.connect(self._on_remove_overlay)
        r_row.addWidget(self._remove_overlay_button)
        marker_form.addRow(r_row)
        m_note = QLabel(_("Places a marker at the cell's current anchor (or "
                          "the bbox centre when there is no anchor). Drag it "
                          "with KiCad's own tools, then “Read position” "
                          "stores the point as anchor_xy and clears any "
                          "Role/Pad anchor. Requires the cell to be placed on "
                          "the chosen Cluster."))
        m_note.setWordWrap(True)
        marker_form.addRow(m_note)
        marker_layout.addWidget(marker_box)
        marker_layout.addStretch(1)
        self._tabs.addTab(marker_page, _("Marker"))

        self._cluster_combo.currentTextChanged.connect(self._on_cluster_changed)
        self._tabs.currentChanged.connect(lambda _i: self._reload_form())

    # ── Loading / context ─────────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """The project root changed (root_changed broadcast) — refresh the
        Sheet choices (from the loaded config) and drop overlay state that is
        only valid for the previous root.

        Phase D (D.2): an ACTUAL root switch closes the current cell's editing
        session for the PREVIOUS project, so its drawn overlay (marker + bbox)
        is cleaned up first (the board may be the same physical board). The
        guard against re-setting the SAME path matters: DockHub also calls
        set_root_path on every graph-shape refresh
        (_refresh_graph_dependent_choices) with the unchanged root — that must
        NOT drop a marker/bbox the user is mid-edit with."""
        if path != self._root_path:
            self.cleanup()
        self._root_path = path
        if path is not None:
            try:
                _cfg, ctx = load_config(str(path))
            except (ValidationError, OSError):
                ctx = None
            if ctx is not None:
                # ctx.sheet_names is a "sheet path -> readable name" dict; the
                # combo shows the NAMES, not the uuid-path keys (same as
                # rename.py's sorted(set(ctx.sheet_names.values()))).
                sheet_names = ctx.sheet_names or {}
                set_combo_items(self._sheet_combo,
                                sorted(set(sheet_names.values())))
        else:
            self._sheet_combo.clear()
        self._marker_uuid = None
        self._bbox_uuid = None
        if self._cell_name is not None:
            self._reload_form()

    def refresh_known_roles(self, snapshot) -> None:
        """Feed the live-board snapshot into the working-context Cluster combo
        (wired into DockHub.push_snapshot like every other dock's
        refresh_known_roles — a Phase C gap fixed with Phase E: the combo was
        never populated, so the anchor page could only be narrowed by hand or
        by "Read from selection"). Fill, never restrict: the combo stays an
        editable picker, the list is only a hint (a typed cluster not on the
        board is still accepted). The Role combo is deliberately NOT touched —
        it is a closed list of THIS cell's own components, not a live-board
        value."""
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        set_combo_items(self._cluster_combo, clusters)

    def load_entry(self, name: str, file_path) -> None:
        """Open the requested cell for anchor editing — (name, owning file),
        the same shape as the Config tree's cell_edit_requested. Reads the
        entry live and fills the form (safe to re-open on a changed file).

        Phase D (D.2): opening a DIFFERENT cell closes the previous cell's
        editing session, so its drawn overlay (marker + bbox) is cleaned up
        first.

        Phase E: the remembered (Cluster, Sheet) context is applied BEFORE the
        form renders, so the Role combo opens already narrowed to the last
        cluster this cell was worked in — no click on the board required."""
        if name != self._cell_name and self._cell_name is not None:
            self.cleanup()
        self._cell_name = name
        self._file_path = Path(file_path) if file_path is not None else None
        self._prefill_cell_context()
        self._reload_form()

    # ── Phase E: remembered (Cluster, Sheet) context ──────────────────────

    def _prefill_cell_context(self) -> None:
        """Seed the working Sheet/Cluster combos from the remembered (Cluster,
        Sheet) this cell was last created/edited in — the page opens already
        narrowed to the Role combo, with no click on the board.

        Strict §E.5 hint semantics: a remembered cluster that does NOT resolve
        on the current live board (renamed/deleted/other board), or a missing
        record, leaves BOTH fields empty — exactly the pre-Phase-E "ask again"
        state. A stale Sheet (not among the current config's sheet names) is
        dropped too. Never a fatal, never an exception."""
        # A reused view must not leak the PREVIOUS cell's working context.
        self._sheet_combo.setCurrentText("")
        self._cluster_combo.setCurrentText("")
        if self._cell_name is None or self._root_path is None:
            return
        cluster, sheet = remembered_cell_edit_context(
            self._root_path, self._cell_name)
        if not cluster:
            return
        if not cluster_present_on_board(self._adapter(), cluster):
            return                      # stale context -> fields stay empty
        self._cluster_combo.setCurrentText(cluster)
        if sheet:
            sheets = {self._sheet_combo.itemText(i)
                      for i in range(self._sheet_combo.count())}
            if sheet in sheets:
                self._sheet_combo.setCurrentText(sheet)

    # ── Persisted overlay uuids ───────────────────────────────────────────

    def _overlay_state(self) -> dict:
        """The persisted overlay-uuid map {root: {cell: {marker, bbox}}}."""
        try:
            raw = settings.state.get(_OVERLAY_STATE_KEY, {}) or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _cell_overlay_state(self) -> dict:
        root = str(self._root_path) if self._root_path is not None else ""
        return (self._overlay_state().get(root, {}) or {}).get(
            self._cell_name or "", {})

    def _remember_overlay(self, marker: Optional[str], bbox: Optional[str]) -> None:
        if self._cell_name is None:
            return
        root = str(self._root_path) if self._root_path is not None else ""
        state = self._overlay_state()
        per_root = state.setdefault(root, {})
        per_root[self._cell_name] = {"marker": marker, "bbox": bbox}
        settings.state.set(_OVERLAY_STATE_KEY, state)

    # ── Entry / form state ────────────────────────────────────────────────

    def _current_entry(self) -> Optional[dict]:
        """Read the CURRENT cell entry from disk (dict or None)."""
        if self._cell_name is None:
            return None
        target_file = find_dict_entry_file(self._root_path, "cells", self._cell_name)
        if target_file is None:
            target_file = self._file_path
        if target_file is None or not Path(target_file).exists():
            return None
        try:
            entry = (read_data(Path(target_file)).get("cells") or {}).get(
                self._cell_name)
        except (ValidationError, OSError):
            return None
        return entry if isinstance(entry, dict) else None

    def _cell_roles(self) -> list:
        """Roles from the CURRENT loaded cell entry (its own components — the
        only legitimate Role choices, never the live board)."""
        entry = self._current_entry()
        if entry is None:
            return []
        return sorted({c.get("role") for c in entry.get("components", [])
                       if c.get("role")})

    def _reload_form(self) -> None:
        """Refill the whole form from the current cell entry — called on open,
        on tab switch and after a save so both tabs stay in sync."""
        self._refresh_overlay_layer_note()
        if self._cell_name is None:
            self._title.setText(_("Pick a Cell in the Config tree, then use "
                                  "“Cell anchor...” from its context menu."))
            self._set_anchor_button.setEnabled(False)
            self._clear_anchor_button.setEnabled(False)
            self._read_selection_button.setEnabled(False)
            self._role_combo.clear()
            self._pad_edit.clear()
            for b in (self._place_marker_button, self._read_marker_button,
                      self._remove_marker_button, self._show_bbox_button,
                      self._hide_bbox_button, self._remove_overlay_button):
                b.setEnabled(False)
            return

        self._title.setText(_("Cell {name!r} — anchor").format(name=self._cell_name))
        entry = self._current_entry()
        if entry is None:
            show_message(_("Cell {name!r} not found in the project config")
                         .format(name=self._cell_name), _ERROR_STYLE, logger)
            self._set_anchor_button.setEnabled(False)
            self._clear_anchor_button.setEnabled(False)
            self._read_selection_button.setEnabled(False)
            return

        # Persisted overlay uuids for this cell/root.
        cell_state = self._cell_overlay_state()
        self._marker_uuid = cell_state.get("marker")
        self._bbox_uuid = cell_state.get("bbox")

        roles = sorted({c.get("role") for c in entry.get("components", [])
                        if c.get("role")})
        self._fill_role_choices(roles, self._cluster_combo.currentText().strip())
        role = entry.get("anchor_role")
        if role:
            self._role_combo.setCurrentText(str(role))
        self._pad_edit.setText(str(entry.get("anchor_pad") or ""))

        self._read_selection_button.setEnabled(True)
        self._set_anchor_button.setEnabled(True)
        self._clear_anchor_button.setEnabled(True)
        self._place_marker_button.setEnabled(True)
        self._show_bbox_button.setEnabled(True)
        self._read_marker_button.setEnabled(bool(self._marker_uuid))
        self._remove_marker_button.setEnabled(bool(self._marker_uuid))
        self._hide_bbox_button.setEnabled(bool(self._bbox_uuid))
        self._remove_overlay_button.setEnabled(
            bool(self._marker_uuid or self._bbox_uuid))
        self._refresh_overlay_layer_note()

    def _fill_role_choices(self, roles: list, cluster: str) -> None:
        cluster = cluster or self._cluster_combo.currentText().strip()
        narrowed = roles_for_cluster(self._adapter(), roles, cluster)
        current = self._role_combo.currentText()
        set_combo_items(self._role_combo, narrowed)
        if current and current in narrowed:
            self._role_combo.setCurrentText(current)

    def _adapter(self):
        board = getattr(self._connection, "board", None)
        if board is None:
            return None
        return getattr(board, "adapter", None)

    # ── Component tab handlers ────────────────────────────────────────────

    def _on_cluster_changed(self) -> None:
        if self._cell_name is None:
            return
        entry = self._current_entry()
        if entry is None:
            return
        roles = sorted({c.get("role") for c in entry.get("components", [])
                        if c.get("role")})
        self._fill_role_choices(roles, self._cluster_combo.currentText().strip())

    def _on_read_from_selection(self) -> None:
        adapter = self._adapter()
        if adapter is None:
            show_message(_("No live board — “Read from selection” needs KiCad. "
                           "Pick Role/Pad and Cluster by hand instead."),
                         _WARN_STYLE, logger)
            return
        entry = self._current_entry()
        if entry is None:
            show_message(_("Cell {name!r} not found in the project config")
                         .format(name=self._cell_name), _ERROR_STYLE, logger)
            return
        try:
            read = read_anchor_source(
                adapter, adapter.get_selected_items(), self._cell_roles(),
                self._cell_name or "", _("cell anchor"))
        except ValidationError as e:
            show_message(str(e), _ERROR_STYLE, logger)
            return
        if read["kind"] in ("via", "other"):
            show_message(
                _("A Via (or a non-component item) is selected — that is the "
                  "Marker tab's case: switch to the Marker tab, place the "
                  "marker at the desired point, then press “Read position”."),
                _WARN_STYLE, logger)
            return
        if read["cluster"]:
            self._cluster_combo.setCurrentText(read["cluster"])
            # Phase E: "Read from selection" brought a fresh Cluster — update
            # this cell's remembered (last-used) context. Sheet is optional:
            # whatever narrowing the user keeps in the Sheet combo is stored
            # with it (the read itself carries no sheet).
            remember_cell_edit_context(
                self._root_path, self._cell_name, read["cluster"],
                self._sheet_combo.currentText().strip() or None)
        roles = sorted({c.get("role") for c in entry.get("components", [])
                        if c.get("role")})
        self._fill_role_choices(roles, read["cluster"] or "")
        self._role_combo.setCurrentText(read["role"] or "")
        self._pad_edit.setText(read["pad"] or "")
        what = _("pad {pad!r}").format(pad=read["pad"]) if read["pad"] \
            else _("footprint")
        show_message(
            _("Read from selection: {what} of role {role!r} (cluster "
              "{cluster!r}) — press “Set as anchor” to store it.")
            .format(what=what, role=read["role"], cluster=read["cluster"]),
            _SUCCESS_STYLE, logger)

    def _on_set_component_anchor(self) -> None:
        """Write anchor_role (+ anchor_pad) and REMOVE anchor_xy (else Phase
        A's GUARD 1 keeps the stale anchor_xy winning over the live pad
        resolution). Role-only = component centre (offline)."""
        if self._cell_name is None:
            return
        entry = self._current_entry()
        if entry is None:
            show_message(_("Cell {name!r} not found in the project config")
                         .format(name=self._cell_name), _ERROR_STYLE, logger)
            return
        role = self._role_combo.currentText().strip()
        if not role:
            show_message(_("Set as anchor: pick a Role of this cell first."),
                         _ERROR_STYLE, logger)
            return
        roles = self._cell_roles()
        if role not in roles:
            show_message(_("Set as anchor: role {role!r} is not a component of "
                           "cell {name!r}").format(role=role, name=self._cell_name),
                         _ERROR_STYLE, logger)
            return
        pad = self._pad_edit.text().strip() or None
        entry["anchor_role"] = role
        if pad:
            entry["anchor_pad"] = pad
        else:
            entry.pop("anchor_pad", None)
        # GUARD 1 (Phase A): anchor_xy must be dropped so the live pad-role
        # resolution actually runs; a Role-only anchor has no pad, so its
        # mount is the component centre (cell_mount_offset).
        entry.pop("anchor_xy", None)
        self._write_entry(entry, _("Set as anchor"))

    def _on_clear_anchor(self) -> None:
        """Drop all three anchor fields — the cell's mount returns to the
        default bbox corner (0,0)."""
        if self._cell_name is None:
            return
        entry = self._current_entry()
        if entry is None:
            return
        entry.pop("anchor_xy", None)
        entry.pop("anchor_role", None)
        entry.pop("anchor_pad", None)
        self._write_entry(entry, _("Clear anchor"))
        self._role_combo.setCurrentText("")
        self._pad_edit.clear()

    def _write_entry(self, entry: dict, desc: str) -> None:
        """merge_write the edited entry to the file that actually holds the
        cell, then refresh the form + emit saved so the Config tree refreshes."""
        target_file = find_dict_entry_file(self._root_path, "cells", self._cell_name)
        if target_file is None:
            target_file = self._file_path
        if target_file is None:
            show_message(_("{desc} failed: no config file for cell {name!r}")
                         .format(desc=desc, name=self._cell_name),
                         _ERROR_STYLE, logger)
            return
        try:
            merge_write(Path(target_file), {"cells": {self._cell_name: entry}},
                        section="cells")
        except (ValidationError, OSError) as e:
            show_message(_("{desc} failed: {error}").format(desc=desc, error=e),
                         _ERROR_STYLE, logger)
            return
        show_message(_("Cell {name!r}: {desc} stored — placed instances shift "
                       "on the next Redraw/Apply.")
                     .format(name=self._cell_name, desc=desc), _SUCCESS_STYLE, logger)
        self.saved.emit()
        self._reload_form()

    # ── Marker tab: context + worker dispatch ─────────────────────────────

    def _context(self):
        """(cfg, sheet_names, cluster, sheet) for the Marker tab's live frame,
        or None with a message shown when the working context can't be
        resolved.

        The placed-instance lookup itself (top-level clone_placement + entity
        placements — C.5.1) runs on the WORKER thread (_resolve_context_then):
        its entity half is materialized from the trees, which reads their
        anchors LIVE from the board. Here we only validate the project/working
        Cluster and hand the inputs over."""
        if self._root_path is None or self._cell_name is None:
            show_message(_("Marker needs a project root — open a project "
                           "first."), _WARN_STYLE, logger)
            return None
        cluster = self._cluster_combo.currentText().strip()
        sheet = self._sheet_combo.currentText().strip()
        if not cluster:
            show_message(_("Marker: pick the working Cluster first (Component "
                           "tab)."), _WARN_STYLE, logger)
            return None
        try:
            cfg, ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            show_message(_("Marker: failed to load the project config: {error}")
                         .format(error=e), _ERROR_STYLE, logger)
            return None
        return cfg, ctx.sheet_names, cluster, sheet

    def _adapter_required(self):
        """The live adapter, or None + a message (used by marker IPC ops)."""
        adapter = self._adapter()
        if adapter is None:
            show_message(_("No live board connection — the overlay needs "
                           "KiCad."), _WARN_STYLE, logger)
            return None
        return adapter

    def _dispatch(self, fn, on_success, on_error, *extra_args):
        """Resolve the working context + live adapter and dispatch one overlay
        worker function on the worker thread via start_long_op (inputs
        collected on the UI thread, the placed-instance lookup + IPC on the
        worker, completion back on the UI thread).

        The worker resolves the placed instance of this cell among the UNION
        of top-level clones and entity placements (C.5.1) and returns the
        _NO_CLONE sentinel when the cell has none in the working context — we
        then explain what to do (the previous "no clone placement ... is
        placed" hint), never a crash."""
        adapter = self._adapter_required()
        if adapter is None:
            return
        ctx = self._context()
        if ctx is None:
            return
        cfg, sheet_names, cluster, sheet = ctx
        widgets = [self._place_marker_button, self._read_marker_button,
                   self._remove_marker_button, self._show_bbox_button,
                   self._hide_bbox_button, self._remove_overlay_button]

        def _ok(result):
            if result is _NO_CLONE:
                show_message(
                    _("Marker: no clone placement of cell {cell!r} on cluster "
                      "{cluster!r} is placed — the live frame cannot be "
                      "derived. Place the cell on that cluster first.")
                    .format(cell=self._cell_name, cluster=cluster),
                    _WARN_STYLE, logger)
                return
            on_success(result)

        self._active_op = start_long_op(
            self._connection, widgets, _resolve_context_then, _ok, on_error,
            adapter, cfg, self._cell_name, cluster, sheet, sheet_names,
            fn, *extra_args)

    def _dispatch_draw(self, worker_fn, success_msg_ok, on_error):
        """Dispatch a DRAW overlay op (worker takes the layer name — Phase D:
        the CURRENT configured overlay layer from Settings, read on the UI
        thread and passed into the worker)."""
        def ok(uuid: Optional[str]) -> None:
            success_msg_ok(uuid)

        self._dispatch(worker_fn, ok, on_error, board_overlay.overlay_layer_name())

    def _on_place_marker(self) -> None:
        def ok(uuid: Optional[str]) -> None:
            if uuid is None:
                show_message(_("Place marker: the cell has no geometry on the "
                               "board."), _WARN_STYLE, logger)
                return
            self._marker_uuid = uuid
            self._remember_overlay(self._marker_uuid, self._bbox_uuid)
            show_message(_("Marker placed (uuid {uuid}) — drag it in KiCad, "
                           "then press “Read position”.").format(uuid=uuid),
                         _SUCCESS_STYLE, logger)
            self._reload_form()

        def err(message: str) -> None:
            show_message(_("Place marker failed: {message}").format(message=message),
                         _ERROR_STYLE, logger)

        self._dispatch_draw(_place_marker_worker, ok, err)

    def _on_show_bbox(self) -> None:
        def ok(uuid: Optional[str]) -> None:
            if uuid is None:
                show_message(_("Show bbox: the cell has no geometry."),
                             _WARN_STYLE, logger)
                return
            self._bbox_uuid = uuid
            self._remember_overlay(self._marker_uuid, self._bbox_uuid)
            show_message(_("Bbox drawn (uuid {uuid}).").format(uuid=uuid),
                         _SUCCESS_STYLE, logger)
            self._reload_form()

        def err(message: str) -> None:
            show_message(_("Show bbox failed: {message}").format(message=message),
                         _ERROR_STYLE, logger)

        self._dispatch_draw(_draw_bbox_worker, ok, err)

    def _on_read_marker(self) -> None:
        if not self._marker_uuid:
            show_message(_("No marker to read — place one first."),
                         _WARN_STYLE, logger)
            return
        marker_uuid = self._marker_uuid

        def ok(xy: Optional[tuple]) -> None:
            if xy is None:
                show_message(_("Marker not found on the board (deleted or "
                               "swept?) — place a new one."), _WARN_STYLE, logger)
                self._marker_uuid = None
                self._remember_overlay(None, self._bbox_uuid)
                return
            entry = self._current_entry()
            if entry is None:
                return
            entry["anchor_xy"] = [xy[0], xy[1]]
            entry.pop("anchor_role", None)
            entry.pop("anchor_pad", None)
            self._write_entry(entry, _("marker point"))
            self._remove_marker_only()

        def err(message: str) -> None:
            show_message(_("Read marker failed: {message}").format(message=message),
                         _ERROR_STYLE, logger)

        self._dispatch(_read_marker_worker, ok, err, marker_uuid)

    def _remove_overlay_uuid(self, uuid: Optional[str]) -> None:
        if not uuid:
            return
        adapter = self._adapter_required()
        if adapter is None:
            return
        self._active_op = start_long_op(
            self._connection,
            [self._read_marker_button, self._remove_marker_button,
             self._hide_bbox_button],
            board_overlay.remove_overlay,
            lambda _ok: None,
            lambda message: show_message(
                _("Remove overlay failed: {message}").format(message=message),
                _ERROR_STYLE, logger),
            adapter, [uuid])

    def _remove_marker_only(self) -> None:
        """Clear the marker uuid + remove the marker shape (after “Read
        position” stored the point)."""
        self._remove_overlay_uuid(self._marker_uuid)
        self._marker_uuid = None
        self._remember_overlay(None, self._bbox_uuid)
        self._reload_form()

    def _on_remove_marker(self) -> None:
        self._remove_marker_only()

    def _on_hide_bbox(self) -> None:
        self._remove_overlay_uuid(self._bbox_uuid)
        self._bbox_uuid = None
        self._remember_overlay(self._marker_uuid, None)
        self._reload_form()

    # ── Phase D: explicit overlay cleanup (D.2) ────────────────────────────

    def _refresh_overlay_layer_note(self) -> None:
        """The Marker tab's layer note — shows the CURRENT configured overlay
        layer (Settings > "Board overlay"), so a setting change is visible on
        every form reload without reopening the page."""
        note = getattr(self, "_overlay_layer_note", None)
        if note is None:
            return
        note.setText(_("Overlay is drawn on the layer “{layer}” as real KiCad "
                       "graphics; there is no colour setting — graphics take "
                       "their layer's colour (managed in KiCad). Use a "
                       "dedicated user layer so cleanup by layer is safe.")
                     .format(layer=board_overlay.overlay_layer_name()))

    def cleanup(self) -> None:
        """Remove THIS cell's drawn overlay (marker + bbox) from the live
        board by the persisted uuids, then forget them (Phase D D.2 — the
        by-uuid fast path). Called when the anchor page is left, when a
        DIFFERENT cell is opened, when the root changes, and by the page's
        own "Remove overlay" button.

        State is cleared FIRST so a missing/dead board never leaves stale
        tracking behind; the shape removal itself is a by-uuid IPC on the
        worker thread via start_long_op (never a synchronous adapter call on
        the UI thread)."""
        if self._cell_name is None:
            return
        marker, bbox = self._marker_uuid, self._bbox_uuid
        self._marker_uuid = None
        self._bbox_uuid = None
        self._remember_overlay(None, None)  # also drops any stale persisted uuid
        uuids = [u for u in (marker, bbox) if u]
        adapter = self._adapter()
        if not uuids or adapter is None:
            return
        self._active_op = start_long_op(
            self._connection, [], board_overlay.remove_overlay,
            lambda _ok: None, lambda _message: None, adapter, uuids)

    def _on_remove_overlay(self) -> None:
        """'Remove overlay' button — the explicit 'по кнопке' cleanup of Phase
        D: removes BOTH the marker and the bbox of this cell from the board."""
        self.cleanup()
        self._reload_form()
