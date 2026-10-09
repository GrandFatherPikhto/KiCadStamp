# gui/docks/create_entity.py
"""
CreateEntityDialog — the small form behind the Config tree's context-menu
item "Create entity" (plan_2026_09_20_create_entity_menu.md, Т1/Т2/Т2а;
reworked in plan_2026_10_09_cells_and_entities, part 3, Т3.1/Т3.2).

ONE dialog serves both sources — a cells: leaf ("cell") and an imprints: leaf
("imprint"):

  - Name — pre-filled, editable. T3.2: uniqueness per include graph is enforced
    WITHOUT a QMessageBox (rule 43): a conflict paints a RED line in the dialog
    and disables OK, exactly like gui/docks/add_entities.py. The default name
    FOLLOWS the chosen instance (its cluster slug, via the project's ONE
    default-name rule, add_entities.default_entity_names) until the user edits
    the field; once edited it is never overwritten. For an imprint the default
    is the record's own name (as it always was).
  - Instance — cells: ONLY (Т3.1). A single EDITABLE combobox carries the whole
    (Cluster, sheet) pair as one line "CLUSTER — sheet", because two
    independent boxes would let the user pick a pair that is not on the board.
    The rows come from the project's ONE rule, ``instance_candidates`` (part 1)
    — this dialog never restates it:
      * a FREE fitting instance is a selectable row (fitting ones on top);
      * a TAKEN fitting instance ("already: <entity>") is shown GREYED and is
        NOT selectable — offered so the user sees the pair is accounted for,
        never offered to be made twice;
      * a NON-fitting instance is NOT a row at all: it is counted in ONE grey
        line under the combobox ("K other instances lack roles", reasons in the
        tooltip) — counted, never silently lost.
    Manual entry is typing into the combobox (there is NO "Manual…" row): the
    line is split back by ``instance_candidates.parse_instance_label``, so the
    phrasing the rows use and the phrasing the user may retype can never drift.
    A pair that is not on the board is a YELLOW line "not on the board — fit
    not checked" — a WARNING, never a refusal.
  - Sheet — imprints: ONLY, and optional. An imprint-based Entity carries no
    cluster at all: it is FATAL at load (config/models.py ~706), so the Cluster
    field is NOT built for an imprint (Т2а). The sheet is the TARGET sheet of
    twin-resolution (design §5.2); it is an EDITABLE combobox over the project's
    own ``ctx.sheet_names`` (the caller hands the names in), and free text is
    still accepted.

SNAPSHOT. The candidates are built by the CALLER inside the door's ``on_ready``
(``DockHub.refresh_snapshot_and_push``, see create_entity_flow) — the dialog
itself owns NO board and NO filesystem. ``snapshot_available`` tells it whether
that open actually had a board snapshot: with none it still OPENS (creating an
entity without a board is legitimate, Т4/С7), the combobox stays empty and
editable, and the YELLOW line says "no board snapshot — fit not checked".

Style is copied from the neighbouring dialogs, NOT invented here (Т2а): the
restore/persist size pair, a QFormLayout, a QDialogButtonBox, and — Т3.2 — the
red-hint-line-and-OK-gate shape of gui/docks/add_entities.py.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QLabel, QLineEdit, QVBoxLayout)

from kicadstamp.i18n import _

from ..ui_utils import persist_dialog_size, restore_dialog_size
from .add_entities import default_entity_names
from .instance_candidates import (
    INSTANCE_LABEL_SEP, instances_others_line, instances_others_tooltip,
    parse_instance_label)

# The grey used for the "taken" rows and the counter line, and the yellow of the
# fit warning — copied from the neighbouring pickers (change_cell.py), where the
# same roles are painted the same way; the red is add_entities.py's own name
# conflict colour (Т3.2).
_GREY = QColor("#888888")
_YELLOW_QSS = "color: #a60;"
_GREY_QSS = "color: #888888;"
_RED_QSS = "color: #a00000;"


class CreateEntityDialog(QDialog):
    """Name + (Instance | Sheet) for a new entities: record. Outcome via
    result_data() after Accepted — the pattern every other dialog of this
    project follows (see instances_dialog.py).

    source_kind — "cell" or "imprint"; source_name — the leaf's own name, used
    as the default of Name when there is no instance to derive one from;
    existing_names — every entities: name already used somewhere in the include:
    graph (the caller collects them; this dialog has no filesystem access).

    candidates — the cell's instances as ``instance_candidates`` records (both
    fitting and not, taken flagged); ignored for an imprint. sheet_names — the
    project's own sheet-name list, for the imprint's Sheet combobox.
    snapshot_available — whether the caller's door open actually had a board
    snapshot; False keeps the dialog open with the yellow "no board snapshot"
    line and an empty, editable combobox."""

    def __init__(self, parent, source_kind: str, source_name: str,
                 existing_names, *, candidates=(), sheet_names=(),
                 snapshot_available: bool = False):
        super().__init__(parent)
        self._source_kind = source_kind
        self._source_name = str(source_name or "")
        self._existing_names = set(existing_names or ())
        self._snapshot_available = bool(snapshot_available)
        self._candidates = list(candidates or ())
        # The fitting instances, split into the FREE rows (selectable) and the
        # TAKEN ones (greyed, non-selectable); the NON-fitting ones are not rows
        # at all, only the count in the grey line.
        self._fitting = [c for c in self._candidates if c.fits]
        self._free = [c for c in self._fitting if not c.taken]
        self._taken = [c for c in self._fitting if c.taken]
        self._others = [c for c in self._candidates if not c.fits]
        # {candidate -> default name}, the project's ONE rule (Т3.2).
        self._defaults = default_entity_names(self._fitting)
        # The name field is only ever overwritten while the user has NOT typed
        # into it (Т3.2: "правил — не трогать"). textEdited fires on the user
        # alone, never on a programmatic setText.
        self._name_edited = False

        self.setWindowTitle(_("Create entity"))
        self.setMinimumWidth(420)
        # Same remembered-size pair as every neighbouring modal (Т2а: "своего
        # стиля не изобретать"). resize is capped by the screen inside
        # restore_dialog_size, so a size remembered on a big monitor can
        # never hang a small one off the display.
        restore_dialog_size(self)
        persist_dialog_size(self)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # ── Name ────────────────────────────────────────────────────────
        self._name_edit = QLineEdit(self._source_name)
        # Object names are stable handles for tests/automation — the VISIBLE
        # labels are translated (see gui/docks/instances_dialog.py's own
        # inherit_anchor_button for the same convention).
        self._name_edit.setObjectName("entity_name_edit")
        form.addRow(_("Name:"), self._name_edit)

        # ── Instance (cells) / Sheet (imprints) ─────────────────────────
        self._instance_combo = None
        self._sheet_combo = None
        if self._source_kind == "cell":
            self._instance_combo = QComboBox()
            self._instance_combo.setObjectName("create_entity_instance_combo")
            self._instance_combo.setEditable(True)
            self._instance_combo.setInsertPolicy(
                QComboBox.InsertPolicy.NoInsert)
            self._instance_combo.lineEdit().setPlaceholderText(
                _("CLUSTER — sheet; type one that is not listed"))
            form.addRow(_("Instance:"), self._instance_combo)
        else:
            self._sheet_combo = QComboBox()
            self._sheet_combo.setObjectName("create_entity_sheet_combo")
            self._sheet_combo.setEditable(True)
            self._sheet_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self._sheet_combo.lineEdit().setPlaceholderText(
                _("optional — the target sheet of twin-resolution"))
            for name in sheet_names or ():
                self._sheet_combo.addItem(str(name))
            form.addRow(_("Sheet:"), self._sheet_combo)

        layout.addLayout(form)

        # The YELLOW fit line — the pair is not on the board / there was no
        # snapshot. A HINT, never a gate (Т3.1: "не запрет").
        self._fit_warning = QLabel("")
        self._fit_warning.setObjectName("create_entity_fit_warning")
        self._fit_warning.setWordWrap(True)
        self._fit_warning.setStyleSheet(_YELLOW_QSS)
        layout.addWidget(self._fit_warning)

        # The ONE grey line under the instance combobox: how many instances were
        # left out (they do not fit), reasons in the tooltip.
        self._others_label = QLabel("")
        self._others_label.setObjectName("create_entity_others")
        self._others_label.setWordWrap(True)
        self._others_label.setStyleSheet(_GREY_QSS)
        layout.addWidget(self._others_label)

        # The RED name line — an empty / already-used name. A GATE, unlike the
        # fit line: OK is disabled while it is shown (Т3.2, no QMessageBox).
        self._name_hint = QLabel("")
        self._name_hint.setObjectName("create_entity_name_hint")
        self._name_hint.setWordWrap(True)
        self._name_hint.setStyleSheet(_RED_QSS)
        layout.addWidget(self._name_hint)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                         | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        if self._instance_combo is not None:
            self._fill_instances()
            self._others_label.setText(instances_others_line(self._others))
            self._others_label.setToolTip(
                instances_others_tooltip(self._others))
            self._others_label.setVisible(bool(self._others))
            self._instance_combo.currentTextChanged.connect(
                self._on_instance_changed)
        else:
            self._others_label.setVisible(False)
            self._fit_warning.setVisible(False)

        # The red line follows EVERY text change — a programmatic setText
        # included, so the gate is visible without an accept; textEdited is the
        # USER-only signal that freezes the derived default name (Т3.2).
        self._name_edit.textChanged.connect(lambda *_: self._revalidate())
        self._name_edit.textEdited.connect(self._on_name_edited)

        self._apply_default_name()
        self._refresh_fit_warning()
        self._revalidate()

    # ── Rows ────────────────────────────────────────────────────────────

    def _fill_instances(self) -> None:
        """Fill the instance combobox: the FREE fitting instances first (the row
        order ``instance_candidates`` already returns), then the TAKEN ones
        greyed and non-selectable. Preselect a free row ONLY when there is
        exactly one — two or more leave the box empty (the user must choose)."""
        combo = self._instance_combo
        for cand in self._free:
            combo.addItem(cand.label, cand)
        for cand in self._taken:
            combo.addItem(self._taken_label(cand), cand)
        model = combo.model()
        for i in range(len(self._free), combo.count()):
            item = model.item(i)
            if item is not None:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setForeground(QBrush(_GREY))
        if len(self._free) == 1:
            combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(-1)

    @staticmethod
    def _taken_label(cand) -> str:
        """The grey row of a taken instance: its own pair plus WHO has it
        ("already: <entity>") — the same wording add_entities uses."""
        return "{label}{sep}{already}".format(
            label=cand.label, sep=INSTANCE_LABEL_SEP,
            already=_("already: {name}").format(name=cand.entity_name))

    # ── Instance → fit warning and default name ─────────────────────────

    def _current_pair(self):
        """The (Cluster, sheet) the instance combobox currently spells — the
        ONE split of a hand-typed line (parse_instance_label)."""
        if self._instance_combo is None:
            return None, None
        return parse_instance_label(self._instance_combo.currentText())

    def _on_instance_changed(self, *_args) -> None:
        self._apply_default_name()
        self._refresh_fit_warning()
        self._revalidate()

    def _apply_default_name(self) -> None:
        """Derive the Name from the chosen instance — UNTIL the user types into
        it (Т3.2). The imprint branch never touches it (its default is the
        record's own name)."""
        if self._instance_combo is None or self._name_edited:
            return
        cluster, sheet = self._current_pair()
        self._name_edit.setText(self._default_name_for(cluster, sheet))

    def _default_name_for(self, cluster, sheet) -> str:
        """The ONE default-name rule (add_entities.default_entity_names): the
        Cluster tag in lower case, plus the sheet when the SAME cluster stands
        on several sheets of the fitting set. With no cluster at all the source's
        own name is kept (the previous pre-fill)."""
        if not cluster:
            return self._source_name
        base = None
        for cand in self._fitting:
            if cand.cluster == cluster and cand.sheet == sheet:
                base = self._defaults.get(cand)
                break
        if not base:
            from .tree_from_selection import cluster_cell_name
            base = cluster_cell_name(cluster)
        return base

    def _refresh_fit_warning(self) -> None:
        """Show the YELLOW line while the typed pair was NOT checked: no
        snapshot at all ("no board snapshot"), or a pair that is not among the
        board's instances ("not on the board"). Hidden for a pair the board
        really carries."""
        if self._instance_combo is None:
            return
        if not self._snapshot_available:
            self._fit_warning.setText(_("no board snapshot — fit not checked"))
            self._fit_warning.setVisible(True)
            return
        cluster, sheet = self._current_pair()
        if not cluster:
            self._fit_warning.setText("")
            self._fit_warning.setVisible(False)
            return
        if any((c.cluster, c.sheet) == (cluster, sheet)
               for c in self._candidates):
            self._fit_warning.setText("")
            self._fit_warning.setVisible(False)
        else:
            self._fit_warning.setText(_("not on the board — fit not checked"))
            self._fit_warning.setVisible(True)

    # ── Result ──────────────────────────────────────────────────────────

    def result_data(self):
        """(name, cluster, sheet) as the CALLER stores them.

        For a cell the pair comes from the instance combobox line (blank ->
        (None, None)); for an imprint the cluster is None — not an empty string,
        and not a key: the record must not carry cluster: at all on that branch
        (С3, models.py ~706 fatals on it) — and the sheet is the Sheet combobox
        line. An empty Sheet is likewise None, so the caller can omit the key
        rather than persist an empty string."""
        name = self._name_edit.text().strip()
        if self._instance_combo is not None:
            cluster, sheet = self._current_pair()
        else:
            cluster = None
            sheet = None
            if self._sheet_combo is not None:
                sheet = self._sheet_combo.currentText().strip() or None
        return name, cluster, sheet

    # ── Validation (red line + OK gate; no QMessageBox, rule 43) ─────────

    def _name_problem(self) -> str:
        """The reason the name cannot be written — empty, or already carried by
        another entities: record (С4: the caller re-checks against the graph
        once the dialog is closed; this is the immediate one)."""
        name = self._name_edit.text().strip()
        if not name:
            return _("Name is required.")
        if name in self._existing_names:
            return _("An entity named {name!r} already exists.").format(name=name)
        return ""

    def _revalidate(self) -> None:
        problem = self._name_problem()
        self._name_hint.setText(problem)
        self._name_hint.setVisible(bool(problem))
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(not problem)

    def _on_name_edited(self, _text: str) -> None:
        # The user took the name into their own hands: stop deriving it (Т3.2).
        self._name_edited = True
        self._revalidate()

    def accept(self) -> None:
        """OK — commit point. Gated on the name alone: the fit warning is a
        hint, never a gate (Т3.1), but an empty / taken name disables OK and the
        red line says why (Т3.2)."""
        self._revalidate()
        if self._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()
