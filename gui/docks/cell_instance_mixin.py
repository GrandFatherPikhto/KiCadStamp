# gui/docks/cell_instance_mixin.py
"""The cell page's plumbing for WHICH cell and WHICH instance it works on — the
Source tab's "Entity" dropdown, the three fields that address an instance, the
"Identified instance" box they live in, and the loaded cell entry they read.

Part 2 of plan_2026_10_05_entities_under_cells; extracted from
cell_anchor_view.py for the reason gui/docks/entity_tree.py was extracted from
config_tree.py (rule 45: a giant only shrinks — the page keeps the wiring, the
plumbing lives in a module of its own).

Three owners, ONE subject (п.1 of the plan):

  * gui/cell_entity_choice.py — WHAT an address is: the row list, the labels, the
    last choice, the working-instance store. Qt-free, so its guards run with no
    QApplication;
  * gui/docks/cell_entity_picker.py — the WIDGET and the rule "a pick IS the
    instance" (CellInstanceGate), including the "a pick is not a manual
    Cluster/Sheet edit" flag;
  * here — the page's half: what the rows are for THIS cell, what the page can
    answer about the last pick, and the one-liners the page's own actions call
    (`open_cell`, `reload_instance`, `refs_field_is_pinned`,
    `refuse_foreign_selection`, `announce_cell_write`,
    `instance_fields_are_being_written`, `active_refs`).

The host must provide: `_root_path`, `_cell_name`, `_file_path`,
`_cluster_combo`, `_sheet_combo`, `_reload_identity()`, `_on_fill_from_selection`,
`_on_refs_edited` and `entity_index_provider`. Everything else lives here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton)

from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ..cell_edit_context import remembered_cell_refs
from ..cell_entity_choice import (
    SOURCE_ENTITY,
    address_matches_selection,
    build_choices,
    entity_addresses,
    not_the_entity_line,
    remembered_last_entity,
    remember_last_entity,
    remember_working_instance,
    write_applies_line,
)
from ._common import ERROR_STYLE, SUCCESS_STYLE, show_message
from .cell_entity_picker import CellInstanceGate, EntityPicker, InstanceFields
from .rename import find_dict_entry_file, read_data as _read_data

logger = logging.getLogger(__name__)


# ── The "Refs" line of the instance (2026-09-17, stage 1 of the spoke work) ──
#
# The refs are the ONE thing that names a SPOKE cell's instance: its cluster holds
# the same role many times, so (Cluster, Sheet) cannot say which pair is being
# edited. They live in the interface state (gui_state.json) and are checked against
# the board on every use, never written to the config.

def parse_refs_field(text: str) -> list:
    """The refdes typed into the "Refs" field: comma and/or whitespace separated
    (and semicolons), in the order given, duplicates dropped — "C43, C44",
    "C43 C44" and "C43,C44" all mean the same pair."""
    out: list = []
    for token in text.replace(",", " ").replace(";", " ").split():
        if token not in out:
            out.append(token)
    return out


def refs_field_text(role_to_ref, roles_in_order=()) -> str:
    """"C43, C44" — the Refs field's text. The cell's own component order first
    (stable across reloads, and the same order the tooltip explains), then any
    role the map carries that the cell does not know."""
    ordered = [role_to_ref[r] for r in roles_in_order if r in role_to_ref]
    ordered += sorted(ref for role, ref in role_to_ref.items()
                      if role not in tuple(roles_in_order))
    return ", ".join(ordered)


def refs_tooltip(role_to_ref, roles_in_order=()) -> str:
    """The role -> refdes mapping, spelled out — the bare "C43, C44" cannot say
    which ref belongs to which role, and for a spoke that is the whole point."""
    if not role_to_ref:
        return _("refdes of the identified instance — filled by “Fill from "
                 "selection”, editable by hand")
    pairs = ["{role} → {ref}".format(role=role, ref=role_to_ref[role])
             for role in roles_in_order if role in role_to_ref]
    pairs += ["{role} → {ref}".format(role=role, ref=role_to_ref[role])
              for role in sorted(role_to_ref) if role not in tuple(roles_in_order)]
    return _("role → refdes of the identified instance: {pairs}").format(
        pairs=", ".join(pairs))


class CellInstanceMixin:
    """See the module docstring — the host page's "which cell / which instance"."""

    # ── Lifecycle (called by the page) ────────────────────────────────────

    def init_cell_instance(self) -> None:
        """The state this mixin adds to the page. Called from __init__ BEFORE the
        UI is built (the gate itself is created by build_instance_box)."""
        # The ONE entity index of part 1 — DockHub supplies the provider, since
        # the Config tree has already walked the graph (no second walk here).
        self.entity_index_provider = None
        # The entity the page was OPENED from (its "Edit cell…" / click / menu),
        # if any — the dropdown's first choice (п.4).
        self._opened_from_entity: Optional[str] = None

    def build_instance_box(self, source_layout) -> None:
        """The whole "Identified instance" box: the "Entity" dropdown (п.1), its
        note, "Fill from selection" and the refs line — and the gate behind the
        dropdown, built LAST because it drives all four widgets."""
        ident_box = QGroupBox(_("Identified instance"))
        ident_form = QFormLayout(ident_box)
        self._entity_picker = EntityPicker()
        self._entity_picker.setToolTip(
            _("The placed instance of this cell: every action of this page uses "
              "the address of the chosen Entity. “Manual…” keeps the "
              "hand-typed Sheet/Cluster/refs."))
        ident_form.addRow(_("Entity:"), self._entity_picker)
        entity_note = QLabel(_("With an Entity chosen, Sheet/Cluster/refs are "
                               "its address and are read-only here — edit them "
                               "in the entity's own form. “Manual…” hands the "
                               "three fields back to you."))
        entity_note.setWordWrap(True)
        ident_form.addRow(entity_note)

        # ONE button and ONE line of refdes: the refs are the thing that pins a
        # SPOKE cell's pair (the role TABLE lives on its own "Refs" tab).
        ident_row = QHBoxLayout()
        self._fill_selection_button = QPushButton(_("Fill from selection"))
        self._fill_selection_button.setToolTip(
            _("Identify ONE placed instance of this cell from the components "
              "selected on the board: fills Sheet, Cluster and the refs of that "
              "pair (a spoke is identified by one pair)."))
        self._fill_selection_button.clicked.connect(self._on_fill_from_selection)
        ident_row.addWidget(self._fill_selection_button)
        ident_form.addRow(ident_row)

        self._refs_edit = QLineEdit()
        self._refs_edit.setPlaceholderText(_("C43, C44"))
        self._refs_edit.setToolTip(refs_tooltip({}, ()))
        self._refs_edit.editingFinished.connect(self._on_refs_edited)
        ident_form.addRow(_("Refs:"), self._refs_edit)
        ident_note = QLabel(_("The refdes of the identified components, in the "
                              "order of the cell's roles — editable by hand "
                              "(comma or space separated). They are checked "
                              "against the board every time they are used; a "
                              "changed or missing component is reported as a "
                              "stale identification."))
        ident_note.setWordWrap(True)
        ident_form.addRow(ident_note)
        source_layout.addWidget(ident_box)

        self._entity_gate = CellInstanceGate(
            self._entity_picker,
            InstanceFields(self._cluster_combo, self._sheet_combo,
                           self._refs_edit, self._fill_selection_button),
            choices=self._address_choices, last_entity=self._remembered_entity,
            remember_pick=self._remember_entity_pick,
            on_applied=self._after_instance_applied)
        self._entity_picker.address_chosen.connect(
            lambda _row: self._entity_gate.choose())

    # ── What the page calls about the instance ────────────────────────────

    def open_cell(self, opened_from=None) -> None:
        """A new cell was loaded: the PREVIOUS cell's rows must not survive into
        this open (the refill keeps the current row when it is still in the list,
        and a same-named entity of another cell would be "kept" by that rule), and
        the row to open on is this open's own (п.4)."""
        self._entity_gate.set_opened_from(opened_from)
        self._entity_gate.clear()

    def clear_instance(self) -> None:
        """No cell loaded — nothing to choose and no row in force."""
        self._entity_gate.clear()

    def reload_instance(self) -> None:
        """Refill the dropdown from the current graph and re-apply the row it
        lands on. LAST in the form reload on purpose: the gate has the final word
        on the three fields it drives, after every other setEnabled."""
        self._entity_gate.reload()

    def select_manual_instance(self) -> None:
        """Put the dropdown on "Manual…" when an action has just made the
        hand-typed fields the instance (an identification, a "Read from
        selection") — the row must say what the page now does."""
        self._entity_gate.select_manual()

    def refs_field_is_pinned(self) -> bool:
        """True while an ENTITY row is in force: the refs field is display only
        then (п.1) — the entity's own `refs:` pins ARE the instance, and they are
        edited in the entity's form. The page's editingFinished handler asks this
        FIRST, because a programmatic finish must not re-identify the cell behind
        the chosen row."""
        return not self._entity_gate.is_manual()

    def instance_fields_are_being_written(self) -> bool:
        """True while a dropdown PICK writes the working fields. The page's
        "remember the working context" guard asks this (п.4): without it the combos
        a pick writes would look like a manual Cluster/Sheet edit and would erase
        the cell's identified refs (gui/cell_edit_context.py:100)."""
        return self._entity_gate.is_applying()

    def refuse_foreign_selection(self, read) -> bool:
        """п.5: while an ENTITY row is in force the board read is PINNED to it.

        Returns True (and says so with a red line) when `read` — the selection's
        address as the reader resolved it — names ANOTHER instance: nothing may be
        read from it. An untagged selection (no cluster field at all) returns
        False on purpose: tagging a fresh pair is exactly what these buttons are
        for, and the entity is what that pair will belong to."""
        row = self._entity_gate.row()
        if row is None or not read.get("cluster"):
            return False
        if address_matches_selection(row, read["cluster"], read.get("sheet")):
            return False
        # часть 3, п.6: the refusal names BOTH addresses — the selection's own
        # (what the reader resolved off the board) and the entity that was
        # expected. The page's read carries no sheet, so it says "(no sheet)"
        # there rather than guessing one.
        show_message(not_the_entity_line(row.entity_name, read["cluster"],
                                         read.get("sheet")),
                     ERROR_STYLE, logger)
        return True

    def announce_cell_write(self) -> None:
        """п.6: a write under an ENTITY row goes to the CELL's file — the ONE
        record every entity of this cell stands on — so say out loud that the
        change reaches them all."""
        row = self._entity_gate.row()
        if row is not None:
            show_message(write_applies_line(self._cell_name, row),
                         SUCCESS_STYLE, logger)

    def active_refs(self) -> Optional[dict]:
        """The refs of the WORKING INSTANCE (п.5): the picked entity's own pins,
        else — on the "Manual…" row — the remembered identification of this cell.
        ONE reader for every action of the page, so none can quietly fall back to
        the remembered pair behind an entity the user chose."""
        if self._entity_gate.is_manual():
            return self._remembered_refs()
        return self._entity_gate.active_refs()

    # ── The loaded cell, its roles and its refs ───────────────────────────

    def _current_entry(self) -> Optional[dict]:
        """Read the CURRENT cell entry from disk (dict or None)."""
        if self._cell_name is None:
            return None
        target_file = find_dict_entry_file(self._root_path, "cells", self._cell_name)
        if target_file is None:
            target_file = self._file_path
        if target_file is None or not Path(target_file).exists():
            return None
        try:
            entry = (_read_data(Path(target_file)).get("cells") or {}).get(
                self._cell_name)
        except (ValidationError, OSError):
            return None
        return entry if isinstance(entry, dict) else None

    def _cell_roles(self) -> list:
        """Roles from the CURRENT loaded cell entry (its own components — the
        only legitimate Role choices, never the live board)."""
        entry = self._current_entry()
        if entry is None:
            return []
        return sorted({c.get("role") for c in entry.get("components", [])
                       if c.get("role")})

    def _cell_role_order(self) -> list:
        """The same roles in the CELL's own order (the Refs line and its tooltip
        follow the cell, not the alphabet)."""
        entry = self._current_entry()
        if entry is None:
            return []
        return [c.get("role") for c in entry.get("components", [])
                if c.get("role")]

    def _remembered_refs(self) -> Optional[dict]:
        """The IDENTIFIED refs of this cell's instance (gui_state.json), or None
        — the role -> refdes map "Fill from selection" wrote.

        Read on the UI thread from state only (never the board); when present,
        EVERY overlay read of this page is pinned to that pair, so a spoke cell
        behaves exactly like an ordinary one. A map whose refs are gone from the
        board is NOT repaired here: the worker refuses it as a stale
        identification, which is what the user must see."""
        if self._root_path is None or self._cell_name is None:
            return None
        return remembered_cell_refs(self._root_path, self._cell_name)

    def reload_refs_field(self) -> None:
        """Show the refs of the WORKING instance (display only, never a write):
        the picked entity's own pins, else the remembered identification of this
        cell. The tooltip carries the role -> refdes mapping the bare
        "C43, C44" cannot express."""
        refs = self.active_refs() or {}
        roles = self._cell_role_order()
        self._refs_edit.setText(refs_field_text(refs, roles))
        self._refs_edit.setToolTip(refs_tooltip(refs, roles))

    # ── What the rows are for THIS cell (п.1 / п.4) ───────────────────────

    def _address_choices(self) -> list:
        """This cell's instance ADDRESSES in the plan's order (п.1 / п.2а): its
        entities from the ONE part-1 index, then its chain spokes, then
        "Manual…" last. No project or no cell — nothing to choose.

        п.3: a cell that has NO entity AND that NOTHING places has nothing to
        choose at all — not even "Manual…". Its board actions are the read-only
        gate's business ("create an entity to edit this cell"), and an empty
        dropdown is what says so here, instead of an "Manual…" row that would
        invite the user into fields nothing can read.

        Without the index (no provider) the page knows nothing about the graph
        and keeps "Manual…": that is every test that builds a bare page, and the
        behaviour before part 2."""
        if self._root_path is None or self._cell_name is None:
            return []
        index = self.entity_index_provider() if self.entity_index_provider \
            else None
        entry = self._current_entry() or {}
        uuid = entry.get("uuid")
        entities = entity_addresses(index, uuid)
        if not entities and index is not None and not index.placed_by_cell(uuid):
            return []
        return build_choices(entities)

    def _remembered_entity(self) -> Optional[str]:
        """The entity NAME this cell was last worked with (п.4)."""
        return remembered_last_entity(self._root_path, self._cell_name)

    def show_entity_instance(self, entity_name) -> bool:
        """Move the dropdown to the ENTITY named — for a door that names an
        entity while this page is ALREADY open on the same cell (2в, п.2).

        The row is selected through the widget and APPLIED through the gate
        (`choose`), so the ordinary path runs: the gate writes the working
        fields, reloads what depends on them and PUBLISHES the record
        (`_after_instance_applied`). Writing the store here instead would be the
        second copy of the instance this part exists to prevent.

        False when this cell has no such row (an entity renamed since the graph
        was read, or a page without the part-1 index): the caller then falls back
        to publishing the store, as before."""
        row = next((r for r in self._address_choices()
                    if r.source == SOURCE_ENTITY and r.entity_name == entity_name),
                   None)
        if row is None or not self._entity_picker.select_address(row.key):
            return False
        self._entity_gate.choose()
        return True

    def _remember_entity_pick(self, name: Optional[str]) -> None:
        """Remember the picked entity BY NAME under its own state key (п.4) — and
        never through remember_cell_edit_context, whose write erases the cell's
        identified refs. A no-op when the name is already the remembered one, so
        a tab switch (which re-applies the same row) never rewrites the state."""
        if not name or self._root_path is None or self._cell_name is None:
            return
        if self._remembered_entity() != name:
            remember_last_entity(self._root_path, self._cell_name, name)

    def _after_instance_applied(self) -> None:
        """The gate wrote the working fields — reload what depends on them (the
        refs line and the Name/Comment identity block), and PUBLISH the working
        instance to the one store every other reader uses (п.1).

        Publishing is what makes the CellDock's payload and the mixed-selection
        door agree with what this page shows: they read that store at the moment
        of use, so no copy can drift out of step with the dropdown."""
        self.reload_refs_field()
        self._reload_identity()
        remember_working_instance(self._root_path, self._cell_name,
                                  self._entity_gate.row())
