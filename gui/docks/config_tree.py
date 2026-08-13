# gui/docks/config_tree.py
"""
ConfigTreeDock — one tree mirroring the actual include: file graph from a
single root config file (2026-08-03, GUI tree roadmap Этап 1/2 — see
techdocs/handoff/handoff_2026_08_03_gui_tree_risks_resolved.md and the
2026-08-03 config-architecture-brainstorm session). Root = the ONE file
carrying metadata — replaces FilePickerDock entirely (removed same day:
"Да не хочу я файл-пикер"), which used to offer a QFileSystemModel
directory browser plus three independent "role" slots (Cells/Extractor/
Placer) other docks read their target file from.

Root OWNERSHIP (Open/New Root file.../Recent dropdown, persisted-recent-
list, restore-on-startup) moved to RootMetadataDock 2026-08-11 (Denis:
"'Открыть корневой файл' и 'Новый корневой файл'... тоже на док проект",
"И 'Недавние' туда же" — see gui/docks/root_metadata.py's module
docstring for the full reasoning). This dock is now a plain SUBSCRIBER:
set_root_file() just rebuilds the tree from whatever path it's given,
root_path is read-only informational, and there is no root_file_changed
signal here anymore — RootMetadataDock's own root_changed is the source
every other dock listens to (see gui/dock_hub.py).

Each file node shows its own directly declared entities (grouped by
section) as leaves, plus its own include: children as nested file nodes,
recursively — built via kicadstamp.config.includes.walk_include_tree(),
NOT resolve_includes() (which merges and loses file boundaries — the wrong
shape here: an earlier version of this dock read one flat file per role
and didn't walk include: at all, corrected same day, see the handoff
above).

6 of the 7 recognized sections route into an existing form when LEFT-
clicked (Cells -> PlacerDock.set_selected_cell, Clone placements ->
PlacerDock.load_placement, Extract profiles -> ExtractDock.pick_profile,
Thermal via arrays -> ThermalViaArrayDock.load_entry, added 2026-08-03;
Points -> PointsDock.load_entry, Rules -> RuleDock.load_entry, both added
2026-08-05) — Clone profiles is the one section still with no GUI edit
form, shown read-only for now, same deliberate scope limit as before.

Cells is special (2026-08-06, CellDock added — see gui/docks/cell_editor.py):
left-click on a Cell leaf keeps its ORIGINAL meaning, "pick this cell as a
placement's content" (cell_picked -> PlacerDock.set_selected_cell,
unchanged) — editing the cell's OWN content (Components/Vias/Tracks/Nested
cells) is a separate action, "Edit cell..." in the right-click menu
(cell_edit_requested -> CellDock.load_entry), so opening a placement form
and opening the cell editor never fight over the same click. "Add cell..."
(same menu) used to write a raw {"components": []} stub straight to YAML
with no form behind it at all — replaced with add_cell_requested ->
CellDock.new_cell(), the same "open the form blank" shape every other
Add-entity action already uses (found live: that raw stub is exactly what
caused Denis's Conn_PM5V placement failure, see cell_editor.py).

Every click (file header, category, or leaf alike) also fires
file_selected with that item's nearest file ancestor — this REPLACES the
three independent Cells/Extractor/Placer role signals FilePickerDock used
to fire: ExtractDock now always targets "whatever file is currently being
browsed in this tree" for both its Cells output and its extract_profiles
output (collapsing what used to be two independently-assignable roles
into one — Denis: "Экстракторы у нас уже автоматизированы", nothing extra
needed since extracting into a file already positioned in the tree means
it's already reachable from root, no separate include: wiring step
required in the common case).

Right-click context menu (2026-08-03): the SAME set of file-level actions
(Add cell/thermal via pad/placer/included file, Remove this file) is
offered no matter which specific item under a file you right-click — the
file/category/leaf distinction only matters for left-click routing above,
not for these actions, which always operate on "the nearest file
ancestor" (Denis: "Если выбран файл или его десцендант..." — the
descendant doesn't change WHICH file the action targets).

Rename (2026-08-04, Denis: "А мы можем добавить конекстное меню в конфиг
чтобы переименовать плэейсменты, целлы, профили извлечения и т.д.?") —
appears ONLY when right-clicking an actual leaf (unlike the file-level
actions above), covers all 7 sections including the 3 with no edit form
(a bare rename needs none), and for cells:/points: specifically also
rewrites every cell:/anchor_point: reference to the old name anywhere in
the whole include: graph, not just the one file the entry is declared in
— see gui/docks/rename.py for the full cross-reference audit this is
based on and why the other 5 sections need no cascading at all.

Delete (2026-08-05, Denis: "надо в контекстном меню... возможность
удалять cell, export, via_thermal_pad, rules и т.д. любую сущность. После
удаления делаем backup файл") — also leaf-only, one entry at a time. Backs
up the whole owning file (timestamped, never overwrites an earlier backup)
before writing. For cells:/points: — the same CASCADE_FIELD pair rename
uses — the whole include: graph is scanned for references first; if any
exist the confirm dialog lists them and asks whether to delete those
referencing entries too (declining cancels the whole delete). See
gui/docks/entity_delete.py.

Export (2026-08-05, same request) — works on the tree's current selection
(ExtendedSelection, turned on for this alone), so several leaves across
different files/sections can be exported together. Pure copy — the
originals are untouched — into a file picked via a Save dialog; an
existing non-empty target additionally offers Merge (default, via the same
merge_write()/upsert_list_entry() every other write path uses) vs.
Overwrite (replaces the target file's whole content). See gui/docks/
entity_export.py.
"""
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDockWidget, QFileDialog,
                              QInputDialog, QMenu, QMessageBox, QTreeWidget,
                              QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config.includes import IncludeTreeNode, walk_include_tree
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from .. import yaml_io
from ._common import add_include, disable_include, display_path, non_includable_keys
from .entity_delete import delete_entry, find_references
from .entity_export import ExportItem, export_entries
from .rename import CASCADE_FIELD, collect_graph_files, rename_entry

