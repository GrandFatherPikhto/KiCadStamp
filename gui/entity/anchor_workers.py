# gui/entity/anchor_workers.py
"""The ENTITY page's Anchor tab, split out: the overlay WORKERS and the pure
selection helpers (step 5 of plan_2026_10_09_entity_page — moved verbatim from
gui/entity/anchor_tab.py, which inherited them from the deleted
gui/docks/cell_anchor_view.py).

The WIDGET (AnchorTabWidget) stays in gui/entity/anchor_tab.py and calls these;
nothing here builds, reads or touches a widget. Two kinds of thing live here:

  * the OVERLAY WORKERS — run on the worker thread via start_long_op (pure
    IPC/file work). The frame is derived from the LIVE CLUSTER inside the worker
    (_live_cluster_frame): no placement, no trees;
  * the PURE SELECTION HELPERS — no Qt at all, unit-testable without a
    QApplication (read_anchor_source, roles_for_cluster, the clone-context
    resolvers, find_pad_owner);
  * the GUI-exit sweep (cleanup_all_overlays_sync) — its `connection.board` read
    is SIGNED (Denis, 09.10.2026): it IS the UI-thread exit path, the adapter is
    taken only to hand it to the worker. THE SIGN TRAVELS WITH THIS FUNCTION —
    moving it here must not turn it into a suspect (tools/door_lint.py).

The board is only ever touched inside the workers the widget dispatches; this
module holds no board handle of its own.
"""
from typing import Any, Optional

from kipy.board_types import Pad as KipyPad

from kicadstamp.cell_geometry_refresh import cell_content_bbox
from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config import clone_placement_effective_name
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM

from .. import board_overlay, overlay_markers
from ..connection import ui_thread_board_read
from ..docks.live_position import (
    _live_cluster_frame,
    world_pos_to_cell_local_offset,
)
from ..worker import start_long_op


# ────────────────────────────────────────────────────────────────────────────
# Phase-D shutdown cleanup (D.2) — remove EVERY overlay shape the owner owns.
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
    """GUI-shutdown cleanup of EVERY overlay shape the owner knows about
    (Phase D D.2) — the by-uuid fast path across all namespaces/keys of the
    overlay map. The removal IPC runs on the worker thread via start_long_op
    (never a synchronous adapter call on the UI thread); this function then
    waits for it with a BOUNDED nested event loop, so quitting never races the
    worker and a dead socket never stalls shutdown for more than timeout_s.

    No-op when there is nothing tracked, no live board, another long op is
    already on the shared kipy socket, or the removal did not finish in time.

    The `connection.board` read is SIGNED (Denis, 09.10.2026): this IS the
    UI-thread exit path — the app is quitting, its windows are already going
    away, and the adapter is only taken here to hand it to the worker."""
    uuids = overlay_markers.owner.all_uuids()
    if not uuids:
        return
    with ui_thread_board_read(
            reason="GUI-exit overlay sweep: take the adapter to hand it to the "
                   "worker that removes the drawn shapes"):
        board = getattr(connection, "board", None)
    adapter = getattr(board, "adapter", None) if board is not None else None
    if adapter is None:
        return
    if getattr(connection, "long_op_active", False):
        return  # never interleave on the shared kipy REQ socket
    # No guard widget, deliberately (Э2, plan_2026_09_12_busy_indicator): this is
    # the GUI-EXIT sweep — the app is quitting and its windows are already going
    # away, so there is nothing left to click and nothing to grey out.
    controller = start_long_op(
        connection, [], board_overlay.remove_overlay,
        lambda _ok: None, lambda _message: None, adapter, uuids)
    waited = _wait_long_op_done(controller, timeout_s)
    # Clear the map only once the removal has genuinely finished (a timeout
    # while the op is still on the socket means the shapes may still be there
    # — leave the map for a later whole-layer sweep).
    if waited and not getattr(connection, "long_op_active", False):
        overlay_markers.owner.forget_all()


# ────────────────────────────────────────────────────────────────────────────
# Overlay worker functions (run on the worker thread via start_long_op — pure
# IPC/file work, NO widget access). The frame is derived from the LIVE CLUSTER
# inside the worker (_live_cluster_frame): no placement, no trees.
# ────────────────────────────────────────────────────────────────────────────

def _cell_entry_mount_offset(entry: dict) -> tuple[float, float]:
    """The cell entry's CURRENT mount A in its stored frame (anchor_xy, else
    anchor_role's centre offset, else (0,0)) — the dict twin of cell_mount_offset."""
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
    mapping apply_clone_geometry uses for content."""
    rotated = rotate_local_offset(along_mm - ax_mm, across_mm - ay_mm, rotation_deg)
    px = origin.x + rotated.x
    py = origin.y + rotated.y
    if mirror:
        px = 2 * origin.x - px
    return (px / MM, py / MM)


def overlay_world_bbox_mm(entry: dict, origin: Vector2, rotation_deg: float,
                          mirror: bool) -> tuple[float, float, float, float] | None:
    """The cell bbox's WORLD, axis-aligned bounding box (x1, y1, x2, y2 in mm)
    for the live instance frame (origin/rotation/mirror). None when the cell
    entry has no geometry."""
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


def _ensure_bbox_worker(adapter, cell, cluster, sheet, sheet_names,
                        key, layer_name, role_to_ref=None) -> Optional[str]:
    """Idempotently make `key` own exactly ONE bbox rectangle over the LIVE
    cluster's instance of this cell — returns the created rectangle's uuid (or
    None when the cell has no geometry). `role_to_ref` (LAST, optional) is the
    entity's identified pair: when it is present, the frame is pinned to it."""
    origin, rotation, mirror = _live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names, role_to_ref)
    box = overlay_world_bbox_mm(_cell_to_entry(cell), origin, rotation, mirror)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    return overlay_markers.owner.ensure_bbox(
        adapter, key, x1, y1, x2, y2, layer_name=layer_name)


