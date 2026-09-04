# gui/docks/instantiate_cell_dialog.py
"""Tools -> Trees -> "Instantiate from Cell..." dialog (2026-09-03, plan
techdocs/handoff/deepseek/plan_2026_09_03_instantiate_from_entity.md; second
tab added 2026-09-04, plan instantiate_new_cell_from_selection).

The dialog adds ONE new group into the CURRENT tree, choosing the group's
internal layout on one of two tabs:

  Tab 1 "Existing cell" (default, today's behavior): reuse an EXISTING Cell
  (e.g. pif_p2v5_vcca) as the layout — name the new Entity for the new
  physical cluster (PIF_1V2_VCCINT), address it by Sheet/Cluster, and decide
  the node's offset from the tree anchor either manually (xy in mm) or from
  the current board selection (the geometric center of the selected group —
  the "координата кучки"). The Cell itself is NEVER generated/copied: the new
  Entity just references it (cell:).

  Tab 2 "Extract new cell from selection" (2026-09-04): EXTRACT a NEW Cell
  right from the current selection (the same cell the tab-1 path would only
  reference if it already existed), instead of requiring the user to first run
  "Extract cluster..." and then separately "Instantiate from Cell...". STRICT
  full-selection semantics (Denis's 2026-09-04 decision): the extraction is
  offered ONLY when the selection contains exactly ONE FULLY selected Cluster
  (the caller runs fully_selected_clusters and passes the result in); a
  partial selection is never captured silently. The user names the new Cell
  and picks the Cell's internal geometry origin ("Relative to zero-slot", the
  ordinary portable convention, or "Absolute (selection center)", whose origin
  is the SAME point the node's "Take from selection" positioning uses — so the
  total position reproduces the live one exactly).

The new Entity carries NO refs/by_selection in either tab: components of the
new cluster may not be placed/selected yet (tab 1) — roles resolve at Apply
by (Cluster, Sheet); on tab 2 the addressing is the fully-selected cluster the
Cell was extracted from. Selection is only an OPTIONAL positioning aid on tab
1, guarded by an explicit opt-in checkbox ("Взять из выделения") so a stray
leftover selection can never be mistaken for intent.

This dialog only COLLECTS the decision (plus the optional manual xy). The
final node xy for the "from selection" mode is computed by the caller (the
TreesDock flow), which owns the live anchor base of the current tree — the
dialog stays decoupled from the tree/anchor machinery.
"""
from typing import Optional

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                             QMessageBox, QRadioButton, QTabWidget,
                             QVBoxLayout, QWidget)

from kicadstamp.i18n import _

from ._anchor_origin import AnchorOriginWidget
from ._common import configure_searchable, set_combo_items
from .tree_from_selection import (
    build_instantiated_entity,
    cell_component_roles,
    cluster_cell_name,
    missing_cluster_roles,
    selection_cluster,
)

# Reused verbatim from the "Extract cluster..." flow (dock_hub.py) so the two
# strict paths share ONE msgid — no second, drifting translation.
_NO_FULLY_SELECTED = (
    "No fully selected Cluster found — select ALL components of a cluster "
    "(its Cluster tag + sheet) first.")


