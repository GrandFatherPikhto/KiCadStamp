# gui/docks/entity_tree.py
"""The Qt plumbing that shows `entities:` as CHILDREN of their cell/imprint in
the Config tree, marks a cell without an entity, and offers the orphan's
"Point to …" / "Delete entity" menu (plan_2026_10_05_entities_under_cells,
part 1).

Why a mixin and not free functions: every method here is about ONE dock's
items, its tree item roles and its context menu, and it reads the dock's own
`_entity_index` / `_file_parents` (built once per refresh from
gui/docks/entity_index.py, the Qt-free graph index). Extracting it into a mixin
keeps the >800-line `config_tree.py` to its wiring (rule 45: a giant only
shrinks; new tree plumbing goes to a module of its own).

ConfigTreeDock inherits EntityTreeMixin; the constants below are the SAME item
data roles the giant reads in its click routing and context menu, imported back
there.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QStyle, QTreeWidgetItem

from kicadstamp.config.name_hint import close_name, close_name_hint
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import upsert_list_entry
from .entity_delete import backup_file
from .entity_index import PLACED_BY_CLONE, PLACED_BY_NESTED, PLACED_BY_SPOKE
from .rename import entry_effective_name

logger = logging.getLogger(__name__)

# Leaf-label marker for an entry carrying a comment — the SAME glyph the giant
# uses when building the label and when stripping it back to recover the name.
_COMMENT_GLYPH = "📝 "

# Extra per-item data roles. The 3-element UserRole tuple stays UNWIDENED
# (several call sites destructure it by fixed arity). A cell leaf stores its
# no-entity state here; an ENTITY leaf stores its OWN declaring file, so every
# routing path (click, context menu, edit, rename, delete, export) takes the
# write target from the RECORD, never from the cell it is shown under (п.4).
# The shape is (file_path, parent_path) — the same as _file_context_for_item.
_ROLE_OWN_FILE = Qt.ItemDataRole.UserRole + 1
_ROLE_CELL_MARK = Qt.ItemDataRole.UserRole + 2
# An entity leaf whose cell/imprint is nowhere in the graph (3а): its menu is
# "Point to …" + "Delete entity" only — it has nothing else to read.
_ROLE_ORPHAN = Qt.ItemDataRole.UserRole + 3
# A cell without any entity is a second kind of orphan (п.3б): "not placed at
# all" vs "placed by something that is not an entity".
_CELL_UNUSED = "unused"
_CELL_PLACED = "placed"
# "placed by" kind -> its human label, the {what} of the cell hint.
_PLACED_BY_LABEL = {
    PLACED_BY_SPOKE: _("spoke of chain"),
    PLACED_BY_CLONE: _("clone placement"),
    PLACED_BY_NESTED: _("nested placement in cell"),
}


class EntityTreeMixin:
    """Tree-item plumbing shared into ConfigTreeDock (see the module docstring)."""

    # ── Entities under their cell/imprint ──────────────────────────────────

    def _entity_leaf(self, parent, ref) -> QTreeWidgetItem:
        """One entity leaf: label (with its comment marker), the usual
        ("leaf", "entities", <record>) payload, and the entity's OWN declaring
        file in _ROLE_OWN_FILE so every routing path reads the write target
        from the RECORD, not from the visible ancestor."""
        name = ref.name or "?"
        comment = ref.data.get("comment")
        leaf = QTreeWidgetItem(
            parent, [f"{_COMMENT_GLYPH}{name}" if comment else name])
        leaf.setData(0, Qt.ItemDataRole.UserRole, ("leaf", "entities", ref.data))
        leaf.setData(0, _ROLE_OWN_FILE,
                     (ref.file_path, self._file_parents.get(ref.file_path)))
        if comment:
            leaf.setToolTip(0, comment)
        return leaf

    def _add_cell_entities(self, leaf, cell_data: dict) -> None:
        index = self._entity_index
        if index is None:
            return
        for ref in index.entities_for_cell(cell_data.get("uuid")):
            self._entity_leaf(leaf, ref)

    def _add_imprint_entities(self, leaf, record: dict) -> None:
        index = self._entity_index
        if index is None:
            return
        for ref in index.entities_for_imprint(record.get("uuid")):
            self._entity_leaf(leaf, ref)

    def _add_orphan_entities(self, file_item, node) -> None:
        """An entity whose cell/imprint is nowhere in the graph stays in its
        OWN file's Entities section (п.3) — that section exists ONLY when such
        an orphan does (мутация «раздел показан при отсутствии сирот»)."""
        index = self._entity_index
        if index is None:
            return
        orphans = [ref for ref in index.orphans if ref.file_path == node.path]
        if not orphans:
            return
        section_item = QTreeWidgetItem(file_item, [_("Entities")])
        section_item.setData(0, Qt.ItemDataRole.UserRole, ("category", "entities"))
        for ref in orphans:
            leaf = self._entity_leaf(section_item, ref)
            leaf.setData(0, _ROLE_ORPHAN, True)
            # п.5: the orphan carries a MARK, not only a hint (п.3 of the plan
            # asks for both, like the cell marks). Critical on purpose: a
            # dangling reference is what makes format 3 refuse to load the whole
            # graph — heavier than "this cell is nowhere placed" (warning).
            leaf.setIcon(0, self.style().standardIcon(
                QStyle.StandardPixmap.SP_MessageBoxCritical))
            self._append_tooltip(leaf, self._orphan_hint(ref))

    def _orphan_hint(self, ref) -> str:
        """The orphan's hint: the missing target plus the closest known name,
        through the SAME close_name_hint the loader refusal uses (3а) — never
        a second implementation."""
        index = self._entity_index
        if index is None:
            return ""
        data = ref.data
        if data.get("cell") is not None:
            section, field, kind = "cells", "cell", "cell"
        elif data.get("imprint") is not None:
            section, field, kind = "imprints", "imprint", "imprint"
        else:
            return _("refers to no cell or imprint — point it at one")
        name = data.get(field)
        return _("refers to a missing {kind} {name!r} ({uuid})").format(
            kind=kind, name=name, uuid=data.get(field + "_uuid")) + \
            close_name_hint(name, index.names_for(section))

    def _mark_cell(self, leaf, name: str, cell_data: dict) -> None:
        """A cell WITHOUT an entity is a second kind of orphan (п.3б): mark it
        (icon + tooltip) and remember the variant for the context menu. A cell
        WITH an entity is left unmarked."""
        index = self._entity_index
        if index is None:
            return
        uuid = cell_data.get("uuid")
        if index.has_entity_for_cell(uuid):
            return
        placed = index.placed_by_cell(uuid)
        if placed:
            state = _CELL_PLACED
            hint = self._placed_hint(name, placed)
            pixmap = QStyle.StandardPixmap.SP_MessageBoxInformation
        else:
            state = _CELL_UNUSED
            hint = _("cell {name!r} has no entity — it is not placed; "
                     "create an entity to edit it").format(name=name)
            pixmap = QStyle.StandardPixmap.SP_MessageBoxWarning
        leaf.setData(0, _ROLE_CELL_MARK, state)
        leaf.setIcon(0, self.style().standardIcon(pixmap))
        self._append_tooltip(leaf, hint)

    def _placed_hint(self, name: str, placed) -> str:
        """The hint of a cell that IS placed but has no entity.

        A cell placed by CHAINS only says exactly that — "cell {name!r} is placed
        by chain(s) {names}" (п.2а of the plan): a spoke is a legitimate instance
        address, so such a cell is not an orphan, and the "no entity" half of the
        old wording only invited a question the chain already answers.

        Anything else that places it (a clone placement, a nested one) keeps the
        part-1 wording that NAMES the kinds — there "no entity" is the point."""
        if all(pb.kind == PLACED_BY_SPOKE for pb in placed):
            return _("cell {name!r} is placed by chain(s) {names}").format(
                name=name, names=self._named_counts(placed))
        return _("cell {name!r} has no entity — placed by {what}").format(
            name=name, what=self._placed_by_text(placed))

    def _named_counts(self, placed) -> str:
        """The placers' display names, each ONCE, with its count where the count
        says more than the name: "MCU Vdd (3), FPGA PWR". Through the ONE
        effective-name rule (the same `entry_effective_name` the "placed by" text
        uses) — a second name rule is exactly what that helper exists to stop."""
        counts: dict = {}
        for pb in placed:
            owner = (pb.owner_name if pb.owner_name is not None
                     else entry_effective_name(pb.section, pb.owner))
            counts[owner] = counts.get(owner, 0) + 1
        return ", ".join(
            _("{name} ({count})").format(name=owner, count=count) if count > 1
            else owner for owner, count in counts.items())

    def _placed_by_text(self, placed) -> str:
        """The {what} token of a placed-but-entityless cell hint — the owner
        display name resolved through the ONE effective-name rule.

        ONE placer is ONE mention, with its count (доделка 1а, п.4): a chain
        whose three spokes place the same cell used to read "spoke of chain:
        MCU Vdd, spoke of chain: MCU Vdd, spoke of chain: MCU Vdd" on the live
        profile, which says nothing the single mention with "(3)" does not. The
        order is the order the index found them in, so the hint is stable."""
        counts: dict = {}
        for pb in placed:
            owner = (pb.owner_name if pb.owner_name is not None
                     else entry_effective_name(pb.section, pb.owner))
            key = (_PLACED_BY_LABEL.get(pb.kind, pb.kind), owner)
            counts[key] = counts.get(key, 0) + 1
        parts = []
        for (kind, owner), count in counts.items():
            if count > 1:
                parts.append(_("{kind}: {name} ({count})").format(
                    kind=kind, name=owner, count=count))
            else:
                parts.append(_("{kind}: {name}").format(kind=kind, name=owner))
        return ", ".join(parts)

    @staticmethod
    def _append_tooltip(leaf, hint: str) -> None:
        """Append a marker hint to a leaf's tooltip without clobbering an
        already-set comment tooltip."""
        if not hint:
            return
        existing = leaf.toolTip(0)
        leaf.setToolTip(0, f"{existing}\n{hint}" if existing else hint)

    # ── Cell / orphan context-menu blocks ──────────────────────────────────

    def _add_cell_menu_items(self, menu, old_name, file_path) -> None:
        """The cell's own menu block: Edit cell / anchor / Copy placement.

        часть 3, п.3: the BOARD items are NOT here any more. A cell leaf
        names no instance, so a board action on it worked an address the user
        could not see (the working-instance store) — they live on the ENTITY
        leaf now, which names its entity in the item itself. ONE rule, no
        "if there is only one entity" exception. Rename / Delete come from the
        generic block; "Create entity" is the entityless cell's own item (3б)."""
        menu.addAction(_("Edit cell...")).triggered.connect(
            lambda: self.cell_edit_requested.emit(old_name, file_path))
        # 2026-09-09 (Phase C of plan_2026_09_09_cell_anchor_v2_
        # declarative_and_board_overlay): the dedicated anchor editor
        # (Component/Marker tabs) as a Config right-QView page.
        menu.addAction(_("Cell anchor...")).triggered.connect(
            lambda: self.cell_anchor_requested.emit(old_name, file_path))
        # 2026-09-06 (plan copy_placement_from_cell): the OFFLINE
        # sibling — copy another cell's placement (component geometry +
        # vias/tracks) into this one; the donor is picked from a minimal
        # role-set-fitted combobox (no live board selection involved).
        menu.addAction(_("Copy placement from cell...")).triggered.connect(
            lambda: self.cell_copy_requested.emit(old_name, file_path))

    def _add_orphan_menu(self, menu, entity, file_path) -> None:
        """3а: an entity whose cell/imprint is nowhere in the graph has nothing
        else to read — it can only be RE-POINTED or deleted."""
        if not isinstance(entity, dict):
            return
        label = (_("Point to imprint…") if entity.get("imprint") is not None
                 else _("Point to cell…"))
        menu.addAction(label).triggered.connect(
            lambda checked=False, e=entity, f=file_path:
            self._on_point_entity(e, f))
        menu.addAction(_("Delete entity")).triggered.connect(
            lambda checked=False, e=entity, f=file_path:
            self._on_delete(f, "entities", e.get("name")))
        menu.addSeparator()

    def add_entity_menu(self, menu, item, file_path) -> bool:
        """The ENTITY leaf's menu block (СЦ-1, and 2б, п.4 grew it).

        Returns True when the leaf is an ORPHAN (3а): that one can only be
        re-pointed or deleted and must not get the generic Rename/Delete pair.

        Moved out of the context-menu builder in gui/docks/config_tree.py (rule
        45: that file is a giant and only shrinks) — the same move part 1 made
        for the CELL menu (`_add_cell_menu_items`).

        Every BOARD item names the entity it was clicked under (2б, п.4): the
        door publishes it as the cell's working instance before the action runs,
        so the action cannot read another channel than the one the user clicked
        under — and the ENTITY's own "Edit cell..." opens the cell PAGE on it."""
        leaf_data = item.data(0, Qt.ItemDataRole.UserRole)
        entity = leaf_data[2] if leaf_data is not None else None
        if item.data(0, _ROLE_ORPHAN):
            # 3а: an entity whose cell/imprint is missing can only be
            # re-pointed or deleted — nothing else can be read from it.
            self._add_orphan_menu(menu, entity, file_path)
            return True
        if isinstance(entity, dict) and entity.get("cell"):
            # СЦ-1: three items in order — components, cell, enclosed.
            # 2б, п.4: the ENTITY's own "Edit cell..." — the page opens ON this
            # entity (`opened_from`), never on the cell's last/first one. The
            # item name matches the cell menu's, and the signal is its own
            # because this door opens the PAGE (anchor/dropdown), not CellDock's
            # dialog.
            entity_edit_action = menu.addAction(_("Edit cell..."))
            entity_edit_action.setObjectName("edit_cell_for_entity_action")
            entity_edit_action.triggered.connect(
                lambda checked=False, n=entity.get("cell"),
                e=entity.get("name"), f=file_path:
                self.cell_anchor_entity_requested.emit(n, f, e))
            # часть 3, п.1: the BOARD items of the entity leaf — the same set the
            # cell leaf used to carry (and loses in п.3), in the SAME order, each
            # naming THIS entity. The write still lands in the CELL's file
            # (`file_path=None` — Н5б: the entity's own file must never become the
            # cell's save target), and the door resolves the name into an address
            # (`gui/entity_doors.door_address`) instead of reading the store.
            self._add_entity_board_items(menu, entity)
            components_action = menu.addAction(_("Select cell components"))
            components_action.setObjectName("select_cell_components_action")
            components_action.triggered.connect(
                lambda checked=False, n=entity.get("cell"),
                c=entity.get("cluster"), s=entity.get("sheet"),
                e=entity.get("name"):
                # Н5б: file_path=None — the ENTITY's file must never become the
                # cell's save target; CellDock resolves the cell's OWN file from
                # the config.
                self.cell_select_components_requested.emit(n, None, c, s, e))
            select_action = menu.addAction(_("Select cell"))
            select_action.setObjectName("select_cell_action")
            select_action.triggered.connect(
                lambda checked=False, n=entity.get("cell"),
                c=entity.get("cluster"), s=entity.get("sheet"),
                e=entity.get("name"):
                self.cell_select_requested.emit(n, None, c, s, e))
            # 2026-10-05: the SAME explicit instance, selecting the whole
            # enclosed copper too. The objectName is for the guard (the label is
            # translated, so the guard reads the name).
            enclosed_action = menu.addAction(_("Select enclosed copper"))
            enclosed_action.setObjectName("select_enclosed_copper_action")
            enclosed_action.triggered.connect(
                lambda checked=False, n=entity.get("cell"),
                c=entity.get("cluster"), s=entity.get("sheet"),
                e=entity.get("name"):
                self.cell_select_enclosed_requested.emit(n, None, c, s, e))
            # Р2: the entity door of the "Разнос" tab — same explicit
            # (cluster, sheet), so no guessing.
            menu.addAction(_("Explode…")).triggered.connect(
                lambda checked=False, n=entity.get("cell"),
                c=entity.get("cluster"), s=entity.get("sheet"),
                e=entity.get("name"):
                self.cell_explode_requested.emit(n, None, c, s, e))
            # часть 3, п.1: the same two reads with the LAYER DIALOG in front,
            # exactly like the cell menu's pair.
            self._add_entity_board_layer_items(menu, entity)
        return False

    def _add_entity_board_items(self, menu, entity: dict) -> None:
        """The three fast board items of an ENTITY leaf (часть 3, п.1).

        ONE builder for the three, and ONE shape: every item emits the cell's
        name, `None` for the file (the CELL's own file is resolved by CellDock —
        Н5б) and the ENTITY's own (cluster, sheet, name). The name is what makes
        the read work THIS entity (`gui/entity_doors.door_address`); the item
        reads nothing itself."""
        args = self._entity_board_args(entity)
        for object_name, label, signal in (
                ("refresh_from_selection_action", _("Update from selection..."),
                 self.cell_refresh_requested),
                ("import_from_selection_action", _("Add selected copper..."),
                 self.cell_import_requested),
                ("subtract_selection_action", _("Subtract selected copper..."),
                 self.cell_subtract_requested)):
            action = menu.addAction(label)
            action.setObjectName(object_name)
            action.triggered.connect(
                lambda checked=False, s=signal, a=args: s.emit(*a))

    def _add_entity_board_layer_items(self, menu, entity: dict) -> None:
        """The two "(choose layers)…" legs of the same reads (часть 3, п.1) —
        the dialog decides the layer set, the read is the very same one."""
        args = self._entity_board_args(entity)
        for object_name, label, signal in (
                ("refresh_from_selection_layers_action",
                 _("Update from selection (choose layers)..."),
                 self.cell_refresh_layers_requested),
                ("import_from_selection_layers_action",
                 _("Add selected copper (choose layers)..."),
                 self.cell_import_layers_requested)):
            action = menu.addAction(label)
            action.setObjectName(object_name)
            action.triggered.connect(
                lambda checked=False, s=signal, a=args: s.emit(*a))

    @staticmethod
    def _entity_board_args(entity: dict) -> tuple:
        """The argument list every board item of an ENTITY leaf emits.

        ONE tuple, built in ONE place: (cell, file_path=None, cluster, sheet,
        entity name). A second copy of this shape is how a door would start
        reading another channel — the very defect часть 3 closes."""
        return (entity.get("cell"), None, entity.get("cluster"),
                entity.get("sheet"), entity.get("name"))

    def _on_point_entity(self, entity: dict, file_path: Path) -> None:
        """Re-point an orphan at a graph cell/imprint, writing BOTH the name
        and its uuid into the entity's OWN file (3а).

        The uuid comes from the RAW index, never from load_config: a dangling
        reference is exactly what makes load_config FATAL, so the picker and the
        uuid are read off the broken graph itself."""
        index = self._entity_index
        if index is None or not isinstance(entity, dict):
            return
        if entity.get("imprint") is not None:
            section, field, title = "imprints", "imprint", _("Point to imprint…")
        else:
            section, field, title = "cells", "cell", _("Point to cell…")
        names = index.names_for(section)
        if not names:
            logger.error(_("Nothing to point {name!r} at: the graph has no {section}.")
                         .format(name=entity.get("name"), section=section))
            return
        suggested = close_name(entity.get(field), names)
        current = names.index(suggested) if suggested in names else 0
        chosen, ok = QInputDialog.getItem(
            self, title, _("Point {name!r} at:").format(name=entity.get("name")),
            names, current, False)
        if not ok or not chosen:
            return
        updated = dict(entity)
        for ref_field in ("cell", "cell_uuid", "imprint", "imprint_uuid"):
            updated.pop(ref_field, None)
        updated[field] = chosen
        updated[field + "_uuid"] = index.target_uuid(section, chosen)
        try:
            backup_file(file_path)
            upsert_list_entry(file_path, "entities", updated,
                              key_fn=lambda e: e.get("name"))
        except (OSError, ValidationError) as e:
            logger.error("%s: %s", _("Point failed"), e)
            return
        self.refresh()
        self.graph_changed.emit()

    # ── Own-file routing (п.4) ─────────────────────────────────────────────

    def _file_context_for_item(self, item) -> Optional[tuple]:
        """The (file_path, parent_path) an item's ACTIONS operate on — ONE
        rule for every routing path (click, context menu, edit, rename,
        delete, export).

        For an ENTITY leaf that is the RECORD's OWN declaring file, taken
        from _ROLE_OWN_FILE (п.4) — never the file of the cell it is shown
        under. Every other kind keeps the nearest file ancestor: whether the
        file header itself, a category or a leaf was clicked."""
        own = item.data(0, _ROLE_OWN_FILE)
        if own is not None:
            return own
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data is not None and data[0] == "file":
                return data[1], data[2]  # (file_path, parent_path)
            item = item.parent()
        return None

    def _nearest_file_path(self, item) -> Optional[Path]:
        """The path of the nearest file ANCESTOR — the file an item is
        VISUALLY under. For an entity leaf this may differ from its own file
        (п.4); the difference is what makes the file block name its target."""
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data is not None and data[0] == "file":
                return data[1]
            item = item.parent()
        return None
