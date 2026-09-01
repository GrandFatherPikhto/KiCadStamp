# gui/docks/tree_from_selection_dialog.py
"""
TreeFromSelectionDialog — "Tools -> Extract tree..." (2026-09-01, plan
extract_selection_as_tree.md): the modal 3-tab dialog between the selection
and the saved tree.

Tabs (Denis's 09-01 decision):
  - "Clusters": the fully-selected Clusters as a checkbox table
    [✓ | Cluster | Sheet | Entity | Cell | ΔX mm | ΔY mm] — the offset columns
    are a live preview once an anchor is chosen (via entity_positions +
    anchor_base_provider). Rows whose cluster has no Entity / missing cell are
    marked and block OK.
  - "Anchor": the root-cluster combo (from the CHECKED clusters, prefills
    Sheet/Cluster/Role from the cluster's own Entity "existing anchor") plus
    explicit Sheet/Cluster/Role/Pad narrowing -> TreeAnchor.
  - "Nets": the inter-cluster nets as a checkbox table [✓ | Net | #tracks |
    #vias] with a master "select all / deselect all" checkbox; checked nets are
    captured as `net_traces:` records on OK.

The dialog is deliberately thin: all mapping/validation logic is pure code in
gui/docks/tree_from_selection.py (tested without Qt); this file only renders
rows and collects the checked ones.
"""
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QTabWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.i18n import _
from kicadstamp.trees import TreeAnchor

from ._common import configure_searchable, set_combo_items
from .reead import ReReadCluster
from .tree_from_selection import InterClusterNet


def _single_line(text: object) -> str:
    """Collapse any whitespace (including newlines) to single spaces — a table
    cell must never render raw multi-line/file content (same convention as the
    retired Re-read dialog, live 2026-08-31)."""
    return " ".join(str(text).split())


def _cluster_label(c: ReReadCluster) -> str:
    """A root-cluster combo label: Entity name when known, else Cluster (+
    sheet) — enough to tell hierarchical instances apart."""
    if c.entity_name:
        return c.entity_name
    if c.cluster:
        return f"{c.cluster} ({c.sheet or '-'})"
    return c.sheet or "?"