class InstantiateCellDialog(QDialog):
    """Modal editor for one "Instantiate from Cell..." decision.

    Built from an ALREADY-LOADED cfg (the caller loads it) + candidate lists
    (cells from cfg.cells; live Sheet/Cluster values for the editable combos) +
    the current board selection and the full-board snapshot (Selected lists)
    for the opt-in "Взять из выделения" mode and the "Cell подходит?" check +
    the caller's fully_selected_clusters result ([(cluster, sheet)...], the
    strict tab-2 enablement source).

    Result is read after exec() == Accepted through result_cell()/entity_name()/
    cluster()/sheet()/manual_xy()/from_selection() — and, on tab 2, through
    is_new_cell()/new_cell_name()/absolute_origin(). The dialog writes NOTHING —
    the caller persists (staged via config_writer) and appends the tree node."""

    def __init__(self, parent, cfg, *, cells, sheets, clusters,
                 selected, snapshot, fully_selected=()):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle(_("Instantiate from Cell"))
        self.setMinimumWidth(500)

        self._cells = list(cells or [])
        self._sheets = list(sheets or [])
        self._clusters = list(clusters or [])
        self._selected = list(selected or [])
        self._snapshot = list(snapshot or [])
        # [(cluster, sheet), ...] of the FULLY selected clusters (the caller's
        # fully_selected_clusters) — the strict tab-2 enablement source. Sheet
        # may be None (no resolvable channel).
        self._fully_selected = [(c[0], c[1]) for c in (fully_selected or ())]
        # Last auto cell-name prefill — so switching the Cluster tab updates
        # the prefill without clobbering a name the user already typed.
        self._auto_cell_name = ""

        root = QVBoxLayout(self)

        # ── Which Cell provides the group's layout? (tab-scoped) ────────────
        self.tabs = QTabWidget()

        # Tab 1 — an EXISTING Cell (today's behavior).
        tab1 = QWidget()
        tab1_form = QFormLayout(tab1)
        self.cell_combo = QComboBox()
        self.cell_combo.setEditable(True)
        set_combo_items(self.cell_combo, self._cells)
        configure_searchable(self.cell_combo)
        tab1_form.addRow(_("Cell:"), self.cell_combo)
        self.suit_label = QLabel("")
        self.suit_label.setWordWrap(True)
        tab1_form.addRow(self.suit_label)

        # Tab 2 — EXTRACT a NEW Cell from the current (fully-selected)
        # cluster's selection.
        tab2 = QWidget()
        tab2_form = QFormLayout(tab2)
        self.new_cell_name_edit = QLineEdit()
        self.new_cell_name_edit.setPlaceholderText(_("e.g. dac_buf"))
        tab2_form.addRow(_("New cell name:"), self.new_cell_name_edit)
        # "Geometry origin" — the extraction's reference point (design
        # §1.1.2): zero-slot is portable; absolute = the selection center.
        self.zero_slot_radio = QRadioButton(_("Relative to zero-slot"))
        self.absolute_radio = QRadioButton(
            _("Absolute (selection center), rotation 0"))
        self.zero_slot_radio.setChecked(True)
        geo_box = QWidget()
        geo_col = QVBoxLayout(geo_box)
        geo_col.setContentsMargins(0, 0, 0, 0)
        geo_col.addWidget(self.zero_slot_radio)
        geo_col.addWidget(self.absolute_radio)
        tab2_form.addRow(_("Geometry origin:"), geo_box)
        self.geometry_warning_label = QLabel(
            _("Absolute geometry only reproduces the live position when "
              "paired with 'Take from selection' — with manual xy the result "
              "is unverified."))
        self.geometry_warning_label.setWordWrap(True)
        self.geometry_warning_label.setStyleSheet("font-style: italic;")
        self.geometry_warning_label.hide()
        tab2_form.addRow(self.geometry_warning_label)
        # Optional MANUAL origin override (2026-09-04, plan
        # extract_origin_pad_restore): opt-in checkbox so "empty" always means
        # "keep the automatic zero-slot detection", never a swallowed widget
        # error (AnchorOriginWidget.build() fatals on empty Ref+Role in anchor
        # mode — an empty build() here is a VALID "leave automatic"). Only
        # meaningful in zero-slot mode — in Absolute the override never applies.
        self.origin_override_check = QCheckBox(_("Override origin (Role/Pad)…"))
        self.origin_override_check.setChecked(False)
        self.origin_widget = AnchorOriginWidget(
            modes=("anchor",), anchor_fields=("pad",))
        tab2_form.addRow(self.origin_override_check)
        tab2_form.addRow(self.origin_widget)
        # Strict-state status line (0 clusters / >1 clusters / ready).
        self.tab2_status_label = QLabel("")
        self.tab2_status_label.setWordWrap(True)
        tab2_form.addRow(self.tab2_status_label)

        self.tabs.addTab(tab1, _("Existing cell"))
        self.tabs.addTab(tab2, _("Extract new cell from selection"))
        root.addWidget(self.tabs)

        # ── Shared: the new Entity's addressing + the node position ─────────
        shared_form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("e.g. PIF_1V2_VCCINT"))
        shared_form.addRow(_("Entity name:"), self.name_edit)

        self.sheet_combo = QComboBox()
        self.sheet_combo.setEditable(True)
        set_combo_items(self.sheet_combo, self._sheets)
        configure_searchable(self.sheet_combo)
        shared_form.addRow(_("Sheet:"), self.sheet_combo)

        self.cluster_combo = QComboBox()
        self.cluster_combo.setEditable(True)
        set_combo_items(self.cluster_combo, self._clusters)
        configure_searchable(self.cluster_combo)
        shared_form.addRow(_("Cluster:"), self.cluster_combo)
        root.addLayout(shared_form)

        # ── Position: opt-in "from selection" vs manual xy ────────────────
        self.from_selection_check = QCheckBox(
            _("Take from selection (one cluster; center of the selected group)"))
        self.from_selection_check.setChecked(False)  # opt-in: never auto-assume
        root.addWidget(self.from_selection_check)

        pos_form = QFormLayout()
        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-1000.0, 1000.0)
        self.x_spin.setDecimals(3)
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-1000.0, 1000.0)
        self.y_spin.setDecimals(3)
        pos_form.addRow(_("X (mm):"), self.x_spin)
        pos_form.addRow(_("Y (mm):"), self.y_spin)
        root.addLayout(pos_form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        root.addWidget(buttons)

        self._wire_updates()

    # ── live wiring ──────────────────────────────────────────────────────

    def _wire_updates(self) -> None:
        self.cell_combo.currentTextChanged.connect(lambda _t: self._refresh_suitability())
        self.sheet_combo.currentTextChanged.connect(lambda _t: self._refresh_suitability())
        self.cluster_combo.currentTextChanged.connect(
            lambda _t: (self._refresh_suitability(), self._refresh_new_cell_name()))
        self.from_selection_check.toggled.connect(self._on_from_selection_toggled)
        self.zero_slot_radio.toggled.connect(lambda _c: self._update_geometry_warning())
        self.absolute_radio.toggled.connect(lambda _c: self._update_geometry_warning())
        self.origin_override_check.toggled.connect(
            lambda _c: self._update_origin_widget_visibility())
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex())
        self._update_geometry_warning()
        self._update_origin_widget_visibility()

    def _update_origin_widget_visibility(self) -> None:
        """The manual-origin override (checkbox + role/pad picker) is only
        meaningful on tab 2 in zero-slot mode: it follows the checkbox AND
        zero_slot_radio.isChecked() — in "Absolute" the override never applies
        (see tree_from_selection.extract_new_cell_for_instantiation), so the
        whole block is hidden regardless of the checkbox (2026-09-04, plan
        extract_origin_pad_restore §3)."""
        visible = (self.origin_override_check.isChecked()
                   and self.zero_slot_radio.isChecked())
        self.origin_widget.setVisible(visible)
        self.origin_widget.setEnabled(visible)

    def _on_from_selection_toggled(self, checked: bool) -> None:
        # Position mode changed — the "Absolute geometry needs 'Take from
        # selection'" notice depends on it, so refresh it on every toggle.
        self._update_geometry_warning()
        if not checked:
            return
        try:
            cluster = selection_cluster(self._selected)
        except ValueError as e:
            QMessageBox.warning(self, self.windowTitle(), str(e))
            self.from_selection_check.setChecked(False)
            return
        if not cluster:
            QMessageBox.information(
                self, self.windowTitle(),
                _("No cluster-tagged component is selected — enter the Cluster "
                  "and the xy manually."))
            self.from_selection_check.setChecked(False)
            return
        # One cluster in the selection: adopt it (the Entity's addressing).
        set_combo_items(self.cluster_combo, [cluster])
        self.cluster_combo.setCurrentText(cluster)
        self._refresh_suitability()
        self._update_geometry_warning()

    def _on_tab_changed(self, index: int) -> None:
        """Tab switch: tab 2 (index 1) re-runs the strict-state autofill — when
        the caller detected EXACTLY ONE fully-selected cluster, adopt its
        cluster/sheet into the shared addressing (the same set_combo_items +
        setCurrentText adoption "_on_from_selection_toggled" uses — one
        synchronization mechanism, never a second one) and prefill the new Cell
        name from its slug."""
        if index == 1:
            self._apply_tab2_autofill()
        self._update_geometry_warning()
        self._update_tab2_ok()
        # Tab 1's "does the Cell fit?" check is meaningless mid-extraction, but
        # the combo text may have changed via adoption — keep it fresh anyway.
        self._refresh_suitability()

    def _single_detected(self) -> Optional[tuple[str, Optional[str]]]:
        """The ONE fully-selected (cluster, sheet) the strict tab 2 may extract
        from, or None when the detection found zero or several clusters."""
        if len(self._fully_selected) != 1:
            return None
        return self._fully_selected[0]

    def _tab2_state_problem(self) -> Optional[str]:
        """A blocking problem on tab 2 caused by the DETECTION (before any
        field validation): zero fully-selected clusters (the strict message,
        shared with "Extract cluster...") or several (ambiguous — a Cell is
        extracted from exactly one cluster)."""
        if len(self._fully_selected) == 0:
            return _(_NO_FULLY_SELECTED)
        if len(self._fully_selected) > 1:
            return _("Select exactly ONE fully selected Cluster — the new Cell "
                     "is extracted from exactly one cluster.")
        return None

    def _apply_tab2_autofill(self) -> None:
        """Adopt the single detected cluster into the shared addressing combos
        and clear/refresh the strict status line. When the detection found 0 or
        several clusters the combos are left untouched — the OK button is
        disabled instead (see _update_tab2_ok)."""
        state = self._tab2_state_problem()
        if state is not None:
            self.tab2_status_label.setText(state)
            return
        self.tab2_status_label.setText("")
        det_cluster, det_sheet = self._single_detected()
        set_combo_items(self.cluster_combo, [det_cluster])
        self.cluster_combo.setCurrentText(det_cluster)
        if det_sheet:
            set_combo_items(self.sheet_combo, [det_sheet])
            self.sheet_combo.setCurrentText(det_sheet)
        # Prefill the new Cell name from the detected cluster's slug (editable).
        self._auto_cell_name = ""
        self._refresh_new_cell_name()
        # Manual-origin role candidates: roles REALLY present in the detected
        # cluster's selected components (never the whole board) — 2026-09-04,
        # plan extract_origin_pad_restore §2.
        self.origin_widget.set_known_roles(self._tab2_cluster_roles(), [])

    def _tab2_cluster_roles(self) -> list[str]:
        """The roles present among the selected footprints of the addressed
        cluster (tab 2, strict single-cluster state) — the manual-origin combo
        candidates. `self._selected` footprints carry the board cluster tag;
        the cluster is FULLY selected, so filtering by the tag yields exactly
        its component roles (the same source cluster_origin_role counts)."""
        cluster = self.cluster()
        roles = {s.role for s in self._selected
                 if s.role and getattr(s, "cluster", None) == cluster}
        return sorted(roles)

    def _update_tab2_ok(self) -> None:
        """The strict gate: on tab 2 OK is only possible when the detection is
        unambiguous (exactly one fully-selected cluster). Field-level problems
        (empty/duplicate name, address mismatch) are reported by validate() on
        accept — this is the "never silently offer a partial extraction" gate."""
        blocked = self.is_new_cell() and self._tab2_state_problem() is not None
        self._ok_button.setEnabled(not blocked)

    def _update_geometry_warning(self) -> None:
        """'Absolute' Cell geometry is only guaranteed to reproduce the live
        position together with the node's 'Take from selection' positioning
        (design §1.1.2) — show the notice exactly in that (non-fatal) case."""
        visible = (self.is_new_cell() and self.absolute_radio.isChecked()
                   and not self.from_selection_check.isChecked())
        self.geometry_warning_label.setVisible(visible)

    def _refresh_new_cell_name(self) -> None:
        """Auto-prefill the new Cell name from the addressed cluster's slug;
        never clobbers a name the user already typed (tracked via the last
        auto value)."""
        auto = cluster_cell_name(self.cluster())
        cur = self.new_cell_name_edit.text().strip()
        if not cur or cur == self._auto_cell_name:
            self.new_cell_name_edit.setText(auto)
        self._auto_cell_name = auto

    def _cluster_footprints(self):
        """The snapshot footprints of the addressed cluster instance (cluster
        tag + sheet chain contains the addressed sheet when set)."""
        cluster = self.cluster_combo.currentText().strip()
        sheet = self.sheet_combo.currentText().strip()
        out = []
        for s in self._snapshot:
            if not getattr(s, "cluster", None) or s.cluster != cluster:
                continue
            if sheet and sheet not in (s.sheet or ()):
                continue
            out.append(s)
        return out

    def _refresh_suitability(self) -> None:
        """Live "Cell подходит?" line (tab 1): every Cell component role must be
        present among the addressed cluster's board footprints (so Apply can
        resolve). A cluster not on the board yet is a WARNING, not a hard error —
        the user may be adding the group before the components are in place."""
        cell_name = self.cell_combo.currentText().strip()
        cell = self._cfg.cells.get(cell_name) if self._cfg is not None else None
        if cell is None:
            self.suit_label.setText("")
            return
        roles = cell_component_roles(cell)
        missing = missing_cluster_roles(cell, self._cluster_footprints())
        if not missing:
            if self._cluster_footprints():
                self.suit_label.setText(
                    _("Cell {cell!r} fits this cluster ({n} roles matched).")
                    .format(cell=cell_name, n=len(roles)))
            else:
                self.suit_label.setText(
                    _("Cell {cell!r}: no footprints of this cluster on the "
                      "board yet — roles will resolve at Apply once they are.")
                    .format(cell=cell_name))
        else:
            self.suit_label.setText(
                _("Cell {cell!r}: roles missing on the board for this cluster: "
                  "{missing}").format(cell=cell_name,
                                      missing=", ".join(sorted(missing))))

    # ── result ──────────────────────────────────────────────────────────

    def is_new_cell(self) -> bool:
        """True when the active tab is 2 ("Extract new cell from selection") —
        the caller then reads new_cell_name()/absolute_origin() instead of
        result_cell() meaning an existing Cell."""
        return self.tabs.currentIndex() == 1

    def new_cell_name(self) -> str:
        """The user-edited name of the NEW Cell (tab 2 only)."""
        return self.new_cell_name_edit.text().strip()

    def absolute_origin(self) -> bool:
        """True when tab 2's geometry radio "Absolute (selection center)" is
        selected (meaningful only when is_new_cell())."""
        return self.is_new_cell() and self.absolute_radio.isChecked()

    def origin_override(self) -> tuple[str | None, str | None]:
        """(origin_role, origin_pad) of the opt-in MANUAL origin override, or
        (None, None) when it is off. The conditions EXPLICITLY repeat the
        widget's visibility — never rely on the widget being physically hidden:
        Absolute mode never applies the override (2026-09-04, plan
        extract_origin_pad_restore §3/§1.2). A build() error returns (None,
        None) — validate() already blocks OK on the same build() result, so
        this getter is never the error surface."""
        if not self.zero_slot_radio.isChecked():
            return (None, None)          # Absolute mode — override never applies
        if not self.origin_override_check.isChecked():
            return (None, None)          # opt-in unchecked — automatic
        fields, err = self.origin_widget.build()
        if err:
            return (None, None)          # validate() already blocks OK on this
        return (fields.get("role"), fields.get("pad"))

    def result_cell(self) -> str:
        """The cell name the new Entity will reference — an EXISTING cell name
        on tab 1, or the brand-new Cell name on tab 2. The single reading point
        so the caller (_instantiate_from_cell) never needs to know about tabs."""
        if self.is_new_cell():
            return self.new_cell_name()
        return self.cell_combo.currentText().strip()

    def entity_name(self) -> str:
        return self.name_edit.text().strip()

    def cluster(self) -> str:
        return self.cluster_combo.currentText().strip()

    def sheet(self) -> str:
        return self.sheet_combo.currentText().strip()

    def from_selection(self) -> bool:
        return self.from_selection_check.isChecked()

    def manual_xy(self) -> Optional[tuple[float, float]]:
        if self.from_selection_check.isChecked():
            return None
        return (self.x_spin.value(), self.y_spin.value())

    def _tab2_mismatch_problem(self) -> Optional[str]:
        """On tab 2 the Entity's addressing (shared combos) must match the
        fully-selected cluster the Cell is extracted from — the user may have
        retyped the combos after the autofill; a silent mismatch would stage a
        Cell whose addressing points elsewhere. Requires the single-detected
        state (the count problems are reported by _tab2_state_problem)."""
        single = self._single_detected()
        if single is None:
            return None
        det_cluster, det_sheet = single
        if (self.cluster().strip() == det_cluster
                and (self.sheet().strip() or None) == (det_sheet or None)):
            return None
        return _("For a new Cell, the Cluster and Sheet must match the fully "
                 "selected cluster the Cell is extracted from.")

    def validate(self) -> Optional[str]:
        """A human-readable problem, or None when the dialog may accept."""
        if self.is_new_cell():
            problem = self._tab2_state_problem()
            if problem is not None:
                return problem
            cell_name = self.new_cell_name()
            if not cell_name:
                return _("A new Cell name is required.")
            if self._cfg is not None and cell_name in self._cfg.cells:
                return _("A cell named {name!r} already exists.").format(
                    name=cell_name)
            problem = self._tab2_mismatch_problem()
            if problem is not None:
                return problem
            # Manual-origin override (only active in zero-slot mode, opt-in):
            # surface the widget's own build() error as a fatal — the same
            # pair of conditions origin_override() uses (2026-09-04, plan
            # extract_origin_pad_restore §3).
            if (self.zero_slot_radio.isChecked()
                    and self.origin_override_check.isChecked()):
                _fields, err = self.origin_widget.build()
                if err:
                    return err
        else:
            cell_name = self.result_cell()
            if not cell_name:
                return _("Pick a Cell (the source of the group's layout).")
            if self._cfg is not None and cell_name not in self._cfg.cells:
                return _("Unknown cell {cell!r}.").format(cell=cell_name)
        name = self.entity_name()
        if not name:
            return _("Entity name is required.")
        if self._cfg is not None and any(e.name == name for e in self._cfg.entities):
            return _("An entity named {name!r} already exists.").format(name=name)
        if not self.cluster():
            return _("Cluster is required (the address of the new instance).")
        return None

    def accept(self) -> None:
        problem = self.validate()
        if problem is not None:
            QMessageBox.warning(self, self.windowTitle(), problem)
            return
        super().accept()
