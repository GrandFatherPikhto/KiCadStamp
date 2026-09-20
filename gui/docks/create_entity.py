# gui/docks/create_entity.py
"""
CreateEntityDialog — the small form behind the Config tree's context-menu
item "Create entity" (plan_2026_09_20_create_entity_menu.md, Т1/Т2/Т2а).

Deliberately minimal, and deliberately blind to everything except three
strings: no board access, no tree, no source record touched (Т4/С6/С7). It
builds a dict of three fields and hands it back; the caller (DockHub) is the
one that reads the include graph and writes the record.

ONE dialog serves both sources — a cells: leaf ("cell") and an imprints: leaf
("imprint"). The only differences between them are which source field the
created record fills (cell: vs imprint:) and whether the Cluster row exists
at all:

  - Name — pre-filled with the source's own name, editable. Uniqueness per
    include graph is enforced twice: here against the caller-supplied set
    (cheap, immediate feedback) and again by the caller with the graph in
    hand (С4, authoritative — the graph may have changed while the dialog was
    open, and this dialog owns no filesystem access).
  - Cluster — cells: ONLY. An imprint-based Entity carries no cluster: at
    all: it is FATAL at load (config/models.py ~706: a recorded snapshot
    already has its literal refs and literal nets, so cluster: has no
    meaning there). The row is therefore NOT built for an imprint, rather
    than built-and-ignored (Т2а: "форма обязана их не предлагать вовсе").
    Blank IS allowed on purpose for a cell: an Entity may exist "not placed"
    without a tag (Entity.cluster's own docstring in models.py), but then it
    cannot be cloned by cluster — the dialog says so in a warning LINE, it
    never forbids it (Т2а: "Предупреждение, не запрет").
  - Sheet — optional for both. For a cell it narrows role resolution, for an
    imprint it is the TARGET sheet of twin-resolution (design §5.2).

Style is copied from the neighbouring modal dialogs, NOT invented here (Т2а):
the restore/persist size pair, a QFormLayout, a QDialogButtonBox, validation
through QMessageBox.warning + a False from the commit step — the same shape
gui/docks/instances_dialog.py uses.
"""
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                              QLineEdit, QMessageBox, QVBoxLayout)

from kicadstamp.i18n import _

from ..ui_utils import persist_dialog_size, restore_dialog_size


class CreateEntityDialog(QDialog):
    """Name/Cluster/Sheet for a new entities: record. Outcome via
    result_data() after Accepted — the pattern every other dialog of this
    project follows (see instances_dialog.py).

    source_kind — "cell" or "imprint"; source_name — the leaf's own name,
    used as the default of Name; existing_names — every entities: name
    already used somewhere in the include: graph (the caller collects them;
    this dialog has no filesystem access)."""

    def __init__(self, parent, source_kind: str, source_name: str,
                 existing_names):
        super().__init__(parent)
        self._source_kind = source_kind
        self._existing_names = set(existing_names or ())
        self.setWindowTitle(_("Create entity"))
        self.setMinimumWidth(380)
        # Same remembered-size pair as every neighbouring modal (Т2а: "своего
        # стиля не изобретать"). resize is capped by the screen inside
        # restore_dialog_size, so a size remembered on a big monitor can
        # never hang a small one off the display.
        restore_dialog_size(self)
        persist_dialog_size(self)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # ── Name ────────────────────────────────────────────────────────
        self._name_edit = QLineEdit(str(source_name or ""))
        # Object names are stable handles for tests/automation — the VISIBLE
        # labels are translated (see gui/docks/instances_dialog.py's own
        # inherit_anchor_button for the same convention).
        self._name_edit.setObjectName("entity_name_edit")
        form.addRow(_("Name:"), self._name_edit)

        # ── Cluster — cells: only, absent for an imprint (Т2а) ─────────
        self._cluster_edit = None
        self._cluster_warning = None
        if self._source_kind == "cell":
            self._cluster_edit = QLineEdit()
            self._cluster_edit.setObjectName("entity_cluster_edit")
            self._cluster_edit.setPlaceholderText(
                _("optional — a cell is cloned by its cluster"))
            form.addRow(_("Cluster:"), self._cluster_edit)

        # ── Sheet — optional for both ───────────────────────────────────
        self._sheet_edit = QLineEdit()
        self._sheet_edit.setObjectName("entity_sheet_edit")
        self._sheet_edit.setPlaceholderText(
            _("optional — narrows role resolution / the twin target"))
        form.addRow(_("Sheet:"), self._sheet_edit)

        layout.addLayout(form)

        # The empty-cluster warning lives OUTSIDE the form as a spanning row:
        # a QFormLayout row built from a string label cannot be shown/hidden
        # on its own, and this line appears and disappears while the user
        # types (same reason entity_page.py builds the whole Imprint row from
        # widgets). It is a HINT, never a gate — Т2а.
        if self._cluster_edit is not None:
            self._cluster_warning = QLabel("")
            self._cluster_warning.setObjectName("entity_cluster_warning")
            self._cluster_warning.setWordWrap(True)
            self._cluster_warning.setVisible(False)
            layout.addWidget(self._cluster_warning)
            self._cluster_edit.textChanged.connect(self._refresh_cluster_warning)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── The warning line (cells only) ───────────────────────────────────

    def _refresh_cluster_warning(self, text: str) -> None:
        """Show the line while the Cluster field is blank, hide it as soon as
        it carries anything. Never touches the OK button's enabled state — the
        whole point of the warning-not-refusal rule."""
        if self._cluster_warning is None:
            return
        if text.strip():
            self._cluster_warning.setVisible(False)
            self._cluster_warning.setText("")
        else:
            self._cluster_warning.setText(_(
                "No cluster — the entity is created without a tag, so it "
                "cannot be cloned by cluster."))
            self._cluster_warning.setVisible(True)

    # ── Result ──────────────────────────────────────────────────────────

    def result_data(self):
        """(name, cluster, sheet) as the CALLER stores them.

        cluster is None for an imprint — not an empty string, and not a key:
        the record must not carry cluster: at all on that branch (С3, models.py
        ~706 fatals on it). An empty Sheet is likewise None, so the caller can
        omit the key rather than persist an empty string."""
        name = self._name_edit.text().strip()
        cluster = (self._cluster_edit.text().strip() or None
                   if self._cluster_edit is not None else None)
        sheet = self._sheet_edit.text().strip() or None
        return name, cluster, sheet

    # ── Validation ──────────────────────────────────────────────────────

    def _validate(self) -> bool:
        """Both gates are MESSAGES, nothing fails silently: an empty name, and
        a name already carried by another entities: record (С4 — the caller
        re-checks against the graph once the dialog is closed; this check is
        the immediate one, on the set it was handed)."""
        name, _cluster, _sheet = self.result_data()
        if not name:
            QMessageBox.warning(self, self.windowTitle(),
                                _("Name is required."))
            return False
        if name in self._existing_names:
            QMessageBox.warning(
                self, self.windowTitle(),
                _("An entity named {name!r} already exists.").format(name=name))
            return False
        return True

    def accept(self) -> None:
        """OK — commit point. The empty-cluster hint is surfaced BEFORE the
        accept is allowed through, so a user who pressed OK without ever
        leaving the field still sees what they are about to make (Т2а:
        предупреждение, не запрет — the accept still goes through)."""
        if (self._cluster_edit is not None
                and not self._cluster_edit.text().strip()):
            self._refresh_cluster_warning("")
        if self._validate():
            super().accept()
