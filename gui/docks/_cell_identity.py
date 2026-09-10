# gui/docks/_cell_identity.py
"""CellIdentityWidget — the shared (Sheet, Cluster, Name, Comment) identity
block (task V, prompt_2026_09_11_cell_page_merge.md).

The block existed twice: once on PlacerDock's "Source" tab (Cell mode) and once
— as a plain Sheet/Cluster working-context group — on CellAnchorView's
"Component" tab. That duplication is exactly what desynchronised the two pages
the task merges, so the block is extracted ONCE and plugged into both. The
precedent is AnchorOriginWidget (gui/docks/_anchor_origin.py), which replaced
four near-identical copies the same way.

Scope is UI + one GENERIC build(), never the callers' entry shapes: a
ClonePlacement writes `name`/`comment`/`sheet`/`cluster`, while CellAnchorView
uses the very same fields for its working context and the placement record it
resolves. Mapping the returned dict into each caller's own entry stays with the
caller — the division of labour AnchorOriginWidget already uses.

The sub-widgets are exposed as attributes (sheet_edit / cluster_edit /
name_edit / comment_edit) so each caller keeps wiring its own change signals;
that is also how AnchorOriginWidget's consumers work.
"""
from typing import Optional

from PyQt6.QtWidgets import (QComboBox, QFormLayout, QLabel, QLineEdit, QWidget)

from kicadstamp.i18n import _

from ._common import configure_searchable


class CellIdentityWidget(QWidget):
    """One QWidget: Sheet, Cluster, Name, Comment (+ an optional note row).

    Placeholders are constructor-overridable because the two callers word them
    differently (PlacerDock's Cluster field is the placement's own Cluster tag,
    CellAnchorView's is the working-context picker) — but the DEFAULTS are the
    PlacerDock wording, so plugging this in there changes nothing visible."""

    def __init__(self, parent: Optional[QWidget] = None,
                 sheet_placeholder: Optional[str] = None,
                 cluster_placeholder: Optional[str] = None,
                 name_placeholder: Optional[str] = None,
                 comment_placeholder: Optional[str] = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        self.sheet_edit = QComboBox()
        configure_searchable(self.sheet_edit)
        self.sheet_edit.lineEdit().setPlaceholderText(
            sheet_placeholder
            or _("sheet name (narrows ambiguous Cluster+Role when this cell is "
                 "cloned across reused sheets, optional)"))
        form.addRow(_("Sheet:"), self.sheet_edit)

        self.cluster_edit = QComboBox()
        configure_searchable(self.cluster_edit)
        self.cluster_edit.lineEdit().setPlaceholderText(
            cluster_placeholder
            or _("cluster tag (written onto the board's components)"))
        form.addRow(_("Cluster:"), self.cluster_edit)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            name_placeholder
            or _("same as Cluster unless changed (identity for Save/--only)"))
        form.addRow(_("Name:"), self.name_edit)

        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(
            comment_placeholder or _("optional free-form note"))
        form.addRow(_("Comment:"), self.comment_edit)

        # Optional explanatory line under the fields (callers that need one).
        self.note = QLabel()
        self.note.setWordWrap(True)
        form.addRow(self.note)

    def build(self) -> dict:
        """The identity fields as a small GENERIC dict — empty strings for
        blank fields, so each caller decides which ones it writes (the
        "don't write a redundant field" rule every existing save path uses)."""
        return {
            "sheet": self.sheet_edit.currentText().strip(),
            "cluster": self.cluster_edit.currentText().strip(),
            "name": self.name_edit.text().strip(),
            "comment": self.comment_edit.text().strip(),
        }

    def load(self, sheet: str = "", cluster: str = "",
             name: str = "", comment: str = "") -> None:
        """Programmatic fill. Callers that must not treat this as user input
        set their own _loading guard around the call (setCurrentText/setText
        still emit the combos' change signals)."""
        self.sheet_edit.setCurrentText(sheet or "")
        self.cluster_edit.setCurrentText(cluster or "")
        self.name_edit.setText(name or "")
        self.comment_edit.setText(comment or "")

    def set_record_fields_editable(self, editable: bool) -> None:
        """Name/Comment are editable only when a TOP-LEVEL clone_placements
        record backs them. A placement materialized from a tree has no record
        to write into — writing one would create a duplicate entry that
        shadows the tree (the mine this task explicitly guards against), so
        the two fields go read-only there. Cluster/Sheet stay editable: they
        are the working-context pickers."""
        self.name_edit.setReadOnly(not editable)
        self.comment_edit.setReadOnly(not editable)
