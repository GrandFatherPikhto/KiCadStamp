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

Working context (Sheet optional / Cluster) — the "Source" tab hosts the
Sheet/Cluster pickers (Denis, 2026-09-09: "У нас должны быть выбраны в первом
табе Лист(если он нужен)/Кластер. Тогда живой инстанс будет работать").

2026-09-10 (plan overlay_frame_from_cluster): the frame is taken from the LIVE
CLUSTER and from nothing else. The cell's roles are resolved to live footprints
by the working (Cluster, Sheet), the reference slot is cell.anchor_role's (else
the first resolved one), and the world frame is re-derived from THAT footprint
via the pure inverse clone_origin_from_component (see _live_cluster_frame).
Mirror/rotation come from the live footprints too — we draw what is on the
board. Neither the top-level clone_placements, nor the entity placements
materialized from the trees, nor resolve_clone_context_live take any part: a
cell that has just been extracted (Entity created, no tree node yet) gets an
overlay like any other, and the frame can no longer follow a stale placement
instead of the cluster standing in front of the user.

The lookup still runs on the WORKER thread (_dispatch / start_long_op), because
it reads the live board (adapter.get_footprints + per-role field reads). An
honest error replaces the old "place the cell first" hint: "cluster X is not on
the live board" or "role Y of this cell has no footprint in cluster X".

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
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.cell_frame import (
    CellFrame,
    fit_cell_frame,
    reference_relative_pairs,
)
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.i18n import _
from kicadstamp.placement.services.component_resolver import resolve_pad_mount
from kicadstamp.placement.services.coordinate_position_calculator import (
    resolve_footprint_by_cluster_role,
)
from kicadstamp.placement.services.role_narrowing import narrow_candidates_by_sheet
from kicadstamp.utils.units import MM

from .. import board_overlay, settings
from ..cell_edit_context import (
    cluster_present_on_board,
    remember_cell_edit_context,
    remembered_cell_edit_context,
)
from ..worker import start_long_op
from ._cell_identity import CellIdentityWidget
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
from .live_position import _reference_slot, world_pos_to_cell_local_offset
from .rename import collect_graph_files, find_dict_entry_file
from .scheme_list import snapshot_with_resolved_sheets

logger = logging.getLogger(__name__)

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
    """True when ONE top-level ClonePlacement is a placement of `cell_name` for
    the working Cluster (+ optional Sheet). A placement matches by its OWN
    cluster field (cluster_prefix_match — the same convention as
    role_narrowing) and, when Sheet is given and the placement carries one, by
    exact sheet. NOT part of the overlay any more (see the module docstring)."""
    if getattr(cp, "cell", None) != cell_name:
        return False
    cp_cluster = getattr(cp, "cluster", None) or ""
    if cluster and not cluster_prefix_match(cp_cluster, cluster):
        return False
    if sheet and getattr(cp, "sheet", None):
        if cp.sheet != sheet:
            return False
    return True


def context_clone_candidates(cfg, cell_name: str, cluster: str,
                             sheet: str) -> list:
    """Top-level ClonePlacements of this cell matching the working Cluster
    (+ optional Sheet) — the OFFLINE (board-free) candidate list. NOT used by
    the overlay any more: since 2026-09-10 the cell page's frame comes from the
    LIVE CLUSTER (see _live_cluster_frame), never from a placement."""
    return [cp for cp in getattr(cfg, "clone_placements", []) or []
            if _matches_clone_context(cp, cell_name, cluster, sheet)]


def _single_candidate(candidates, cell_name: str, cluster: str):
    """None when nothing matches; the sole candidate when exactly one; fatal
    when several DISTINCT placements match (never "take the first" — the C.3 /
    C.5.1 "several matched -> fatal with enumeration" rule)."""
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
    (never "take the first"). Board-free, and NOT part of the overlay any more:
    since 2026-09-10 the cell page's frame comes from the LIVE CLUSTER
    (_live_cluster_frame), not from a placement of any kind. Kept as the
    flat clone_placements lookup for record-oriented readers.

    The union-with-trees twin (resolve_clone_context_live) and its worker
    wrapper (_resolve_context_then / the _NO_CLONE sentinel) were DELETED with
    that change — nothing else used them, and their "no clone placement ... is
    placed" hint was the very message the live case disproved."""
    return _single_candidate(
        context_clone_candidates(cfg, cell_name, cluster, sheet),
        cell_name, cluster)


