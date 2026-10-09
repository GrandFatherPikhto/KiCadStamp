# gui/entity/page.py
"""EntityPage — the Config right-QView page for a selected Entity record
(moved here from gui/docks/entity_page.py, class renamed EntityPage, step 1 of
plan_2026_10_09_entity_page; design config_qview_chain_entity_pages §5).

Shows the Entity RECORD, not its placement:
  - "Справка": Name (read-only here — rename goes through the Config tree's
    F2/Rename), Comment (the only identity field edited in place), the CELL
    COMBOBOX (the choice itself — fitting cells first, the rest greyed with
    their reason; picking one goes through the ONE writer
    change_cell_flow.apply_cell_change — the same the "Change cell…" menu item
    uses, step 1 of plan_2026_10_09_entity_page), Sheet / Cluster read-only (set
    at creation from a template/extract); an imprint-based Entity
    (plan_2026_09_05_scheme_list.md §5.1, cell=None) shows its Imprint identity on
    a dedicated row instead;
  - "Размещения": a clickable list of the trees: placement nodes whose
    node.ref == this Entity's name. Today at most one node (trees rule 2),
    designed for N once the rule is relaxed to per-tree uniqueness (§8.1).
    Clicking a placement jumps to that tree in TreesDock.

The board-touching TABS the entity owns (the address is visible HERE, so the tabs
came over from the Cell page): "Explode" (step 2) and "Refs" (step 3) — the latter
the ONE RefsTabWidget, handed in by DockHub through add_refs_tab and fed the
ENTITY's cell (roles of the WHOLE cell, Р2), this page's snapshot and the project's
override store. Anchor joins in step 4.

Deliberately NOT shown: Retired/Skip and the electrical overrides
(nets/net_overrides/refs) — electrical editing stays in the Tools "Edit
template" dock (kept in the Tools menu, design §8.3). Positioning/anchor of
an Entity is TreesDock's job; the Cell's internal anchor lives on the Cell
page. No origin/position fields here — an Entity never carries a position.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.config import load_config, load_entity
from kicadstamp.config.aliases import read_entity_field
from kicadstamp.config_writer import read_data, upsert_entity
from kicadstamp.exceptions import ValidationError
from kicadstamp.field_overrides import load_field_overrides
from kicadstamp.i18n import _
from kicadstamp.utils.paths import overrides_path_for_config

from ..docks._common import (ERROR_STYLE as _ERROR_STYLE,
                             SUCCESS_STYLE as _SUCCESS_STYLE)
from ..docks.change_cell_flow import (apply_cell_change, cell_choices,
                                      cell_role_order)
from ..docks.instance_candidates import (cell_row_label, others_line,
                                         others_tooltip)
from ..docks.rename import find_list_entry_file
from ..role_table_model import records_from_items

logger = logging.getLogger(__name__)

# Placement-node kinds that carry an Entity name in node.ref (kind "module"
# refs are TREE names, never entities; kind None = auto/clone-aliased).
_PLACEMENT_KINDS = (None, "placement", "clone")

_GREY = QColor("#888888")


class EntityPage(QWidget):
    """The Config right-QView page that appears when an Entities leaf is
    selected — a read-mostly "record editor" for one Entity, with the Cell
    combobox replacing the old read-only Cell row and the Source tab."""

    saved = pyqtSignal()
    # Fired when a placement row is activated — payload is the TREE NAME to
    # jump to in TreesDock.
    open_tree = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.setObjectName("entity_page_dock")
        self._main_window = main_window
        self._root_path: Optional[Path] = None
        # Raw entity dict + the file it lives in (for the comment/cell writes)
        # and the entity name currently shown.
        self._entity_data: Dict[str, Any] = {}
        self._entity_file: Optional[Path] = None
        self._current_name: Optional[str] = None
        # Whether the combobox offers every cell because the fit was never
        # checked (no snapshot / dangling graph / orphan instance).
        self._cell_orphan = True
        # The board snapshot this page was last fed (refresh_known_roles) and its
        # SHEET-RESOLVED twin — the Refs tab's board columns come from here, never
        # from a board read on the UI thread (door rule 31).
        self._snapshot: list = []
        self._resolved_snapshot: list = []
        # The config's {uuid: name} sheet map and the project's OVERRIDE STORE
        # (both hang off the root; loaded in set_root_path) — the Refs tab is fed
        # them so its "differs" marks and Role hints follow the project.
        self._sheet_names: dict = {}
        self._overrides = None
        # The two write events the Refs tab raises reach these hooks; DockHub owns
        # the wiring (each to its OWN owner — the store one and the board one,
        # never one callback standing in for the other).
        self.on_board_written = None
        self.on_overrides_written = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        heading = QHBoxLayout()
        heading.addWidget(QLabel(_("Entity")))
        self.name_label = QLabel("—")
        heading.addWidget(self.name_label, 1)
        layout.addLayout(heading)

        # The page is TABBED (step 2 of plan_2026_10_09_entity_page): "Справка" +
        # the Cell combobox first, "Размещения" second; the board-touching tabs
        # DockHub hands in ("Explode", "Refs" now, "Anchor" in step 4) are added by
        # add_explode_tab / add_refs_tab. "Справка" stays index 0, so it is the one
        # visible on open.
        self.tabs = QTabWidget()
        self.tabs.setObjectName("entity_page_tabs")
        layout.addWidget(self.tabs)

        # ── Tab 1 — "Справка": identity + the Cell combobox ──────────────────
        guide = QWidget()
        form = QFormLayout(guide)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        self.comment_edit.editingFinished.connect(self._on_comment_commit)
        form.addRow(_("Comment:"), self.comment_edit)
        # Cell — a COMBOBOX (step 1 of plan_2026_10_09_entity_page: it replaces
        # the read-only Cell row and the Source tab). The BOX IS the choice — no
        # dialog is opened from the page; fitting cells come first, the rest stay
        # greyed with their reason. The pick goes through the ONE writer
        # change_cell_flow.apply_cell_change, shared with the "Change cell…" menu
        # item — never a second copy of the write.
        self.cell_combo = QComboBox()
        self.cell_combo.setObjectName("entity_cell_combo")
        self.cell_combo.activated.connect(self._on_cell_chosen)
        form.addRow(_("Cell:"), self.cell_combo)
        # The ONE grey line under the box: how many OTHER cells do not fit (their
        # reasons in the tooltip) — no huge list of every cell (Денис, 09.10.2026).
        self.cell_others_label = QLabel("")
        self.cell_others_label.setObjectName("entity_cell_others")
        self.cell_others_label.setWordWrap(True)
        self.cell_others_label.setStyleSheet("color: #888888;")
        self.cell_others_label.setVisible(False)
        form.addRow("", self.cell_others_label)
        # Imprint row (2026-09-06, P6 Stage 4 .cell audit): a
        # imprint-based Entity (cell=None) shows its recorded-snapshot
        # identity here instead of a misleading blank Cell. Hidden for a
        # regular cell-based Entity. Both the label and the value are explicit
        # QWidgets so the WHOLE row can be hidden (a QFormLayout row created
        # from a string label cannot be hidden individually).
        self._imprint_label_widget = QLabel(_("Imprint:"))
        self.imprint_label = QLabel("—")
        form.addRow(self._imprint_label_widget, self.imprint_label)
        self._set_imprint_visible(False)
        self.sheet_label = QLabel("—")
        form.addRow(_("Sheet:"), self.sheet_label)
        self.cluster_label = QLabel("—")
        form.addRow(_("Cluster:"), self.cluster_label)
        self.tabs.addTab(guide, _("Reference"))

        # ── Tab 2 — "Размещения" ─────────────────────────────────────────────
        placements = QWidget()
        placements_layout = QVBoxLayout(placements)
        placements_layout.setContentsMargins(4, 4, 4, 4)
        placements_layout.addWidget(QLabel(_("Placements (trees):")))
        self.placements_list = QListWidget()
        self.placements_list.setMaximumHeight(140)
        self.placements_list.itemClicked.connect(self._on_placement_clicked)
        placements_layout.addWidget(self.placements_list)
        self.placements_hint = QLabel("")
        self.placements_hint.setWordWrap(True)
        placements_layout.addWidget(self.placements_hint)
        placements_layout.addStretch(1)
        self.tabs.addTab(placements, _("Placements"))

        # The board-touching tabs DockHub hands in (the "Explode" page and, since
        # step 3 of plan_2026_10_09_entity_page, the ONE "Refs" tab).
        self._explode_page = None
        self._refs_tab = None

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

    # ── Root / load ─────────────────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — clears the form (the record
        may have moved/vanished with the new root) and re-scopes the Refs tab: a
        different project means different cell roles, a different sheet map and a
        different override store."""
        self._root_path = path
        self._entity_data = {}
        self._entity_file = None
        self._current_name = None
        self._clear_form()
        self._snapshot = []
        self._resolved_snapshot = []
        self._sheet_names = {}
        self._overrides = (load_field_overrides(overrides_path_for_config(str(path)))
                           if path is not None else None)
        if path is not None:
            try:
                _cfg, ctx = load_config(str(path))
            except (ValidationError, OSError):
                ctx = None
            if ctx is not None:
                self._sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
        self._sync_refs_tab()

    def _hub(self):
        """The DockHub behind the window (None in a bare widget test) — the
        combobox write needs it, the record display does not."""
        return getattr(self._main_window, "_dock_hub", None)

    def _set_imprint_visible(self, visible: bool) -> None:
        """Show/hide the Imprint identity row (whole row: label + value)."""
        self.imprint_label.setVisible(visible)
        self._imprint_label_widget.setVisible(visible)

    def _clear_form(self) -> None:
        self.name_label.setText("—")
        self.comment_edit.setText("")
        self.cell_combo.blockSignals(True)
        self.cell_combo.clear()
        self.cell_combo.blockSignals(False)
        self.cell_others_label.setText("")
        self.cell_others_label.setToolTip("")
        self.cell_others_label.setVisible(False)
        self._cell_orphan = True
        self._set_imprint_visible(False)
        self.sheet_label.setText("—")
        self.cluster_label.setText("—")
        self.placements_list.clear()
        self.placements_hint.setText("")
        self._status_label.setText("")

    def load_entity(self, name: str) -> None:
        """Public entry point — Config tree's Entities leaf single click
        (entity_picked) routes here via DockHub. Renders the record, its Cell
        combobox and its placement list. `name` that no longer exists just
        clears the form."""
        self._clear_form()
        self._current_name = name or None
        if not name or self._root_path is None:
            return
        found = self._load_entity_dict(name)
        if found is None:
            self._show_message(_("Entity {name!r} not found.").format(name=name))
            return
        raw, file_path = found
        self._entity_data = raw
        self._entity_file = file_path
        self.name_label.setText(str(raw.get("name", name)))
        self.comment_edit.setText(str(raw.get("comment") or ""))
        # read_entity_field tolerates the pre-2026-09-20 legacy key of a profile
        # that has not been written since the Imprint rename (see
        # kicadstamp/config/aliases.py).
        imprint = read_entity_field(raw)
        if imprint:
            # imprint-based Entity (cell=None) — show the recorded
            # snapshot it clones instead of a misleading blank Cell row.
            self.imprint_label.setText(str(imprint))
            self._set_imprint_visible(True)
        else:
            self._set_imprint_visible(False)
        self.sheet_label.setText(str(raw.get("sheet") or "—"))
        self.cluster_label.setText(str(raw.get("cluster") or "—"))
        self._fill_cell_combo()
        self._load_placements(name)
        self._sync_explode_context()
        self._sync_refs_tab()

    def _fill_cell_combo(self) -> None:
        """Fill the Cell combobox: the FITTING cells plus the entity's CURRENT
        cell (marked; when it does not fit its own row says "current, does not
        fit: <reason>"). Every other non-fitting cell is NOT a row — only the one
        grey line under the box counts them. Candidates come from the LAST PUSHED
        snapshot (change_cell_flow.cell_choices — NO board read on the UI thread);
        with no snapshot or on a dangling graph every cell is offered, fit not
        checked (an orphan is fixed exactly this way)."""
        combo = self.cell_combo
        combo.blockSignals(True)
        combo.clear()
        hub = self._hub()
        index = getattr(getattr(hub, "config_tree_dock", None),
                        "_entity_index", None)
        snapshot = getattr(getattr(self._main_window, "connection", None),
                           "snapshot", None)
        choices = cell_choices(self._root_path, snapshot,
                               self._entity_data, index)
        self._cell_orphan = choices.orphan
        for cand in choices.candidates:
            combo.addItem(cell_row_label(cand), cand.name)
            item = combo.model().item(combo.count() - 1)
            if item is not None and not (cand.fits or choices.orphan):
                item.setEnabled(False)
                item.setForeground(QBrush(_GREY))
        current = (self._entity_data or {}).get("cell")
        pos = combo.findData(current) if current else -1
        combo.setCurrentIndex(pos)
        combo.blockSignals(False)
        # The ONE grey line under the box (hidden when nothing was left out).
        self.cell_others_label.setText(others_line(choices.others))
        self.cell_others_label.setToolTip(others_tooltip(choices.others))
        self.cell_others_label.setVisible(bool(choices.others))

    def _on_cell_chosen(self, index: int) -> None:
        """The user picked a cell in the combobox — the choice ITSELF, no dialog.
        The apply is the ONE change_cell_flow.apply_cell_change (the same writer
        the "Change cell…" menu item uses)."""
        chosen = self.cell_combo.itemData(index)
        hub = self._hub()
        if (not chosen or hub is None or self._entity_file is None
                or not self._entity_data):
            return
        if chosen == self._entity_data.get("cell"):
            return
        apply_cell_change(hub, self._entity_data, self._entity_file, chosen)
        if self._current_name:
            self.load_entity(self._current_name)

    # ── The "Explode" tab (step 2 of plan_2026_10_09_entity_page) ───────────
    def add_explode_tab(self, widget) -> None:
        """DockHub hands the ONE ExplodePage over; the page ONLY adds it — the tab
        itself lives in gui/docks/explode_page.py. The address it is told comes
        from the loaded entity RECORD (see _sync_explode_context), never from a
        dropdown."""
        self._explode_page = widget
        self.tabs.addTab(widget, _("Explode"))
        self._sync_explode_context()

    def select_explode_tab(self) -> None:
        """Bring the "Explode" tab to the front — the door's last step."""
        if self._explode_page is not None:
            self.tabs.setCurrentWidget(self._explode_page)

    def _sync_explode_context(self) -> None:
        """Tell the "Explode" tab the ENTITY's address: its cell, its (cluster,
        sheet) and its OWN file — read from the record. An empty form (nothing
        loaded) clears the context."""
        if self._explode_page is None:
            return
        raw = self._entity_data or {}
        self._explode_page.set_context(
            raw.get("cell"), raw.get("cluster"), raw.get("sheet"),
            self._entity_file)

    # ── The "Refs" tab (step 3 of plan_2026_10_09_entity_page) ──────────────
    def add_refs_tab(self, widget) -> None:
        """DockHub hands the ONE RefsTabWidget over (the same widget the CELL page
        used to own); the page ONLY adds it and syncs it. The address it is told is
        the loaded entity RECORD's cell, never a dropdown (Р2: the roles are the
        roles of the WHOLE cell, shared by every entity of it)."""
        self._refs_tab = widget
        # BOTH of the tab's write events land in the page's own handler: a STORE
        # record fires on_overrides_written and a BOARD write fires on_board_written
        # (gui/docks/cell_refs_tab.py). The CELL page wired only the board half,
        # which left a store write unnotified — the move fixes that, and DockHub
        # then routes each page hook to its OWN owner.
        widget.on_overrides_written = self._on_refs_written
        widget.on_board_written = self._on_refs_written
        self.tabs.addTab(widget, _("Refs"))
        self._sync_refs_tab()

    def refresh_known_roles(self, snapshot) -> None:
        """Feed the Refs tab the live-board snapshot (wired into
        DockHub.push_snapshot like every other dock's own refresh_known_roles).
        The snapshot is STORED and re-resolved against the cached config sheet map
        — a live Board's own .sheet is all-None — and the tab then reads its board
        columns from THIS page's copy, never from the board (door rule 31)."""
        self._snapshot = list(snapshot or [])
        if self._sheet_names and all(hasattr(s, "fp") for s in self._snapshot):
            from ..docks.imprint import snapshot_with_resolved_sheets
            self._resolved_snapshot = snapshot_with_resolved_sheets(
                self._snapshot, self._sheet_names)
        else:
            # No sheet map, or a synthetic snapshot without the raw .fp handle
            # (some guards) — nothing to re-resolve, use it as-is.
            self._resolved_snapshot = list(self._snapshot)
        self._sync_refs_tab()

    def reload_overrides(self) -> None:
        """Re-read the project's override store from its FILE and hand the fresh
        copy to the Refs tab. The stop for "another pane recorded": our object was
        loaded when the project opened, and the file is the truth — a no-op without
        a project."""
        if self._root_path is None:
            return
        self._overrides = load_field_overrides(
            overrides_path_for_config(str(self._root_path)))
        if self._refs_tab is not None:
            self._refs_tab.set_overrides(self._overrides)

    def _on_refs_written(self) -> None:
        """The Refs tab wrote (a store record or a board write): hand the news to
        the page's two hooks, each with its OWN owner — on_overrides_written
        (DockHub re-reads that store wherever another pane holds a copy) and
        on_board_written (MainWindow.request_refresh, so Pending changes is
        recomputed). Deliberately NO form reload: unlike the cell page, an entity
        RECORD is not touched by a Role/Cluster write."""
        if self.on_overrides_written:
            self.on_overrides_written()
        if self.on_board_written:
            self.on_board_written()

    def _sync_refs_tab(self) -> None:
        """Hand the "Refs" tab the context this page holds — the root, the ENTITY's
        cell (roles of the WHOLE cell, Р2), the board snapshot and the override
        store. UI thread only, and only data already read: the tab answers "what
        does the board say about this row" from the snapshot this page was fed, so
        no board access is needed to render it."""
        if self._refs_tab is None:
            return
        cell_name = (self._entity_data or {}).get("cell")
        self._refs_tab.set_context(
            self._root_path, cell_name, self._cell_roles(cell_name),
            self._refs_records(), sheet_names=self._sheet_names,
            overrides=self._overrides)

    def _refs_records(self) -> list:
        """This page's own snapshot as role-table records — the SHEET-RESOLVED
        copy when it carries raw handles (the tab needs the two field-existence
        flags, which a bare SelectionRecord does not carry)."""
        return records_from_items(self._resolved_snapshot or self._board_snapshot())

    def _board_snapshot(self) -> list:
        """The last PUSHED whole-board snapshot (connection.snapshot) — the copy
        the GUI already holds, never a board read on the UI thread."""
        return getattr(getattr(self._main_window, "connection", None),
                       "snapshot", None) or []

    def _cell_roles(self, cell_name: Optional[str]) -> list:
        """The roles of the ENTITY's cell in the cell's own order (Р2) — the ONE
        owner change_cell_flow.cell_role_order, read off the loaded graph. A missing
        cell or a dangling graph offers no role hints (the tab then says the fit was
        not checked), never a board read."""
        if not cell_name or self._root_path is None:
            return []
        try:
            cfg, _ctx = load_config(str(self._root_path))
        except (ValidationError, OSError):
            return []
        cell = (getattr(cfg, "cells", None) or {}).get(cell_name)
        if cell is None:
            return []
        return cell_role_order(getattr(cell, "components", None) or ())

    def _load_entity_dict(self, name: str) -> Optional[Tuple[Dict[str, Any], Optional[Path]]]:
        """(raw entities: dict, file) for `name` — same graph-wide lookup as
        ToolsDock._load_entity_dict."""
        if self._root_path is None:
            return None
        try:
            file_path = find_list_entry_file(self._root_path, "entities", {"name": name})
        except (ValidationError, OSError):
            return None
        if file_path is None:
            return None
        try:
            data = read_data(file_path)
        except (ValidationError, OSError):
            return None
        for entry in data.get("entities") or []:
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry, file_path
        return None

    def _load_placements(self, name: str) -> None:
        """Fill the placements list from cfg.trees — every node (recursively,
        kind placement/clone/None) whose ref == this Entity's name, per tree.
        Today ≤1 (trees rule 2); the widget is built for N (design §8.1)."""
        self.placements_list.clear()
        rows: List[Tuple[str, str]] = []  # (tree name, node ref label)
        if self._root_path is not None:
            try:
                cfg, _ctx = load_config(str(self._root_path))
            except (ValidationError, OSError):
                cfg = None
            if cfg is not None:
                for tree in cfg.trees:
                    for node in _iter_nodes(tree.nodes):
                        if node.ref == name and node.kind in _PLACEMENT_KINDS:
                            rows.append((tree.name, node.name or node.ref))
        if not rows:
            self.placements_hint.setText(_("Not placed in any tree."))
            return
        self.placements_hint.setText(
            _("{n} placement(s) — click to open the tree").format(n=len(rows)))
        for tree_name, ref in sorted(rows):
            item = QListWidgetItem(f"{tree_name}  ({ref})")
            item.setData(Qt.ItemDataRole.UserRole, tree_name)
            self.placements_list.addItem(item)

    def _on_placement_clicked(self, item: QListWidgetItem) -> None:
        tree_name = item.data(Qt.ItemDataRole.UserRole)
        if tree_name:
            self.open_tree.emit(tree_name)

    # ── Comment editing (the one identity field edited here) ───────────────

    def _on_comment_commit(self) -> None:
        """Comment field commit point — merge the new comment into the raw
        entity record (its own file) via upsert_entity, working-set aware.
        Only on success does `saved` fire (config tree refresh)."""
        if not self._entity_data or self._entity_file is None:
            return
        if self._current_name is None:
            return
        entry: Dict[str, Any] = dict(self._entity_data)
        comment = self.comment_edit.text().strip()
        if comment:
            entry["comment"] = comment
        else:
            entry.pop("comment", None)
        try:
            load_entity(entry)  # validate before writing anything
        except ValidationError as e:
            self._status_label.setText(str(e))
            self._status_label.setStyleSheet(_ERROR_STYLE)
            logger.warning("%s", e)
            return
        try:
            upsert_entity(self._entity_file, entry)
        except OSError as e:
            self._status_label.setText(
                _("Write failed: {error}").format(error=e))
            self._status_label.setStyleSheet(_ERROR_STYLE)
            return
        self._entity_data = entry
        self._status_label.setText(
            _("Wrote entity {name!r} in {path}").format(
                name=entry.get("name"), path=self._entity_file))
        self._status_label.setStyleSheet(_SUCCESS_STYLE)
        self.saved.emit()

    def _show_message(self, text: str, style: str = "") -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(style)
        if text:
            logger.info("%s", text)


def _iter_nodes(nodes):
    """Depth-first walk over TreeNode lists, yielding every node (parents
    before children)."""
    for node in nodes:
        yield node
        yield from _iter_nodes(node.children)
