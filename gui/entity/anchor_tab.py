# gui/entity/anchor_tab.py
"""AnchorTabWidget — the ENTITY page's "Anchor" tab: the cell's Role anchor and
Marker anchor, moved off the CELL page (step 4 of plan_2026_10_09_entity_page).

WHY IT MOVED. The anchor writes the CELL TEMPLATE (cells:) — one mount for every
placed instance of the cell — so the address it resolves against must be visible
where it is edited. On the ENTITY page the address is the entity RECORD's own
(cell, cluster, sheet, refs): there are no working-context comboboxes any more,
and every board read is pinned to THAT instance.

THE ONE MEANING CHANGE (announced in the commit, Денис 09.10.2026): "Read from
selection" fills Role/Pad only from the ENTITY's OWN instance — a selection of a
foreign cluster is refused with a line (the same "another instance is another
entity" rule the part-3 doors follow), never silently accepted as the old
working-context page did.

THE DOOR (Denis, 09.10.2026). The two board reads the CELL page used to make are
NOT carried over as bare suspects:
  * `cleanup_all_overlays_sync` — the GUI-exit overlay sweep; it belongs on the
    UI thread (the app is quitting), so its `connection.board` read is SIGNED;
  * `AnchorTabWidget._adapter` — the anchor's board access: ONE explicit
    selection read of the "Read from selection" button plus handing the adapter
    to the overlay workers; SIGNED, with the reason spelled out.
Both signs say, in words, why the read belongs on the UI thread (the whole point
of the sign — gui/connection.ui_thread_board_read).

The pure selection helpers and the overlay workers live here now (moved verbatim
from gui/docks/cell_anchor_view.py, which step 5 deletes); a cell's roles in the
cell's order are the ONE owner instance_candidates.cell_role_order.
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
from kicadstamp.config import clone_placement_effective_name, load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM

from .. import board_overlay, overlay_markers, settings  # noqa: F401
from ..connection import ui_thread_board_read
from ..docks._common import (
    ERROR_STYLE as _ERROR_STYLE,
    SUCCESS_STYLE as _SUCCESS_STYLE,
    WARN_STYLE as _WARN_STYLE,
    merge_write,
    read_data,
    set_combo_items,
    show_message,
)
from ..docks.instance_candidates import cell_role_order
from ..docks.live_position import (
    _live_cluster_frame,
    world_pos_to_cell_local_offset,
)
from ..docks.rename import find_dict_entry_file
from ..worker import start_long_op

logger = logging.getLogger(__name__)


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


# ────────────────────────────────────────────────────────────────────────────
# The widget
# ────────────────────────────────────────────────────────────────────────────

class AnchorTabWidget(QWidget):
    """The ENTITY page's "Anchor" tab — Role anchor + Marker anchor for ONE
    entity's cell. The address (cell, cluster, sheet, refs) is FIXED by the
    entity RECORD (set_context); the cells: template itself is what both tabs
    write, shared by every entity of that cell (said aloud above the tabs)."""

    saved = pyqtSignal()

    def __init__(self, main_window=None, connection=None, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._connection = connection
        self._root_path: Optional[Path] = None
        self._file_path: Optional[Path] = None
        self._cell_name: Optional[str] = None
        # The FIXED address — from the entity record, never a dropdown.
        self._cluster: str = ""
        self._sheet: str = ""
        self._refs: dict = {}
        self._entity_count: int = 1
        self._sheet_names: dict = {}
        self._snapshot: list = []
        self._resolved_snapshot: list = []
        self._active_op = None
        self._loading = False
        self._overlay = overlay_markers.owner
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # The line that says WHOSE map is being edited (replaces the removed
        # write_applies_line of the instance dropdown): the anchor is one value
        # per CELL, so a change here lands on every entity of it.
        self._applies_label = QLabel("")
        self._applies_label.setObjectName("anchor_applies_label")
        self._applies_label.setWordWrap(True)
        layout.addWidget(self._applies_label)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # ── Role anchor (the old "Component" tab) ─────────────────────────
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
            _("Fill Role (+ Pad) from ONE selected component or pad of THIS "
              "entity's instance on the live board."))
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

        # ── Marker anchor (the old "Marker" tab) ──────────────────────────
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
                          "this entity's Cluster."))
        m_note.setWordWrap(True)
        marker_form.addRow(m_note)
        marker_layout.addWidget(marker_box)
        marker_layout.addStretch(1)
        self._tabs.addTab(marker_page, _("Marker anchor"))

    @staticmethod
    def _template_scope_note() -> QLabel:
        """Both tabs write the CELL TEMPLATE (cells:) — every placed instance of
        the cell shifts at once. Said in the interface; the applies-line above
        the tabs says it too, with the entity count."""
        note = QLabel(_("This tab edits the CELL TEMPLATE (cells:) — every "
                        "placed instance of this cell changes at once."))
        note.setWordWrap(True)
        return note

    # ── Context (fed by the entity page) ──────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """The project root changed — re-read the config's sheet map and clean
        up overlay state that is only valid for the previous root. A re-set of
        the SAME path (a graph-shape refresh) keeps a marker/bbox the user is
        mid-edit with."""
        if path != self._root_path:
            self.cleanup()
            self._snapshot = []
            self._resolved_snapshot = []
        self._root_path = path
        self._sheet_names = {}
        if path is not None:
            try:
                _cfg, ctx = load_config(str(path))
            except (ValidationError, OSError):
                ctx = None
            if ctx is not None:
                self._sheet_names = dict(ctx.sheet_names or {})
        self._reload_form()

    def set_context(self, cell_name: Optional[str], cluster: Optional[str],
                    sheet: Optional[str], file_path,
                    refs=None, entity_count: int = 1) -> None:
        """Point the tab at ONE entity's address — its cell, its (cluster,
        sheet), its OWN file, its refs (the identified pair, when the entity
        carries one) and how many entities share the cell (for the applies
        line). Opening a DIFFERENT cell closes the previous cell's editing
        session: its drawn overlay is cleaned up first."""
        if cell_name != self._cell_name and self._cell_name is not None:
            self.cleanup()
        self._cell_name = cell_name
        self._cluster = str(cluster or "")
        self._sheet = str(sheet or "")
        self._file_path = Path(file_path) if file_path is not None else None
        self._refs = dict(refs or {}) if isinstance(refs, dict) else {}
        self._entity_count = int(entity_count or 1)
        self._reload_form()

    def refresh_known_roles(self, snapshot) -> None:
        """Feed the page's board snapshot — the Role hint's narrowing reads it
        (never the board). A live Board's own .sheet is all-None, so a snapshot
        with raw handles is re-resolved against the cached sheet map; a
        synthetic snapshot without .fp is used as-is."""
        from ..docks.imprint import snapshot_with_resolved_sheets
        self._snapshot = list(snapshot or [])
        if self._sheet_names and all(hasattr(s, "fp") for s in self._snapshot):
            self._resolved_snapshot = snapshot_with_resolved_sheets(
                self._snapshot, self._sheet_names)
        else:
            self._resolved_snapshot = list(self._snapshot)
        self._reload_form()

    # ── Current entry / roles ─────────────────────────────────────────────

    def _current_entry(self) -> Optional[dict]:
        """Read the CURRENT cell entry from disk (dict or None)."""
        if self._cell_name is None:
            return None
        target = find_dict_entry_file(self._root_path, "cells", self._cell_name)
        if target is None:
            target = self._file_path
        if target is None or not Path(target).exists():
            return None
        try:
            entry = (read_data(Path(target)).get("cells") or {}).get(self._cell_name)
        except (ValidationError, OSError):
            return None
        return entry if isinstance(entry, dict) else None

    def _cell_roles(self) -> list:
        """Roles of the CURRENT cell entry, in the cell's own order — ONE owner
        instance_candidates.cell_role_order."""
        entry = self._current_entry()
        if entry is None:
            return []
        return cell_role_order(entry.get("components", []))

    def _board_snapshot(self):
        """The page's own snapshot, sheet-resolved when it carries handles —
        the Role hint's source (fill, never restrict)."""
        return self._resolved_snapshot or self._snapshot

    def _adapter(self):
        """The live adapter. SIGNED (Denis, 09.10.2026): the anchor's board
        access belongs on the UI thread — the ONE explicit selection read of
        "Read from selection", and handing the adapter to the overlay workers
        (which do all the real IPC)."""
        with ui_thread_board_read(
                reason="anchor: one explicit selection read (Read from selection) "
                       "and hand the adapter to the overlay workers"):
            board = getattr(self._connection, "board", None)
        if board is None:
            return None
        return getattr(board, "adapter", None)

    def _adapter_required(self):
        adapter = self._adapter()
        if adapter is None:
            show_message(_("No live board connection — the overlay needs "
                           "KiCad."), _WARN_STYLE, logger)
            return None
        return adapter

    # ── Overlay keys (the map itself is owned by gui/overlay_markers) ─────

    def _marker_key(self) -> Optional[str]:
        if self._root_path is None or self._cell_name is None:
            return None
        return overlay_markers.cell_anchor_key(
            self._root_path, self._cell_name, "marker")

    def _bbox_key(self) -> Optional[str]:
        if self._root_path is None or self._cell_name is None:
            return None
        return overlay_markers.cell_anchor_key(
            self._root_path, self._cell_name, "bbox")

    def _overlay_scope(self) -> Optional[str]:
        if self._root_path is None or self._cell_name is None:
            return None
        return overlay_markers.cell_anchor_scope(
            self._root_path, self._cell_name)

    # ── The form ──────────────────────────────────────────────────────────

    def _fill_role_choices(self, roles: list, cluster: str) -> None:
        narrowed = roles_for_cluster(self._board_snapshot(), roles, cluster)
        current = self._role_combo.currentText()
        set_combo_items(self._role_combo, narrowed)
        if current and current in narrowed:
            self._role_combo.setCurrentText(current)

    def _reload_form(self) -> None:
        """Refill from the current cell entry and re-apply the applies line and
        the marker buttons. Called on context set, on a snapshot tick and after
        a write."""
        self._refresh_overlay_layer_note()
        self._applies_label.setText(
            _("Cell “{cell}” — changes apply to every entity of this cell "
              "({n}).").format(cell=self._cell_name or "—",
                               n=self._entity_count)
            if self._cell_name else "")
        entry = self._current_entry()
        if entry is None:
            self._role_combo.clear()
            self._pad_edit.clear()
            for b in (self._set_anchor_button, self._clear_anchor_button,
                      self._read_selection_button,
                      self._place_marker_button, self._read_marker_button,
                      self._remove_marker_button, self._show_bbox_button,
                      self._hide_bbox_button, self._remove_overlay_button):
                b.setEnabled(False)
            return

        roles = sorted({c.get("role") for c in entry.get("components", [])
                        if c.get("role")})
        self._fill_role_choices(roles, self._cluster)
        role = entry.get("anchor_role")
        if role:
            self._role_combo.setCurrentText(str(role))
        self._pad_edit.setText(str(entry.get("anchor_pad") or ""))

        self._read_selection_button.setEnabled(True)
        self._set_anchor_button.setEnabled(True)
        self._clear_anchor_button.setEnabled(True)
        self._place_marker_button.setEnabled(True)
        self._show_bbox_button.setEnabled(True)
        marker_key, bbox_key = self._marker_key(), self._bbox_key()
        has_marker = bool(marker_key and self._overlay.has_key(marker_key))
        has_bbox = bool(bbox_key and self._overlay.has_key(bbox_key))
        self._read_marker_button.setEnabled(has_marker)
        self._remove_marker_button.setEnabled(has_marker)
        self._hide_bbox_button.setEnabled(has_bbox)
        self._remove_overlay_button.setEnabled(has_marker or has_bbox)
        self._refresh_overlay_layer_note()

    # ── Role anchor handlers ──────────────────────────────────────────────

    def _on_read_from_selection(self) -> None:
        adapter = self._adapter()
        if adapter is None:
            show_message(_("No live board — “Read from selection” needs KiCad. "
                           "Pick Role/Pad by hand instead."),
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
        # THE MEANING CHANGE (step 4 of plan_2026_10_09_entity_page): the anchor
        # belongs to ONE entity's instance. A selection of ANOTHER cluster is
        # refused with a line — never silently read as this entity's anchor.
        if self._cluster and read["cluster"] \
                and not cluster_prefix_match(read["cluster"], self._cluster):
            show_message(
                _("Read from selection: the selected component sits in cluster "
                  "{other!r}, but this entity is {cluster!r} — select a "
                  "component of THIS instance.").format(
                      other=read["cluster"], cluster=self._cluster),
                _WARN_STYLE, logger)
            return
        roles = sorted({c.get("role") for c in entry.get("components", [])
                        if c.get("role")})
        self._fill_role_choices(roles, self._cluster)
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
        None with a message. Purely UI-thread validation; the frame derivation
        happens in the WORKER. The address is the ENTITY's: the Cluster is not a
        prerequisite when the entity carries its own refs (the refs path of
        _live_cluster_frame never searches the cluster)."""
        if self._root_path is None or self._cell_name is None:
            show_message(_("Marker needs a project root — open a project "
                           "first."), _WARN_STYLE, logger)
            return None
        if not self._cluster and not self._refs:
            show_message(_("Marker: this entity has no Cluster — set one on the "
                           "entity record first."), _WARN_STYLE, logger)
            return None
        try:
            cfg, ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            show_message(_("Marker: failed to load the project config: {error}")
                         .format(error=e), _ERROR_STYLE, logger)
            return None
        return cfg, ctx.sheet_names, self._cluster, self._sheet

    def _dispatch(self, fn, on_success, on_error, *extra_args):
        """Resolve the entity's address + live adapter and dispatch one overlay
        worker on the worker thread via start_long_op. The entity's refs travel
        as the LAST positional argument (`role_to_ref`), pinning a spoke cell."""
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
            adapter, cell, cluster, sheet, sheet_names, *extra_args,
            self._refs or None)

    def _dispatch_draw(self, worker_fn, key, ok, on_error):
        """Dispatch a DRAW overlay op for `key` — the worker receives the key
        and the CURRENT configured overlay layer name."""
        self._dispatch(worker_fn, ok, on_error, key,
                       board_overlay.overlay_layer_name())

    def _on_place_marker(self) -> None:
        key = self._marker_key()
        if key is None:
            return

        def ok(uuid: Optional[str]) -> None:
            if uuid is None:
                show_message(_("Place marker: the cell has no geometry on the "
                               "board."), _WARN_STYLE, logger)
                return
            show_message(_("Marker placed — drag it in KiCad, then press "
                           "“Read position”."), _SUCCESS_STYLE, logger)
            self._reload_form()

        def err(message: str) -> None:
            show_message(_("Place marker failed: {message}").format(message=message),
                         _ERROR_STYLE, logger)

        self._dispatch_draw(_ensure_marker_worker, key, ok, err)

    def _on_show_bbox(self) -> None:
        key = self._bbox_key()
        if key is None:
            return

        def ok(uuid: Optional[str]) -> None:
            if uuid is None:
                show_message(_("Show bbox: the cell has no geometry."),
                             _WARN_STYLE, logger)
                return
            show_message(_("Bbox drawn."), _SUCCESS_STYLE, logger)
            self._reload_form()

        def err(message: str) -> None:
            show_message(_("Show bbox failed: {message}").format(message=message),
                         _ERROR_STYLE, logger)

        self._dispatch_draw(_ensure_bbox_worker, key, ok, err)

    def _on_read_marker(self) -> None:
        key = self._marker_key()
        if key is None or not self._overlay.has_key(key):
            show_message(_("No marker to read — place one first."),
                         _WARN_STYLE, logger)
            return

        def ok(xy: Optional[tuple]) -> None:
            if xy is None:
                show_message(_("Marker not found on the board (deleted or "
                               "swept?) — place a new one."), _WARN_STYLE, logger)
                self._overlay.forget_key(key)
                self._reload_form()
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

        self._dispatch(_read_marker_worker, ok, err, key)

    def _remove_uuids(self, uuids: list) -> None:
        """Delete the given overlay shape uuids on the worker thread — the IPC
        half of a removal whose KEY the owner already forgot synchronously."""
        doomed = [u for u in (uuids or []) if u]
        if not doomed:
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
            adapter, doomed)

    def _remove_marker_only(self) -> None:
        """Forget the marker key (state cleared FIRST, so the buttons update
        immediately) + remove the shape on the worker thread (after “Read
        position” stored the point)."""
        key = self._marker_key()
        uuid = self._overlay.forget_key(key) if key else None
        self._remove_uuids([uuid])
        self._reload_form()

    def _on_remove_marker(self) -> None:
        self._remove_marker_only()

    def _on_hide_bbox(self) -> None:
        key = self._bbox_key()
        uuid = self._overlay.forget_key(key) if key else None
        self._remove_uuids([uuid])
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
        """Forget THIS cell's drawn overlay keys (marker + bbox) and remove
        their shapes from the live board (Phase D D.2 — the by-uuid fast path
        scoped to this cell). State is cleared FIRST so a missing/dead board
        never leaves stale tracking behind."""
        if self._cell_name is None:
            return
        if getattr(self._connection, "long_op_active", False):
            return
        scope = self._overlay_scope()
        uuids = self._overlay.forget_scope(scope) if scope else []
        adapter = self._adapter()
        if not uuids or adapter is None:
            return
        self._active_op = start_long_op(
            self._connection, [], board_overlay.remove_overlay,
            lambda _ok: None, lambda _message: None, adapter, uuids)

    def _on_remove_overlay(self) -> None:
        """'Remove overlay' button — removes BOTH the marker and the bbox of
        this cell from the board."""
        self.cleanup()
        self._reload_form()