# ────────────────────────────────────────────────────────────────────────────
# Overlay worker functions (run on the worker thread via start_long_op —
# pure IPC/file work, NO widget access). The frame is derived from the LIVE
# CLUSTER inside the worker (_live_cluster_frame): no placement, no trees.
# ────────────────────────────────────────────────────────────────────────────


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


def _live_cluster_frame(adapter, cell, cluster: str, sheet: str, sheet_names):
    """(mount, rotation_deg, mirror) of ONE cell EXACTLY as it stands on the
    board right now — derived from the LIVE CLUSTER alone (2026-09-10, plan
    overlay_frame_from_cluster).

    The cell's roles are resolved to live footprints by the working
    (Cluster, Sheet) — resolve_footprint_by_cluster_role, the same exact-match
    resolver "Select on board" and the dumb placer use, sheet narrowing included
    — the reference slot is cell.anchor_role's when it resolved, else the first
    resolved slot (_reference_slot, live_position.py), and the frame comes out
    of THAT footprint's live position/angle through the pure inverse
    clone_origin_from_component. Mirror is read from the live footprint's own
    side against the cell's layer, never from a record: we draw what is on the
    board.

    Neither resolve_clone_context_live, nor materialize_entity_placements, nor
    cfg.clone_placements, nor the trees take any part in this path. The cluster
    standing on the board is the truth (Denis, 2026-09-10: "Размещение берётся
    bbox из текущего положения кластера. И никак иначе. Никакой привязки к
    деревьям быть не должно"), so a cell that has just been extracted — with an
    Entity but no tree node yet — gets an overlay like any other.

    Raises ValidationError with an HONEST message — "cluster X is not on the
    live board" / "role Y of this cell has no footprint in cluster X" — never
    the old "place the cell first"."""
    label = _("cell {cell!r} on cluster {cluster!r}").format(
        cell=getattr(cell, "name", "?"), cluster=cluster)
    role_to_fp: dict = {}
    for slot in cell.components:
        try:
            role_to_fp[slot.role] = resolve_footprint_by_cluster_role(
                adapter, cluster, slot.role, label, sheet=sheet or None,
                sheet_names=sheet_names)
        except ValidationError:
            # This role is not uniquely present in the working cluster (absent,
            # or a tagging ambiguity) — it simply cannot be the reference slot.
            continue
    if not role_to_fp:
        on_board = {adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
                    for fp in adapter.get_footprints()}
        if cluster not in on_board:
            raise ValidationError(format_fatal_error(
                _("cluster {cluster!r} is not on the live board")
                .format(cluster=cluster),
                [_("check the working Cluster on the Source tab, or place the "
                   "cluster on this board")]))
        first_role = cell.components[0].role if cell.components else "?"
        raise ValidationError(format_fatal_error(
            _("role {role!r} of this cell has no footprint in cluster "
              "{cluster!r}").format(role=first_role, cluster=cluster),
            [_("the cell's roles must be the components tagged with the working "
               "Cluster on the board")]))
    slot = _reference_slot(cell, role_to_fp)
    fp = role_to_fp[slot.role]
    # Mirror comes from the LIVE footprint's side against the cell's own layer
    # (clone_geometry's rule) — not from clone.mirror.
    mirror = (fp.layer == BoardLayer.BL_B_Cu) != (cell.layer == "B.Cu")
    role_to_ref = {role: live_fp.ref for role, live_fp in role_to_fp.items()}
    resolved_mount = resolve_pad_mount(adapter, cell, role_to_ref, label)
    if resolved_mount is None:
        ax_mm, ay_mm = cell_mount_offset(cell)
    else:
        ax_mm, ay_mm = resolved_mount
    # The ROTATION is fitted from the stored offsets against the live deltas of
    # EVERY resolved role at once — the ONE cell<->world transform of
    # kicadstamp/cell_frame.py, the same one the geometry refresh uses. Never
    # from one component's angle: a two-pin part is symmetric and its angle
    # ambiguous by 180° (measured 2026-09-10: four capacitors gave +90 while
    # FB_PI_FLT gave -90). Mirror stays the physical layer rule above (which
    # side of the board the instance stands on), which the fit is constrained
    # to; how far the cluster is from a rigid copy of the cell comes back as
    # residual_mm (unused here — the overlay still draws what is on the board).
    slot_by_role = {s.role: s for s in cell.components}
    reference = (float(slot.offset_along_mm), float(slot.offset_across_mm),
                 fp.position.x / MM, fp.position.y / MM)
    fit = fit_cell_frame(
        reference_relative_pairs(reference, [
            (float(slot_by_role[role].offset_along_mm),
             float(slot_by_role[role].offset_across_mm),
             live.position.x / MM, live.position.y / MM)
            for role, live in role_to_fp.items() if role in slot_by_role]),
        mirror=mirror)
    frame = CellFrame.from_reference(
        rotation_deg=fit.rotation_deg if fit is not None else 0.0,
        mirror=mirror, mount=(ax_mm, ay_mm), stored_ref=reference[:2],
        live_ref_mm=reference[2:],
        residual_mm=fit.residual_mm if fit is not None else 0.0)
    return frame.placement_origin, frame.rotation_deg, frame.mirror


