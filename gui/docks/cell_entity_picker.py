# gui/docks/cell_entity_picker.py
"""The "Entity" dropdown of the cell page's Source tab AND the ONE rule behind it
(part 2 of plan_2026_10_05_entities_under_cells).

Two objects live here:

  * `EntityPicker` — the QComboBox over the cell's instance ADDRESSES. The rows
    and every rule about THEM live in the Qt-free gui/cell_entity_choice.py; this
    is the widget only, so the model's guards run without a QApplication.
  * `CellInstanceGate` — WHICH instance the page works with. The page owns the
    form (three instance fields plus "Fill from selection"), the gate owns the
    DECISION: an entity row writes its own (cluster, sheet) into those fields and
    makes them display-only, "Manual…" hands them back. It lives HERE, not in the
    page, so the >800-line cell_anchor_view keeps only the wiring (rule 45) —
    the same reason the address list itself lives in a module of its own.

One behaviour worth stating: filling the combo never emits `address_chosen`. The
page refills it on every form reload (a tab switch, a save), and a programmatic
fill must not be mistaken for the user picking a row. Only a user-driven change
emits.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox

from ..cell_entity_choice import default_index


class EntityPicker(QComboBox):
    """The dropdown of instance addresses of ONE cell."""

    # The CHOSEN row — an InstanceAddress from gui/cell_entity_choice.py.
    address_chosen = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._addresses: list = []
        self._filling = False
        self.currentIndexChanged.connect(self._on_index_changed)

    # ── Filling ───────────────────────────────────────────────────────────

    def addresses(self) -> list:
        """The rows currently offered, in order."""
        return list(self._addresses)

    def set_addresses(self, addresses, index: int = 0) -> None:
        """Replace the rows and select `index` (clamped) — never emitting the
        pick signal (see the module docstring)."""
        self._filling = True
        try:
            self._addresses = list(addresses)
            self.clear()
            for row in self._addresses:
                self.addItem(row.label)
            if self._addresses:
                self.setCurrentIndex(
                    max(0, min(index, len(self._addresses) - 1)))
        finally:
            self._filling = False

    def select_address(self, key) -> bool:
        """Select the row whose `.key` is `key`; True when it was found. Also
        silent — the caller holds the row already (see `current_address`)."""
        for i, row in enumerate(self._addresses):
            if row.key == key:
                self._filling = True
                try:
                    self.setCurrentIndex(i)
                finally:
                    self._filling = False
                return True
        return False

    # ── Reading ───────────────────────────────────────────────────────────

    def current_address(self):
        """The selected InstanceAddress, or None when the list is empty."""
        i = self.currentIndex()
        if 0 <= i < len(self._addresses):
            return self._addresses[i]
        return None

    def _on_index_changed(self, _index: int) -> None:
        if self._filling:
            return
        self.address_chosen.emit(self.current_address())


@dataclasses.dataclass
class InstanceFields:
    """The Source tab's three instance fields plus the identification button —
    the widgets whose instance the gate decides."""
    cluster: object
    sheet: object
    refs: object
    fill_button: object


class CellInstanceGate:
    """Which INSTANCE a cell page works with (п.1 / п.2 / п.4 of the plan).

    The gate keeps NO shadow copy of the instance: the row in force is the
    picker's own `current_address()`, and the values are the fields' own text.
    What it owns is the rule, and the three decisions that follow from it —
    which row is the address, when the fields are editable, and which refs the
    page's actions must use.

    All wiring comes in from the page: `choices` (this cell's addresses, from the
    part-1 index), `last_entity` (the name remembered for this cell), and two
    callbacks only the page can serve — `remember_pick` (state) and `on_applied`
    (reload what depends on the working fields).
    """

    def __init__(self, picker: EntityPicker, fields: InstanceFields, *,
                 choices: Callable, last_entity: Callable,
                 remember_pick: Callable, on_applied: Callable):
        self._picker = picker
        self._fields = fields
        self._choices = choices
        self._last_entity = last_entity
        self._remember_pick = remember_pick
        self._on_applied = on_applied
        self._opened_from: Optional[str] = None
        self._applying = False

    # ── What the page asks about the working instance ─────────────────────

    def row(self):
        """The ENTITY row in force, or None while the page is on "Manual…"."""
        row = self._picker.current_address()
        return row if row is not None and not row.is_manual else None

    def is_manual(self) -> bool:
        """True on the "Manual…" row / an empty dropdown — the hand-typed fields
        ARE the instance there."""
        return self.row() is None

    def active_refs(self):
        """The pins of the entity row in force, or None on "Manual…". An entity
        row with NO pins returns None too — which is why callers ask is_manual()
        FIRST: with an entity chosen the cell's remembered refs must never be
        used behind it (п.5)."""
        row = self.row()
        return row.refs if row is not None else None

    def is_applying(self) -> bool:
        """True while the gate writes the working fields — the "do not remember
        this as a manual Cluster/Sheet edit" flag the page consults (п.4)."""
        return self._applying

    # ── What the page calls ───────────────────────────────────────────────

    def set_opened_from(self, name) -> None:
        """The entity this open CAME from (п.4), if any."""
        self._opened_from = name or None

    def clear(self) -> None:
        """No cell loaded — nothing to choose and no row in force."""
        self._picker.set_addresses([], -1)

    def reload(self) -> None:
        """Refill from the current graph and APPLY the row it lands on.

        The row the user already picked is KEPT when it survives the refill — the
        page calls this on every form reload, and a tab switch must not undo a
        pick. Elsewhere the plan's default decides (п.4): the entity the page came
        FROM, else the last entity of this cell, else the first by name, else
        "Manual…" — a cell placed without an entity behaves exactly as before."""
        choices = self._choices()
        kept = next((i for i, row in enumerate(choices)
                     if row.key == getattr(self._picker.current_address(),
                                           "key", None)), -1)
        if kept < 0:
            kept = default_index(choices, opened_from=self._opened_from,
                                 last_entity=self._last_entity())
        self._picker.set_addresses(choices, kept)
        self._apply(remember=False)

    def choose(self) -> None:
        """The user picked a row (the widget's signal) — apply it and remember
        the choice BY NAME (п.4)."""
        self._apply(remember=True)

    def select_manual(self) -> None:
        """Put the dropdown on "Manual…" when an action has just made the
        hand-typed fields the instance (an identification, a "Read from
        selection") — the row must say what the page now does."""
        row = next((r for r in self._picker.addresses() if r.is_manual), None)
        if row is not None:
            self._picker.select_address(row.key)
        self._apply_fields_state()

    # ── The one apply path ────────────────────────────────────────────────

    def _apply(self, *, remember: bool) -> None:
        entity = self.row()
        # The flag spans the writes below: the page's combo handlers run from
        # inside them, and a nested fill clears the page's own _loading guard.
        self._applying = entity is not None
        try:
            if entity is not None:
                self._fields.cluster.setCurrentText(entity.cluster or "")
                self._fields.sheet.setCurrentText(entity.sheet or "")
        finally:
            self._applying = False
        if remember and entity is not None:
            self._remember_pick(entity.entity_name)
        self._apply_fields_state()
        # The row may be the one already in the fields (a re-apply after a form
        # reload), in which case no combo signal fired: the page reloads what
        # depends on them itself.
        self._on_applied()

    def _apply_fields_state(self) -> None:
        """п.1: with an ENTITY row the three instance fields are DISPLAY ONLY —
        the address is the entity's and is edited in the entity's own form. The
        "Manual…" row keeps them, and "Fill from selection" with them: an
        identification IS the manual path (п.2)."""
        manual = self.is_manual()
        for field in (self._fields.cluster, self._fields.sheet, self._fields.refs):
            field.setEnabled(manual)
        self._fields.fill_button.setEnabled(manual)