class TreeFromSelectionDialog(QDialog):
    """Modal 3-tab dialog for building a tree from the current selection."""

    def __init__(self, clusters: list[ReReadCluster],
                 inter_nets: list[InterClusterNet],
                 existing_names: list[str],
                 sheet_names: Optional[dict] = None,
                 role_candidates: Optional[list[str]] = None,
                 cluster_candidates: Optional[list[str]] = None,
                 parent=None,
                 cluster_errors: Optional[list[str]] = None,
                 entity_positions: Optional[dict] = None,
                 anchor_base_provider: Optional[Callable] = None,
                 prefills: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle(_("Extract tree"))
        self.setObjectName("tree_from_selection_dialog")
        self._clusters = list(clusters)
        self._inter_nets = list(inter_nets)
        self._existing_names = set(existing_names)
        self._errors = list(cluster_errors) if cluster_errors is not None \
            else [""] * len(self._clusters)
        self._entity_positions = entity_positions or {}
        self._anchor_base_provider = anchor_base_provider
        # row index -> TreeAnchor "existing cluster anchor" prefill, computed
        # by the dock hub (tree_anchor_from_cluster_entity) so this dialog
        # stays config-free.
        self._prefills = prefills or {}
        self._sheet_names = list(sheet_names or {})
        self._role_candidates = list(role_candidates or [])
        self._cluster_candidates = list(cluster_candidates or [])

        layout = QVBoxLayout(self)

        # ── Tree name (top, shared by all tabs) ────────────────────────────
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(_("Tree name:")))
        self.tree_name_edit = QLineEdit()
        self.tree_name_edit.setPlaceholderText(_("e.g. power_tree"))
        name_row.addWidget(self.tree_name_edit, 1)
        layout.addLayout(name_row)

        # ── Three tabs ─────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_clusters_tab(), _("Clusters"))
        self.tabs.addTab(self._build_anchor_tab(), _("Anchor"))
        self.tabs.addTab(self._build_nets_tab(), _("Tracks and vias between clusters"))
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_offsets()

    # ── Tab 1: Clusters ───────────────────────────────────────────────────

    def _build_clusters_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self._table = QTableWidget(len(self._clusters), 7)
        # The checkbox column's header is a PLAIN empty string — never _(""):
        # gettext stores the .po file header under the empty msgid (the same
        # retired Re-read dialog pitfall, found live 2026-08-31).
        self._table.setHorizontalHeaderLabels(
            ["", _("Cluster"), _("Sheet"), _("Entity"), _("Cell"),
             _("ΔX mm"), _("ΔY mm")])
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in (1, 2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col in (5, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._checkboxes: list[QCheckBox] = []
        for row, c in enumerate(self._clusters):
            cb = QCheckBox()
            cb.setChecked(True)
            cb.toggled.connect(self._on_row_checkbox_toggled)
            self._table.setItem(row, 0, QTableWidgetItem(""))
            self._table.setCellWidget(row, 0, cb)
            self._checkboxes.append(cb)
            for col, text in enumerate(
                    (c.cluster, c.sheet or "-", c.entity_name or "-", c.cell), 1):
                item = QTableWidgetItem(_single_line(text))
                if self._errors[row]:
                    # Mark the invalid row (Denis: "нет cell" rows must be
                    # visible, not just silently blocked at OK).
                    item.setForeground(Qt.GlobalColor.red)
                self._table.setItem(row, col, item)
            for col in (5, 6):
                self._table.setItem(row, col, QTableWidgetItem("—"))
        layout.addWidget(self._table)
        return tab

    def _on_row_checkbox_toggled(self) -> None:
        """A cluster checkbox changed -> keep the root-cluster combo in sync
        (it lists the CHECKED clusters) and refresh the offset preview."""
        self._refresh_root_cluster_combo()
        self._refresh_offsets()

    # ── Tab 2: Anchor ─────────────────────────────────────────────────────

    def _build_anchor_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.root_cluster_combo = QComboBox()
        configure_searchable(self.root_cluster_combo)
        self.root_cluster_combo.currentIndexChanged.connect(self._on_root_cluster_selected)
        form.addRow(_("Root cluster:"), self.root_cluster_combo)

        self.sheet_edit = QComboBox()
        configure_searchable(self.sheet_edit)
        set_combo_items(self.sheet_edit, self._sheet_names)
        self.sheet_edit.lineEdit().setPlaceholderText(
            _("sheet (narrows the role, optional)"))
        self.sheet_edit.currentTextChanged.connect(self._on_anchor_field_changed)
        form.addRow(_("Sheet:"), self.sheet_edit)

        self.cluster_edit = QComboBox()
        configure_searchable(self.cluster_edit)
        set_combo_items(self.cluster_edit, self._cluster_candidates)
        self.cluster_edit.lineEdit().setPlaceholderText(
            _("cluster (narrows the role, optional)"))
        self.cluster_edit.currentTextChanged.connect(self._on_anchor_field_changed)
        form.addRow(_("Cluster:"), self.cluster_edit)

        self.role_edit = QComboBox()
        configure_searchable(self.role_edit)
        set_combo_items(self.role_edit, self._role_candidates)
        self.role_edit.lineEdit().setPlaceholderText(
            _("role (the anchor component's Role field)"))
        self.role_edit.currentTextChanged.connect(self._on_anchor_field_changed)
        form.addRow(_("Role:"), self.role_edit)

        self.pad_edit = QLineEdit()
        self.pad_edit.setPlaceholderText(_("pad (optional)"))
        self.pad_edit.textChanged.connect(self._on_anchor_field_changed)
        form.addRow(_("Pad:"), self.pad_edit)

        hint = QLabel(_("Pick a root cluster to prefill from its Entity, or "
                        "narrow the anchor explicitly below."))
        hint.setWordWrap(True)
        form.addRow(hint)

        self._refresh_root_cluster_combo()
        return tab

    def _refresh_root_cluster_combo(self) -> None:
        """The root-cluster combo lists the CHECKED clusters' Entities (from
        the checked rows), preserving the current pick when still present."""
        current = self.root_cluster_combo.currentText()
        self.root_cluster_combo.blockSignals(True)
        self.root_cluster_combo.clear()
        self._root_cluster_rows: dict[str, int] = {}
        for row, c in enumerate(self._clusters):
            if not self._checkboxes[row].isChecked():
                continue
            label = _cluster_label(c)
            self.root_cluster_combo.addItem(label)
            self._root_cluster_rows[label] = row
        self.root_cluster_combo.setCurrentText(current)
        self.root_cluster_combo.blockSignals(False)
        # A fresh dialog auto-selects the first checked cluster (addItem sets
        # the current index without firing currentIndexChanged) — apply that
        # selection's "existing cluster anchor" prefill explicitly, or a
        # chosen root cluster would leave the anchor fields blank.
        idx = self.root_cluster_combo.currentIndex()
        if idx >= 0:
            self._on_root_cluster_selected(idx)

    def _on_root_cluster_selected(self, index: int) -> None:
        """Prefill the anchor fields from the picked cluster's Entity (its
        own sheet/cluster + the zero-slot role of its cell — the "existing
        cluster anchor"). itemText(index), NOT currentText(): the combo is
        editable (configure_searchable), and an editable combo's currentText
        is its line-edit text — empty until an item is explicitly activated,
        even when currentIndex() is already 0 (found while writing
        test_tree_from_selection_dialog.py)."""
        if index < 0:
            return
        row = self._root_cluster_rows.get(self.root_cluster_combo.itemText(index))
        if row is None:
            return
        c = self._clusters[row]
        # The "existing cluster anchor" prefill (computed by the dock hub from
        # the Entity + cfg); without one, at least fill sheet + cluster.
        prefill = self._prefills.get(row)
        if prefill is None:
            self.sheet_edit.setCurrentText(c.sheet or "")
            self.cluster_edit.setCurrentText(c.cluster or "")
            return
        self.sheet_edit.setCurrentText(prefill.anchor_sheet or "")
        self.cluster_edit.setCurrentText(prefill.anchor_cluster or "")
        self.role_edit.setCurrentText(prefill.role or "")
        self.pad_edit.setText(prefill.anchor_pad or "")

    def _on_anchor_field_changed(self, *_args) -> None:
        self._refresh_offsets()

    def build_anchor(self) -> Optional[TreeAnchor]:
        """The TreeAnchor from the current anchor fields (role required)."""
        role = self.role_edit.currentText().strip()
        if not role:
            return None
        return TreeAnchor(
            role=role, is_origin=False,
            anchor_sheet=self.sheet_edit.currentText().strip() or None,
            anchor_cluster=self.cluster_edit.currentText().strip() or None,
            anchor_pad=self.pad_edit.text().strip() or None,
        )

    # ── Tab 3: Nets ───────────────────────────────────────────────────────

    def _build_nets_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        if not self._inter_nets:
            empty = QLabel(_("No selected tracks/vias connect two or more "
                             "clusters — nothing to capture."))
            empty.setWordWrap(True)
            layout.addWidget(empty)
            self._net_master = None
            self._net_table = None
            self._net_checkboxes: list[QCheckBox] = []
            return tab

        self._net_master = QCheckBox(_("Select all / deselect all"))
        self._net_master.setChecked(True)
        self._net_master.toggled.connect(self._on_net_master_toggled)
        layout.addWidget(self._net_master)

        self._net_table = QTableWidget(len(self._inter_nets), 4)
        self._net_table.setHorizontalHeaderLabels(
            ["", _("Net"), _("#tracks"), _("#vias")])
        self._net_table.verticalHeader().setVisible(False)
        header = self._net_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._net_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._net_checkboxes: list[QCheckBox] = []
        for row, n in enumerate(self._inter_nets):
            cb = QCheckBox()
            cb.setChecked(True)
            cb.toggled.connect(self._on_net_row_checkbox_toggled)
            self._net_table.setItem(row, 0, QTableWidgetItem(""))
            self._net_table.setCellWidget(row, 0, cb)
            self._net_checkboxes.append(cb)
            self._net_table.setItem(row, 1, QTableWidgetItem(_single_line(n.net)))
            self._net_table.setItem(row, 2, QTableWidgetItem(str(n.track_count)))
            self._net_table.setItem(row, 3, QTableWidgetItem(str(n.via_count)))
        layout.addWidget(self._net_table)
        return tab

    def _on_net_master_toggled(self, checked: bool) -> None:
        """Master checkbox: checked -> all rows checked, unchecked -> none.
        Blocking signals so the per-row handler doesn't fight the loop."""
        if self._net_checkboxes is None:
            return
        for cb in self._net_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)

    def _on_net_row_checkbox_toggled(self) -> None:
        """A row checkbox changed -> reflect the aggregate in the master
        (checked when all, unchecked when none, partial otherwise)."""
        if self._net_master is None or not self._net_checkboxes:
            return
        checked = sum(1 for cb in self._net_checkboxes if cb.isChecked())
        self._net_master.blockSignals(True)
        if checked == 0:
            self._net_master.setCheckState(Qt.CheckState.Unchecked)
        elif checked == len(self._net_checkboxes):
            self._net_master.setCheckState(Qt.CheckState.Checked)
        else:
            self._net_master.setCheckState(Qt.CheckState.PartiallyChecked)
        self._net_master.blockSignals(False)

    # ── Offset preview (tab 1 columns ΔX/ΔY) ──────────────────────────────

    def _refresh_offsets(self) -> None:
        """Fill the ΔX/ΔY preview columns: entity_positions[entity] minus the
        anchor base (via anchor_base_provider on the current anchor fields).
        Without positions/anchor base the columns stay "—" (no autopositioning
        preview — the tree build then just saves nodes without xy, live at
        apply)."""
        anchor = self.build_anchor()
        base = None
        if anchor is not None and self._anchor_base_provider is not None:
            try:
                base = self._anchor_base_provider(anchor)
            except Exception:  # noqa: BLE001 — a live read must never crash the dialog
                base = None
        for row, c in enumerate(self._clusters):
            dx = dy = None
            pos = self._entity_positions.get(c.entity_name) if c.entity_name else None
            if pos is not None and base is not None:
                dx = pos[0] - base[0]
                dy = pos[1] - base[1]
            self._table.setItem(
                row, 5, QTableWidgetItem(f"{dx:.3f}" if dx is not None else "—"))
            self._table.setItem(
                row, 6, QTableWidgetItem(f"{dy:.3f}" if dy is not None else "—"))

    # ── Results + validation ──────────────────────────────────────────────

    def selected_clusters(self) -> list[ReReadCluster]:
        """The clusters the user left checked, in dialog order."""
        return [c for c, cb in zip(self._clusters, self._checkboxes) if cb.isChecked()]

    def selected_nets(self) -> list[InterClusterNet]:
        """The inter-cluster nets the user left checked, in dialog order."""
        return [n for n, cb in zip(self._inter_nets, self._net_checkboxes)
                if cb.isChecked()]

    def tree_name(self) -> str:
        return self.tree_name_edit.text().strip()

    def _on_ok(self) -> None:
        name = self.tree_name()
        if not name:
            QMessageBox.warning(self, _("Extract tree"),
                                _("Tree name must not be empty."))
            return
        if name in self._existing_names:
            # Phase E (2026-09-01): entering an existing tree's name means
            # RE-EXTRACT — the tree is rebuilt from the current selection and
            # replaces the old one. Confirmed (No is the safe default).
            ret = QMessageBox.question(
                self, _("Extract tree"),
                _("A tree named {name!r} already exists. Update it from the "
                  "current selection?").format(name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        if not self.role_edit.currentText().strip():
            QMessageBox.warning(self, _("Extract tree"),
                                _("Role is required for the tree anchor."))
            return
        bad = [c.cluster for i, c in enumerate(self._clusters)
               if self._checkboxes[i].isChecked() and self._errors[i]]
        if bad:
            QMessageBox.warning(
                self, _("Extract tree"),
                _("Some selected clusters have no Entity or cell:\n{list}")
                .format(list="\n".join(bad)))
            return
        self.accept()
