# gui/docks/extract_spoke_dialog.py
"""Tools -> "Extract spoke..." dialog — stage 5 of the spoke work
(plan_2026_09_17_spoke_s5_extract_spoke.md §2 Р1/Р3/Р4, §3 Door; design
design_2026_09_17_spoke_cell_editing.md §0/§1/§2.5).

NON-MODAL on purpose (Р1): every board read behind it runs on a worker thread,
and the user must be able to keep working with the board meanwhile. The widget
itself owns NO board access and NO config writes: it renders the records
gui/docks/extract_spoke.py's worker read produced, decides through the PURE
functions of kicadstamp/spoke_extraction.py, and hands the write request back to
its caller (DockHub), which runs the write on the worker as well (П3.2).

What the user sees, and why it is worded that way (design Р3 — never a silent
mode switch):
  * the spoke EVIDENCE: "role(s) C_OUT_BULK ×4 repeat inside cluster ... —
    extracting as a spoke"; when the roles are UNIQUE, the dialog says this is an
    ordinary cluster, points at "Extract cluster..." and locks OK;
  * the CELL: the new cell (with its Role/Pad origin) or an existing candidate —
    a candidate whose placed instance stands MIRRORED is offered but refused by
    name, because ManualSpoke has no mirror field and the spoke could never put
    the pair back where it is;
  * the CHAIN and its ANCHOR PAD; a net with no chain offers to create one
    anchored to the nearest foreign pad;
  * what the POOL will give this spoke (Ф8): exactly the selected pair (OK free),
    ANOTHER pair (OK needs the explicit "Components may swap" checkbox, and the
    owning spoke is named by chain and pad), or nothing at all (OK refused — no
    checkbox can make a config entry the pool can never fill true);
  * "Replace" whenever the chosen pad already carries a spoke.
No modal error boxes anywhere: everything lands in the status line here and in
the Log (П3.5).
"""
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QLabel, QLineEdit, QPushButton,
                             QVBoxLayout)

from kicadstamp.config import chain_effective_name
from kicadstamp.i18n import _
from kicadstamp.spoke_extraction import (
    OrderedPool,
    chain_assignment,
    find_pair_owner,
    new_chain_dict,
    pool_outcome,
    spoke_entry,
    spoke_offset,
)

from ..ui_utils import persist_dialog_size, restore_dialog_size
from ._common import ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE
from .extract_spoke import (CellChoice, ChainChoice, OrphanNet, SpokeContext,
                            spoke_identification)
from .reead import _slugify


