# gui/docks/anchor_tree.py
"""
AnchorTreeDock — the anchor dependency tree (§1 of
plan_2026_08_21_anchor_dependency_tree_cascade_redraw.md): the SAME set of
config records as ConfigTreeDock, regrouped by the STATIC anchor edges from
kicadstamp/anchor_graph.py instead of by file/section.

Read-only navigation + two context-menu actions (§1.3):
  - "Redraw" — one record (the existing Redraw/--only).
  - "Redraw dependents" — cascade (§2): the node's record(s) + every record
    transitively anchored on them, in topological order, each applied via its
    own ApplyPipeline --only run (see gui/docks/cascade.py).

Rendering follows §1.1/§1.2: roots are records with no anchor (absolute
placements), external anchor_ref targets (FPGA-like), and points (leaves by
the plan's decision). A child with Sheet metadata is shown under a synthetic
Sheet folder inside its parent's branch, with its generated "{Sheet}_" prefix
stripped for readability; a node with several parents (a DAG point) is
duplicated under each parent — the standard tree rendering of a DAG.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDockWidget, QMenu, QMessageBox, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from kicadstamp.anchor_graph import AnchorGraph, build_anchor_graph
from kicadstamp.config import load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _

from ..worker import start_long_op
from ._common import highlight_stylesheet_for
from .cascade import cascade_records, run_cascade_worker

logger = logging.getLogger(__name__)

# Short kind tags, shown only for the kinds whose bare name is not already
# self-explanatory in the tree (clone/coordinate names are the --only
# identities the user manages; the rest benefit from a disambiguating tag).
_KIND_TAGS = {
    "rule": _("rule"),
    "net_trace": _("net trace"),
    "thermal_via": _("thermal"),
    "point": _("point"),
}


class AnchorTreeDock(QDockWidget):
    """Builds the anchor graph from the project's ROOT config (the whole
    include: graph, after sheet_templates: expansion) on every root change —
    the same source ConfigTreeDock mirrors, but consumed through
    load_config() + build_anchor_graph() instead of walk_include_tree()."""

    def __init__(self, main_window):
        super().__init__(_("Anchor tree"), main_window)
        self._main_window = main_window
        self._root_path: Optional[Path] = None
        self._cfg = None
        self._ctx = None
        self._graph: Optional[AnchorGraph] = None
        self._active_op = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree)

        self.setWidget(container)

    def apply_highlight(self) -> None:
        """Re-apply the highlight stylesheet — same consumer shape as
        ConfigTreeDock/RoleClusterTreeDock (see gui/dock_hub.py)."""
        self.tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))

    # ── Setting/refreshing the root ─────────────────────────────────────

    def set_root_file(self, path: Optional[Path]) -> None:
        """Slot — RootMetadataDock.root_changed (wired in gui/dock_hub.py)."""
        self._root_path = path
        self.refresh()

    def refresh(self) -> None:
        """Public — also called after an entity dock's Save, so the anchor
        tree reflects new/renamed/removed records without reassigning root."""
        self.tree.clear()
        self._graph = None
        self._cfg = None
        self._ctx = None
        if self._root_path is None:
            return
        try:
            self._cfg, self._ctx = load_config(str(self._root_path))
            self._graph = build_anchor_graph(self._cfg)
        except ValidationError as e:
            QTreeWidgetItem(self.tree, [str(e)])
            return
        self._render()

    # ── Rendering ────────────────────────────────────────────────────────

    def _render(self) -> None:
        for key in self._graph.roots:
            self._render_branch(self.tree.invisibleRootItem(), key,
                                inside_sheet=None, ancestors=())
        self.tree.expandAll()

    def _render_branch(self, parent_item, key, inside_sheet, ancestors) -> None:
        if key in ancestors:
            return  # cycle guard — a valid config can't produce one, but be safe
        ancestors = ancestors + (key,)
        if key in self._graph.external:
            leaf = self._graph.external[key]
            item = QTreeWidgetItem(parent_item, [f"{leaf.ref} ({_('external')})"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("external", key))
        else:
            item = QTreeWidgetItem(parent_item, [self._display_name(key, inside_sheet)])
            item.setData(0, Qt.ItemDataRole.UserRole, ("record", key))
        self._render_children(item, key, ancestors)

    def _display_name(self, key: str, inside_sheet: Optional[str]) -> str:
        rec = self._graph.by_key.get(key)
        if rec is None:
            return key
        name = rec.name
        # Inside a Sheet folder, strip the generated "{Sheet}_" prefix so the
        # tree shows "DAC_BUF" under "Channel_0" instead of "Channel_0_DAC_BUF".
        if inside_sheet and name.startswith(inside_sheet + "_"):
            name = name[len(inside_sheet) + 1:]
        tag = _KIND_TAGS.get(rec.kind)
        return f"{name} ({tag})" if tag else name

    def _child_sheet(self, key: str) -> Optional[str]:
        rec = self._graph.by_key.get(key)
        if rec is None or not rec.sheet:
            return None
        return rec.sheet

    def _render_children(self, parent_item, parent_key, ancestors) -> None:
        children = self._graph.children.get(parent_key, [])
        sheeted: dict[str, list[str]] = {}
        sheetless: list[str] = []
        for ckey in children:
            sheet = self._child_sheet(ckey)
            if sheet:
                sheeted.setdefault(sheet, []).append(ckey)
            else:
                sheetless.append(ckey)

        for sheet in sorted(sheeted):
            folder = QTreeWidgetItem(parent_item, [sheet])
            folder.setData(0, Qt.ItemDataRole.UserRole, ("sheet_folder", sheet))
            for ckey in sheeted[sheet]:
                self._render_branch(folder, ckey, inside_sheet=sheet, ancestors=ancestors)
        for ckey in sheetless:
            self._render_branch(parent_item, ckey, inside_sheet=None, ancestors=ancestors)

    # ── Context menu (§1.3) ─────────────────────────────────────────────

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None or data[0] == "sheet_folder":
            return  # sheet folders are synthetic grouping — no action on them
        kind, key = data

        menu = QMenu(self.tree)
        if kind == "record":
            menu.addAction(_("Redraw")).triggered.connect(
                lambda: self._on_redraw(key))
        menu.addAction(_("Redraw dependents")).triggered.connect(
            lambda: self._on_redraw_dependents(key))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_redraw(self, key: str) -> None:
        """Single-record Redraw (the existing Redraw/--only action)."""
        rec = self._graph.by_key.get(key)
        if rec is None or rec.kind == "point":
            return
        self._run_cascade([rec.name])

    def _on_redraw_dependents(self, key: str) -> None:
        try:
            records = cascade_records(self._cfg, key)
        except ValidationError as e:
            QMessageBox.warning(self, _("Redraw dependents"), str(e))
            return
        names = [r.name for r in records]
        if not names:
            QMessageBox.information(
                self, _("Redraw dependents"),
                _("No records anchor on this node."))
            return
        self._run_cascade(names)

    def _run_cascade(self, names: list) -> None:
        payload = {
            "config_path": str(self._root_path),
            "cfg": self._cfg,
            "ctx": self._ctx,
            "names": names,
        }
        logger.info(_("Redraw: {count} record(s) in order: {order}")
                    .format(count=len(names), order=" -> ".join(names)))
        self._active_op = start_long_op(
            self._main_window.connection, (),
            run_cascade_worker, self._finish_cascade, self._on_cascade_failed, payload)

    def _finish_cascade(self, results) -> None:
        ok = sum(1 for _name, good, _err in results if good)
        failed = len(results) - ok
        status = ", ".join(
            f"{name}={'ok' if good else 'FAILED'}" for name, good, _err in results)
        logger.info(_("Redraw: {ok}/{total} ok — {status}")
                    .format(ok=ok, total=len(results), status=status))
        if failed:
            logger.warning(_("Redraw: {failed} record(s) failed — see the log above")
                           .format(failed=failed))

    def _on_cascade_failed(self, message: str) -> None:
        logger.error(_("Redraw failed: {error}").format(error=message))
