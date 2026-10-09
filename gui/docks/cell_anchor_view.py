# gui/docks/cell_anchor_view.py
"""Cell-anchor editor — a Config right-QView page + context-menu action.

Since step 4 of plan_2026_10_09_entity_page this page keeps ONLY the "Source"
tab: the placement identity (Name/Comment, task V), the working (Cluster,
Sheet) context, and the identification of ONE instance of the cell from the
board selection (2026-09-17, stage 1 of the spoke work).

The TWO ANCHOR TABS (Role anchor + Marker anchor) MOVED to the ENTITY page
(gui/entity/anchor_tab.AnchorTabWidget): the anchor writes the CELL TEMPLATE
(cells:), so the address it resolves against must be visible where it is edited,
and on the entity page the address is the entity RECORD's own (cell, cluster,
sheet, refs) — no working-context comboboxes. Step 5 deletes this page's last
remaining piece (the Source tab) with the instance dropdown.

Entry read/write uses the Config tree's path (collect_graph_files + read_data /
merge_write) — the same pair CellDock/Placer use.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config import load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _
from kicadstamp.placement.services.role_narrowing import narrow_candidates_by_sheet
from kicadstamp.selection_narrowing import cell_clusters as cell_clusters_for
from kicadstamp.sheet_names import resolve_sheet_path_names

from .. import settings  # noqa: F401 — tests reach view_mod.settings.state
from ..cell_edit_context import (
    remember_cell_edit_context,
    remember_cell_instance,
    remembered_cell_edit_context,
)
from ..cell_identification import (
    KIND_SPOKE,
    SelectionRecord,
    identify_cell_instance,
)
from ..worker import socket_busy, start_long_op
from ._cell_identity import CellIdentityWidget
from ._common import (
    ERROR_STYLE as _ERROR_STYLE,
    SUCCESS_STYLE as _SUCCESS_STYLE,
    WARN_STYLE as _WARN_STYLE,
    merge_write,
    read_data,
    set_combo_items,
    show_message,
)
from ..entity_doors import read_only_cell
from .cell_instance_mixin import CellInstanceMixin, parse_refs_field
from .cell_read_only import ReadOnlyGate
from .rename import collect_graph_files
from .imprint import snapshot_with_resolved_sheets

logger = logging.getLogger(__name__)


def _resolved_sheet_chain(fp, sheet_names) -> tuple:
    """The footprint's sheet chain with readable names — the project's ONE
    resolution (kicadstamp.sheet_names.resolve_sheet_path_names). A resolution
    failure is not fatal here: an empty chain narrows nothing, exactly like a
    stale Sheet everywhere else in the (Sheet, Cluster) cascade."""
    if not sheet_names:
        return tuple(getattr(fp, "sheet_path_uuids", None) or ())
    try:
        return tuple(resolve_sheet_path_names(fp, sheet_names) or ())
    except Exception:  # noqa: BLE001 — a best-effort chain, never fatal
        return ()


def read_identification_worker(payload: dict) -> dict:
    """start_long_op worker: read the LIVE board selection plus the members of
    the clusters it names — the raw material identify_cell_instance decides on
    (Т1.7 of plan_2026_09_17_spoke_s1_identify_by_selection.md).

    Everything that touches the board happens HERE (П3.1). The board is REFRESHED
    first (K.1 discipline: the poll tick is a no-op while connected, so a member
    list read from an old cache could miss a component added in KiCad — and the
    role multiplicity that decides "is this a spoke?" would then be wrong). The
    selection is read and, in the SAME pass, every footprint of the clusters the
    selection names: those members give both the multiplicity and the refs of the
    roles the user did not click. Returns plain records; the pure identification
    runs on the UI thread."""
    adapter = payload["adapter"]
    sheet_names = dict(payload.get("sheet_names") or {})
    adapter.refresh_board()
    footprints = [item for item in (adapter.get_selected_items() or ())
                  if isinstance(item, Footprint)]
    if not footprints:
        raise ValidationError(format_fatal_error(
            _("nothing is selected on the board — select the components of ONE "
              "instance of cell {cell!r}")
            .format(cell=payload.get("cell_name") or "?"),
            [_("a spoke cell is identified by exactly ONE pair; an ordinary "
               "cluster can be identified from any part of it")]))

    def record(fp):
        return SelectionRecord(
            ref=fp.ref,
            role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
            cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
            sheet=_resolved_sheet_chain(fp, sheet_names))

    selected = [record(fp) for fp in footprints]
    clusters = {r.cluster for r in selected if r.cluster}
    members = [record(fp) for fp in adapter.get_footprints()
               if (adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or "")
               in clusters]
    return {"selected": selected, "members": members,
            "sheet_names": sheet_names}


# ────────────────────────────────────────────────────────────────────────────
# The widget
# ────────────────────────────────────────────────────────────────────────────

class CellAnchorView(CellInstanceMixin, QWidget):
    """The cell page's ONE remaining tab — Source: the placement identity, the
    working (Cluster, Sheet) context and the identification of ONE instance.
    (The Role/Marker anchor tabs live on the ENTITY page — see the module
    docstring.)"""

    saved = pyqtSignal()

    def __init__(self, main_window, connection=None, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._connection = connection
        self._cell_name: Optional[str] = None
        self._file_path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        self._active_op = None
        self._sheet_names: dict = {}
        self._snapshot: list = []
        self._resolved_snapshot: list = []
        self._loading = False
        self.init_cell_instance()
        self._build_ui()
        self._reload_form()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._title = QLabel()
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._read_only_gate = ReadOnlyGate(layout)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # ── Source: identity + working context (task V) ────────────────────
        # The identity block is the SHARED CellIdentityWidget — also plugged into
        # PlacerDock. Its (Cluster, Sheet) pair is the working context the
        # identification resolves against.
        source_page = QWidget()
        source_layout = QVBoxLayout(source_page)
        source_layout.setContentsMargins(4, 4, 4, 4)
        self._identity = CellIdentityWidget(
            sheet_placeholder=_("Sheet (optional)"),
            cluster_placeholder=_("Cluster of the placed cell"))
        self._cluster_combo = self._identity.cluster_edit
        self._sheet_combo = self._identity.sheet_edit
        self._name_edit = self._identity.name_edit
        self._comment_edit = self._identity.comment_edit
        self._identity.note.setText(
            _("(Cluster, Sheet) set the working context for the identification — "
              "they decide which placed instance of this cell is pinned."))
        source_layout.addWidget(self._identity)

        # The "Identified instance" box (the "Entity" dropdown, "Fill from
        # selection" and the refs line) lives in gui/docks/cell_instance_mixin.py
        # together with the rule behind it.
        self.build_instance_box(source_layout)
        source_layout.addStretch(1)
        self._tabs.addTab(source_page, _("Source"))

        self._cluster_combo.currentTextChanged.connect(self._on_cluster_changed)
        # G.2: the Sheet narrows the Cluster list; G.3: both combos persist the
        # working context on a manual pick.
        self._sheet_combo.currentTextChanged.connect(self._on_sheet_changed)
        # Name/Comment write back into the top-level clone_placements record of
        # this cell — only when one exists (see _reload_identity).
        self._name_edit.editingFinished.connect(self._on_identity_edited)
        self._comment_edit.editingFinished.connect(self._on_identity_edited)
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, _index: int) -> None:
        """A tab switch re-renders the form (as before)."""
        self._reload_form()

    # ── Loading / context ─────────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """The project root changed (root_changed broadcast) — refresh the Sheet
        choices (from the loaded config). Re-setting the SAME path (a
        graph-shape refresh) keeps the working context."""
        self._root_path = path
        self._sheet_names = {}
        if path is not None:
            try:
                _cfg, ctx = load_config(str(path))
            except (ValidationError, OSError):
                ctx = None
            if ctx is not None:
                self._sheet_names = dict(ctx.sheet_names or {})
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
        self._refill_cluster_choices()
        if self._cell_name is not None:
            self._reload_form()

    def refresh_known_roles(self, snapshot) -> None:
        """Feed the live-board snapshot into the working-context Cluster combo
        (wired into DockHub.push_snapshot). The snapshot is STORED and
        re-resolved against the cached config sheet map — a live Board's own
        .sheet is all-None. Fill, never restrict."""
        self._snapshot = list(snapshot or [])
        if all(hasattr(s, "fp") for s in self._snapshot):
            self._resolved_snapshot = snapshot_with_resolved_sheets(
                self._snapshot, self._sheet_names)
        else:
            self._resolved_snapshot = list(self._snapshot)
        self._refill_cluster_choices()

    def _refill_cluster_choices(self) -> None:
        """Refill the Cluster combo from the sheet-resolved snapshot, narrowed by
        the currently selected Sheet (G.2): only the clusters whose footprints
        actually sit on that sheet survive."""
        snapshot = self._resolved_snapshot
        if all(hasattr(s, "fp") for s in snapshot):
            kept = {id(fp) for fp in narrow_candidates_by_sheet(
                [s.fp for s in snapshot], self._sheet_combo.currentText().strip(),
                self._sheet_names)}
            clusters = sorted({s.cluster for s in snapshot
                               if s.cluster and id(s.fp) in kept})
        else:
            clusters = sorted({s.cluster for s in snapshot if s.cluster})
        self._loading = True
        try:
            set_combo_items(self._cluster_combo, clusters)
        finally:
            self._loading = False

    def _on_sheet_changed(self) -> None:
        """The working Sheet changed — re-narrow the Cluster list (G.2), persist
        the working context (G.3) and re-resolve the placement identity."""
        if self._loading:
            return
        self._refill_cluster_choices()
        self._remember_working_context()
        self.reload_refs_field()
        self._reload_identity()

    def _remember_working_context(self) -> None:
        """Persist the working (Cluster, Sheet) for the loaded cell (G.3) —
        manual combo picks must survive too. Nothing is written when the pair is
        ALREADY the remembered one: a plain context write ERASES the identified
        refs, so "do not write what is already there" is what keeps reopening a
        cell from silently forgetting its pair."""
        if self._loading or self.instance_fields_are_being_written() \
                or self._cell_name is None:
            return
        cluster = self._cluster_combo.currentText().strip()
        if not cluster:
            return
        sheet = self._sheet_combo.currentText().strip() or None
        if remembered_cell_edit_context(self._root_path, self._cell_name) == \
                (cluster, sheet):
            return
        remember_cell_edit_context(
            self._root_path, self._cell_name, cluster, sheet)

    def load_entry(self, name: str, file_path, read_only: bool = False,
                   opened_from: Optional[str] = None) -> None:
        """Open the requested cell — (name, owning file), the same shape as the
        Config tree's cell_edit_requested. Reads the entry live and fills the
        form (safe to re-open on a changed file). The remembered (Cluster, Sheet)
        context is applied BEFORE the form renders."""
        self._cell_name = name
        self._file_path = Path(file_path) if file_path is not None else None
        self._open_read_only = bool(read_only)
        self.open_cell(opened_from)
        self._prefill_cell_context()
        # The board buttons go to the gate explicitly, BEFORE the form renders —
        # `_reload_form` re-enables them by its own rules and its tail re-applies
        # this rule (`reapply`), ONE owner per refill.
        self._read_only_gate.apply(self._tabs, read_only, (
            self._fill_selection_button,))
        self._reload_form()

    # ── Phase E: remembered (Cluster, Sheet) context ──────────────────────

    def _prefill_cell_context(self) -> None:
        """Seed the working Sheet/Cluster combos from the remembered (Cluster,
        Sheet) this cell was last created/edited in. THE JUDGE IS THE PAGE'S OWN
        SNAPSHOT, never the board. An empty snapshot means nothing has been read
        yet — the remembered pair is applied as a HINT."""
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
        if self._cluster_known_gone(cluster):
            return
        if sheet and self._sheet_known_gone(sheet):
            sheet = None
        self._loading = True
        try:
            self._cluster_combo.setCurrentText(cluster)
            if sheet:
                self._sheet_combo.setCurrentText(sheet)
        finally:
            self._loading = False

    def _cluster_known_gone(self, cluster: str) -> bool:
        """True when this page's own snapshot PROVES `cluster` is not on the
        board. An EMPTY snapshot proves nothing (nothing read yet), so it returns
        False and the caller keeps the remembered cluster as a hint."""
        snapshot = self._snapshot or self._resolved_snapshot
        if not snapshot:
            return False
        return not any(getattr(s, "cluster", None)
                       and cluster_prefix_match(s.cluster, cluster)
                       for s in snapshot)

    def _sheet_known_gone(self, sheet: str) -> bool:
        """True when the Sheet combo's list is non-empty and does NOT carry
        `sheet`. An EMPTY list means the sheet map is not known yet, which says
        nothing about the remembered sheet and must not throw it away."""
        known = {self._sheet_combo.itemText(i)
                 for i in range(self._sheet_combo.count())}
        return bool(known) and sheet not in known

    # Part 2 (Денис, 08.10) — WHICH cell and WHICH instance this page works on —
    # lives in gui/docks/cell_instance_mixin.py: the address list, the labels, the
    # last pick, the working-instance store (gui/cell_entity_choice.py) and the
    # dropdown with the rule behind it (gui/docks/cell_entity_picker.py).

    # ── Task V: the Source tab's placement identity (Name/Comment) ─────────

    def _identity_raw_matches(self) -> list:
        """Every RAW top-level clone_placements dict of this cell matching the
        working (Cluster, Sheet) as (path, item) pairs. RAW and OFFLINE on
        purpose: the write path is raw, and a tree-materialized placement has NO
        record to write into — the case that must stay read-only."""
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
        """Fill Name/Comment from the ONE top-level record of this cell, and mark
        them editable only when such a record exists. Several matches stay a
        fatal with the enumeration (same rule as _single_candidate)."""
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
        record, IN PLACE. Never creates a record: with no top-level placement in
        context the two fields are read-only and this is a no-op."""
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
            merge_write(path, {"clone_placements": items})
        except (ValidationError, OSError) as e:
            show_message(_("Identity save failed: {error}").format(error=e),
                         _ERROR_STYLE, logger)
            return
        show_message(_("Cell {name!r}: placement identity stored.")
                     .format(name=self._cell_name), _SUCCESS_STYLE, logger)
        self.saved.emit()

    def _reload_form(self) -> None:
        """Refill the Source form from the current cell entry — called on open,
        on tab switch and after a save."""
        self._reload_identity()
        if self._cell_name is None:
            self._title.setText(_("Pick a Cell in the Config tree."))
            self._fill_selection_button.setEnabled(False)
            self.clear_instance()
            return

        self._title.setText(_("Cell {name!r}").format(name=self._cell_name))
        entry = self._current_entry()
        if entry is None:
            show_message(_("Cell {name!r} not found in the project config")
                         .format(name=self._cell_name), _ERROR_STYLE, logger)
            self._fill_selection_button.setEnabled(False)
            self.clear_instance()
            return

        self._fill_selection_button.setEnabled(True)
        # The remembered refs are shown here as well as after an identification:
        # reopening the page must bring them back (acceptance item 7).
        self.reload_refs_field()
        # LAST, so it has the final word on the instance (the mixin re-applies the
        # working row after every setEnabled above).
        self.reload_instance()
        # LAST OF ALL: the read-only answer is ASKED AFRESH (creating an entity on
        # an orphan lifts it right here), and every button the rules above just
        # re-enabled goes back off through that ONE gate.
        self._read_only_gate.reapply(read_only_cell(self, self._open_read_only))

    def _board_snapshot(self):
        """The WHOLE-BOARD snapshot (explore.Selected items, with `role`/`cluster`
        as FIELD VALUES) — never the adapter. Deliberately NOT `self._snapshot`:
        the identification's presence checks use the page's OWN snapshot, the copy
        the page knows it was fed."""
        return getattr(self._connection, "snapshot", None) or ()

    def _on_cluster_changed(self) -> None:
        """A manual Cluster pick persists the working context (G.3) and FORGETS
        the identified refs (Р8) — the field must show that. The placement
        record the identity fields edit is scoped by the working (Cluster, Sheet)
        too, so it is re-resolved."""
        self._remember_working_context()
        self.reload_refs_field()
        self._reload_identity()

    # ── Source tab: identifying the instance (2026-09-17, stage 1) ────────

    def _identification_context(self):
        """(cfg, sheet_names) for an identification, or None with a message.
        Deliberately NOT requiring the working Cluster/Sheet: the identification
        DISCOVERS them from the selection. Purely UI-thread validation."""
        if self._root_path is None or self._cell_name is None:
            show_message(_("Identification needs a project root — open a project "
                           "first."), _WARN_STYLE, logger)
            return None
        try:
            cfg, ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            show_message(_("Identification: failed to load the project config: "
                           "{error}").format(error=e), _ERROR_STYLE, logger)
            return None
        if cfg.cells.get(self._cell_name) is None:
            show_message(_("cell {cell!r} not found in config")
                         .format(cell=self._cell_name), _ERROR_STYLE, logger)
            return None
        return cfg, dict(ctx.sheet_names or {})

    def _snapshot_records(self) -> list:
        """The whole board snapshot as SelectionRecords — the SHEET-RESOLVED copy
        when the snapshot carries raw handles."""
        snapshot = self._resolved_snapshot or self._board_snapshot()
        return [SelectionRecord(
            ref=str(getattr(s, "ref", "")), role=getattr(s, "role", None),
            cluster=getattr(s, "cluster", None),
            sheet=tuple(getattr(s, "sheet", None) or ())) for s in snapshot]

    def _on_fill_from_selection(self) -> None:
        """Button action: identify ONE instance of this cell from the board
        selection (П3.1 — the selection is read in the WORKER, the pure
        identification runs here, on the UI thread, on the data it returns)."""
        ctx = self._identification_context()
        if ctx is None:
            return
        cfg, sheet_names = ctx
        adapter = self._adapter_required()
        if adapter is None:
            return
        if socket_busy(self._connection):
            show_message(
                _("the board is busy (another operation is reading it) — try "
                  "again in a moment"), _WARN_STYLE, logger)
            return
        if self._cell_name is None or self._root_path is None:
            return
        payload = {"adapter": adapter, "sheet_names": sheet_names,
                   "cell_name": self._cell_name}
        self._active_op = start_long_op(
            self._connection, (self._fill_selection_button,),
            read_identification_worker,
            lambda result: self._finish_fill_from_selection(result, cfg),
            self._on_fill_failed, payload)

    def _adapter_required(self):
        """The live adapter, or None + a message. Read through the connection's
        board property — the identification always dispatches to the worker, so
        this handle is taken only to hand it over."""
        from ..connection import ui_thread_board_read
        with ui_thread_board_read(
                reason="cell identification: take the adapter to hand it to the "
                       "worker that reads the selection"):
            board = getattr(self._connection, "board", None)
        adapter = getattr(board, "adapter", None) if board is not None else None
        if adapter is None:
            show_message(_("No live board connection — connect to KiCad."),
                         _WARN_STYLE, logger)
            return None
        return adapter

    def _finish_fill_from_selection(self, result, cfg) -> None:
        """UI thread (worker finished): run the PURE identification on the data
        the worker read, then store it and show it."""
        self._active_op = None
        cell = cfg.cells.get(self._cell_name)
        try:
            ident = identify_cell_instance(
                cell, result.get("selected") or (), result.get("members") or (),
                entities=getattr(cfg, "entities", ()) or (),
                sheet_names=result.get("sheet_names") or {},
                cell_clusters=cell_clusters_for(cfg, self._cell_name))
        except ValidationError as e:
            show_message(str(e), _ERROR_STYLE, logger)
            return
        self._apply_identification(ident, cell, result.get("selected") or ())

    def _on_fill_failed(self, message: str) -> None:
        self._active_op = None
        show_message(_("Fill from selection failed: {message}")
                     .format(message=message), _ERROR_STYLE, logger)

    def _on_refs_edited(self) -> None:
        """The hand-typed Refs field: resolve the refdes against the snapshot the
        GUI already has (no board read on the UI thread) and pass them through the
        SAME identification rules as the button, so a typo is refused in the Log
        and the refs are NOT remembered."""
        if self.refs_field_is_pinned():
            return
        if self._loading or self._cell_name is None or self._root_path is None:
            return
        ctx = self._identification_context()
        if ctx is None:
            return
        cfg, sheet_names = ctx
        cell = cfg.cells.get(self._cell_name)
        text = self._refs_edit.text().strip()
        if not text:
            cluster = self._cluster_combo.currentText().strip()
            if cluster:
                remember_cell_edit_context(
                    self._root_path, self._cell_name, cluster,
                    self._sheet_combo.currentText().strip() or None)
            return
        records = self._snapshot_records()
        if not records:
            show_message(_("no live board read yet — connect to KiCad (or press "
                           "Refresh) before typing refs by hand"),
                         _ERROR_STYLE, logger)
            return
        by_ref = {r.ref: r for r in records}
        missing = [ref for ref in parse_refs_field(text) if ref not in by_ref]
        if missing:
            show_message(
                _("the refs {refs} are not in the last board read — press "
                  "Refresh, or check the refdes").format(
                      refs=", ".join(missing)), _ERROR_STYLE, logger)
            return
        selected = [by_ref[ref] for ref in parse_refs_field(text)]
        try:
            ident = identify_cell_instance(
                cell, selected, records,
                entities=getattr(cfg, "entities", ()) or (),
                sheet_names=sheet_names,
                cell_clusters=cell_clusters_for(cfg, self._cell_name))
        except ValidationError as e:
            show_message(str(e), _ERROR_STYLE, logger)
            return
        self._apply_identification(ident, cell, selected)

    def _apply_identification(self, ident, cell, selected) -> None:
        """Remember the identification and show it on the Source tab — the ONE
        place both the button and the hand-typed Refs field end in."""
        if self._root_path is None or self._cell_name is None:
            return
        cell_roles = {getattr(c, "role", None)
                      for c in (getattr(cell, "components", None) or ())} - {None}
        refs = ", ".join(ident.role_to_ref.get(r, "?")
                         for r in self._cell_role_order()
                         if r in ident.role_to_ref)
        # The combos FIRST, the identification LAST: moving the working context
        # is a genuine change, so the nested handlers may write a ref-less context
        # on the way — the refs of the identification must be the ones left.
        self._loading = True
        try:
            self._cluster_combo.setCurrentText(ident.cluster)
            self._sheet_combo.setCurrentText(ident.sheet or "")
        finally:
            self._loading = False
        remember_cell_instance(self._root_path, self._cell_name, ident)
        if ident.kind == KIND_SPOKE:
            role, occurrences = ident.repeated_roles[0] if ident.repeated_roles \
                else ("?", 0)
            show_message(
                _("identified as a SPOKE: role {role!r} occurs {count} times in "
                  "cluster {cluster!r} — this pair is pinned: {refs}")
                .format(role=role, count=occurrences, cluster=ident.cluster,
                        refs=refs), _SUCCESS_STYLE, logger)
        else:
            selected_refs = {getattr(s, "ref", None) for s in selected}
            picked = [m for m in ident.members if m in selected_refs]
            if ident.members and len(picked) < len(ident.members):
                show_message(
                    _("{selected} of {total} components of cluster {cluster!r} "
                      "selected — the instance was pinned from what was selected")
                    .format(selected=len(picked), total=len(ident.members),
                            cluster=ident.cluster), _WARN_STYLE, logger)
            else:
                show_message(_("identified {cell!r} on cluster {cluster!r}: "
                               "{refs}").format(cell=self._cell_name,
                                                cluster=ident.cluster, refs=refs),
                             _SUCCESS_STYLE, logger)
        absent = sorted(cell_roles - set(ident.role_to_ref))
        if absent:
            show_message(
                _("the cluster has no component for these role(s) of the cell: "
                  "{roles} — they stay unpinned until a pair is tagged")
                .format(roles=", ".join(absent)), _WARN_STYLE, logger)
        # An identification IS a manual instance (п.2) — the dropdown must say so.
        self.select_manual_instance()
        self._reload_form()
