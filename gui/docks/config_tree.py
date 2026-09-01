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
Rules -> RuleDock.load_entry, added 2026-08-05) — Clone profiles is the one
section still with no GUI edit form, shown read-only for now, same deliberate
scope limit as before. Points and Entities are the two DIALOG sections
since 2026-09-01 (plan plan_2026_09_01_points_dialog.md /
plan_2026_09_01_tools_dialog_and_entity_roles.md): Points in the non-modal
PointsDialog, an Entity's electrical fields (Nets/Net overrides/Refs) in the
non-modal ToolsDialog — both opened by a DOUBLE click on their leaf
(points_edit_requested / entity_edit_requested). A single click on a points:
leaf does nothing; on an Entities leaf it keeps loading Placer's Entity
source (entity_picked).

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

Right-click context menu (2026-08-03): file-level actions always operate on
"the nearest file ancestor" (Denis: "Если выбран файл или его десцендант..."
— the descendant doesn't change WHICH file the action targets). Since
2026-08-13 (plan context_menu_by_section) the "Add ..." block is ALSO
section-aware: right-clicking a leaf or category shows only that section's
own Add action (cells -> Add cell, rules -> Add rule, ...); clone_profiles
and extract_profiles show none (an extract profile is created via the
unconditional "New Extract..." with the dialog's "Also save as extract_
profile" checked), and a file
header (or a read-only nested cell node) shows ALL of them — Denis's
explicit decision, otherwise a fresh file with no sections yet couldn't
create its first entity. Remove this file still appears only for non-root
files.

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
import cProfile
import io
import logging
import os
import pstats
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QDockWidget, QFileDialog,
                              QInputDialog, QMenu, QMessageBox, QTreeWidget,
                              QTreeWidgetItem, QTreeWidgetItemIterator,
                              QVBoxLayout, QWidget)

from kicadstamp.config.includes import IncludeTreeNode, walk_include_tree
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from .. import settings, yaml_io
from ._common import (add_include, disable_include, display_path,
                      highlight_stylesheet_for, non_includable_keys,
                      upsert_list_entry)
from .entity_delete import backup_file, delete_entry, find_references
from .entity_export import ExportItem, export_entries
from .rename import CASCADE_FIELD, collect_graph_files, entry_effective_name, rename_entry

logger = logging.getLogger(__name__)

# Display label per recognized section, in the order shown under a file
# node. Order matches config/includes.py's _LIST_SECTIONS + _DICT_SECTIONS.
_SECTION_LABELS = {
    "chains": _("Chains"),
    "clone_placements": _("Clone placements"),
    "thermal_via_arrays": _("Thermal via arrays"),
    "coordinate_placements": _("Coordinate placements"),
    "net_traces": _("Net traces"),
    "cells": _("Cells"),
    "points": _("Points"),
    "extract_profiles": _("Extract profiles"),
    "clone_profiles": _("Clone profiles"),
    # Entity/Placement split (phase 5.6): entities are the new "what" records,
    # trees are their placement storage — both shown as categories (Trees is
    # navigation-only; the TreesDock owns editing).
    "entities": _("Entities"),
    "trees": _("Trees"),
}

# Section -> (menu label, signal name) for the context menu's "Add ..."
# block (2026-08-13, plan context_menu_by_section): right-clicking a leaf or
# category of a section shows ONLY that section's own Add action. Order here
# is the order the actions appear when ALL of them are shown (file header).
# extract_profiles is deliberately ABSENT — creating an extract profile is a
# plain "New Extract..." (unconditional, below) with the dialog's "Also save
# as extract_profile" checked, so it needs no section Add action; clone_
# profiles is deliberately ABSENT too — it has no GUI edit form (same
# deliberate scope limit as the module docstring's read-only note), so a
# right-click on it shows no Add action at all.
_ADD_ACTION_BY_SECTION = {
    "cells": (_("Add cell..."), "add_cell_requested"),
    "thermal_via_arrays": (_("Add thermal via pad..."), "add_thermal_via_requested"),
    "coordinate_placements": (_("Add coordinate placement..."), "add_coordinate_placement_requested"),
    "clone_placements": (_("Add placer..."), "add_placer_requested"),
    "points": (_("Add point..."), "add_point_requested"),
    "chains": (_("Add chain..."), "add_chain_requested"),
}

# Leaf-label marker for an entry carrying a comment — a single source of truth,
# used BOTH when building the leaf label (_build_file_item) and when stripping
# it back to recover the entry's name (_item_identity, for selection restore).
# One copy of the glyph string, so the two can never drift apart.
_COMMENT_GLYPH = "📝 "


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
    # as placement_picked. Since 2026-09-01 (plan plan_2026_09_01_thermal_via
    # _dialog.md) DockHub also opens the standalone Thermal via dialog
    # (_open_thermal_via_dialog) on this signal, instead of a DetailDock page.
    thermal_via_picked = pyqtSignal(object)
    # Fired by the context menu's "Add placer..." — PlacerDock listens via
    # its new_placement() entry point (opens the form blank rather than
    # writing a raw stub straight to YAML).
    add_placer_requested = pyqtSignal(object)
    # Fired by the context menu's "Add thermal via pad..." (2026-08-03,
    # replaces writing a raw {"name": ...} stub straight to YAML) —
    # ThermalViaArrayDock listens via its new_thermal_via() entry point,
    # same reasoning as add_placer_requested above. Since 2026-09-01 DockHub's
    # delegate opens the standalone Thermal via dialog with a fresh blank form
    # (see _start_new_thermal_via), not a DetailDock page.
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
    # Fired by a DOUBLE click on a points: leaf (2026-09-01, plan
    # plan_2026_09_01_points_dialog.md) — opens the (non-modal) Points edit
    # dialog (DockHub: points_edit_requested -> load_entry + _open_points_
    # dialog). Single click on a points: leaf does NOTHING anymore — the old
    # points_picked emission was removed from _on_clicked (the Points form
    # lives in a dialog now, not a DetailDock page). points: is a DICT
    # section (see _entries()), so the payload is just the name, unlike
    # placement_picked/thermal_via_picked's full-dict payload; PointsDock's
    # load_entry() re-reads the file for the actual data.
    points_edit_requested = pyqtSignal(str)
    # Fired by the context menu's "Add point..." (2026-08-05) — opens the
    # (non-modal) Points dialog with a fresh blank form (DockHub: add_point_
    # requested -> _start_new_point), rather than writing a raw stub straight
    # to YAML.
    add_point_requested = pyqtSignal(object)
    # Fired by a DOUBLE click on a chains: CHAIN node (2026-09-01, plan
    # plan_2026_09_01_rules_to_chains.md) — the payload is the full chain
    # dict; DockHub opens the chain-edit dialog. A single click on a chain/
    # pad node does NOTHING (like points/entities after 2026-09-01).
    chain_edit_requested = pyqtSignal(object)
    # Fired by a DOUBLE click on a chains: PAD leaf — (chain_dict, pad_index);
    # DockHub opens the pad-edit dialog for that specific spoke.
    pad_edit_requested = pyqtSignal(object, int)
    # Fired by the context menu's "Add chain..." — opens the chain-edit dialog
    # with a fresh blank form (mirror of add_point_requested).
    add_chain_requested = pyqtSignal(object)
    # Fired by the context menu's "Add spoke..." on a chain node — payload is
    # the chain dict the new pad belongs to; DockHub opens the pad-edit dialog
    # in add mode.
    add_pad_requested = pyqtSignal(object)
    # Fired by the context menu's "Redraw chains..." on a chains: ANCHOR node
    # (2026-09-01, Denis: "если корневой компонент, то вообще все его спицы")
    # — payload is the LIST of chain dicts under that anchor; DockHub runs ONE
    # ApplyPipeline for all of them (chain_dock.redraw_chains), so an anchor's
    # whole spoke set redraws together.
    anchor_redraw_requested = pyqtSignal(object)
    # Fired by the context menu's "Redraw chain" on a chains: CHAIN node
    # (2026-09-01, plan rules_to_chains) — payload is the chain dict. Moved
    # OUT of the old RuleDock's button into the tree's context menu (the plan's
    # "Redraw цепей/падов и Bulk-set Cell переносим в контекстные меню дерева"):
    # DockHub routes it to the ChainDock redraw machinery (worker-thread
    # ApplyPipeline run, same as the old _on_redraw_rule).
    chain_redraw_requested = pyqtSignal(object)
    # Fired by the context menu's "Redraw spoke" on a chains: PAD leaf —
    # (chain dict, pad index), isolates exactly that one spoke for redraw.
    pad_redraw_requested = pyqtSignal(object, int)
    # Fired by the context menu's "Bulk set Cell for net..." on the Chains
    # CATEGORY / a chains: CHAIN node — payload is the net to pre-select in
    # the bulk dialog (or None when unknown); DockHub opens the
    # BulkSetCellDialog and applies the chosen cell across the whole graph
    # (moved from the old RuleDock, plan rules_to_chains).
    bulk_set_cell_requested = pyqtSignal(object)
    # Fired when a net_traces leaf is clicked (2026-08-21, plan net_trace_dock) —
    # same list-section full-dict payload as rule_picked. NetTraceDock.
    # load_entry() listens.
    net_trace_picked = pyqtSignal(object)
    # Fired by the context menu's "New Extract..." and the Tools menu's
    # "New Extract..." (2026-08-31, plan extract_dialog_and_hide_existing.
    # md): a plain fresh capture — no file argument (the project root is
    # already known). Opens the (non-modal) Extract dialog and clears/auto-
    # fills the fields via ExtractDock.prepare_new_extract.
    new_extract_requested = pyqtSignal()
    # Fired on EVERY click in the tree (file header, category, or leaf) —
    # see module docstring for why this replaces the three independent
    # FilePickerDock role signals.
    file_selected = pyqtSignal(object)
    # Entity leaf click (phase 5.6): emitted with the Entity's NAME so
    # PlacerDock's Entity source can load it (see placer.set_selected_entity).
    entity_picked = pyqtSignal(str)
    # Fired by a DOUBLE click on an Entities leaf (2026-09-01, plan
    # plan_2026_09_01_tools_dialog_and_entity_roles.md) — opens the (non-modal)
    # "Edit template" dialog (ToolsDock) pre-loaded with that Entity (DockHub:
    # entity_edit_requested -> tools_dock.load_entity + _open_tools_dialog).
    # Single click stays entity_picked (Placer Entity source), unchanged.
    entity_edit_requested = pyqtSignal(str)
    # Fired AFTER a ConfigTreeDock action actually changed the include:
    # graph's file set or an entry's name (_on_rename/_on_delete/
    # _add_included_file/_remove_file) — DockHub listens to refresh every
    # dock's graph-derived combo choices (file/name lists), which would
    # otherwise go stale until the root is reassigned (2026-08-15, plan
    # graph_changed_broadcast). Deliberately NOT emitted from the initial
    # refresh() (that's first population, not a change) or from _on_export()
    # (copies content without wiring it into include: — the graph only
    # changes once the file is added via _add_included_file separately).
    graph_changed = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(_("Config"), main_window)
        # Stable QDockWidget identity for QMainWindow.saveState()/restoreState()
        # (handoff sync_skip_message_and_view_menu §0) — without a unique
        # objectName Qt cannot reliably map a saved layout blob back to this
        # dock between runs.
        self.setObjectName("config_tree_dock")
        self._main_window = main_window
        self._root_path: Optional[Path] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # Highlight for the selected item (2026-08-15, plan
        # configurator_panel) — a bare QTreeWidget with no stylesheet had
        # near-invisible selection on Windows, same as the Components tree;
        # applied at startup and re-applied live by DockHub when the
        # Settings tab's highlight changes. Selector is written against the
        # base class (QTreeView), since QSS selectors for a QTreeWidget
        # subclass match by its base class name.
        self.tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))
        # Multi-select (2026-08-05) — only Export needs several leaves
        # selected at once (Denis: "экспортировать сущность (выделенные
        # сущности)"); Delete stays one entry at a time (see _on_delete),
        # and left-click routing (_on_clicked) is unaffected by selection
        # mode either way.
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemClicked.connect(self._on_clicked)
        # Double click on a points: leaf -> Points edit dialog (2026-09-01,
        # plan plan_2026_09_01_points_dialog.md) — see _on_double_clicked.
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)

        # F2 = Rename on the current leaf (2026-08-25, the project's first
        # shortcut — see docs/hotkeys.md). WidgetWithChildrenShortcut, NOT the
        # default WindowShortcut: F2 must fire only while this tree (or one of
        # its children) has focus, so it never steals F2 from other widgets
        # that may get their own shortcut later.
        self._rename_shortcut = QShortcut(QKeySequence("F2"), self.tree)
        self._rename_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._rename_shortcut.activated.connect(self._on_rename_shortcut)

        self.setWidget(container)

    def apply_highlight(self) -> None:
        """Re-apply the highlight stylesheet to this tree's selected item —
        one of the three highlight consumers (see gui/docks/configurator.py).
        Called at construction (reads the current settings.state) and by
        DockHub whenever the Settings tab's highlight_changed fires."""
        self.tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))

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

    # ── Selection capture/restore around refresh() (2026-08-27) ──────────
    #
    # Every dock's saved signal feeds into refresh() (gui/dock_hub.py), which
    # does tree.clear() + a full rebuild — without capture the selection was
    # dropped on EVERY Save anywhere in the app (Denis: "теряется выделенный
    # компонент... при экстракте или размещении"). Identity is rebuilt from
    # the parent chain + the item's own label — the 3-element UserRole tuples
    # ("file"/"category"/"leaf") are NOT widened (several call sites
    # destructure them by fixed arity: _on_clicked, _on_context_menu,
    # _on_rename/_on_delete, _on_export).

    def _item_identity(self, item: QTreeWidgetItem) -> Optional[tuple]:
        """A rebuild-stable identity for `item`, or None for a kind this can't
        identify. Walks the parent chain: item's own data alone is not enough
        for a leaf/category — the owning file's path is needed too (same
        name/section can exist in several files). Kinds built by this tree:
        file / category / leaf / anchor / chain / pad (the chains: nested
        nodes from _add_chains_children)."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return None
        kind = data[0]
        if kind == "file":
            return ("file", data[1])          # data[1] is the file's own Path
        if kind == "category":
            file_item = item.parent()
            file_data = file_item.data(0, Qt.ItemDataRole.UserRole) if file_item else None
            if file_data is None:
                return None
            return ("category", file_data[1], data[1])   # (file path, section)
        if kind == "leaf":
            section_item = item.parent()
            file_item = section_item.parent() if section_item else None
            file_data = file_item.data(0, Qt.ItemDataRole.UserRole) if file_item else None
            if file_data is None:
                return None
            label = item.text(0)
            name = (label[len(_COMMENT_GLYPH):] if label.startswith(_COMMENT_GLYPH)
                    else label)
            return ("leaf", file_data[1], data[1], name)  # (file path, section, name)
        if kind in ("anchor", "chain", "pad"):
            # chains: nested node — its owning file is the nearest file node
            # (anchor -> category -> file / chain -> anchor -> category -> file
            # / pad -> chain -> anchor -> category -> file).
            file_item = self._file_context_for_item(item)
            if file_item is None:
                return None
            file_path = file_item[0]
            if kind == "anchor":
                return ("anchor", file_path, data[1], data[2])  # anchor key
            if kind == "chain":
                chain = data[2]
                name = entry_effective_name("chains", chain)
                return ("chain", file_path, data[1], name)
            # pad — identity is (parent chain effective name, pad index).
            chain = data[2]
            chain_name = entry_effective_name("chains", chain)
            return ("pad", file_path, data[1], chain_name, data[3])
        return None

    def _capture_selection(self) -> list:
        """Rebuild-stable identities of the currently selected items — the
        only thing refresh() may safely remember across tree.clear()."""
        return [ident for item in self.tree.selectedItems()
                if (ident := self._item_identity(item)) is not None]

    def _restore_selection(self, identities: list) -> None:
        """Best-effort: an identity that no longer exists (renamed/deleted
        entry) is simply not re-selected, never an error. Scrolls to the first
        match only (matches this tree's existing single-focus navigation, even
        though selectedItems() elsewhere allows multi-select)."""
        if not identities:
            return
        wanted = set(identities)
        first_match = None
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            ident = self._item_identity(item)
            if ident is not None and ident in wanted:
                item.setSelected(True)
                if first_match is None:
                    first_match = item
            it += 1
        if first_match is not None:
            self.tree.scrollToItem(first_match)

    def refresh(self) -> None:
        """Public — also called by PlacerDock's saved signal (see
        gui/dock_hub.py) so a successful Save shows up here without
        reassigning the root file.

        TEMPORARY (2026-08-22, diagnosing a slow-redraw complaint): when
        KICADSTAMP_PROFILE_TREE=1 is set, times the three phases and runs
        the whole body under cProfile, printing a one-line summary to the
        console and the full cProfile stats to
        diagnostics/tree_refresh_profile.txt — NOT to the Log dock (see
        [[project_linux_small_screen_gui_constraint]], a dump this size
        would be disruptive there). Remove this block once the bottleneck
        is found and fixed."""
        if os.environ.get("KICADSTAMP_PROFILE_TREE") == "1":
            self._refresh_profiled()
            return
        selection = self._capture_selection()
        self.tree.clear()
        if self._root_path is None:
            return
        try:
            node = walk_include_tree(str(self._root_path))
        except (ValidationError, OSError) as e:
            QTreeWidgetItem(self.tree, [str(e)])
            return
        self._build_file_item(self.tree.invisibleRootItem(), node, parent_path=None)
        self.tree.expandAll()
        self._restore_selection(selection)

    def _refresh_profiled(self) -> None:
        """See the TEMPORARY note on refresh() — same body, instrumented.
        Each call writes its OWN numbered output file (_refresh_call_count)
        instead of overwriting the same one — a driver script calling
        refresh() N times in a row (cold vs. warm walk_include_tree cache)
        needs each run's cProfile detail kept separately, not just the
        last one."""
        ConfigTreeDock._refresh_call_count = getattr(
            ConfigTreeDock, "_refresh_call_count", 0) + 1
        call_no = ConfigTreeDock._refresh_call_count
        profiler = cProfile.Profile()
        profiler.enable()
        t0 = time.perf_counter()
        selection = self._capture_selection()
        self.tree.clear()
        if self._root_path is None:
            profiler.disable()
            return
        try:
            node = walk_include_tree(str(self._root_path))
        except (ValidationError, OSError) as e:
            QTreeWidgetItem(self.tree, [str(e)])
            profiler.disable()
            return
        t1 = time.perf_counter()
        self._build_file_item(self.tree.invisibleRootItem(), node, parent_path=None)
        t2 = time.perf_counter()
        self.tree.expandAll()
        self._restore_selection(selection)
        t3 = time.perf_counter()
        # repaint() is a SYNCHRONOUS immediate repaint (unlike update(), which
        # just schedules one for later) — without this, a deferred paint cost
        # would happen invisibly after profiler.disable() below and never show
        # up in either the timings or the cProfile stats, understating the
        # actual on-screen slowness Denis is seeing.
        self.tree.repaint()
        t4 = time.perf_counter()
        profiler.disable()
        print(
            f"[ConfigTreeDock.refresh #{call_no}] walk={1000*(t1-t0):.1f}ms "
            f"build={1000*(t2-t1):.1f}ms expand={1000*(t3-t2):.1f}ms "
            f"paint={1000*(t4-t3):.1f}ms total={1000*(t4-t0):.1f}ms"
        )
        out_path = (Path(__file__).resolve().parents[2] / "diagnostics" /
                    f"tree_refresh_profile_{call_no:02d}.txt")
        out_path.parent.mkdir(exist_ok=True)
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(40)
        out_path.write_text(stream.getvalue(), encoding="utf-8")
        print(f"[ConfigTreeDock.refresh #{call_no}] full profile written to {out_path}")

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
            # Tag the category with its section (2026-08-13, plan
            # context_menu_by_section) so the context menu can show only that
            # section's "Add ..." action — category headers used to carry NO
            # UserRole data at all (the old "if data[0] == 'category'" branch
            # in _on_clicked was unreachable); left-click stays a no-op either
            # way (the plan deliberately doesn't touch _on_clicked).
            section_item.setData(0, Qt.ItemDataRole.UserRole, ("category", section))
            if section == "chains":
                # Nested tree (2026-09-01, plan rules_to_chains): category ->
                # anchor -> chain -> pad leaves. A chain is identified by its
                # anchor (anchor_ref/anchor_role/anchor_point) and its net; its
                # pads (ManualSpokes) are LEAVES, not a table. See
                # _add_chains_children for the node UserRole shapes.
                self._add_chains_children(section_item, raw)
                continue
            for name, payload in self._entries(raw, section):
                # entry's comment marker. NOTE: `payload` is NOT always the
                # record dict — for the dict-sections (cells/points, keyed by
                # name) _entries() yields the NAME as payload, so the comment
                # must come from raw.get(name); list-sections (chains/
                # clone_placements/...) already yield the full record dict.
                entry_data = raw.get(name) if isinstance(raw, dict) else payload
                comment = entry_data.get('comment') if isinstance(entry_data, dict) else None
                label = f"{_COMMENT_GLYPH}{name}" if comment else name
                leaf = QTreeWidgetItem(section_item, [label])
                # Always set, not just for _CLICKABLE_SECTIONS — the
                # context menu's Rename action (2026-08-04) needs to
                # identify a leaf's section regardless of whether left-click
                # routes it anywhere yet (Rules/Points/Clone profiles have
                # no edit form, but a bare rename doesn't need one).
                leaf.setData(0, Qt.ItemDataRole.UserRole, ("leaf", section, payload))
                if comment:
                    leaf.setToolTip(0, comment)
                if section == "cells" and isinstance(raw, dict):
                    self._add_nested_cell_children(leaf, raw.get(name) or {})
        for child in node.children:
            self._build_file_item(file_item, child, parent_path=node.path)

    @staticmethod
    def _chain_anchor_key(chain: dict) -> str:
        """The anchor identity a chain node groups under — anchor_ref /
        anchor_role / anchor_point (or '?' for a not-yet-valid chain)."""
        return (chain.get("anchor_ref") or chain.get("anchor_role")
                or chain.get("anchor_point") or "?")

    @staticmethod
    def _chain_anchor_label(anchor_key: str) -> str:
        """Display label of an anchor group node — the anchor key plus a
        stable prefix so it reads as a grouping header, not a chain."""
        return f"{_('Anchor')}: {anchor_key}"

    def _add_chains_children(self, section_item, raw) -> None:
        """chains: category -> anchor -> chain -> pad leaves (2026-09-01, plan
        plan_2026_09_01_rules_to_chains.md). UserRole shapes:
          - anchor node: ("anchor", "chains", anchor_key) — grouping only;
          - chain node: ("chain", "chains", chain_payload) — full chain dict,
            double-click -> chain_edit_requested;
          - pad leaf:  ("pad", "chains", chain_payload, pad_index) — full
            chain dict + index of this spoke, double-click -> pad_edit_requested.
        Anchors sort by key, chains by effective name, pads by pad number."""
        anchors: dict[str, QTreeWidgetItem] = {}
        chains: list[dict] = [c for c in raw if isinstance(c, dict)]
        for chain in sorted(chains,
                            key=lambda c: entry_effective_name("chains", c)):
            anchor_key = self._chain_anchor_key(chain)
            anchor_item = anchors.get(anchor_key)
            if anchor_item is None:
                anchor_item = QTreeWidgetItem(
                    section_item, [self._chain_anchor_label(anchor_key)])
                anchor_item.setData(0, Qt.ItemDataRole.UserRole,
                                    ("anchor", "chains", anchor_key))
                anchors[anchor_key] = anchor_item
            name = entry_effective_name("chains", chain)
            comment = chain.get('comment') if isinstance(chain, dict) else None
            label = f"{_COMMENT_GLYPH}{name}" if comment else name
            chain_item = QTreeWidgetItem(anchor_item, [label])
            chain_item.setData(0, Qt.ItemDataRole.UserRole,
                               ("chain", "chains", chain))
            if comment:
                chain_item.setToolTip(0, comment)
            # Pads sort by pad number (plan rules_to_chains: "пады по
            # pad-номеру"), NOT by source order. The tree index in the pad's
            # UserRole must stay the spoke's INDEX in the chain's spokes: list
            # (that's what the pad dialog/redraw/delete operate on), so we
            # sort a list of (index, spoke) pairs, not the dicts themselves.
            for idx, spoke in sorted(
                    ((i, s) for i, s in enumerate(chain.get("spokes") or [])
                     if isinstance(s, dict)),
                    key=lambda pair: str(pair[1].get("pad", "?"))):
                pad_label = str(spoke.get("pad", "?"))
                pad_item = QTreeWidgetItem(chain_item, [pad_label])
                pad_item.setData(0, Qt.ItemDataRole.UserRole,
                                 ("pad", "chains", chain, idx))
                cell = spoke.get("cell")
                if cell:
                    pad_item.setToolTip(0, _("cell {cell}").format(cell=cell))

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
    def _entries(raw, section):
        """Yields (display name, click payload), sorted by name. Dict
        sections (cells/extract_profiles/...) are keyed by name — the
        payload is the name itself. List sections (chains/clone_placements/
        thermal_via_arrays/coordinate_placements) carry their own name
        field — the payload is the whole entry, needed by placement_picked
        (load_placement wants the full dict, not just the name). The display
        name comes from rename.py's shared entry_effective_name(section, e):
        chains: entries may omit name: (falling back to net:), coordinate_
        placements: may likewise omit name: (falling back to cluster/role) —
        ONE formula, not a per-section inline copy (2026-08-13 review, bug 4).

        Non-empty check, not key-presence — `cluster: null` (or empty
        strings) must fall through to "no display name", not render as a
        literal "None/ROLE" (2026-08-12, Group 2 fix) — entry_effective_name's
        cluster/role fallback is built on the same non-empty condition."""
        if isinstance(raw, dict):
            for name in sorted(raw.keys()):
                yield name, name
            return
        named = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            display_name = entry_effective_name(section, e)
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
        if data[0] in ("anchor", "chain", "pad"):
            # chains: nodes are edited via DOUBLE click (chain_edit_requested /
            # pad_edit_requested) or the context menu — a single click on an
            # anchor/chain/pad node does nothing (same as points/entities since
            # 2026-09-01).
            return
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
            # Single click on a points: leaf does NOTHING since 2026-09-01
            # (plan plan_2026_09_01_points_dialog.md) — the Points form lives
            # in a dialog now, opened by a DOUBLE click (see
            # _on_double_clicked / points_edit_requested).
            pass
        elif section == "net_traces":
            self.net_trace_picked.emit(ref)
        elif section == "entities":
            # The payload is the full entity dict (list section) — emit the
            # NAME (phase 5.6), Placer's Entity source selects by name.
            name = ref.get("name") if isinstance(ref, dict) else ref
            if name:
                self.entity_picked.emit(name)
        elif section == "trees":
            # Trees are edited in the TreesDock (its own QDockWidget) — a
            # leaf click here is navigation only for now.
            pass

    def _on_double_clicked(self, item, column) -> None:
        """Double click opens the matching edit dialog:
          - points: leaf -> points_edit_requested;
          - Entities leaf -> entity_edit_requested;
          - chains: CHAIN node -> chain_edit_requested (the whole chain dict);
          - chains: PAD leaf -> pad_edit_requested (chain dict, pad index).
        Any other leaf/file/category keeps its default double-click behavior."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        kind = data[0]
        if kind in ("anchor", "chain", "pad") and data[1] == "chains":
            if kind == "chain":
                self.chain_edit_requested.emit(data[2])
            elif kind == "pad":
                self.pad_edit_requested.emit(data[2], data[3])
            return
        if kind != "leaf":
            return
        _kind, section, ref = data
        if section == "points":
            self.points_edit_requested.emit(ref)
        elif section == "entities":
            name = ref.get("name") if isinstance(ref, dict) else ref
            if name:
                self.entity_edit_requested.emit(name)

    # ── Context menu (right-click anywhere under a file) ────────────────

    @staticmethod
    def _add_section_for_item(item) -> Optional[str]:
        """The section a right-clicked item's "Add ..." menu block is
        filtered to (2026-08-13, plan context_menu_by_section):
        - a leaf -> its own section (data ("leaf", section, payload));
        - a category header -> its section (tagged ("category", section) in
          _build_file_item);
        - anything else (file header, or a read-only nested cell child with
          no UserRole data at all, see _add_nested_cell_children) -> None,
          meaning "section unknown" -> the caller shows ALL the Add actions
          (Denis's explicit decision: a fresh file with no sections yet must
          still be able to create its first entity)."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return None
        if data[0] in ("leaf", "category"):
            return data[1]
        if data[0] in ("anchor", "chain", "pad") and data[1] == "chains":
            return "chains"
        return None

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

    def _rename_target_for_item(self, item) -> Optional[tuple]:
        """(file_path, section, old_name) for a Rename action, or None when
        `item` is not renameable. Renameable: a regular leaf, and a chains:
        CHAIN node (its name/net is the --only identity; pads and anchors have
        no name of their own). The ONE extraction shared by the context menu's
        "Rename..." and the F2 shortcut (2026-08-25) — the two entry points can
        never drift apart."""
        file_ctx = self._file_context_for_item(item)
        if file_ctx is None:
            return None
        leaf_data = item.data(0, Qt.ItemDataRole.UserRole)
        if leaf_data is None:
            return None
        if leaf_data[0] == "leaf":
            _kind, section, _payload = leaf_data
            return file_ctx[0], section, item.text(0)
        if leaf_data[0] == "chain":
            # old_name = the chain's effective name (name or net), rebuilt from
            # the label the same way _item_identity does (strip the comment
            # glyph).
            label = item.text(0)
            name = (label[len(_COMMENT_GLYPH):] if label.startswith(_COMMENT_GLYPH)
                    else label)
            return file_ctx[0], "chains", name
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

        # Chains: node-specific actions (2026-09-01, plan rules_to_chains) —
        # the nested tree's per-node context menu. The generic Add block below
        # (via _add_section_for_item -> "chains") already supplies "Add
        # chain..." for every chains node (anchor/category get the plan's
        # "Add chain..."; a chain node gets it too, "add another net under this
        # anchor"); these are the node kinds' EXTRA actions:
        #   * chain node -> "Add spoke...", "Redraw chain", "Bulk set Cell...";
        #   * pad leaf   -> "Redraw spoke", "Delete pad...".
        node_data = item.data(0, Qt.ItemDataRole.UserRole)
        if (node_data is not None and node_data[0] in ("anchor", "chain", "pad")
                and node_data[1] == "chains"):
            if node_data[0] == "anchor":
                # Root component (anchor) -> "Redraw chains..." redraws ALL the
                # chains (all the anchor's spokes) under it in ONE pipeline run
                # (Denis, 2026-09-01: "если корневой компонент, то вообще все
                # его спицы").
                chain_payloads = []
                for i in range(item.childCount()):
                    child = item.child(i)
                    child_data = child.data(0, Qt.ItemDataRole.UserRole)
                    if child_data is not None and child_data[0] == "chain":
                        chain_payloads.append(child_data[2])
                if chain_payloads:
                    menu.addAction(_("Redraw chains...")).triggered.connect(
                        lambda checked=False, payloads=chain_payloads:
                        self.anchor_redraw_requested.emit(payloads))
                    menu.addSeparator()
            elif node_data[0] == "chain":
                chain = node_data[2]
                menu.addAction(_("Add spoke...")).triggered.connect(
                    lambda checked=False, c=chain: self.add_pad_requested.emit(c))
                menu.addAction(_("Redraw chain")).triggered.connect(
                    lambda checked=False, c=chain: self.chain_redraw_requested.emit(c))
                net = chain.get("net") if isinstance(chain, dict) else None
                menu.addAction(_("Bulk set Cell for net...")).triggered.connect(
                    lambda checked=False, n=net: self.bulk_set_cell_requested.emit(n))
                menu.addSeparator()
            elif node_data[0] == "pad":
                chain, idx = node_data[2], node_data[3]
                menu.addAction(_("Redraw spoke")).triggered.connect(
                    lambda checked=False, c=chain, i=idx: self.pad_redraw_requested.emit(c, i))
                menu.addAction(_("Delete pad...")).triggered.connect(
                    lambda checked=False, c=chain, i=idx: self._on_delete_pad(file_path, c, i))
                menu.addSeparator()

        # Leaf-only block (Edit cell/Rename/Delete) — the (file_path, section,
        # old_name) triple is extracted by the same _rename_target_for_item the
        # F2 shortcut uses (2026-08-25), so the two entry points stay in sync.
        rename_target = self._rename_target_for_item(item)
        if rename_target is not None:
            section, old_name = rename_target[1], rename_target[2]
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

        # "Add ..." block — since 2026-08-13 (plan context_menu_by_section)
        # filtered by the section of whatever was right-clicked, three
        # distinct outcomes (not collapsed into "known vs unknown", or
        # clone_profiles would wrongly get all seven):
        #   * known section with an Add action -> that ONE action;
        #   * known section with none (clone_profiles, read-only) -> nothing;
        #   * unknown (file header, read-only nested cell node) -> ALL of
        #     them (Denis's decision — how else to create the FIRST entity
        #     in a file that has no sections yet).
        section = self._add_section_for_item(item)
        if section is None:
            add_sections = list(_ADD_ACTION_BY_SECTION)
        elif section in _ADD_ACTION_BY_SECTION:
            add_sections = [section]
        else:
            add_sections = []
        for add_section in add_sections:
            label, signal_name = _ADD_ACTION_BY_SECTION[add_section]
            signal = getattr(self, signal_name)
            # QAction.triggered emits a positional `bool checked`; the leading
            # parameter swallows it so `sig` keeps its default (2026-08-14
            # crash fix: 'bool' object has no attribute 'emit').
            menu.addAction(label).triggered.connect(
                lambda checked=False, sig=signal: sig.emit(file_path))
        # "Add included file..." is about the FILE, not a section, so it stays
        # unconditional — it's relevant in every context.
        menu.addAction(_("Add included file...")).triggered.connect(
            lambda: self._add_included_file(file_path))
        # "New Extract..." (2026-08-31, plan extract_dialog_and_hide_existing
        # .md): a plain fresh capture, also unconditional — the Extract form is
        # a dialog now, reachable from anywhere in the tree.
        menu.addAction(_("New Extract...")).triggered.connect(
            lambda: self.new_extract_requested.emit())
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
        self.graph_changed.emit()
        message = _("Renamed {old!r} to {new!r} — {count} file(s) updated: {files}").format(
            old=old_name, new=new_name, count=len(changed),
            files=", ".join(display_path(p) for p in changed))
        if section not in CASCADE_FIELD:
            message += " " + _(
                "If any CLI command uses --only/--profile {old!r}, update that separately — "
                "this only rewrites YAML files, it can't see command-line usage.").format(old=old_name)
        if settings.state.get("rename_confirmation_enabled", True):
            QMessageBox.information(self, _("Renamed"), message)
        else:
            # Silent rename (Settings -> Config tree -> "Show confirmation
            # after rename" unchecked, 2026-08-25): same summary, just not
            # modal — Log dock instead of a blocking popup.
            logger.info(message)

    def _on_rename_shortcut(self) -> None:
        """F2 — Rename on the tree's current leaf (2026-08-25). Silently does
        nothing when there is no selection or the current item isn't a
        renameable leaf: F2 on a file header / category is normal tree
        navigation, not a user error, so no message."""
        item = self.tree.currentItem()
        if item is None:
            return
        target = self._rename_target_for_item(item)
        if target is None:
            return
        file_path, section, old_name = target
        self._on_rename(file_path, section, old_name)

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
        self.graph_changed.emit()

        message = _("Deleted {name!r}. Backed up: {backups}.").format(
            name=name, backups=", ".join(display_path(p) for p in report["backups"]))
        if report["cascade_files"]:
            message += " " + _("Also removed references from: {files}.").format(
                files=", ".join(display_path(p) for p in report["cascade_files"]))
        QMessageBox.information(self, _("Deleted"), message)

    def _on_delete_pad(self, file_path: Path, chain: dict, pad_index: int) -> None:
        """Deletes ONE pad from a chain (context menu on a pad leaf,
        2026-09-01, plan rules_to_chains). Unlike a whole-chain delete
        (delete_entry), this loads the chain, drops the spoke at `pad_index`
        from its spokes:, and rewrites the chain via upsert_list_entry — with
        a backup_file first, exactly like every other write path here."""
        spokes = chain.get("spokes") or []
        if pad_index < 0 or pad_index >= len(spokes):
            return
        pad = spokes[pad_index]
        pad_label = str(pad.get("pad", "?")) if isinstance(pad, dict) else "?"
        name = entry_effective_name("chains", chain)
        reply = QMessageBox.question(
            self, _("Delete pad {pad!r}").format(pad=pad_label),
            _("Delete pad {pad!r} from chain {name!r} in {file}?").format(
                pad=pad_label, name=name, file=display_path(file_path)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        modified = dict(chain)
        modified["spokes"] = [s for i, s in enumerate(spokes) if i != pad_index]
        backup_file(file_path)
        upsert_list_entry(file_path, "chains", modified,
                          key_fn=lambda e: entry_effective_name("chains", e))
        self.refresh()
        self.graph_changed.emit()
        QMessageBox.information(
            self, _("Deleted"),
            _("Deleted pad {pad!r} from chain {name!r}.").format(
                pad=pad_label, name=name))

    def selected_chain(self) -> Optional[tuple]:
        """The currently selected chains: CHAIN node as (file_path, chain_dict),
        or None when there is no selection or the selection is a different node
        kind (anchor/pad/file/category/leaf). The Tools menu's "Add spoke..." /
        "Delete net..." (DockHub.add_spoke/delete_selected_chain) operate on the
        chain currently selected in the Config tree (2026-09-01, plan
        rules_to_chains) — this is the ONE extraction of that selection."""
        for tree_item in self.tree.selectedItems():
            data = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if data is not None and data[0] == "chain" and data[1] == "chains":
                file_ctx = self._file_context_for_item(tree_item)
                if file_ctx is not None:
                    return file_ctx[0], data[2]
        return None

    def _selected_export_items(self) -> list:
        """Currently selected tree leaves/nodes, as ExportItem tuples — file/
        category headers and chains: anchor groups are ignored (Export only
        makes sense for actual entries). A chains: CHAIN node exports the whole
        chain dict (pads included); a chains: PAD leaf exports its parent chain
        (a pad is not a standalone record — see plan rules_to_chains §3)."""
        items = []
        for tree_item in self.tree.selectedItems():
            data = tree_item.data(0, Qt.ItemDataRole.UserRole)
            if data is None or data[0] == "file" or data[0] == "category":
                continue
            if data[0] == "anchor":
                continue  # grouping header, nothing to export
            file_ctx = self._file_context_for_item(tree_item)
            if file_ctx is None:
                continue
            if data[0] == "leaf":
                _kind, section, payload = data
                items.append(ExportItem(source_path=file_ctx[0], section=section,
                                        name=tree_item.text(0), payload=payload))
            elif data[0] == "chain":
                items.append(ExportItem(source_path=file_ctx[0], section="chains",
                                        name=tree_item.text(0), payload=data[2]))
            elif data[0] == "pad":
                # Export the parent chain (a pad alone has no standalone record).
                items.append(ExportItem(source_path=file_ctx[0], section="chains",
                                        name=tree_item.text(0), payload=data[2]))
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
            "Config files (*.sexp *.json)")
        if not chosen:
            return
        target_path = Path(chosen)
        if not target_path.exists():
            # Format-aware empty placeholder — a new .sexp target must be a
            # valid (kicadstamp-config), not the YAML-era "{}\n" (which would
            # make the next read_data/merge_write fatal and the export fail).
            if target_path.suffix.lower() == ".sexp":
                target_path.write_text("(kicadstamp-config)\n", encoding="utf-8")
            else:
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
            self, _("Add included file"), str(file_path.parent), "Config files (*.sexp *.json)")
        if not chosen:
            return
        chosen_path = Path(chosen)
        if not chosen_path.exists():
            # Format-aware empty placeholder (same as _on_export): a new
            # .sexp file must be a valid (kicadstamp-config).
            if chosen_path.suffix.lower() == ".sexp":
                chosen_path.write_text("(kicadstamp-config)\n", encoding="utf-8")
            else:
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
        self.graph_changed.emit()

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
        self.graph_changed.emit()