class ExtractSpokeDialog(QDialog):
    """The one live instance DockHub keeps (the ToolsDialog pattern): reopening
    the menu entry raises it instead of building a second one."""

    # The two things the dialog may NOT do itself: read the board and write the
    # config. Both are emitted (door П3.1/П3.2 — the worker owns them).
    refresh_requested = pyqtSignal()
    write_requested = pyqtSignal()

    def __init__(self, main_window, connection=None, root_path=None, parent=None):
        super().__init__(parent if parent is not None else main_window)
        self.setWindowTitle(_("Extract spoke"))
        self.setObjectName("extract_spoke_dialog")
        self.setMinimumWidth(560)
        restore_dialog_size(self)
        persist_dialog_size(self)
        self._main_window = main_window
        self._connection = connection
        self._root_path = root_path
        self._data: Optional[SpokeContext] = None
        self._cfg = None
        self._rows: list = []
        self._ok_allowed = False
        # Programmatic combo refills must not look like user choices.
        self._loading = False

        layout = QVBoxLayout(self)
        self.criterion_label = QLabel()
        self.criterion_label.setWordWrap(True)
        layout.addWidget(self.criterion_label)

        cell_form = QFormLayout()
        self.cell_combo = QComboBox()
        self.cell_combo.currentIndexChanged.connect(self._on_cell_changed)
        cell_form.addRow(_("Cell:"), self.cell_combo)
        self.new_cell_edit = QLineEdit()
        self.new_cell_edit.textChanged.connect(lambda _t: self._recompute())
        cell_form.addRow(_("New cell name:"), self.new_cell_edit)
        self.origin_role_combo = QComboBox()
        self.origin_role_combo.currentIndexChanged.connect(self._on_origin_role_changed)
        cell_form.addRow(_("Origin role:"), self.origin_role_combo)
        self.origin_pad_combo = QComboBox()
        self.origin_pad_combo.currentIndexChanged.connect(lambda _i: self._recompute())
        cell_form.addRow(_("Origin pad:"), self.origin_pad_combo)
        layout.addLayout(cell_form)

        chain_form = QFormLayout()
        self.chain_combo = QComboBox()
        self.chain_combo.currentIndexChanged.connect(self._on_chain_changed)
        chain_form.addRow(_("Chain:"), self.chain_combo)
        self.pad_combo = QComboBox()
        self.pad_combo.currentIndexChanged.connect(lambda _i: self._recompute())
        chain_form.addRow(_("Anchor pad:"), self.pad_combo)
        layout.addLayout(chain_form)

        self.replace_check = QCheckBox()
        self.replace_check.toggled.connect(lambda _c: self._recompute())
        layout.addWidget(self.replace_check)
        self.swap_check = QCheckBox(_("Components may swap"))
        self.swap_check.toggled.connect(lambda _c: self._recompute())
        layout.addWidget(self.swap_check)

        self.outcome_label = QLabel()
        self.outcome_label.setWordWrap(True)
        layout.addWidget(self.outcome_label)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        self.refresh_button = QPushButton(_("Refresh"))
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        buttons.addButton(self.refresh_button,
                          QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)

        self._set_busy(False)

    # ── the caller's API (DockHub) ─────────────────────────────────────────

    def set_root_path(self, root_path) -> None:
        self._root_path = root_path

    def set_context(self, data: SpokeContext, cfg) -> None:
        """Render ONE worker read. Called on the UI thread, never by a worker."""
        self._data = data
        self._cfg = cfg
        self._loading = True
        try:
            self._refill()
        finally:
            self._loading = False
        self._recompute()

    def show_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text or "")
        self.status_label.setStyleSheet(_ERROR_STYLE if error else "")

    def show_success(self, text: str) -> None:
        self.status_label.setText(text or "")
        self.status_label.setStyleSheet(_SUCCESS_STYLE)

    @property
    def ok_allowed(self) -> bool:
        """True when OK may be pressed (nothing else to tick). Public because it
        IS the dialog's verdict: the guards assert it instead of poking at the
        button's enabled state."""
        return self._ok_allowed

    def identification(self):
        """The stage-1 identification of the pair this dialog was read from —
        DockHub remembers it after a successful write (Р8), so the cell editor
        opens on THIS pair and its frame/marker/Read work without a selection."""
        return spoke_identification(self._data)

    def write_request(self) -> Optional[dict]:
        """Everything the worker needs for OK, or None when OK is not allowed."""
        if not self._ok_allowed:
            return None
        row = self._current_row()
        cell = self._current_cell()
        pad = self._current_pad()
        if row is None or cell is None or pad is None:
            return None
        data = self._data
        if row["kind"] == "chain":
            chain_file, chain_entry = row["chain"].file, dict(row["chain"].entry)
        else:
            anchor = row["anchor"]
            chain_file = None
            chain_entry = new_chain_dict(
                row["net"], anchor_role=anchor.role,
                anchor_cluster=anchor.cluster, anchor_sheet=anchor.sheet)
        return {
            "root_path": self._root_path,
            "cell_name": self._cell_name(),
            "cell_is_new": bool(cell.is_new),
            "chain_file": chain_file,
            "chain_entry": chain_entry,
            "spoke": self._spoke(),
            "replace": self.replace_check.isChecked(),
            "origin_role": self.origin_role_combo.currentData() or None,
            "origin_pad": self.origin_pad_combo.currentData() or None,
            "expected_refs": tuple(data.selection.refs),
        }

    # ── filling the form ───────────────────────────────────────────────────

    def _slug(self) -> str:
        cluster = self._data.selection.cluster if self._data is not None else ""
        try:
            return _slugify(cluster) or "spoke"
        except Exception:  # noqa: BLE001 — a suggested name is never fatal
            return "spoke"

    def _refill(self) -> None:
        data = self._data
        self.cell_combo.clear()
        self.chain_combo.clear()
        self.origin_role_combo.clear()
        self.pad_combo.clear()
        self._rows = []
        if data is None:
            self.criterion_label.setText(_("reading the board…"))
            return
        if data.selection is None:
            self.criterion_label.setText("\n".join(data.problems)
                                         or _("nothing is selected on the board"))
            return
        cluster = data.selection.cluster
        if data.repeated:
            evidence = ", ".join("{role} ×{count}".format(role=role, count=count)
                                 for role, count in sorted(data.repeated.items()))
            self.criterion_label.setText(
                _("role(s) {roles} repeat inside cluster {cluster} — extracting "
                  "as a spoke").format(roles=evidence, cluster=cluster))
        else:
            self.criterion_label.setText(
                _("the roles are unique inside cluster {cluster} — this is an "
                  "ordinary cluster: use “Extract cluster…”").format(cluster=cluster))

        for choice in data.cells:
            if choice.is_new:
                self.cell_combo.addItem(
                    _("new: {name}").format(name=self._slug()), choice)
            else:
                label = choice.name
                if choice.problem:
                    label = _("{name} — unusable: {why}").format(
                        name=choice.name, why=choice.problem)
                self.cell_combo.addItem(label, choice)
        self.new_cell_edit.setText(self._slug())

        self.origin_role_combo.addItem(_("automatic (the cell's own origin)"), "")
        for component in data.pair:
            self.origin_role_combo.addItem(
                "{role} ({ref})".format(role=component.role, ref=component.ref),
                component.role)

        for chain in data.chains:
            self._rows.append({"kind": "chain", "chain": chain})
            self.chain_combo.addItem(
                _("{net} — chain {name} (anchor {ref})").format(
                    net=chain.net, name=chain.name, ref=chain.anchor_ref))
        for orphan in data.orphans:
            for anchor in self._anchors_of(orphan):
                self._rows.append({"kind": "new", "net": orphan.net,
                                   "anchor": anchor.pads[0],
                                   "pads": anchor.pads, "pools": orphan.pools})
                self.chain_combo.addItem(
                    _("new chain on {net} — anchor {ref} [{role}]").format(
                        net=orphan.net, ref=anchor.ref,
                        role=anchor.pads[0].role or "?"))
        self._refill_pads()
        self._refill_origin_pads()

    @staticmethod
    def _anchors_of(orphan: OrphanNet):
        """The nearest distinct anchor FOOTPRINTS of a chainless net (the user
        picks the component first, its pad second) — the same "nearest wins"
        rule _nearest_pad applies to an anchor's pads."""
        seen: dict = {}
        for pad in orphan.pads:
            seen.setdefault(pad.ref, {"ref": pad.ref, "pads": []})["pads"].append(pad)
        return list(seen.values())

    def _refill_pads(self) -> None:
        self.pad_combo.clear()
        row = self._current_row()
        if row is None:
            return
        if row["kind"] == "chain":
            chain = row["chain"]
            for point in chain.pads:
                occupant = next((s for s in chain.spokes
                                 if str(s.get("pad")) == point.pad), None)
                suffix = (" — " + _("spoke here (cell {cell})").format(
                    cell=occupant.get("cell"))) if occupant else ""
                self.pad_combo.addItem("pad {pad}{suffix}".format(
                    pad=point.pad, suffix=suffix), point.pad)
        else:
            for pad in row["pads"]:
                self.pad_combo.addItem("pad {pad}".format(pad=pad.pad), pad.pad)

    def _refill_origin_pads(self) -> None:
        self.origin_pad_combo.clear()
        self.origin_pad_combo.addItem(_("component centre"), "")
        role = self.origin_role_combo.currentData()
        if not role or self._data is None:
            return
        component = next((c for c in self._data.pair if c.role == role), None)
        for point in (component.pads if component else ()):
            self.origin_pad_combo.addItem(
                _("pad {pad}").format(pad=point.pad), point.pad)

    # ── current selection ─────────────────────────────────────────────────

    def _current_row(self) -> Optional[dict]:
        index = self.chain_combo.currentIndex()
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def _current_cell(self) -> Optional[CellChoice]:
        return self.cell_combo.currentData()

    def _current_pad(self) -> Optional[tuple]:
        """(pad number, x_mm, y_mm) of the chosen anchor pad, or None."""
        row = self._current_row()
        chosen = self.pad_combo.currentData()
        if row is None or chosen is None:
            return None
        if row["kind"] == "chain":
            for point in row["chain"].pads:
                if point.pad == chosen:
                    return (point.pad, point.x_mm, point.y_mm)
            return None
        for pad in row["pads"]:
            if pad.pad == chosen:
                return (pad.pad, pad.x_mm, pad.y_mm)
        return None

    def _cell_name(self) -> str:
        cell = self._current_cell()
        if cell is None:
            return ""
        return self.new_cell_edit.text().strip() if cell.is_new else cell.name

    def _cell_roles(self, cell: Optional[CellChoice]) -> set:
        if cell is None:
            return set()
        if cell.is_new:
            return set(self._data.selection.role_to_ref)
        loaded = (getattr(self._cfg, "cells", None) or {}).get(cell.name)
        return {slot.role for slot in (getattr(loaded, "components", None) or ())}

    def _cells_map(self, cell: Optional[CellChoice]) -> dict:
        """{name: cell-like} for the pool simulation — the real cells plus the
        NEW one, whose only role information the pair itself carries."""
        cells = dict(getattr(self._cfg, "cells", None) or {})
        if cell is not None and cell.is_new:
            roles = sorted(self._data.selection.role_to_ref)
            cells[self._cell_name()] = _CellStub(roles)
        return cells

    def _pools(self, net: str, orders: dict) -> dict:
        return {cluster: OrderedPool(order, net_name=net)
                for cluster, order in (orders or {}).items()}

    def _spoke(self) -> dict:
        pad = self._current_pad()
        return spoke_entry(pad[0], self._cell_name(), self._offset(pad),
                           cluster=self._data.selection.cluster)

    def _offset(self, pad: tuple):
        """What the spoke stores (Ф2/Р7): for an EXISTING cell the frame of its
        placed instance, for a NEW one the pair's own origin (the extraction uses
        the very same point, so the geometry is what the recipe promises)."""
        cell = self._current_cell()
        pad_mm = (pad[1], pad[2])
        if cell is not None and not cell.is_new and cell.frame is not None:
            frame = cell.frame
            return spoke_offset((frame.origin_x_mm, frame.origin_y_mm), pad_mm,
                                frame.rotation_deg)
        return spoke_offset(self._origin_mm(), pad_mm, 0.0)

    def _origin_mm(self) -> tuple:
        """The extraction's origin: the chosen Role's component centre, or the
        chosen pad of it — the SAME origin_component_role/pad the worker passes
        to extract_template_from_selection, so the stored shift is self-consistent."""
        role = self.origin_role_combo.currentData()
        pad = self.origin_pad_combo.currentData()
        component = next((c for c in self._data.pair if c.role == role), None)
        if component is None:
            component = self._data.pair[0] if self._data.pair else None
            return (component.x_mm, component.y_mm) if component else (0.0, 0.0)
        if pad:
            for point in component.pads:
                if point.pad == pad:
                    return (point.x_mm, point.y_mm)
        return (component.x_mm, component.y_mm)

    def _occupied(self, row: dict, pad: str):
        """The spoke already written for this pad, or None."""
        if row["kind"] != "chain":
            return None
        return next((s for s in row["chain"].spokes
                     if str(s.get("pad")) == pad), None)

    # ── the verdict (Ф8/Р3) ───────────────────────────────────────────────

    def _pair_owner(self):
        """Which chain/pad already holds the SELECTED pair (the CURRENT state,
        the new spoke not included): a pair can be claimed by another chain on
        the same net, and the user must hear which one before parts move."""
        data = self._data
        refs = set(data.selection.role_to_ref.values())
        current = []
        for row in self._rows:
            if row["kind"] != "chain":
                continue
            chain = row["chain"]
            assignment, _problem = chain_assignment(
                chain.spokes, self._cells_map(None), self._pools(chain.net, chain.pools),
                {point.pad for point in chain.pads})
            current.append((chain.name, assignment))
        return find_pair_owner(current, refs)

    def _outcome(self, row: dict, cell: CellChoice, pad: tuple):
        data = self._data
        cluster = data.selection.cluster
        refs = set(data.selection.role_to_ref.values())
        spoke = self._spoke()
        if row["kind"] == "chain":
            chain = row["chain"]
            spokes = [dict(s) for s in chain.spokes]
            index = next((i for i, s in enumerate(spokes)
                          if str(s.get("pad")) == pad[0]), None)
            if index is None:
                spokes.append(spoke)
            else:
                spokes[index] = spoke
            orders = chain.pools
            pad_numbers = {point.pad for point in chain.pads}
            chain_name = chain.name
            net = chain.net
        else:
            spokes = [spoke]
            orders = row["pools"]
            pad_numbers = {point.pad for point in row["pads"]}
            chain_name = net = row["net"]
        roles = self._cell_roles(cell)
        order = (orders or {}).get(cluster, {})
        capacity = min((len(order.get(role, [])) for role in roles), default=0)
        assignment, _problem = chain_assignment(
            spokes, self._cells_map(cell), self._pools(net, orders), pad_numbers)
        return pool_outcome(assignment, pad[0], refs, chain_name=chain_name,
                            owner=self._pair_owner(), cluster=cluster,
                            total_pairs=capacity, consumers=len(assignment))

    def _recompute(self) -> None:
        if self._loading:
            return
        self._ok_allowed = False
        data = self._data
        if data is None or data.selection is None:
            self._set_busy(False)
            return
        if not data.repeated:
            # Design Р3: an ordinary cluster — say WHICH tool does the job
            # instead of quietly writing a spoke nobody asked for.
            self.outcome_label.setText(
                _("nothing to extract as a spoke here — use “Extract cluster…” "
                  "for this selection"))
            self._set_busy(False)
            return
        row = self._current_row()
        cell = self._current_cell()
        pad = self._current_pad()
        self._update_cell_widgets(cell)
        if row is None or cell is None or pad is None:
            self.outcome_label.setText(_("pick a chain and a pad"))
            self._set_busy(False)
            return
        if cell.problem:
            self.outcome_label.setText(cell.problem)
            self._set_busy(False)
            return
        if not self._cell_name():
            self.outcome_label.setText(_("give the new cell a name"))
            self._set_busy(False)
            return

        occupant = self._occupied(row, pad[0])
        self.replace_check.setVisible(occupant is not None)
        if occupant is None:
            self.replace_check.setChecked(False)
        else:
            self.replace_check.setText(
                _("Replace the spoke on pad {pad} (cell {cell})").format(
                    pad=pad[0], cell=occupant.get("cell")))

        outcome = self._outcome(row, cell, pad)
        self.outcome_label.setText(outcome.message)
        needed = outcome.needs_swap_confirm
        self.swap_check.setVisible(needed)
        if not needed:
            self.swap_check.setChecked(False)

        replace_ok = occupant is None or self.replace_check.isChecked()
        if not replace_ok:
            # С7: an occupied pad is never overwritten silently.
            self.status_label.setText(
                _("pad {pad} already has a spoke — tick “Replace” to overwrite "
                  "it").format(pad=pad[0]))
            self._set_busy(False)
            return
        if not outcome.ok and not (needed and self.swap_check.isChecked()):
            self.status_label.setText("")
            self._set_busy(False)
            return
        self.status_label.setText("")
        self._set_busy(True)

    def _update_cell_widgets(self, cell: Optional[CellChoice]) -> None:
        is_new = bool(cell is not None and cell.is_new)
        self.new_cell_edit.setVisible(is_new)
        self.origin_role_combo.setVisible(is_new)
        self.origin_pad_combo.setVisible(is_new)
        self.new_cell_edit.setEnabled(is_new)
        self.origin_role_combo.setEnabled(is_new)
        self.origin_pad_combo.setEnabled(is_new and bool(self.origin_role_combo.currentData()))

    def _set_busy(self, ok: bool) -> None:
        self._ok_allowed = bool(ok)
        self._ok_button.setEnabled(bool(ok))

    # ── signals ───────────────────────────────────────────────────────────

    def _on_cell_changed(self, _index: int) -> None:
        self._recompute()

    def _on_chain_changed(self, _index: int) -> None:
        self._loading = True
        try:
            self._refill_pads()
        finally:
            self._loading = False
        self._recompute()

    def _on_origin_role_changed(self, _index: int) -> None:
        self._loading = True
        try:
            self._refill_origin_pads()
        finally:
            self._loading = False
        self._recompute()

    def _on_ok(self) -> None:
        """OK does NOT close: the write runs on the worker, and the dialog stays
        open to report a refusal (П3.5 — no modal error boxes)."""
        if self._ok_allowed:
            self.write_requested.emit()


class _CellStub:
    """The role list of a cell that does not exist yet — all the pool simulation
    needs of a cell (its component roles)."""

    def __init__(self, roles):
        self.components = [type("_Slot", (), {"role": role})() for role in roles]
