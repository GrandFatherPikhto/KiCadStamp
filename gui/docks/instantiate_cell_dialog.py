# gui/docks/instantiate_cell_dialog.py
"""Tools -> Trees -> "Instantiate from Cell..." dialog (2026-09-03, plan
techdocs/handoff/deepseek/plan_2026_09_03_instantiate_from_entity.md).

Adds ONE new group into the CURRENT tree by reusing an EXISTING Cell as the
internal layout: pick the Cell (e.g. pif_p2v5_vcca), name the new Entity for
the new physical cluster (PIF_1V2_VCCINT), address it by Sheet/Cluster, and
decide the node's offset from the tree anchor either manually (xy in mm) or
from the current board selection (the geometric center of the selected
cluster's footprints — the "координата кучки"). The Cell itself is NEVER
generated/copied: the new Entity just references it (cell:); every group of
this kind shares one Cell.

The new Entity carries NO refs/by_selection: components of the new cluster may
not be placed/selected yet, so roles resolve at Apply by (Cluster, Sheet) —
the same path the template Entity uses. Selection is only an OPTIONAL
positioning/cluster aid, guarded by an explicit opt-in checkbox ("Взять из
выделения") so a stray leftover selection can never be mistaken for intent.

This dialog only COLLECTS the decision (plus the optional manual xy). The
final node xy for the "from selection" mode is computed by the caller (the
TreesDock flow), which owns the live anchor base of the current tree — the
dialog stays decoupled from the tree/anchor machinery.
"""
from typing import Optional

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                             QMessageBox, QVBoxLayout)

from kicadstamp.i18n import _

from ._common import configure_searchable, set_combo_items
from .tree_from_selection import (
    build_instantiated_entity,
    cell_component_roles,
    missing_cluster_roles,
    selection_cluster,
)


class InstantiateCellDialog(QDialog):
    """Modal editor for one "Instantiate from Cell..." decision.

    Built from an ALREADY-LOADED cfg (the caller loads it) + candidate lists
    (cells from cfg.cells; live Sheet/Cluster values for the editable combos) +
    the current board selection and the full-board snapshot (Selected lists)
    for the opt-in "Взять из выделения" mode and the "Cell подходит?" check.

    Result is read after exec() == Accepted through result_cell()/entity_name()/
    cluster()/sheet()/manual_xy()/from_selection(). The dialog writes NOTHING —
    the caller persists (staged via config_writer) and appends the tree node."""

    def __init__(self, parent, cfg, *, cells, sheets, clusters,
                 selected, snapshot):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle(_("Instantiate from Cell"))
        self.setMinimumWidth(430)

        self._cells = list(cells or [])
        self._sheets = list(sheets or [])
        self._clusters = list(clusters or [])
        self._selected = list(selected or [])
        self._snapshot = list(snapshot or [])

        form = QFormLayout()
        # Cell — the source of the group's internal layout; never regenerated.
        self.cell_combo = QComboBox()
        self.cell_combo.setEditable(True)
        set_combo_items(self.cell_combo, self._cells)
        configure_searchable(self.cell_combo)
        form.addRow(_("Cell:"), self.cell_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("e.g. PIF_1V2_VCCINT"))
        form.addRow(_("Entity name:"), self.name_edit)

        self.sheet_combo = QComboBox()
        self.sheet_combo.setEditable(True)
        set_combo_items(self.sheet_combo, self._sheets)
        configure_searchable(self.sheet_combo)
        form.addRow(_("Sheet:"), self.sheet_combo)

        self.cluster_combo = QComboBox()
        self.cluster_combo.setEditable(True)
        set_combo_items(self.cluster_combo, self._clusters)
        configure_searchable(self.cluster_combo)
        form.addRow(_("Cluster:"), self.cluster_combo)

        layout = QVBoxLayout(self)
        layout.addLayout(form)

        # ── Position: opt-in "from selection" vs manual xy ────────────────
        self.from_selection_check = QCheckBox(
            _("Take from selection (one cluster; center of the selected group)"))
        self.from_selection_check.setChecked(False)  # opt-in: never auto-assume
        layout.addWidget(self.from_selection_check)

        pos_form = QFormLayout()
        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(-1000.0, 1000.0)
        self.x_spin.setDecimals(3)
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(-1000.0, 1000.0)
        self.y_spin.setDecimals(3)
        pos_form.addRow(_("X (mm):"), self.x_spin)
        pos_form.addRow(_("Y (mm):"), self.y_spin)
        layout.addLayout(pos_form)

        # ── "Cell подходит?" live check ───────────────────────────────────
        self.suit_label = QLabel("")
        self.suit_label.setWordWrap(True)
        layout.addWidget(self.suit_label)

        self._wire_updates()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── live wiring ──────────────────────────────────────────────────────

    def _wire_updates(self) -> None:
        self.cell_combo.currentTextChanged.connect(lambda _t: self._refresh_suitability())
        self.sheet_combo.currentTextChanged.connect(lambda _t: self._refresh_suitability())
        self.cluster_combo.currentTextChanged.connect(lambda _t: self._refresh_suitability())
        self.from_selection_check.toggled.connect(self._on_from_selection_toggled)
        self._refresh_suitability()

    def _on_from_selection_toggled(self, checked: bool) -> None:
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
        """Live "Cell подходит?" line: every Cell component role must be present
        among the addressed cluster's board footprints (so Apply can resolve).
        A cluster not on the board yet is a WARNING, not a hard error — the
        user may be adding the group before the components are in place."""
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

    def result_cell(self) -> str:
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

    def validate(self) -> Optional[str]:
        """A human-readable problem, or None when the dialog may accept."""
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