def _draw_bbox_worker(adapter, cell, cluster, sheet, sheet_names,
                      layer_name) -> Optional[str]:
    """Draw the cell's bbox rectangle over the LIVE cluster's instance of this
    cell — returns the created rectangle's uuid. The frame is read from the
    cluster standing on the board (_live_cluster_frame), never from a
    placement."""
    layer = _resolve_layer(adapter, layer_name)
    origin, rotation, mirror = _live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names)
    box = overlay_world_bbox_mm(_cell_to_entry(cell), origin, rotation, mirror)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    # Phase D: the stroke width comes from the Settings "Board overlay" page
    # (board_overlay module constants are only the DEFAULTS).
    return board_overlay.draw_bbox(adapter, layer, x1, y1, x2, y2,
                                   board_overlay.overlay_bbox_stroke_mm())


def _place_marker_worker(adapter, cell, cluster, sheet, sheet_names,
                         layer_name) -> Optional[str]:
    """Draw the draggable marker circle at the cell's CURRENT anchor (the
    live cluster's mount) or, when the cell has no anchor, at the centre of its
    bbox — returns the marker's uuid."""
    layer = _resolve_layer(adapter, layer_name)
    origin, rotation, mirror = _live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names)
    entry = _cell_to_entry(cell)
    ax, ay = _cell_entry_mount_offset(entry)
    if ax or ay:
        x_mm, y_mm = origin.x / MM, origin.y / MM
    else:
        bbox = cell_content_bbox(entry)
        centre = ((bbox[0] + bbox[1]) / 2.0, (bbox[2] + bbox[3]) / 2.0) \
            if bbox else (0.0, 0.0)
        x_mm, y_mm = _cell_point_to_world_mm(
            origin, rotation, mirror, 0.0, 0.0, centre[0], centre[1])
    # Phase D: radius/stroke come from the Settings "Board overlay" page
    # (board_overlay module constants are only the DEFAULTS).
    return board_overlay.draw_marker(adapter, layer, x_mm, y_mm,
                                     board_overlay.overlay_marker_radius_mm(),
                                     board_overlay.overlay_marker_stroke_mm())


def _remove_overlay_silently(adapter, uuids) -> None:
    """Best-effort removal of already-drawn overlay shapes (J.2). A shape the
    user has already deleted in KiCad, or a stale uuid from a previous session,
    is NOT an error — the caller is about to draw a fresh one."""
    doomed = [u for u in (uuids or ()) if u]
    if not doomed:
        return
    try:
        board_overlay.remove_overlay(adapter, doomed)
    except Exception:  # noqa: BLE001 — the draw that follows is the point
        logger.debug("overlay replacement could not remove %s", doomed,
                     exc_info=True)


def _replace_bbox_worker(adapter, cell, cluster, sheet, sheet_names,
                         layer_name, old_uuids) -> Optional[str]:
    """Draw the cell's bbox, REPLACING any rectangle already drawn for it: the
    remembered uuid(s) are removed in the SAME worker operation as the draw —
    ONE start_long_op, never two in a row (that would race the redraw), because
    Denis, 2026-09-10: "Если он есть, его не надо рисовать ещё!" A stale bbox
    left from a previous position was also a direct cause of "маркер не попадает
    в bbox"."""
    _remove_overlay_silently(adapter, old_uuids)
    return _draw_bbox_worker(adapter, cell, cluster, sheet, sheet_names,
                             layer_name)