# Display label per recognized section, in the order shown under a file
# node. Order matches config/includes.py's _LIST_SECTIONS + _DICT_SECTIONS.
_SECTION_LABELS = {
    "rules": _("Rules"),
    "clone_placements": _("Clone placements"),
    "thermal_via_arrays": _("Thermal via arrays"),
    "coordinate_placements": _("Coordinate placements"),
    "cells": _("Cells"),
    "points": _("Points"),
    "extract_profiles": _("Extract profiles"),
    "clone_profiles": _("Clone profiles"),
}


class ConfigTreeDock(QDockWidget):
    # Fired when a Cell leaf is clicked — PlacerDock listens to fill its
    # Cell field (see gui/dock_hub.py). Left CLICK stays "pick this cell as
    # a placement's content" (unchanged) — editing a cell's own content is a
    # DIFFERENT action, see cell_edit_requested/add_cell_requested below,
    # deliberately not routed through this same signal.
    cell_picked = pyqtSignal(str)
    # Fired by the context menu's "Edit cell..." (2026-08-06, added
    # alongside CellDock — see gui/docks/cell_editor.py) — CellDock listens
    # via its load_entry() entry point. (name, file_path), same "leaf name +
    # owning file" shape points_picked/rule_picked's file context carries.
    cell_edit_requested = pyqtSignal(str, object)
    # Fired by the context menu's "Add cell..." (2026-08-06, replaces a raw
    # {"components": []} stub write straight to YAML with no form behind it
    # — the exact root cause of a live bug, see cell_editor.py's module
    # docstring) — CellDock listens via its new_cell() entry point, same
    # "open the form blank" reasoning as add_placer_requested/add_point_
    # requested/add_rule_requested/add_thermal_via_requested below.
    add_cell_requested = pyqtSignal(object)
    # Fired when a Clone placement leaf is clicked — PlacerDock listens to
    # load it back into the form.
    placement_picked = pyqtSignal(object)
    # Fired when an Extract profile leaf is clicked — ExtractDock listens
    # via its pick_profile() entry point.
    profile_picked = pyqtSignal(str)
    # Fired when a Thermal via array leaf is clicked (2026-08-03) —
    # ThermalViaArrayDock listens to load it back into the form, same shape
    # as placement_picked.
    thermal_via_picked = pyqtSignal(object)
    # Fired by the context menu's "Add placer..." — PlacerDock listens via
    # its new_placement() entry point (opens the form blank rather than
    # writing a raw stub straight to YAML).
    add_placer_requested = pyqtSignal(object)
    # Fired by the context menu's "Add thermal via pad..." (2026-08-03,
    # replaces writing a raw {"name": ...} stub straight to YAML) —
    # ThermalViaArrayDock listens via its new_thermal_via() entry point,
    # same reasoning as add_placer_requested above.
    add_thermal_via_requested = pyqtSignal(object)
    # Fired when a Coordinate placement leaf is clicked (2026-08-12, Group 1:
    # coordinate_placements became a normal named-records section — the same
    # leaf-per-record shape as clone_placements/rules, one-entry-per-leaf
    # form editing instead of the old whole-list table) — payload is the full
    # entry dict (like placement_picked), loaded into the merged PlacerDock's
    # coordinate mode. add_coordinate_placement_requested (context menu)
    # opens a blank coordinate form for a file.
    coordinate_placements_picked = pyqtSignal(object)
    add_coordinate_placement_requested = pyqtSignal(object)
    # Fired when a Points leaf is clicked (2026-08-05) — points: is a DICT
    # section (see _entries()), so the payload is just the name, unlike
    # placement_picked/thermal_via_picked's full-dict payload; PointsDock's
    # load_entry() re-reads the file for the actual data, same "read fresh
    # from yaml_io" discipline root_metadata.py's set_target_file already
    # uses. add_point_requested mirrors add_thermal_via_requested/
    # add_placer_requested — opens the form blank rather than writing a raw
    # stub straight to YAML.
    points_picked = pyqtSignal(str)
    add_point_requested = pyqtSignal(object)
    # Fired when a Rules leaf is clicked (2026-08-05) — rules: is a LIST
    # section (see _entries()), so unlike points_picked the payload is
    # already the full dict, same shape as placement_picked/thermal_via_
    # picked. add_rule_requested mirrors add_point_requested/
    # add_thermal_via_requested — opens the form blank.
    rule_picked = pyqtSignal(object)
    add_rule_requested = pyqtSignal(object)
    # Fired on EVERY click in the tree (file header, category, or leaf) —
    # see module docstring for why this replaces the three independent
    # FilePickerDock role signals.
    file_selected = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(_("Config"), main_window)
        self._main_window = main_window
        self._root_path: Optional[Path] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # Multi-select (2026-08-05) — only Export needs several leaves
        # selected at once (Denis: "экспортировать сущность (выделенные
        # сущности)"); Delete stays one entry at a time (see _on_delete),
        # and left-click routing (_on_clicked) is unaffected by selection
        # mode either way.
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)

        self.setWidget(container)

    # ── Setting/refreshing the root ─────────────────────────────────────

    @property
    def root_path(self) -> Optional[Path]:
        """Current root file, if any — informational only now (root
        OWNERSHIP moved to RootMetadataDock 2026-08-11, see module
        docstring); this dock's own operations (rename/delete/export/Add
        included file) still need it directly, since they resolve paths
        relative to the graph root."""
        return self._root_path

    def set_root_file(self, path: Optional[Path]) -> None:
        """Slot — the project's root changed (RootMetadataDock.root_changed,
        wired in gui/dock_hub.py). Just rebuilds the tree; no longer the
        SOURCE of this event (used to also persist recent-list state and
        emit its own root_file_changed before the 2026-08-11 move)."""
        self._root_path = path
        self.refresh()

    def refresh(self) -> None:
        """Public — also called by PlacerDock's saved signal (see
        gui/dock_hub.py) so a successful Save shows up here without
        reassigning the root file."""
        self.tree.clear()
        if self._root_path is None:
            return
        try:
            node = walk_include_tree(str(self._root_path))
        except ValidationError as e:
            QTreeWidgetItem(self.tree, [str(e)])
            return
        self._build_file_item(self.tree.invisibleRootItem(), node, parent_path=None)
        self.tree.expandAll()

    # ── Building the tree from an IncludeTreeNode ───────────────────────

    def _build_file_item(self, parent_item, node: IncludeTreeNode,
                          parent_path: Optional[Path]) -> None:
        file_item = QTreeWidgetItem(parent_item, [node.path.name])
        file_item.setData(0, Qt.ItemDataRole.UserRole, ("file", node.path, parent_path))
        for section, label in _SECTION_LABELS.items():
            raw = node.sections.get(section)
            if not raw:
                continue
            section_item = QTreeWidgetItem(file_item, [label])
            for name, payload in self._entries(raw):
                leaf = QTreeWidgetItem(section_item, [name])
                # Always set, not just for _CLICKABLE_SECTIONS — the
                # context menu's Rename action (2026-08-04) needs to
                # identify a leaf's section regardless of whether left-click
                # routes it anywhere yet (Rules/Points/Clone profiles have
                # no edit form, but a bare rename doesn't need one).
                leaf.setData(0, Qt.ItemDataRole.UserRole, ("leaf", section, payload))
                if section == "cells" and isinstance(raw, dict):
                    self._add_nested_cell_children(leaf, raw.get(name) or {})
        for child in node.children:
            self._build_file_item(file_item, child, parent_path=node.path)

    @staticmethod
    def _add_nested_cell_children(leaf, cell_data: dict) -> None:
        """Composite cells (clone_placements:, Phase 4 recursion) show their
        nested content as read-only child nodes — the "tree" Denis actually
        meant (2026-08-06: "если у нас вложенные целлы могут быть, то
        скорее не список, а дерево") once CellDock's own internal editor
        was built as tabs instead (see gui/docks/cell_editor.py's module
        docstring on why). Not clickable (no UserRole leaf data set) —
        purely a navigation aid; editing content still goes through
        CellDock via "Edit cell...", not by clicking these."""
        for nested in cell_data.get("clone_placements") or []:
            if not isinstance(nested, dict):
                continue
            content = (f"cell:{nested['cell']}" if nested.get("cell") is not None
                      else f"role:{nested.get('role', '?')}")
            QTreeWidgetItem(leaf, [f"{nested.get('name', '?')} ({content})"])

    @staticmethod
    def _entries(raw):
        """Yields (display name, click payload), sorted by name. Dict
        sections (cells/extract_profiles/...) are keyed by name — the
        payload is the name itself. List sections (rules/clone_placements/
        thermal_via_arrays/coordinate_placements) carry their own name
        field — the payload is the whole entry, needed by placement_picked
        (load_placement wants the full dict, not just the name). rules:
        entries may omit name: entirely (same "name or net" fallback as
        config/models.py's rule_effective_name()); coordinate_placements:
        entries may likewise omit name: (defaults to cluster/role, see
        coordinate_placement_effective_name()) — same fallback shape,
        cluster+role instead of net."""
        if isinstance(raw, dict):
            for name in sorted(raw.keys()):
                yield name, name
            return
        named = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            # Non-empty check, not key-presence — `cluster: null` (or empty
            # strings) must fall through to "no display name", not render as a
            # literal "None/ROLE" (2026-08-12, Group 2 fix).
            display_name = (e.get("name") or e.get("net")
                            or (f"{e.get('cluster')}/{e.get('role')}"
                                if e.get("cluster") and e.get("role") else None))
            if display_name:
                named.append((display_name, e))
        for display_name, entry in sorted(named, key=lambda pair: pair[0]):
            yield display_name, entry

    # ── Click routing (left-click anywhere in the tree) ─────────────────

    def _on_clicked(self, item, column) -> None:
        file_ctx = self._file_context_for_item(item)
        if file_ctx is not None:
            self.file_selected.emit(file_ctx[0])

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return  # clicked a file/category header with no click target
        if data[0] == "category":
            return  # clicked a section header with no click target
        if data[0] != "leaf":
            return
        _kind, section, ref = data
        if section == "cells":
            self.cell_picked.emit(ref)
        elif section == "clone_placements":
            self.placement_picked.emit(ref)
        elif section == "extract_profiles":
            self.profile_picked.emit(ref)
        elif section == "thermal_via_arrays":
            self.thermal_via_picked.emit(ref)
        elif section == "coordinate_placements":
            # A normal leaf now (2026-08-12, Group 1) — the payload is the
            # full entry dict, loaded into the merged PlacerDock's coordinate
            # mode, exactly like clone_placements/placement_picked.
            self.coordinate_placements_picked.emit(ref)
        elif section == "points":
            self.points_picked.emit(ref)
        elif section == "rules":
            self.rule_picked.emit(ref)

    # ── Context menu (right-click anywhere under a file) ────────────────

    def _file_context_for_item(self, item) -> Optional[tuple]:
        """Walks up from `item` (inclusive) to the nearest file node —
        every action below operates on that file regardless of whether the
        file header itself, a category, or a leaf was actually clicked."""
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data is not None and data[0] == "file":
                return data[1], data[2]  # (file_path, parent_path)
            item = item.parent()
        return None

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        file_ctx = self._file_context_for_item(item)
        if file_ctx is None:
            return
        file_path, parent_path = file_ctx

        # Right-clicking outside the current selection replaces it with
        # just this item — standard tree UX, and what makes a plain
        # single right-click on one leaf (the common case) behave exactly
        # like before ExtendedSelection was turned on for Export.
        if item not in self.tree.selectedItems():
            self.tree.clearSelection()
            item.setSelected(True)

        menu = QMenu(self.tree)

        leaf_data = item.data(0, Qt.ItemDataRole.UserRole)
        if leaf_data is not None and leaf_data[0] == "leaf":
            _kind, section, _payload = leaf_data
            old_name = item.text(0)
            if section == "cells":
                menu.addAction(_("Edit cell...")).triggered.connect(
                    lambda: self.cell_edit_requested.emit(old_name, file_path))
            menu.addAction(_("Rename...")).triggered.connect(
                lambda: self._on_rename(file_path, section, old_name))
            menu.addAction(_("Delete...")).triggered.connect(
                lambda: self._on_delete(file_path, section, old_name))
            menu.addSeparator()

        selected_leaves = self._selected_export_items()
        if selected_leaves:
            label = _("Export selected...") if len(selected_leaves) > 1 else _("Export...")
            menu.addAction(label).triggered.connect(
                lambda: self._on_export(selected_leaves))
            menu.addSeparator()

        menu.addAction(_("Add cell...")).triggered.connect(
            lambda: self.add_cell_requested.emit(file_path))
        menu.addAction(_("Add thermal via pad...")).triggered.connect(
            lambda: self.add_thermal_via_requested.emit(file_path))
        menu.addAction(_("Add coordinate placement...")).triggered.connect(
            lambda: self.add_coordinate_placement_requested.emit(file_path))
        menu.addAction(_("Add placer...")).triggered.connect(
            lambda: self.add_placer_requested.emit(file_path))
        menu.addAction(_("Add point...")).triggered.connect(
            lambda: self.add_point_requested.emit(file_path))
        menu.addAction(_("Add rule...")).triggered.connect(
            lambda: self.add_rule_requested.emit(file_path))
        menu.addAction(_("Add included file...")).triggered.connect(
            lambda: self._add_included_file(file_path))
        if parent_path is not None:
            menu.addSeparator()
            menu.addAction(_("Remove this file")).triggered.connect(
                lambda: self._remove_file(file_path, parent_path))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_rename(self, file_path: Path, section: str, old_name: str) -> None:
        """Renames a leaf entry — for cells:/points: (see gui/docks/
        rename.py's CASCADE_FIELD) this also rewrites every cell:/
        anchor_point: reference to it anywhere in the whole include: graph,
        not just file_path itself, so the entry's own file is resolved
        again from the CURRENT root (self._root_path), not assumed to be
        the graph root."""
        new_name, ok = QInputDialog.getText(
            self, _("Rename"), _("New name for {old!r}:").format(old=old_name), text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        try:
            changed = rename_entry(self._root_path, file_path, section, old_name, new_name)
        except OSError as e:
            QMessageBox.warning(self, _("Rename failed"), str(e))
            return

        self.refresh()
        message = _("Renamed {old!r} to {new!r} — {count} file(s) updated: {files}").format(
            old=old_name, new=new_name, count=len(changed),
            files=", ".join(display_path(p) for p in changed))
        if section not in CASCADE_FIELD:
            message += " " + _(
                "If any CLI command uses --only/--profile {old!r}, update that separately — "
                "this only rewrites YAML files, it can't see command-line usage.").format(old=old_name)
        QMessageBox.information(self, _("Renamed"), message)

    def _on_delete(self, file_path: Path, section: str, name: str) -> None:
        """Removes a leaf entry (one at a time — see the tree's
        ExtendedSelection docstring, Export is the multi-entity one, not
        this). Backs up file_path (and, for a cascade, every other file it
        touches) before writing — see gui/docks/entity_delete.py. For
        cells:/points: (CASCADE_FIELD), the whole include: graph is scanned
        for references FIRST: with none found this is a plain confirm; with
        some found the dialog lists them and asks whether to also delete
        those referencing entries (Denis, 2026-08-05: "Предупреждать.
        Спросить, удалить ли связанные ссылки? Если да, их тоже удалить.")
        — declining cancels the whole delete rather than leaving a
        dangling reference behind."""
        field_name = CASCADE_FIELD.get(section)
        refs = {}
        if field_name and self._root_path is not None:
            refs = find_references(collect_graph_files(self._root_path), field_name, name)

        cascade = False
        if refs:
            lines = "\n".join(
                _("{file}: {entries}").format(file=display_path(path), entries=", ".join(descs))
                for path, descs in refs.items())
            reply = QMessageBox.question(
                self, _("Delete {name!r}").format(name=name),
                _("{name!r} is still referenced by:\n{refs}\n\n"
                  "Also delete these referencing entries? Cancel leaves everything untouched.")
                .format(name=name, refs=lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if reply != QMessageBox.StandardButton.Yes:
                return
            cascade = True
        else:
            reply = QMessageBox.question(
                self, _("Delete {name!r}").format(name=name),
                _("Delete {name!r} from {section}: in {file}?")
                .format(name=name, section=section, file=display_path(file_path)))
            if reply != QMessageBox.StandardButton.Yes:
                return

        report = delete_entry(self._root_path, file_path, section, name, cascade=cascade)
        self.refresh()

        message = _("Deleted {name!r}. Backed up: {backups}.").format(
            name=name, backups=", ".join(display_path(p) for p in report["backups"]))
        if report["cascade_files"]:
            message += " " + _("Also removed references from: {files}.").format(
                files=", ".join(display_path(p) for p in report["cascade_files"]))
        QMessageBox.information(self, _("Deleted"), message)

    def _selected_export_items(self) -> list:
        """Currently selected tree leaves, as ExportItem tuples — file/
        category headers in the selection are ignored (Export only makes
        sense for actual entries)."""
        items = []
        for tree_item in self.tree.selectedItems():
            data = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if data is None or data[0] != "leaf":
                continue
            file_ctx = self._file_context_for_item(tree_item)
            if file_ctx is None:
                continue
            _kind, section, payload = data
            items.append(ExportItem(source_path=file_ctx[0], section=section,
                                    name=tree_item.text(0), payload=payload))
        return items

    def _on_export(self, items: list) -> None:
        """Copies `items` into a separate file — the originals are left
        exactly as they are (Denis, 2026-08-05: "Запись остаётся на месте.
        Просто экспортирует выделенное в отдельный файл. Перенос пока не
        делаем."). Merge is the default and only choice when the target is
        new/empty; an existing non-empty target additionally offers
        Overwrite (Denis: "галочку в экспортном диалоге завести: смержить,
        перезаписать")."""
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _("Export to..."), str(self._root_path.parent if self._root_path else ""),
            "YAML (*.yaml *.yml)")
        if not chosen:
            return
        target_path = Path(chosen)
        if not target_path.exists():
            target_path.write_text("{}\n", encoding="utf-8")

        overwrite = False
        if yaml_io.load_data(target_path):
            box = QMessageBox(self)
            box.setWindowTitle(_("Export"))
            box.setText(_("{name} already has content — merge the exported entries into it, "
                          "or overwrite the whole file?").format(name=target_path.name))
            merge_btn = box.addButton(_("Merge"), QMessageBox.ButtonRole.AcceptRole)
            overwrite_btn = box.addButton(_("Overwrite"), QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in (merge_btn, overwrite_btn):
                return
            overwrite = clicked is overwrite_btn

        try:
            export_entries(target_path, items, overwrite=overwrite)
        except OSError as e:
            QMessageBox.warning(self, _("Export failed"), str(e))
            return
        QMessageBox.information(
            self, _("Exported"),
            _("Exported {count} entr{suffix} to {name}").format(
                count=len(items), suffix=_("y") if len(items) == 1 else _("ies"),
                name=display_path(target_path)))

    def _add_included_file(self, file_path: Path) -> None:
        """QFileDialog's SAVE mode (not Open) is used deliberately — it
        lets the user type a filename that doesn't exist yet, per Denis:
        "если включаем файл, его может реально и не быть". The dialog
        itself never touches disk; if the chosen path doesn't exist, an
        empty file is created here before wiring include:."""
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _("Add included file"), str(file_path.parent), "YAML (*.yaml)")
        if not chosen:
            return
        chosen_path = Path(chosen)
        if not chosen_path.exists():
            chosen_path.write_text("{}\n", encoding="utf-8")

        bad_keys = non_includable_keys(chosen_path)
        if bad_keys:
            QMessageBox.warning(
                self, _("Cannot include"),
                _("{name} has root-config-only key(s) {keys} that include: can't merge — "
                  "move them out, or point Root at this file directly instead.")
                .format(name=chosen_path.name, keys=sorted(bad_keys)))
            return

        rel = Path(os.path.relpath(chosen_path, file_path.parent)).as_posix()
        add_include(file_path, rel)
        self.refresh()

    def _remove_file(self, file_path: Path, parent_path: Path) -> None:
        reply = QMessageBox.question(
            self, _("Remove file"),
            _("Remove {name!r} from {parent!r}'s include:? The file itself is not deleted — "
              "this can be undone later by adding it again.")
            .format(name=file_path.name, parent=parent_path.name))
        if reply != QMessageBox.StandardButton.Yes:
            return
        disable_include(parent_path, file_path)
        self.refresh()
