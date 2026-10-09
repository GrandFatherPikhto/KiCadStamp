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

THE DOOR (Denis, 09.10.2026). The anchor's board access is NOT a bare suspect:
`AnchorTabWidget._adapter` is SIGNED — the ONE explicit selection read of the
"Read from selection" button plus handing the adapter to the overlay workers. Its
sign says, in words, why the read belongs on the UI thread
(gui/connection.ui_thread_board_read).

The overlay workers and the pure selection helpers live in
gui/entity/anchor_workers.py (split out in step 5 of the same plan — this module
must stay under 800 lines); a cell's roles in the cell's order are the ONE owner
instance_candidates.cell_role_order.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config import load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

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
from ..docks.rename import find_dict_entry_file
from ..worker import start_long_op
from .anchor_workers import (
    _ensure_bbox_worker,
    _ensure_marker_worker,
    _read_marker_worker,
    read_anchor_source,
    roles_for_cluster,
)

logger = logging.getLogger(__name__)


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
