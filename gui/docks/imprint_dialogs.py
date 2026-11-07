# gui/docks/imprint_dialogs.py
"""The two Imprint dialogues: "Record.../Re-source..." (two source tabs) and the
per-net boundary decision.

Moved VERBATIM out of gui/docks/imprint.py on 07.11 (plan
plan_2026_11_07_dialog_sizes_and_row_shift, part Б): a giant may only shrink
(rule 45), and a dialog is a window with its own size to remember. The pure
sheet/record helpers they call live in gui/docks/imprint_record_scope.py;
`gui/docks/imprint.py` re-exports both classes, so `gui.docks.imprint.<name>`
keeps working unchanged (dock_hub, diagnostics/probe_gui_min_sizes and the tests
all import them from there).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QTabWidget, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ..worker import refresh_snapshot_then_with_retry
from ._common import ERROR_STYLE as _ERROR_STYLE, show_message
from ..ui_utils import remember_dialog_size
from .imprint_record_scope import (_pivot_mm_text, all_sheet_paths,
                                   pivot_centre_frame_from_selection,
                                   refs_on_sheet, sheet_subtree_plan)

logger = logging.getLogger(__name__)


class RecordImprintDialog(QDialog):
    """Record... / Re-source... dialog with TWO source tabs (design §2, plan
    plan_2026_09_06_scheme_list_sheet_capture.md 5a.3 — the same two-tab
    pattern "Instantiate from Cell..." already uses):

      - "By sheet" (DEFAULT tab) — the WHOLE live hierarchy as ONE tree
        (QTreeWidget, Commit E): every real sheet is a CHECKABLE node (top
        sheets like Channel_0/1/2 first, nested under their parents down to
        DAC/OpAmp...), container sheets with no footprints of their own
        included. ALL start UNCHECKED — tick a sheet (its whole subtree
        follows via the Commit D tri-state cascade: checking Channel_0 turns on
        DAC/OpAmp, a parent with a mixed subtree shows PartiallyChecked) to
        record it. A sheet is captured (its DIRECT refs included) whenever it
        is NOT unchecked; container paths are stored in the scope too (they add
        no refs today but keep a future Reread aware of the branch).
      - "By selection" (secondary tab) — the pre-existing P2 behavior: the
        CURRENT board selection, unchanged, for irregular cases.

    NO anchor is picked at Record time (design_2026_09_07_scheme_list_pivot.md):
    the record's frame is the CENTRE of the captured region's bbox and its
    pivot defaults to that centre — there is no anchor component to choose.
    The shared name_edit sits OUTSIDE the tabs. This dialog only reports what
    was picked; the caller (DockHub) derives the actual capture refs via
    record_refs_for().

    Re-source mode (``fixed_name``, plan_2026_09_06_scheme_list_sheet_capture.md
    5b.2): the SAME dialog is reused to RE-SOURCE an existing record — the name
    is pinned (read-only) to the record being replaced, the title/OK label say
    "Re-source" and an explicit in-dialog warning explains the "points at, does
    not copy" consequence (the record's refs/geometry are replaced; entities
    placed from it pick the new geometry on their next Apply/Redraw). Both
    source tabs stay available — re-sourcing can come from either mode."""

    def __init__(self, snapshot: list, selection_refs: List[str], parent=None,
                 fixed_name: Optional[str] = None, *,
                 adapter=None, selected_footprints=None, pivot_initial=None,
                 selection_provider=None, snapshot_provider=None,
                 connection=None):
        super().__init__(parent)
        remember_dialog_size(self, 900, 640)
        self._fixed_name = fixed_name
        # Pivot/Anchor tab context (Commit F): the live adapter + current board
        # selection feed "Take from selection"; pivot_initial prefills the tab
        # (Re-source = the stored record's pivot, Record = None -> (0,0) centre).
        # `adapter` is kept ONLY as a "live board connected" gate (button enable
        # + the Connect-first warning) — position data comes from the snapshot,
        # never from a direct adapter.get_footprints() call (Commit H).
        self._adapter = adapter
        self._selected_footprints = list(selected_footprints or [])
        # Commit G: the modal dialog outlives the selection it was opened with,
        # so "Take from selection" must read the CURRENT board selection at
        # click time, not the open-time snapshot. selection_provider is a
        # callable returning the live selected footprints (DockHub's polled
        # copy — kept fresh by the main window's selection timer even under the
        # dialog's nested event loop). None -> fall back to the static copy.
        self._selection_provider = selection_provider
        # Commit H: the recorded refs' POSITIONS must be read the same way —
        # from the full-board footprint SNAPSHOT, and LIVE at click time (the
        # open-time `snapshot` argument is stale once the board has moved under
        # the modal dialog). snapshot_provider returns the current snapshot
        # (DockHub's connection.snapshot, refreshed by the poll timer); None ->
        # fall back to the constructor snapshot (tests/fallback).
        self._snapshot_provider = snapshot_provider
        # R.2.1 (2026-09-11, plan_2026_09_11_stale_snapshot_positions.md): the
        # snapshot_provider above can only ever return a snapshot built BEFORE
        # this modal dialog opened — the main window's own Refresh button is
        # blocked while exec() runs, and the automatic poll tick is a no-op once
        # connected — so its positions are frozen at connect/manual-refresh
        # time. "Take from selection" therefore REBUILDS the snapshot first, on
        # the worker thread, before reading any position out of it. None (or a
        # connection without a live board) keeps the cached snapshot: the
        # documented provider fallback, same as _live_snapshot.
        self._connection = connection
        self._pivot_op: Optional[Any] = None
        self._pivot_initial = (tuple(pivot_initial) if pivot_initial is not None
                               else (0.0, 0.0))
        if fixed_name:
            self.setWindowTitle(
                _("Re-source Imprint {name!r}").format(name=fixed_name))
        else:
            self.setWindowTitle(_("Record Imprint"))
        self._snapshot = list(snapshot or [])
        # FULL live hierarchy — every real sheet incl. containers without own
        # footprints (all_sheet_paths adds the prefixes), so the "By sheet"
        # tree reflects the schematic instead of only footprint-bearing leaves
        # (Commit E, plan_2026_09_07_scheme_list_by_sheet_full_tree.md).
        self._sheet_paths = all_sheet_paths(self._snapshot)
        self._selection_refs = list(selection_refs or [])
        # Reentrancy guard for the tri-state cascade (Commit D): our own
        # setCheckState calls re-enter itemChanged, so they are no-ops while a
        # cascade is already running.
        self._cascading = False
        # The SOURCE tab (0="By sheet", 1="By selection"), tracked separately
        # from the widget's currentIndex(): the Pivot/Anchor tab (index 2) is
        # NOT a source — visiting it must not silently flip is_by_sheet()'s
        # answer (plan_2026_09_08_scheme_list_pivot_tab_source_tracking_fix.md).
        # Default "By sheet" (0) matches the tab the dialog opens on.
        self._source_tab_index: int = 0

        layout = QVBoxLayout(self)
        name_form = QFormLayout()
        if fixed_name:
            # Re-source: the name is pinned — this is not a new record, it is
            # the same record being re-pointed at a different source.
            self.name_edit = QLineEdit(fixed_name)
            self.name_edit.setReadOnly(True)
            name_form.addRow(_("Name:"), self.name_edit)
            layout.addLayout(name_form)
            warn = QLabel(
                _("This replaces the recorded refs/geometry of {name!r} — "
                  "entities placed from it will pick up the new geometry on "
                  "their next Apply/Redraw. The sheet/selection it currently "
                  "comes from will NOT update automatically — Place onto it "
                  "too if it should follow.").format(name=fixed_name))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #a60;")
            layout.addWidget(warn)
        else:
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText(
                _("name (used by --only and Entity.imprint, must be unique)"))
            name_form.addRow(_("Name:"), self.name_edit)
            layout.addLayout(name_form)

        # Two source tabs — "By sheet" is added/selected FIRST (the default).
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1 — "By sheet": the WHOLE sheet hierarchy as one tree (Commit E).
        tab1 = QWidget()
        tab1_form = QFormLayout(tab1)
        # Every real sheet of the live hierarchy is a checkable node (containers
        # without own footprints included). ALL start UNCHECKED — you tick a
        # sheet to record it; its whole subtree follows via the Commit D
        # tri-state cascade. Captured = DIRECT refs of every sheet that is not
        # unchecked; container paths are stored in the scope too.
        self.sheet_tree = QTreeWidget()
        self.sheet_tree.setHeaderHidden(True)
        tab1_form.addRow(_("Sheets (check what to record):"), self.sheet_tree)
        # Optional "Save as preset" (plan_2026_09_06_scheme_list_named_presets.md
        # §6): the CURRENT checked tree becomes a NAMED preset saved in the
        # record. Fully optional — empty text = nothing saved, zero effect on
        # OK-gating or the 5a/5b result_data contract.
        self.save_preset_edit = QLineEdit()
        self.save_preset_edit.setPlaceholderText(
            _("optional — save this checklist as a named preset (same name overwrites it)"))
        tab1_form.addRow(_("Save as preset:"), self.save_preset_edit)
        # Tri-state cascade (Commit D): a sheet click toggles its whole subtree
        # and ancestors re-sync; _sync_ok_state re-gates OK on every change.
        self.sheet_tree.itemChanged.connect(self._on_sheet_item_changed)
        self.sheet_tree.itemChanged.connect(self._sync_ok_state)
        self.tabs.addTab(tab1, _("By sheet"))

        # Tab 2 — "By selection": the current board selection (unchanged).
        # Read-only summary (no anchor pick — the capture frame is the region
        # centre, pivot defaults to the centre).
        tab2 = QWidget()
        tab2_form = QFormLayout(tab2)
        self.selection_refs_label = QLabel(self._selection_summary_text())
        self.selection_refs_label.setWordWrap(True)
        tab2_form.addRow(_("Selection:"), self.selection_refs_label)
        self.tabs.addTab(tab2, _("By selection"))

        # Tab 3 — "Pivot/Anchor": the record's pivot set AT CREATION (Commit F,
        # plan_2026_09_07_scheme_list_commit_f_pivot_tab_in_record.md). x/y are
        # mm in the record's centre-frame, default (0,0) = the region centre;
        # "Centre" writes 0/0, "Take from selection" reads the live board.
        # There is NO Apply here — the dialog OK (Record/Re-source) is the save
        # and stores these fields as the new record's pivot.
        tab3 = QWidget()
        tab3_form = QFormLayout(tab3)
        self.pivot_x_edit = QLineEdit()
        self.pivot_x_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pivot_x_edit.setFixedWidth(110)
        self.pivot_y_edit = QLineEdit()
        self.pivot_y_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pivot_y_edit.setFixedWidth(110)
        self._set_pivot_fields(*self._pivot_initial)
        pivot_xy_row = QHBoxLayout()
        pivot_xy_row.setSpacing(4)
        pivot_xy_row.addWidget(QLabel("x"))
        pivot_xy_row.addWidget(self.pivot_x_edit)
        pivot_xy_row.addWidget(QLabel("y"))
        pivot_xy_row.addWidget(self.pivot_y_edit)
        pivot_xy_row.addStretch(1)
        self.pivot_hint_label = QLabel(
            _("(0, 0) = the region centre — x/y are mm offsets from it."))
        self.pivot_hint_label.setWordWrap(True)
        pivot_editor_lay = QVBoxLayout()
        pivot_editor_lay.setContentsMargins(0, 0, 0, 0)
        pivot_editor_lay.setSpacing(2)
        pivot_editor_lay.addLayout(pivot_xy_row)
        pivot_editor_lay.addWidget(self.pivot_hint_label)
        pivot_btn_row = QHBoxLayout()
        pivot_btn_row.setSpacing(4)
        self.pivot_centre_button = QPushButton(_("Centre"))
        self.pivot_centre_button.clicked.connect(self._on_pivot_centre)
        pivot_btn_row.addWidget(self.pivot_centre_button)
        self.pivot_from_selection_button = QPushButton(_("Take from selection"))
        self.pivot_from_selection_button.clicked.connect(
            self._on_pivot_from_selection)
        self.pivot_from_selection_button.setEnabled(self._adapter is not None)
        pivot_btn_row.addWidget(self.pivot_from_selection_button)
        pivot_btn_row.addStretch(1)
        pivot_editor_lay.addLayout(pivot_btn_row)
        pivot_widget = QWidget()
        pivot_widget.setLayout(pivot_editor_lay)
        tab3_form.addRow(_("Pivot:"), pivot_widget)
        self.tabs.addTab(tab3, _("Pivot / Anchor"))
        self.pivot_x_edit.textChanged.connect(self._sync_ok_state)
        self.pivot_y_edit.textChanged.connect(self._sync_ok_state)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel, self)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if fixed_name:
            # Explicit irreversibility: the OK label says what will happen.
            self._ok_button.setText(_("Re-source"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # OK is gated on a non-empty capturable ref set of the ACTIVE tab.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._rebuild_sheet_tree()  # build the full hierarchy tree
        self._sync_ok_state()

    # ── helpers ─────────────────────────────────────────────────────────

    def _selection_summary_text(self) -> str:
        """Read-only summary of the "By selection" tab: the current board
        selection refs, or a note when there is none."""
        if self._selection_refs:
            return _("current board selection ({n}): {refs}").format(
                n=len(self._selection_refs), refs=", ".join(self._selection_refs))
        return _("nothing is selected on the board")

    # ── Pivot / Anchor tab (Commit F) ─────────────────────────────────────

    def _set_pivot_fields(self, x: float, y: float) -> None:
        """Write the x/y edits (mm, centre-frame)."""
        self.pivot_x_edit.setText(_pivot_mm_text(x))
        self.pivot_y_edit.setText(_pivot_mm_text(y))

    def pivot_value(self) -> tuple[float, float]:
        """(x, y) mm of the Pivot/Anchor tab in the record's centre-frame — the
        pivot the OK'd record stores (Commit F). Invalid input -> ValidationError
        (pattern: never write a malformed pivot)."""
        try:
            x = float(self.pivot_x_edit.text().strip())
            y = float(self.pivot_y_edit.text().strip())
        except ValueError:
            raise ValidationError(_("Pivot x/y must be numbers (mm).")) from None
        return (x, y)

    def _pivot_fields_ok(self) -> bool:
        """True when the Pivot/Anchor tab holds valid numbers (used to gate OK
        on top of a non-empty capturable ref set)."""
        try:
            self.pivot_value()
        except ValidationError:
            return False
        return True

    def _live_selection(self) -> list:
        """The selected footprints "Take from selection" should use: the LIVE
        board selection read at click time (selection_provider — Commit G), or
        the open-time snapshot when no provider was given (tests/fallback)."""
        if self._selection_provider is not None:
            return list(self._selection_provider())
        return list(self._selected_footprints)

    def _live_snapshot(self) -> list:
        """The full-board footprint snapshot "Take from selection" reads the
        recorded refs' POSITIONS from: the LIVE snapshot at click time
        (snapshot_provider — Commit H), or the open-time snapshot when no
        provider was given (tests/fallback). Reading positions from this cache
        (not the adapter) keeps the click off the shared kipy REQ socket — see
        live_record_centre_mm's docstring."""
        if self._snapshot_provider is not None:
            return list(self._snapshot_provider())
        return list(self._snapshot)

    def _on_pivot_centre(self) -> None:
        """'Centre' — write the centre default 0/0 into the x/y fields."""
        self._set_pivot_fields(0.0, 0.0)

    def _on_pivot_from_selection(self) -> None:
        """'Take from selection' — read the CURRENT board selection's centre and
        write x/y as the pivot in the centre-frame of the refs we would record
        now (selected centre minus the live centre of those refs' footprints,
        Commit B2 helpers). Needs a live board; the selection is read LIVE at
        click time (Commit G), and the recorded refs' POSITIONS are read from a
        freshly rebuilt full-board snapshot (R.2.1,
        plan_2026_09_11_stale_snapshot_positions.md): the rebuild runs on the
        worker thread first, so no adapter call happens on this GUI thread
        (Commit H — see live_record_centre_mm). Without a refreshable
        connection the cached snapshot is used as before (tests/fallback)."""
        if self._adapter is None:
            # Connection state, not user input — a Log line, never a modal
            # (plan_2026_09_11_no_modals_and_busy_kicad X.1). RecordImprint
            # Dialog has no _show_message (only the embedded ImprintForm
            # Widget has one), so this goes through the shared helper. The
            # button is disabled without an adapter anyway; the pivot is still
            # never guessed here.
            show_message(_("Connect to KiCad first."), _ERROR_STYLE, logger)
            return
        refs = self._checked_refs()
        if not refs:
            QMessageBox.warning(
                self, _("Imprints"),
                _("No footprints to record — pick a sheet that has footprints "
                  "on the 'By sheet' tab, or select footprints on the board "
                  "for 'By selection'."))
            return
        self._pivot_op = refresh_snapshot_then_with_retry(
            self._connection, (self.pivot_from_selection_button,),
            lambda: self._pivot_from_selection_now(refs),
            self._on_pivot_snapshot_refresh_failed,
            busy_text=_("reading the board"),
            owner=self.pivot_from_selection_button,
            # Э2.5: the pivot reads POSITIONS out of the snapshot, so the
            # cached-snapshot fallback would write wrong geometry, not just an
            # old list — keep the refusal and let the USER know (ERROR: this is
            # the user-facing line, distinct from gui.worker's diagnostic
            # WARNING).
            on_still_busy=lambda: self._on_pivot_snapshot_refresh_failed(
                _("another operation is using the board")))

    def _pivot_from_selection_now(self, refs: List[str]) -> None:
        """UI thread, AFTER the snapshot rebuild (see
        _on_pivot_from_selection): compute the pivot and fill the x/y fields.
        Without a refreshable connection this is the old synchronous
        cached-snapshot path (tests/fallback)."""
        try:
            x, y = pivot_centre_frame_from_selection(
                refs, self._live_snapshot(), self._live_selection())
        except ValidationError as e:
            QMessageBox.warning(self, _("Imprints"), str(e))
            return
        self._set_pivot_fields(x, y)

    def _on_pivot_snapshot_refresh_failed(self, message: str) -> None:
        """UI thread: the worker could not rebuild the snapshot (the live board
        is gone — BoardConnection.refresh() drops the connection). Say so
        instead of silently computing the pivot from stale coordinates.

        Also a CONNECTION-state failure (not user input) — a Log line, never a
        modal (plan_2026_09_11_no_modals_and_busy_kicad X.1.2's "more cases of
        the same kind"): the message IS about the lost board connection."""
        show_message(
            _("Could not refresh the board snapshot: {error}").format(
                error=message),
            _ERROR_STYLE, logger)

    # ── "By sheet" helpers ──────────────────────────────────────────────

    def _rebuild_sheet_tree(self) -> None:
        """(Re)build the FULL-hierarchy "By sheet" tree (Commit E): one subtree
        per TOP-LEVEL sheet (Channel_0/1/2, ...), every real sheet a checkable
        node, ALL UNCHECKED by default — you tick what to record, and checking
        a node turns its whole subtree on via the Commit D cascade (a parent
        with a mixed subtree shows PartiallyChecked and is still read)."""
        paths = self._sheet_paths
        self.sheet_tree.blockSignals(True)
        self.sheet_tree.clear()
        if paths:
            tops = sorted({p[:1] for p in paths if p})
            for top in tops:
                under = [p for p in paths if p[:1] == top]
                plan = sheet_subtree_plan(top, under)
                self._add_tree_nodes(self.sheet_tree.invisibleRootItem(), plan)
        self.sheet_tree.expandAll()
        self.sheet_tree.setVisible(True)
        self.sheet_tree.blockSignals(False)
        self._sync_ok_state()

    def _add_tree_nodes(self, parent_item, nodes: list) -> None:
        """Recursively append `nodes` (sheet_subtree_plan output) under
        `parent_item`. Every node is a REAL sheet of the hierarchy and is
        CHECKABLE — UserRole = the full path (a tuple, so _checked_sheet_paths
        keeps returning the same tuple shape the tests compare against),
        tooltip = the "/"-joined path, ItemIsUserTristate lets the Commit D
        cascade show PartiallyChecked parents. ALL start UNCHECKED — you tick
        what to record (Commit E)."""
        for node in nodes:
            path = node["path"]
            item = QTreeWidgetItem(parent_item, [node["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, tuple(path))
            item.setToolTip(0, "/".join(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                          | Qt.ItemFlag.ItemIsUserTristate)
            item.setCheckState(0, Qt.CheckState.Unchecked)  # tick to record
            self._add_tree_nodes(item, node["children"])

    def _on_sheet_item_changed(self, item, column) -> None:
        """Tri-state cascade (Commit D, plan_2026_09_07_imprint_commit_d_
        tristate_parent.md §3): a change to a checkable sheet row turns its
        WHOLE subtree on/off and re-syncs every ancestor (PartiallyChecked when
        some but not all sheets under it are on). Reentrancy-guarded by
        `self._cascading` — our own setCheckState calls re-enter this slot."""
        if self._cascading:
            return
        if column != 0 or item.data(0, Qt.ItemDataRole.UserRole) is None:
            return  # not a sheet row's checkbox
        on = item.checkState(0) != Qt.CheckState.Unchecked
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._cascading = True
        try:
            # A tristate click on an OFF row lands on PartiallyChecked first —
            # normalize it to Checked so "tick to record" is a single click.
            item.setCheckState(0, state)
            self._set_descendant_states(item, state)
            self._sync_ancestors(item)
        finally:
            self._cascading = False

    def _set_descendant_states(self, parent_item, state) -> None:
        """Set `state` on every checkable sheet UNDER `parent_item`. Structural
        branches carry no checkbox and are skipped, but the walk passes through
        them so deeper sheets are reached."""
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) is not None:
                child.setCheckState(0, state)
            self._set_descendant_states(child, state)

    def _sync_ancestors(self, item) -> None:
        """Recompute the check state of every checkable ancestor of `item` from
        its whole subtree (Checked when every sheet under it is on, Unchecked
        when none is, otherwise PartiallyChecked). Structural ancestors are
        skipped (no checkbox) but the climb continues past them."""
        parent = item.parent()
        while parent is not None:
            if parent.data(0, Qt.ItemDataRole.UserRole) is not None:
                parent.setCheckState(0, self._branch_state(parent))
            parent = parent.parent()

    @staticmethod
    def _branch_state(item) -> Qt.CheckState:
        """What a sheet row's checkbox should show given `item` and its whole
        subtree of checkable sheets: Checked when every one is on, Unchecked
        when none is, PartiallyChecked when mixed."""
        states = set()

        def _collect(parent) -> None:
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) is not None:
                    states.add(child.checkState(0))
                _collect(child)

        _collect(item)
        if item.data(0, Qt.ItemDataRole.UserRole) is not None:
            states.add(item.checkState(0))
        if not states or states <= {Qt.CheckState.Unchecked}:
            return Qt.CheckState.Unchecked
        if states <= {Qt.CheckState.Checked}:
            return Qt.CheckState.Checked
        return Qt.CheckState.PartiallyChecked

    def _checked_sheet_paths(self) -> List[Any]:
        """The sheet paths actually included — the single source of truth for
        "what is really read" (Commit D/E rule): every checkable node that is
        NOT Unchecked (Checked OR PartiallyChecked) contributes its DIRECT refs
        — a parent with some sub-sheets excluded is still read. Container
        sheets (no own footprints) are stored too: they add no refs today but
        keep a future Reread aware of the branch."""
        out: List[Any] = []

        def _walk(parent_item) -> None:
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path is not None and child.checkState(0) != Qt.CheckState.Unchecked:
                    out.append(path)
                _walk(child)

        _walk(self.sheet_tree.invisibleRootItem())
        return out

    def _checked_refs(self) -> List[str]:
        """The refs the ACTIVE tab would capture — direct refs of the CHECKED
        paths ("By sheet") or the caller's selection_refs ("By selection").
        Used ONLY to gate OK on a non-empty capturable set; the caller
        re-derives the final capture refs from result_data via
        record_refs_for()."""
        if self.is_by_sheet():
            return sorted({r for p in self._checked_sheet_paths()
                           for r in refs_on_sheet(self._snapshot, p)})
        return sorted(set(self._selection_refs))

    def _sync_ok_state(self) -> None:
        """OK needs a capturable set AND a valid Pivot/Anchor tab: the ACTIVE
        SOURCE tab (By sheet / By selection — NOT the Pivot tab, index 2) must
        yield at least one ref (an empty board selection / all-unchecked sheet
        leaves nothing to record) and the pivot x/y must be numbers (a malformed
        pivot must never reach the record)."""
        ok = bool(self._checked_refs()) and self._pivot_fields_ok()
        self._ok_button.setEnabled(ok)

    def _on_tab_changed(self, index: int) -> None:
        """Track which SOURCE tab (0=By sheet, 1=By selection) is active,
        ignoring visits to the Pivot/Anchor tab (index 2) — it is not a source,
        switching to it must not silently change is_by_sheet()'s answer
        (plan_2026_09_08_scheme_list_pivot_tab_source_tracking_fix.md §0)."""
        if index in (0, 1):
            self._source_tab_index = index
        self._sync_ok_state()

    def is_by_sheet(self) -> bool:
        return self._source_tab_index == 0

    def result_data(self):
        """(name, sheet_path_or_None, checked_paths_or_None).
        sheet_path is ALWAYS None (Commit E has no root combobox); the caller
        derives the capture refs itself (record_refs_for) from the CHECKED
        paths for "By sheet", or uses its OWN selection_refs for "By selection"
        (this dialog does not own that list)."""
        name = self.name_edit.text().strip()
        if self.is_by_sheet():
            return (name, None, self._checked_sheet_paths())
        return (name, None, None)

    def preset_name_to_save(self) -> Optional[str]:
        """Non-empty text of the optional "Save as preset" field on the "By
        sheet" tab, or None (nothing to save — the default). Meaningless on
        "By selection" (scope_sheet_paths/scope_presets stay [] there)."""
        if not self.is_by_sheet():
            return None
        text = self.save_preset_edit.text().strip()
        return text or None


class BoundaryNetDialog(QDialog):
    """Per-net boundary decision (G1, plan §5-G1; replaces the v1
    ``confirm_boundary_exclusions`` QMessageBox, which could only CONFIRM the
    all-exclude outcome). One row per boundary net: the net + external-ref
    diagnostics and an ``Exclude | Truncate`` combo, DEFAULT Exclude; an OK
    ("Record") + Cancel button row. This dialog ONLY collects the per-net
    choice — it never applies anything; applying the actions is the caller's
    job (the two-phase Record flow re-runs capture with the chosen actions in
    G2)."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Boundary nets"))
        remember_dialog_size(self, 620, 420)
        self._combos: List[tuple] = []  # (net, QComboBox) in row order
        layout = QVBoxLayout(self)
        explain = QLabel(
            _("Choose how to treat each boundary net. Exclude drops the whole "
              "connected component; Truncate keeps only the copper inside the "
              "capture region."))
        explain.setWordWrap(True)
        layout.addWidget(explain)

        form = QFormLayout()
        for row in rows:
            if row.external_ref:
                net_label = _("{net} (touched by {external_ref})").format(
                    net=row.net, external_ref=row.external_ref)
            else:
                net_label = row.net
            combo = QComboBox()
            combo.addItem(_("Exclude"), "exclude")
            combo.addItem(_("Truncate"), "truncate")
            self._combos.append((row.net, combo))
            form.addRow(QLabel(net_label), combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        record_button = QPushButton(_("Record"))
        buttons.addButton(record_button,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_actions(self) -> Dict[str, str]:
        """Read the widgets — the per-net truncate choices ONLY. A net left on
        Exclude is omitted ({} = everything excluded); the capture (Stage 4)
        defaults any net not mentioned in the dict to exclude. Pure widget
        reading, no decision logic."""
        return {net: "truncate" for net, combo in self._combos
                if combo.currentData() == "truncate"}