def _ensure_marker_worker(adapter, cell, cluster, sheet, sheet_names,
                          key, layer_name, role_to_ref=None) -> Optional[str]:
    """Idempotently make `key` own exactly ONE draggable marker circle at the
    cell's CURRENT anchor (the live cluster's mount) or, when the cell has no
    anchor, at the centre of its bbox — returns the created circle's uuid (or
    None when the cell has no geometry)."""
    origin, rotation, mirror = _live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names, role_to_ref)
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
    return overlay_markers.owner.ensure_marker(
        adapter, key, x_mm, y_mm, layer_name=layer_name)


def _read_marker_worker(adapter, cell, cluster, sheet, sheet_names,
                        key, role_to_ref=None) -> Optional[tuple[float, float]]:
    """Read the (user-dragged) marker's world position for `key` and convert it
    into the cell's own bbox-frame anchor (anchor_xy) — the world->local
    inversion uses the SAME live-cluster frame the marker was placed from. None
    when the marker is no longer on the board."""
    pos = overlay_markers.owner.read_position(adapter, key)
    if pos is None:
        return None
    origin, rotation, mirror = _live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names, role_to_ref)
    world = Vector2.from_xy(int(round(pos[0] * MM)), int(round(pos[1] * MM)))
    rel = world_pos_to_cell_local_offset(origin, rotation, mirror, world)
    a0, a1 = cell_mount_offset(cell)
    return (round(a0 + rel[0], 9), round(a1 + rel[1], 9))


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
    against every footprint's cached pads."""
    for fp in footprints or []:
        for pad in adapter.get_footprint_pads(fp):
            if _pad_uuid(pad) == pad_uuid:
                return fp
    return None


def read_anchor_source(adapter, items, cell_roles, cell_name: str,
                       label: str = "cell anchor") -> dict:
    """Read the current board selection and classify it into the Role anchor
    tab's three cases (§0.2 / C.2):

      - a pad is selected -> its owner footprint is found by uuid scan; the
        footprint's Role + Cluster and the pad's number are returned;
      - a footprint is selected -> Role + Cluster, pad left unset;
      - only a Via/Track (or non-content graphics) -> kind 'via'/'other' (NOT
        an error — the caller tells the user this is the Marker tab's case).

    Fatal (ValidationError, never a silent guess): nothing selected; the
    selection spans several Clusters; several roles among the selected content;
    a pad whose owner footprint is not on the board; the inferred role is not
    one of this cell's components. `cell_roles` may be [] — the last check is
    then skipped."""
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


def roles_for_cluster(snapshot, cell_roles, cluster: str) -> list:
    """The cell roles that are actually present among the footprints of
    `cluster` in the page's board SNAPSHOT (cluster_prefix_match against the
    Cluster field), sorted. Falls back to the FULL cell_roles when the snapshot
    is unavailable/empty or nothing of this cell is on the cluster — the
    narrowing is a HINT, never a hard filter that hides a valid role."""
    if not cell_roles or not cluster:
        return sorted(cell_roles)
    present: set = set()
    for item in snapshot or ():
        fp_cluster = getattr(item, "cluster", None) or ""
        if cluster_prefix_match(fp_cluster, cluster):
            role = getattr(item, "role", None)
            if role in cell_roles:
                present.add(role)
    return sorted(present) if present else sorted(cell_roles)


def _matches_clone_context(cp, cell_name: str, cluster: str, sheet: str) -> bool:
    """True when ONE top-level ClonePlacement is a placement of `cell_name` for
    the entity's Cluster (+ optional Sheet)."""
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
    """Top-level ClonePlacements of this cell matching the entity's Cluster
    (+ optional Sheet) — the OFFLINE (board-free) candidate list. NOT used by
    the overlay: the frame comes from the LIVE CLUSTER (_live_cluster_frame)."""
    return [cp for cp in getattr(cfg, "clone_placements", []) or []
            if _matches_clone_context(cp, cell_name, cluster, sheet)]


def _single_candidate(candidates, cell_name: str, cluster: str):
    """None when nothing matches; the sole candidate when exactly one; fatal
    when several DISTINCT placements match (never "take the first")."""
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
    """The ONE top-level clone placement of this cell for the entity's
    Cluster/Sheet — None when nothing matches; fatal when several match.
    Board-free, and NOT part of the overlay any more."""
    return _single_candidate(
        context_clone_candidates(cfg, cell_name, cluster, sheet),
        cell_name, cluster)