def _replace_marker_worker(adapter, cell, cluster, sheet, sheet_names,
                           layer_name, old_uuids) -> Optional[str]:
    """The marker twin of _replace_bbox_worker — the previously drawn marker is
    removed in the same worker op before the new one is drawn."""
    _remove_overlay_silently(adapter, old_uuids)
    return _place_marker_worker(adapter, cell, cluster, sheet, sheet_names,
                                layer_name)


def _read_marker_worker(adapter, cell, cluster, sheet, sheet_names,
                        marker_uuid) -> Optional[tuple[float, float]]:
    """Read the (user-dragged) marker's world position and convert it into the
    cell's own bbox-frame anchor (anchor_xy) — the world->local inversion uses
    the SAME live-cluster frame the marker was placed from
    (world_pos_to_cell_local_offset, live_position.py — one implementation); the
    result is the ABSOLUTE bbox offset (the current mount + the offset relative
    to it). None when the marker is no longer on the board."""
    pos = board_overlay.read_marker(adapter, marker_uuid)
    if pos is None:
        return None
    origin, rotation, mirror = _live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names)
    world = Vector2.from_xy(int(round(pos[0] * MM)), int(round(pos[1] * MM)))
    rel = world_pos_to_cell_local_offset(origin, rotation, mirror, world)
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
        # G.2: the config's {uuid: name} sheet map, cached in set_root_path so
        # the per-snapshot-tick re-resolution/narrowing never reloads the config
        # itself. The live snapshot and its sheet-resolved twin (see
        # refresh_known_roles); the resolved one is what the cluster narrowing
        # filters.
        self._sheet_names: dict = {}
        self._snapshot: list = []
        self._resolved_snapshot: list = []
        # G.3: the same programmatic-population guard CellDock uses — combo
        # refills / prefill must never write the remembered context over fresh
        # data.
        self._loading = False
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

        # ── Tab 1 — Source: identity + working context (task V) ───────────
        # The ONE page a cell selection opens (prompt_2026_09_11_cell_page_
        # merge.md). The identity block is the SHARED CellIdentityWidget — also
        # plugged into PlacerDock, since duplicating it is exactly what
        # desynchronised the two pages this task merges. Its (Cluster, Sheet)
        # pair is the working context every other tab resolves against.
        source_page = QWidget()
        source_layout = QVBoxLayout(source_page)
        source_layout.setContentsMargins(4, 4, 4, 4)
        self._identity = CellIdentityWidget(
            sheet_placeholder=_("Sheet (optional)"),
            cluster_placeholder=_("Cluster of the placed cell"))
        # The pre-task-V attribute names stay: the working-context combos moved
        # here from the Component tab and G.2/G.3 are wired to these objects.
        self._cluster_combo = self._identity.cluster_edit
        self._sheet_combo = self._identity.sheet_edit
        self._name_edit = self._identity.name_edit
        self._comment_edit = self._identity.comment_edit
        self._identity.note.setText(
            _("(Cluster, Sheet) set the working context for the other tabs — "
              "they decide which placed instance of this cell is edited."))
        source_layout.addWidget(self._identity)
        source_layout.addStretch(1)
        self._tabs.addTab(source_page, _("Source"))

        # ── Tab 2 — Role anchor (the old "Component" tab) ─────────────────
        comp_page = QWidget()
        comp_layout = QVBoxLayout(comp_page)
        comp_layout.setContentsMargins(4, 4, 4, 4)
        comp_layout.addWidget(self._template_scope_note())

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
        self._tabs.addTab(comp_page, _("Role anchor"))

        # ── Tab 3 — Marker anchor (the old "Marker" tab) ──────────────────
        marker_page = QWidget()
        marker_layout = QVBoxLayout(marker_page)
        marker_layout.setContentsMargins(4, 4, 4, 4)
        marker_layout.addWidget(self._template_scope_note())

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
        self._tabs.addTab(marker_page, _("Marker anchor"))

        self._cluster_combo.currentTextChanged.connect(self._on_cluster_changed)
        # G.2: the Sheet narrows the Cluster list; G.3: both combos persist the
        # working context on a manual pick.
        self._sheet_combo.currentTextChanged.connect(self._on_sheet_changed)
        # Name/Comment (task V) write back into the top-level clone_placements
        # record of this cell — only when one exists; otherwise the fields are
        # read-only (see _load_identity / _identity_record).
        self._name_edit.editingFinished.connect(self._on_identity_edited)
        self._comment_edit.editingFinished.connect(self._on_identity_edited)
        self._tabs.currentChanged.connect(lambda _i: self._reload_form())

    @staticmethod
    def _template_scope_note() -> QLabel:
        """Both anchor tabs write the CELL TEMPLATE (cells:), not this
        placement — every placed instance of the cell shifts at once. Said in
        the interface because the Source tab's fields write the placement only
        (task V)."""
        note = QLabel(_("This tab edits the CELL TEMPLATE (cells:) — every "
                        "placed instance of this cell changes at once."))
        note.setWordWrap(True)
        return note

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
            self._snapshot = []      # a stale snapshot belongs to the old root
        self._root_path = path
        self._sheet_names = {}
        if path is not None:
            try:
                _cfg, ctx = load_config(str(path))
            except (ValidationError, OSError):
                ctx = None
            if ctx is not None:
                # ctx.sheet_names is a "sheet path -> readable name" dict; the
                # combo shows the NAMES, not the uuid-path keys (same as
                # rename.py's sorted(set(ctx.sheet_names.values()))). The map is
                # CACHED too — the sheet->cluster narrowing and the snapshot
                # re-resolution need it on every snapshot tick without
                # reloading the config there (G.2).
                self._sheet_names = ctx.sheet_names or {}
                self._loading = True
                try:
                    set_combo_items(self._sheet_combo,
                                    sorted(set(self._sheet_names.values())))
                finally:
                    self._loading = False
        else:
            self._loading = True
            try:
                self._sheet_combo.clear()
            finally:
                self._loading = False
        self._marker_uuid = None
        self._bbox_uuid = None
        # The sheet map may have changed — re-narrow the Cluster list.
        self._refill_cluster_choices()
        if self._cell_name is not None:
            self._reload_form()

    def refresh_known_roles(self, snapshot) -> None:
        """Feed the live-board snapshot into the working-context Cluster combo
        (wired into DockHub.push_snapshot like every other dock's
        refresh_known_roles — a Phase C gap fixed with Phase E: the combo was
        never populated, so the anchor page could only be narrowed by hand or
        by "Read from selection").

        G.2: the snapshot is STORED and re-resolved through
        snapshot_with_resolved_sheets against the cached config sheet map — a
        live Board's own .sheet is always a list of None (Board.connect passes
        no schematic_dir), so filtering on it directly would match nothing at
        any sheet. The Cluster list is then refilled narrowed by the currently
        selected Sheet. Fill, never restrict: the combo stays an editable
        picker, the list is only a hint (a typed cluster not on the board is
        still accepted). The Role combo is deliberately NOT touched — it is a
        closed list of THIS cell's own components, not a live-board value."""
        self._snapshot = list(snapshot or [])
        # Re-resolve .sheet against the config map — a live Board's own sheet
        # resolution is always all-None (Board.connect passes no schematic_dir).
        # A synthetic snapshot without the raw .fp handle (some tests) has
        # nothing to re-resolve, so it is used as-is rather than crashing the
        # whole feed.
        if all(hasattr(s, "fp") for s in self._snapshot):
            self._resolved_snapshot = snapshot_with_resolved_sheets(
                self._snapshot, self._sheet_names)
        else:
            self._resolved_snapshot = list(self._snapshot)
        self._refill_cluster_choices()

    def _refill_cluster_choices(self) -> None:
        """Refill the Cluster combo from the sheet-resolved snapshot, narrowed by
        the currently selected Sheet (G.2): only the clusters whose footprints
        actually sit on that sheet survive. Uses ONLY narrow_candidates_by_sheet —
        it narrows only when that genuinely reduces the set, an empty sheet is a
        no-op, and a cluster deeper than the chosen sheet segment still matches
        (_fp_on_sheet checks every path segment). set_combo_items preserves the
        current text when it survives the refill, so a cluster the user already
        picked is not reset silently."""
        snapshot = self._resolved_snapshot
        if all(hasattr(s, "fp") for s in snapshot):
            # narrow_candidates_by_sheet narrows a list of FOOTPRINTS (its
            # _fp_on_sheet reads fp.sheet_path_uuids) — pass the Selected pair's
            # raw .fp, then map the survivors back to their clusters.
            kept = {id(fp) for fp in narrow_candidates_by_sheet(
                [s.fp for s in snapshot], self._sheet_combo.currentText().strip(),
                self._sheet_names)}
            clusters = sorted({s.cluster for s in snapshot
                               if s.cluster and id(s.fp) in kept})
        else:
            # A synthetic snapshot without the raw fp handle (tests) carries no
            # sheet information to narrow on — plain distinct clusters.
            clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self._loading = True
        try:
            set_combo_items(self._cluster_combo, clusters)
        finally:
            self._loading = False

    def _on_sheet_changed(self) -> None:
        """The working Sheet changed — re-narrow the Cluster list to that sheet
        (G.2), persist the working context (G.3) and re-resolve the placement
        identity, whose record is scoped by (Cluster, Sheet) too (task V)."""
        if self._loading:
            return
        self._refill_cluster_choices()
        self._remember_working_context()
        self._reload_identity()

    def _remember_working_context(self) -> None:
        """Persist the working (Cluster, Sheet) for the loaded cell (G.3) —
        manual combo picks must survive too, not only "Read from selection".
        Best-effort and never raises (remember_cell_edit_context swallows
        everything internally); guarded by _loading so prefill / snapshot
        refills never write over fresh data, and an empty cluster writes
        nothing (also guaranteed inside the callee)."""
        if self._loading or self._cell_name is None:
            return
        cluster = self._cluster_combo.currentText().strip()
        if not cluster:
            return
        remember_cell_edit_context(
            self._root_path, self._cell_name, cluster,
            self._sheet_combo.currentText().strip() or None)

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
        dropped too. Never a fatal, never an exception.

        G.3: "unresolvable" only applies when the board IS connected. With no
        adapter the remembered cluster is a HINT we simply cannot confirm (not a
        stale one), so it is applied as-is — cluster_present_on_board() returns
        False for a missing adapter, which used to throw a perfectly good
        remembered context away offline."""
        # A reused view must not leak the PREVIOUS cell's working context, and
        # this prefill must never persist anything itself (G.3).
        self._loading = True
        try:
            self._sheet_combo.setCurrentText("")
            self._cluster_combo.setCurrentText("")
        finally:
            self._loading = False
        if self._cell_name is None or self._root_path is None:
            return
        cluster, sheet = remembered_cell_edit_context(
            self._root_path, self._cell_name)
        if not cluster:
            return
        adapter = self._adapter()
        if adapter is not None and not cluster_present_on_board(adapter, cluster):
            return                      # live board, cluster gone -> stay empty
        self._loading = True
        try:
            self._cluster_combo.setCurrentText(cluster)
            if sheet:
                sheets = {self._sheet_combo.itemText(i)
                          for i in range(self._sheet_combo.count())}
                if sheet in sheets:
                    self._sheet_combo.setCurrentText(sheet)
        finally:
            self._loading = False

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

    def _stale_overlay_uuids(self, which: str) -> list:
        """Every uuid this cell's overlay may still own for `which`
        ('marker' | 'bbox') — the in-memory one AND the persisted one, so a
        leftover from a previous session is replaced too (J.2, 2026-09-10)."""
        current = self._marker_uuid if which == "marker" else self._bbox_uuid
        persisted = self._cell_overlay_state().get(which)
        return [u for u in dict.fromkeys([current, persisted]) if u]

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

    # ── Task V: the Source tab's placement identity (Name/Comment) ─────────

    def _identity_raw_matches(self) -> list:
        """Every RAW top-level clone_placements dict of this cell matching the
        working (Cluster, Sheet) as (path, item) pairs.

        RAW and OFFLINE on purpose: the write path is raw (read_data/
        merge_write), and a tree-materialized placement has NO record to write
        into — the case that must stay read-only (writing one would duplicate
        the tree). Only the flat top-level clone_placements are considered.
        The matching rule mirrors context_clone_candidates' on the loaded
        dataclasses: cluster_prefix_match + exact sheet when both sides carry
        one."""
        if self._root_path is None or self._cell_name is None:
            return []
        cluster = self._cluster_combo.currentText().strip()
        if not cluster or not Path(self._root_path).exists():
            return []
        sheet = self._sheet_combo.currentText().strip()
        matches = []
        for path in collect_graph_files(self._root_path):
            try:
                items = read_data(path).get("clone_placements") or []
            except (ValidationError, OSError):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("cell") != self._cell_name:
                    continue
                if not cluster_prefix_match(item.get("cluster") or "", cluster):
                    continue
                if sheet and item.get("sheet") and item["sheet"] != sheet:
                    continue
                matches.append((path, item))
        return matches

    def _reload_identity(self) -> None:
        """Fill Name/Comment from the ONE top-level record of this cell, and
        mark them editable only when such a record exists. Several matches stay
        a FATAL with the enumeration (same rule as _single_candidate), never
        'take the first'. Runs under the _loading guard so a programmatic fill
        never writes the working context back (G.3)."""
        matches = self._identity_raw_matches()
        if len(matches) > 1:
            names = ", ".join(sorted(
                str(m[1].get("name") or m[1].get("cluster") or "?")
                for m in matches))
            show_message(
                _("cell {cell!r}: several clone placements match cluster "
                  "{cluster!r} — {names}").format(
                    cell=self._cell_name,
                    cluster=self._cluster_combo.currentText().strip(),
                    names=names),
                _ERROR_STYLE, logger)
        editable = len(matches) == 1
        self._identity.set_record_fields_editable(editable)
        self._loading = True
        try:
            if editable:
                item = matches[0][1]
                self._name_edit.setText(str(item.get("name") or ""))
                self._comment_edit.setText(str(item.get("comment") or ""))
            else:
                self._name_edit.setText("")
                self._comment_edit.setText("")
        finally:
            self._loading = False

    def _on_identity_edited(self) -> None:
        """Name/Comment committed — write them back into the ONE top-level
        record, IN PLACE.

        Never creates a record: with no top-level placement in context the two
        fields are read-only and this is a no-op, so a tree placement's
        position is never duplicated as a clone_placements entry. The entry is
        re-located by its PRE-edit identity, because Name itself is the record's
        identity — a plain identity lookup after the edit would miss it and
        append a duplicate."""
        if self._loading or self._cell_name is None:
            return
        if self._name_edit.isReadOnly():
            return
        matches = self._identity_raw_matches()
        if len(matches) != 1:
            return
        path, item = matches[0]
        old_key = item.get("name") or item.get("cluster")
        name = self._name_edit.text().strip()
        comment = self._comment_edit.text().strip()
        new_item = dict(item)
        # Same "never write a redundant field" rule every other save path uses
        # (placer.py's _build_entry_dict): absent Name means "equal to Cluster".
        if name and name != (item.get("cluster") or ""):
            new_item["name"] = name
        else:
            new_item.pop("name", None)
        if comment:
            new_item["comment"] = comment
        else:
            new_item.pop("comment", None)
        try:
            items = list(read_data(path).get("clone_placements") or [])
            for i, raw in enumerate(items):
                if (isinstance(raw, dict)
                        and (raw.get("name") or raw.get("cluster")) == old_key):
                    items[i] = new_item
                    break
            else:
                return
            # clone_placements is a LIST section: merge_write's `section=`
            # form is for dict sections, so the whole (re-read) list is merged
            # at the top level — every other top-level key is preserved.
            merge_write(path, {"clone_placements": items})
        except (ValidationError, OSError) as e:
            show_message(_("Identity save failed: {error}").format(error=e),
                         _ERROR_STYLE, logger)
            return
        show_message(_("Cell {name!r}: placement identity stored.")
                     .format(name=self._cell_name), _SUCCESS_STYLE, logger)
        self.saved.emit()

    def _reload_form(self) -> None:
        """Refill the whole form from the current cell entry — called on open,
        on tab switch and after a save so all tabs stay in sync."""
        self._refresh_overlay_layer_note()
        self._reload_identity()
        if self._cell_name is None:
            self._title.setText(_("Pick a Cell in the Config tree."))
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

        self._title.setText(_("Cell {name!r}").format(name=self._cell_name))
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
        # G.3: a manual Cluster pick persists the working context (guarded by
        # _loading, so programmatic refills/prefill never write).
        self._remember_working_context()
        if self._cell_name is None:
            return
        entry = self._current_entry()
        if entry is None:
            return
        roles = sorted({c.get("role") for c in entry.get("components", [])
                        if c.get("role")})
        self._fill_role_choices(roles, self._cluster_combo.currentText().strip())
        # Task V: the placement record the identity fields edit is scoped by the
        # working (Cluster, Sheet) too — re-resolve it on a context change.
        self._reload_identity()

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
        """(cfg, sheet_names, cluster, sheet) for the overlay's live frame, or
        None with a message shown when the working context can't be resolved.

        Purely UI-thread validation: the project root, the loaded cell and the
        working Cluster must be set. Everything that touches the board — and
        therefore the whole frame derivation — happens on the WORKER thread
        (_live_cluster_frame / _dispatch)."""
        if self._root_path is None or self._cell_name is None:
            show_message(_("Marker needs a project root — open a project "
                           "first."), _WARN_STYLE, logger)
            return None
        cluster = self._cluster_combo.currentText().strip()
        sheet = self._sheet_combo.currentText().strip()
        if not cluster:
            show_message(_("Marker: pick the working Cluster first (Source "
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
        collected on the UI thread, the live-cluster frame + all IPC on the
        worker, completion back on the UI thread).

        The worker takes the loaded CELL plus the working (Cluster, Sheet) and
        derives its frame from the LIVE CLUSTER (2026-09-10, plan
        overlay_frame_from_cluster): no placement lookup, no
        materialize_entity_placements, no _NO_CLONE sentinel and no "place the
        cell first" hint. An honest ValidationError from it (cluster not on the
        board / role not in the cluster) travels the normal failure path and is
        shown verbatim."""
        adapter = self._adapter_required()
        if adapter is None:
            return
        ctx = self._context()
        if ctx is None:
            return
        cfg, sheet_names, cluster, sheet = ctx
        cell = cfg.cells.get(self._cell_name)
        if cell is None:
            show_message(_("cell {cell!r} not found in config")
                         .format(cell=self._cell_name), _ERROR_STYLE, logger)
            return
        widgets = [self._place_marker_button, self._read_marker_button,
                   self._remove_marker_button, self._show_bbox_button,
                   self._hide_bbox_button, self._remove_overlay_button]
        self._active_op = start_long_op(
            self._connection, widgets, fn, on_success, on_error,
            adapter, cell, cluster, sheet, sheet_names, *extra_args)

    def _dispatch_draw(self, worker_fn, success_msg_ok, on_error, *extra):
        """Dispatch a DRAW overlay op (worker takes the layer name — Phase D:
        the CURRENT configured overlay layer from Settings, read on the UI
        thread and passed into the worker, then `*extra` — J.2's stale uuids)."""
        def ok(uuid: Optional[str]) -> None:
            success_msg_ok(uuid)

        self._dispatch(worker_fn, ok, on_error,
                       board_overlay.overlay_layer_name(), *extra)

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

        # J.2: REPLACE the marker instead of piling a second one on the board —
        # the remembered uuid(s) are dropped in the same worker op.
        self._dispatch_draw(_replace_marker_worker, ok, err,
                            self._stale_overlay_uuids("marker"))

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

        # J.2: the same replacement semantics for the bbox — a stale rectangle
        # left at the previous position was the "marker doesn't land in the
        # bbox" complaint.
        self._dispatch_draw(_replace_bbox_worker, ok, err,
                            self._stale_overlay_uuids("bbox"))

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
        if getattr(self._connection, "long_op_active", False):
            # Never interleave two IPC ops on the shared kipy REQ socket —
            # rapid Config-tree clicks switch cells faster than an overlay
            # removal completes. Leave this cell's state AND its persisted
            # uuids in place so the GUI-exit / whole-layer sweep still finds
            # the shape later (same discipline as cleanup_all_overlays_sync).
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
