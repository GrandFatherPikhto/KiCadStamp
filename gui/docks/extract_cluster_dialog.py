# gui/docks/extract_cluster_dialog.py
"""Tools -> "Extract cluster..." dialog (2026-09-03, plan
extract_cluster_entity).

A deliberately small modal dialog (scale of TreeInstancesDialog, NOT the
retired 3-tab Extract dock): pick ONE fully-selected Cluster from the current
selection and give it a flat Entity — the Cell is generated from the cluster's
own selection when it doesn't exist yet. NO tree node, NO anchor, NO
net_traces, NO net-alias UI — the Entity is placed later by any existing
mechanism (a manual tree node, a tree_instances template, ...).

The (cluster, sheet)-matched Entity, when it already exists in cfg.entities,
wins: the name field is READ-ONLY and OK just "reuses" it (nothing is created —
creating a second Entity for the same (cluster, sheet) would duplicate it). For
a NEW Entity the auto-derived name (resolve_cluster_entity) is prefilled and
editable, with the project's usual duplicate-name validation on OK.

Thin dialog: all resolution/validation logic is pure code in
gui/docks/tree_from_selection.py (resolve_cluster_entity /
create_cell_and_entity_for_cluster) — this file only renders the single-cluster
choice and collects the chosen (cluster, entity name). It writes NOTHING
itself; the caller (DockHub.extract_cluster_from_selection) owns the
backup_file/read_data/write_data round-trip.
"""
from typing import Optional

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QVBoxLayout)

from kicadstamp.i18n import _

from .reead import ReReadCluster
from .tree_from_selection import resolve_cluster_entity


def _cluster_item_label(c: ReReadCluster) -> str:
    """A single-line list label for one fully-selected Cluster instance —
    Cluster tag + sheet (same disambiguation TreeFromSelectionDialog uses)."""
    return f"{c.cluster} ({c.sheet or '-'})"


class ExtractClusterDialog(QDialog):
    """Modal: pick one fully-selected Cluster -> one flat Entity (+ its Cell if
    missing). The caller already detected the fully-selected clusters (DockHub,
    reead.fully_selected_clusters + the same "\n" filter as "Extract tree...").
    """

    def __init__(self, parent, clusters: list[ReReadCluster], cfg):
        super().__init__(parent)
        self.setWindowTitle(_("Extract cluster"))
        self.setObjectName("extract_cluster_dialog")
        self.setMinimumWidth(440)
        self._clusters = list(clusters)
        self._cfg = cfg
        self._existing = False

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(_("Fully selected Cluster:")))
        self.cluster_list = QListWidget()
        self.cluster_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for c in self._clusters:
            QListWidgetItem(_cluster_item_label(c), self.cluster_list)
        if self._clusters:
            self.cluster_list.setCurrentRow(0)
        self.cluster_list.currentRowChanged.connect(
            lambda _row: self._refresh_entity_name())
        layout.addWidget(self.cluster_list)

        name_row = QVBoxLayout()
        name_label = QLabel(_("Entity name:"))
        name_row.addWidget(name_label)
        self.entity_name_edit = QLineEdit()
        name_row.addWidget(self.entity_name_edit)
        layout.addLayout(name_row)

        # Status/hint line under the name field — "already exists, will be
        # reused" (read-only field) vs "a new Entity will be created" hint.
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_entity_name()

    # ── state ─────────────────────────────────────────────────────────────

    @property
    def existing(self) -> bool:
        """True when the currently selected cluster's Entity already exists in
        cfg.entities (the name field is read-only and OK reuses it)."""
        return self._existing

    def selected_cluster(self) -> Optional[ReReadCluster]:
        """The chosen cluster, or None when nothing is selected."""
        row = self.cluster_list.currentRow()
        if row < 0 or row >= len(self._clusters):
            return None
        return self._clusters[row]

    def entity_name(self) -> str:
        """The chosen Entity name (possibly edited by the user)."""
        return self.entity_name_edit.text().strip()

    # ── prefill ───────────────────────────────────────────────────────────

    def _refresh_entity_name(self) -> None:
        """Prefill the Entity-name field for the currently selected cluster:
        existing Entity -> read-only (reuse, never a duplicate); new Entity ->
        editable auto-derived name (resolve_cluster_entity)."""
        c = self.selected_cluster()
        if c is None:
            self._existing = False
            self.entity_name_edit.clear()
            self.entity_name_edit.setReadOnly(False)
            self.status_label.setText(_("Select the Cluster to extract first."))
            return
        entity_name, cell_name, is_new = resolve_cluster_entity(c, self._cfg)
        self._existing = not is_new
        self.entity_name_edit.setText(entity_name)
        self.entity_name_edit.setReadOnly(self._existing)
        if self._existing:
            self.status_label.setText(
                _("This Entity already exists for this Cluster + sheet — it will "
                  "be reused, nothing new will be created."))
        else:
            self.status_label.setText(
                _("A new Entity will be created with this name (its cell "
                  "{cell!r} is generated from the cluster's selection when "
                  "missing).").format(cell=cell_name))

    # ── validation + accept ───────────────────────────────────────────────

    def _validate(self) -> Optional[str]:
        """A human-readable problem with the current choice, or None when valid."""
        c = self.selected_cluster()
        if c is None:
            return _("Select the Cluster to extract first.")
        name = self.entity_name()
        if not name:
            return _("Entity name must not be empty.")
        if not self._existing and any(e.name == name for e in self._cfg.entities):
            return _("An Entity named {name!r} already exists.").format(name=name)
        return None

    def _on_ok(self) -> None:
        """Validate + accept (the caller persists the result on Accepted)."""
        problem = self._validate()
        if problem is not None:
            QMessageBox.warning(self, self.windowTitle(), problem)
            return
        self.accept()
