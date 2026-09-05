# gui/docks/entity_page.py
"""
EntityInfoDock — the Config right-QView page for a selected Entity record
(2026-09-05, design config_qview_chain_entity_pages §5).

Shows the Entity RECORD, not its placement:
  - "Справка": Name (read-only here — rename goes through the Config tree's
    F2/Rename), Comment (the only identity field edited in place), Cell /
    Sheet / Cluster read-only (set at creation from a template/extract);
  - "Размещения": a clickable list of the trees: placement nodes whose
    node.ref == this Entity's name. Today at most one node (trees rule 2),
    designed for N once the rule is relaxed to per-tree uniqueness (§8.1).
    Clicking a placement jumps to that tree in TreesDock.

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
from PyQt6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.config import load_config, load_entity
from kicadstamp.config_writer import read_data, upsert_entity
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE)
from .rename import find_list_entry_file

logger = logging.getLogger(__name__)

# Placement-node kinds that carry an Entity name in node.ref (kind "module"
# refs are TREE names, never entities; kind None = auto/clone-aliased).
_PLACEMENT_KINDS = (None, "placement", "clone")


class EntityInfoDock(QWidget):
    """The Config right-QView page that appears when an Entities leaf is
    selected — a read-mostly "record editor" for one Entity."""

    saved = pyqtSignal()
    # Fired when a placement row is activated — payload is the TREE NAME to
    # jump to in TreesDock.
    open_tree = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.setObjectName("entity_page_dock")
        self._main_window = main_window
        self._root_path: Optional[Path] = None
        # Raw entity dict + the file it lives in (for the comment write) and
        # the entity name currently shown.
        self._entity_data: Dict[str, Any] = {}
        self._entity_file: Optional[Path] = None
        self._current_name: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        heading = QHBoxLayout()
        heading.addWidget(QLabel(_("Entity")))
        self.name_label = QLabel("—")
        heading.addWidget(self.name_label, 1)
        layout.addLayout(heading)

        form = QFormLayout()
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText(_("optional free-form note"))
        self.comment_edit.editingFinished.connect(self._on_comment_commit)
        form.addRow(_("Comment:"), self.comment_edit)
        self.cell_label = QLabel("—")
        form.addRow(_("Cell:"), self.cell_label)
        self.sheet_label = QLabel("—")
        form.addRow(_("Sheet:"), self.sheet_label)
        self.cluster_label = QLabel("—")
        form.addRow(_("Cluster:"), self.cluster_label)
        layout.addLayout(form)

        layout.addWidget(QLabel(_("Placements (trees):")))
        self.placements_list = QListWidget()
        self.placements_list.setMaximumHeight(140)
        self.placements_list.itemClicked.connect(self._on_placement_clicked)
        layout.addWidget(self.placements_list)
        self.placements_hint = QLabel("")
        self.placements_hint.setWordWrap(True)
        layout.addWidget(self.placements_hint)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

    # ── Root / load ─────────────────────────────────────────────────────────

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — clears the form (the
        record may have moved/vanished with the new root)."""
        self._root_path = path
        self._entity_data = {}
        self._entity_file = None
        self._current_name = None
        self._clear_form()

    def _clear_form(self) -> None:
        self.name_label.setText("—")
        self.comment_edit.setText("")
        self.cell_label.setText("—")
        self.sheet_label.setText("—")
        self.cluster_label.setText("—")
        self.placements_list.clear()
        self.placements_hint.setText("")
        self._status_label.setText("")

    def load_entity(self, name: str) -> None:
        """Public entry point — Config tree's Entities leaf single click
        (entity_picked) routes here via DockHub. Renders the record + its
        placement list. `name` that no longer exists just clears the form."""
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
        self.cell_label.setText(str(raw.get("cell") or "—"))
        self.sheet_label.setText(str(raw.get("sheet") or "—"))
        self.cluster_label.setText(str(raw.get("cluster") or "—"))
        self._load_placements(name)

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
