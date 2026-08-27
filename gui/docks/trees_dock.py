# gui/docks/trees_dock.py
"""TreesDock — hand-authored s-expr "trees" editor (design
techdocs/handoff/deepseek/design_2026_08_27_trees_gui_dock.md, then moved
into the root config as the trees: section — design_2026_08_27_trees_in_
config_file.md, FORK-5).

Unlike AnchorTreeDock (read-only automatic anchor graph over the config),
this dock edits the OPTIONAL manual trees: section of the ROOT config
(design_2026_08_27_trees_in_config_file.md): it follows the root via
root_changed (like ConfigTreeDock/AnchorTreeDock), has no file identity of
its own, per-tree tabs, structural editing, Save + dirty tracking through
the single config_writer chokepoint, checkbox subtree selection + background
curated Redraw through run_curated_tree_redraw_worker.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDockWidget,
                             QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMenu, QMessageBox, QPushButton,
                             QTabWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from kicadstamp.anchor_graph import Record, build_records
from kicadstamp.config import load_config, load_tree
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.link_trees import (
    LinkedNode,
    LinkedTree,
    _PLACEABLE_KINDS,
    _build_by_key_index,
    _build_by_name_index,
    _resolve_node_ref,
    link_trees,
)
from kicadstamp.tree_position import (
    resolve_base_live_position,
    resolve_base_rotation_deg,
    relative_rotation_deg,
)
from kicadstamp.trees import KINDS, Tree, TreeAnchor, TreeNode, tree_to_dict
from kicadstamp.utils.units import MM

from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (configure_searchable, highlight_stylesheet_for,
                      set_combo_items)
from .cascade import run_curated_tree_redraw_worker
from .entity_delete import backup_file

logger = logging.getLogger(__name__)

# Short kind tags, shown next to a node's ref when the kind is set. "external"
# is included here (unlike anchor_tree's _KIND_TAGS, which has no external
# leaf — trees need it).
_KIND_TAGS = {
    "clone": _("clone"),
    "rule": _("rule"),
    "coordinate": _("coordinate"),
    "point": _("point"),
    "external": _("external"),
}

_ORIGIN = Vector2.from_xy(0, 0)


def _resolve_probe_ref(cfg, ref: str, kind: str | None) -> tuple[Record | None, bool]:
    """Same resolution rules as a real tree node — reused via link_trees's own
    private index builders (already partially imported here), not
    reimplemented. Returns (record, is_external); record is None only when
    is_external. Raises ValidationError on 0/2+ matches (not found /
    ambiguous), exactly like a real node — the dialog catches it and shows a
    warning instead of letting it propagate (never silently guess)."""
    records = build_records(cfg)
    by_key = _build_by_key_index(records)
    by_name = _build_by_name_index(records)
    probe = TreeNode(ref=ref, kind=kind, xy=None, polar=None, rotation=0.0,
                     name=None, group=None, children=[])
    return _resolve_node_ref(probe, by_key, by_name)


def _find_linked_node(linked: LinkedTree, node: TreeNode) -> Optional[LinkedNode]:
    """DFS by node identity — the LinkedNode wrapping `node`, or None."""
    def walk(linked_node: LinkedNode) -> Optional[LinkedNode]:
        if linked_node.node is node:
            return linked_node
        for child in linked_node.children:
            found = walk(child)
            if found is not None:
                return found
        return None

    for top in linked.nodes:
        found = walk(top)
        if found is not None:
            return found
    return None


def _linked_base_for(cfg, tree: Tree,
                     parent_node: Optional[TreeNode]) -> tuple[str | None, Record | None, bool]:
    """(ref, record, is_origin) for the base a new/edited node is relative to
    — either the tree's own anchor (parent_node is None) or another node's own
    resolved record (record None for an external node). Raises ValidationError
    if link_trees fails on the tree's current state (e.g. a broken existing
    node) — the caller reports it instead of crashing over an unrelated tree
    problem."""
    linked = link_trees(cfg, [tree])[0]
    if parent_node is None:
        anchor = linked.anchor
        return anchor.anchor.ref, anchor.record, anchor.is_origin
    found = _find_linked_node(linked, parent_node)
    if found is None:
        raise ValidationError(_("Couldn't locate node {ref!r} in the linked tree")
                              .format(ref=parent_node.ref))
    return found.node.ref, found.record, False


def _resolve_live_offset(cfg, adapter, sheet_names, tree: Tree,
                         parent_node: Optional[TreeNode], ref: str, kind: str | None
                         ) -> tuple[tuple[float, float], Optional[float]]:
    """((offset_x_mm, offset_y_mm), relative_rotation_deg | None) for the
    "would-be" child `ref`/`kind` relative to `parent_node` (None = the tree's
    own anchor). Reuses the EXACT link_trees resolution rules via
    _resolve_probe_ref/_linked_base_for and the existing tree_position
    resolvers — nothing duplicated here. Rotation is None when either side has
    no rotation concept (point kind) — the caller must leave the field blank,
    never write a fake 0. Raises ValidationError on any resolution failure (ref
    not found/ambiguous, adapter not connected, ref missing on the live board,
    broken tree state via link_trees)."""
    parent_ref, parent_record, parent_is_origin = _linked_base_for(cfg, tree, parent_node)
    child_record, _is_external = _resolve_probe_ref(cfg, ref, kind)

    try:
        if parent_is_origin:
            # The tree's own (origin) anchor — an absolute base at board (0,0),
            # rotation 0.0. resolve_base_live_position/resolve_base_rotation_deg
            # never see it (they'd treat ref=None as a live external read).
            parent_pos = _ORIGIN
            parent_deg = 0.0
        else:
            parent_pos = resolve_base_live_position(adapter, cfg, parent_ref, parent_record, {}, sheet_names)
            parent_deg = resolve_base_rotation_deg(adapter, cfg, parent_ref, parent_record, sheet_names)

        child_pos = resolve_base_live_position(adapter, cfg, ref, child_record, {}, sheet_names)
        child_deg = resolve_base_rotation_deg(adapter, cfg, ref, child_record, sheet_names)
    except KeyError as e:
        # ClonePositionCalculator._resolve_anchor's anchor_point branch does a
        # bare resolved_points[...] lookup that ONLY works when the caller (the
        # real apply pipeline) has pre-populated the dict in dependency order
        # (dependency_order.py). This ad-hoc GUI live read always passes {} —
        # surface it as a clear ValidationError (which _on_read_position and
        # _reread_node_flow already catch and turn into a warning) instead of
        # an uncaught KeyError escaping into a Qt slot.
        point = e.args[0] if e.args else str(e)
        guilty: Optional[str] = None
        for r, rec in ((parent_ref, parent_record), (ref, child_record)):
            if rec is not None and getattr(rec.obj, "anchor_point", None) == point:
                guilty = r
                break
        if guilty is None:
            guilty = ref
        raise ValidationError(_(
            "Record {ref!r} is anchored via anchor_point ({point!r}), which a "
            "live read cannot resolve outside the full apply pipeline — apply "
            "the record once first, or anchor it by ref/role instead").format(
                ref=guilty, point=point)) from e

    offset_mm = ((child_pos.x - parent_pos.x) / MM, (child_pos.y - parent_pos.y) / MM)
    rotation = (relative_rotation_deg(child_deg, parent_deg)
                if parent_deg is not None and child_deg is not None else None)
    return offset_mm, rotation


class TreesDock(QDockWidget):
    """QDockWidget hosting the hand-authored trees editor for the root
    config's trees: section (design_2026_08_27_trees_in_config_file.md). Owns
    a toolbar, the per-tree tab widget and the status line; dock_hub adds and
    tabifies it like the other tree docks. No file identity of its own — the
    trees live in the root config (cfg.trees), read via root_changed and
    saved through config_writer."""

    def __init__(self, main_window):
        super().__init__(_("Trees"), main_window)
        # Stable QDockWidget identity for QMainWindow.saveState()/restoreState()
        # (handoff sync_skip_message_and_view_menu §0) — without a unique
        # objectName Qt cannot reliably map a saved layout blob back to this
        # dock between runs.
        self.setObjectName("trees_dock")
        self._main_window = main_window
        self._trees: list[Tree] = []
        self._root_path: Optional[Path] = None   # for link_trees + Save, via root_changed
        self._cfg = None
        self._ctx = None
        self._dirty: bool = False                # used from Phase 2, field kept from the start
        self._active_op = None
        # ref -> QTreeWidgetItem, rebuilt on every render — needed for the
        # checkbox selection (Phase 4) and for the move "not into own
        # descendant" guard (Phase 2).
        self._node_items: dict[str, QTreeWidgetItem] = {}

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        self.setWidget(container)

        # ── Toolbar (Add/Rename tree + Save + Redraw; no Open/New — the trees
        #    live in the root config, which RootMetadataDock owns) ─────────
        toolbar = QHBoxLayout()
        self.add_tree_button = QPushButton(_("Add tree…"))
        self.add_tree_button.setEnabled(True)
        self.add_tree_button.clicked.connect(self._on_add_tree)
        toolbar.addWidget(self.add_tree_button)
        self.rename_tree_button = QPushButton(_("Rename tree…"))
        self.rename_tree_button.setEnabled(True)
        self.rename_tree_button.clicked.connect(self._on_rename_tree)
        toolbar.addWidget(self.rename_tree_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.setEnabled(True)
        self.save_button.clicked.connect(self._do_save)
        toolbar.addWidget(self.save_button)
        self.redraw_button = QPushButton(_("Redraw selected"))
        self.redraw_button.setEnabled(True)
        self.redraw_button.clicked.connect(self._on_redraw_selected)
        toolbar.addWidget(self.redraw_button)
        self.dirty_label = QLabel("")
        toolbar.addWidget(self.dirty_label)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # ── Per-tree tabs ────────────────────────────────────────────────
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # ── Status line (static node_offset() preview) ───────────────────
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._rebuild_tabs()

    # ── Root config (for link_trees at Save) ─────────────────────────────

    def set_root_file(self, path: Optional[Path]) -> None:
        """Slot — RootMetadataDock.root_changed (wired in gui/dock_hub.py).
        The trees live in the ROOT CONFIG's trees: section (design_2026_08_27_
        trees_in_config_file.md FORK-5): this refreshes _root_path, loads the
        config (cfg/ctx for link_trees at Save) and reads self._trees = the
        section's trees. Empty when there is no root yet or the section is
        absent. Same pattern as ConfigTreeDock.set_root_file."""
        self._root_path = path
        self._cfg = None
        self._ctx = None
        self._trees = []
        if path is None:
            self._dirty = False
            self._rebuild_tabs()
            self._update_toolbar_state()
            return
        try:
            self._cfg, self._ctx = load_config(str(path))
            self._trees = list(self._cfg.trees)
        except ValidationError as e:
            # A broken root config must not crash the trees dock — cfg stays
            # None, trees empty, and Save's link_trees round-trip is skipped
            # until a good root is loaded.
            logger.warning(_("Trees: root config failed to load: {error}")
                           .format(error=e))
        self._dirty = False
        self._rebuild_tabs()
        self._update_toolbar_state()

    def apply_highlight(self) -> None:
        """Re-apply the highlight stylesheet — same consumer shape as the
        other tree docks (see gui/dock_hub.py)."""
        for i in range(self.tabs.count()):
            tree = self.tabs.widget(i)
            if isinstance(tree, QTreeWidget):
                tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))

    def _confirm_discard_changes(self) -> bool:
        """True to proceed (either nothing to lose, or the user confirmed
        discarding). Asked before discarding unsaved tree edits (close guard) —
        "lose unsaved changes" prompt, Save goes through the same _do_save."""
        if not self._dirty:
            return True
        ret = QMessageBox.question(
            self, _("Unsaved changes"),
            _("Save changes to {path}?").format(path=self._root_path),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if ret == QMessageBox.StandardButton.Cancel:
            return False
        if ret == QMessageBox.StandardButton.Save:
            self._do_save()
            return not self._dirty  # save failed -> keep the current state
        return True  # Discard

    def closeEvent(self, event) -> None:
        """Close guard: with unsaved changes, ask before discarding them."""
        if self._confirm_discard_changes():
            super().closeEvent(event)
        else:
            event.ignore()

    # ── Tab building + rendering ─────────────────────────────────────────

    def _rebuild_tabs(self) -> None:
        """One tab per Tree in self._trees; a single placeholder tab when the
        list is empty (Phase 2's "Add tree…" fills it)."""
        self.tabs.clear()
        self._node_items = {}
        if not self._trees:
            self.tabs.addTab(QTreeWidget(), _("(no trees)"))
            return
        for tree in self._trees:
            tree_widget = QTreeWidget()
            tree_widget.setHeaderHidden(True)
            tree_widget.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))
            tree_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tree_widget.customContextMenuRequested.connect(self._on_context_menu)
            tree_widget.itemSelectionChanged.connect(self._on_selection_changed)
            self._render_tree(tree_widget, tree)
            self.tabs.addTab(tree_widget, tree.name)
        self.tabs.setCurrentIndex(0)

    def _render_tree(self, tree_widget: QTreeWidget, tree: Tree) -> None:
        """Read-only render: a pseudo-root item for the anchor, then the
        tree's top-level nodes recursively (Phase 1 has no checkboxes —
        those land in Phase 4)."""
        # Pseudo-root showing the anchor, visually distinct (not selectable).
        anchor_item = QTreeWidgetItem(tree_widget.invisibleRootItem())
        anchor_text = f"⚓ {tree.anchor.ref}" if not tree.anchor.is_origin else _("⚓ (origin)")
        anchor_item.setText(0, anchor_text)
        anchor_item.setFlags(anchor_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        for node in tree.nodes:
            self._render_node(anchor_item, node)

    def _render_node(self, parent_item: QTreeWidgetItem, node: TreeNode) -> None:
        item = QTreeWidgetItem(parent_item)
        text = node.ref
        if node.kind is not None:
            tag = _KIND_TAGS.get(node.kind)
            if tag:
                text = f"{text} ({tag})"
        item.setText(0, text)
        # Keep the TreeNode itself on the item — needed by the static preview
        # and structural editing.
        item.setData(0, Qt.ItemDataRole.UserRole, node)
        # Checkbox (Phase 4): tristate so a parent automatically shows a
        # partially-checked state when only some children are selected.
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                      | Qt.ItemFlag.ItemIsUserTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        self._node_items[node.ref] = item
        for child in node.children:
            self._render_node(item, child)

    # ── Static preview (Phase 1, §5) ─────────────────────────────────────

    def _current_tree_widget(self) -> Optional[QTreeWidget]:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, QTreeWidget) else None

    def _on_selection_changed(self) -> None:
        tree_widget = self._current_tree_widget()
        if tree_widget is None:
            return
        items = tree_widget.selectedItems()
        if not items:
            self._show_status("")
            return
        node = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(node, TreeNode):
            # The pseudo-root anchor item carries no node — show nothing.
            self._show_status("")
            return
        self._show_status(self._node_preview_text(node))

    @staticmethod
    def _node_preview_text(node: TreeNode) -> str:
        """Human-readable static offset of the node, straight from the
        dataclass numbers (mm / deg) — NOT recomputed from the Vector2."""
        if node.xy is not None:
            return _("{ref}: xy=({x:.3f}, {y:.3f}) mm").format(
                ref=node.ref, x=node.xy[0], y=node.xy[1])
        if node.polar is not None:
            return _("{ref}: r={r:.3f} mm, angle={a:.3f}°").format(
                ref=node.ref, r=node.polar[0], a=node.polar[1])
        return _("{ref}: no offset").format(ref=node.ref)

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ── Toolbar / dirty state helpers ────────────────────────────────────

    def _update_toolbar_state(self) -> None:
        """Dirty indicator reflects _dirty; Save enabled from Phase 3, Redraw
        from Phase 4 (their skeletons are visible but disabled until then)."""
        self.dirty_label.setText(_("●") if self._dirty else "")
        self.save_button.setEnabled(True)
        self.redraw_button.setEnabled(True)

    def _do_save(self) -> None:
        """Save the trees: section into the ROOT config through the single
        config_writer chokepoint (design_2026_08_27_trees_in_config_file.md
        §5.2): BACKUP the write target (the root config file) BEFORE writing
        (entity_delete's timestamped backup_file — never overwrites an earlier
        backup), then write_data(root, {**read_data(root), "trees": [...]}) —
        the whole section is replaced, every other root key is preserved. Then
        round-trip through read_data -> load_tree + link_trees to surface a
        grammar/link violation. A round-trip failure leaves the file already
        written (by design) but the fresh .bak is the recovery point — show the
        message, do not roll back."""
        if self._root_path is None:
            return  # Save unavailable without a root config
        backup_file(self._root_path)
        trees_dict = [tree_to_dict(t) for t in self._trees]
        write_data(self._root_path, {**read_data(self._root_path), "trees": trees_dict})
        try:
            reloaded = [load_tree(t) for t in trees_dict]
            if self._cfg is not None:
                link_trees(self._cfg, reloaded)
        except ValidationError as e:
            QMessageBox.warning(self, _("Save"), str(e))
            return  # file written, .bak is fresh — report, don't roll back
        self._dirty = False
        self._update_toolbar_state()

    def _mark_dirty(self) -> None:
        """Central dirty setter — every structural mutator (Phase 2) calls
        this instead of setting _dirty inline, so the indicator can never be
        forgotten."""
        self._dirty = True
        self._update_toolbar_state()

    # ── Structural editing (Phase 2) ─────────────────────────────────────

    def _current_tree(self) -> Optional[Tree]:
        """The Tree behind the current tab, or None (no trees loaded)."""
        idx = self.tabs.currentIndex()
        if 0 <= idx < len(self._trees):
            return self._trees[idx]
        return None

    def _current_tree_name(self) -> Optional[str]:
        tree = self._current_tree()
        return tree.name if tree is not None else None

    def _used_refs(self) -> set[str]:
        """Every node ref already used anywhere in the current file — the
        grammar's "a ref appears in at most one node" invariant, surfaced as
        a "(used)" marker in the node dialog's ref combo."""
        used: set[str] = set()
        for tree in self._trees:
            for node in tree.nodes:
                self._collect_refs(node, used)
        return used

    @staticmethod
    def _collect_refs(node: TreeNode, into: set[str]) -> None:
        into.add(node.ref)
        for child in node.children:
            TreesDock._collect_refs(child, into)

    def _all_ref_candidates(self) -> list[str]:
        """Refs offered in the node dialog's combo: the 4 placeable kinds'
        record names from build_records(cfg), deduped, sorted. None when no
        root config is loaded (dialog still works via free text)."""
        if self._cfg is None:
            return []
        names = {r.name for r in build_records(self._cfg)
                 if r.kind in _PLACEABLE_KINDS}
        return sorted(names)

    def _on_context_menu(self, pos) -> None:
        tree_widget = self._current_tree_widget()
        if tree_widget is None:
            return
        item = tree_widget.itemAt(pos)
        if item is None:
            return
        node = item.data(0, Qt.ItemDataRole.UserRole)
        tree = self._current_tree()
        if tree is None:
            return

        menu = QMenu(tree_widget)
        if isinstance(node, TreeNode):
            # Node-level actions.
            menu.addAction(_("Add child")).triggered.connect(
                lambda: self._add_child_flow(tree, node))
            menu.addAction(_("Add sibling")).triggered.connect(
                lambda: self._add_sibling_flow(tree, node))
            menu.addAction(_("Reread current position")).triggered.connect(
                lambda: self._reread_node_flow(tree, node))
            menu.addAction(_("Edit node…")).triggered.connect(
                lambda: self._edit_node_flow(tree, node))
            menu.addAction(_("Delete node")).triggered.connect(
                lambda: self._delete_node_flow(tree, node))
            menu.addAction(_("Rename…")).triggered.connect(
                lambda: self._rename_node_flow(tree, node))
            menu.addAction(_("Move to…")).triggered.connect(
                lambda: self._move_node_flow(tree, node))
        else:
            # Anchor pseudo-root: set the tree anchor, or add its first/next
            # top-level node (the only way a tree gets nodes at all — there
            # is no TreeNode to right-click until one exists).
            menu.addAction(_("Add node")).triggered.connect(
                lambda: self._add_node_flow(tree))
            menu.addAction(_("Set anchor…")).triggered.connect(
                lambda: self._set_anchor_flow(tree))
        menu.exec(tree_widget.viewport().mapToGlobal(pos))

    # ── Node dialog helpers ──────────────────────────────────────────────

    def _live_adapter(self):
        """The live KiCad board adapter (or None when not connected) — the
        same main_window.connection.board.adapter access pattern every other
        dock uses (PlacerDock, RoleClusterTreeDock, ...)."""
        board = getattr(self._main_window.connection, "board", None)
        return getattr(board, "adapter", None)

    def _prompt_node(self, title: str, tree: Tree,
                     parent_node: Optional[TreeNode] = None,
                     existing: Optional[TreeNode] = None) -> Optional[TreeNode]:
        """Open the node dialog (add, or edit when `existing` is set) and
        return the built TreeNode, or None on cancel. `tree` + `parent_node`
        (None = the tree's own anchor) give the dialog the parent context it
        needs for the "Read current position" button; `existing` pre-fills the
        form and relaxes the "ref already used" check to exclude itself."""
        dialog = _NodeDialog(
            self, self._all_ref_candidates(), self._used_refs(), title,
            cfg=self._cfg,
            adapter=self._live_adapter(),
            sheet_names=self._ctx.sheet_names if self._ctx is not None else {},
            tree=tree,
            parent_node=parent_node,
            existing=existing,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.build_node()

    def _add_child_flow(self, tree: Tree, parent: TreeNode) -> None:
        node = self._prompt_node(_("Add child"), tree, parent_node=parent)
        if node is not None:
            parent.children.append(node)
            self._mark_dirty()
            self._rebuild_tabs()

    def _add_sibling_flow(self, tree: Tree, sibling: TreeNode) -> None:
        parent = self._find_parent(tree, sibling)
        node = self._prompt_node(_("Add sibling"), tree, parent_node=parent)
        if node is None:
            return
        if parent is None:
            tree.nodes.append(node)
        else:
            parent.children.append(node)
        self._mark_dirty()
        self._rebuild_tabs()

    def _add_node_flow(self, tree: Tree) -> None:
        node = self._prompt_node(_("Add node"), tree, parent_node=None)
        if node is not None:
            tree.nodes.append(node)
            self._mark_dirty()
            self._rebuild_tabs()

    @staticmethod
    def _find_parent(tree: Tree, node: TreeNode) -> Optional[TreeNode]:
        """The TreeNode that owns `node` as a child, or None if `node` is a
        top-level node of `tree` (its parent is the anchor)."""
        for top in tree.nodes:
            found = TreesDock._find_parent_in(top, node)
            if found is not None:
                return found
        return None

    @classmethod
    def _find_parent_in(cls, candidate: TreeNode, node: TreeNode) -> Optional[TreeNode]:
        if node in candidate.children:
            return candidate
        for child in candidate.children:
            found = cls._find_parent_in(child, node)
            if found is not None:
                return found
        return None

    def _delete_node_flow(self, tree: Tree, node: TreeNode) -> None:
        parent = self._find_parent(tree, node)
        if parent is None:
            tree.nodes.remove(node)
        else:
            parent.children.remove(node)
        self._mark_dirty()
        self._rebuild_tabs()

    def _rename_node_flow(self, tree: Tree, node: TreeNode) -> None:
        new_name, ok = QInputDialog.getText(
            self, _("Rename node"), _("Display name (label, not the ref identity):"),
            text=node.name or node.ref)
        if ok and new_name.strip():
            node.name = new_name.strip()
            self._mark_dirty()
            self._rebuild_tabs()

    def _reread_node_flow(self, tree: Tree, node: TreeNode) -> None:
        """Recompute the node's xy/polar/rotation from its CURRENT live
        position relative to its parent (the same §3+§4 resolution the dialog
        button uses, no dialog), overwriting in place. No confirmation — same
        precedent as "Delete node" (undo is "don't Save"). On resolution
        failure the node is left untouched and the underlying message is shown
        as a warning."""
        adapter = self._live_adapter()
        if adapter is None:
            QMessageBox.warning(
                self, _("Reread current position"),
                _("No live board connection — connect KiCad first."))
            return
        try:
            offset_mm, rotation = _resolve_live_offset(
                self._cfg, adapter,
                self._ctx.sheet_names if self._ctx is not None else {},
                tree, self._find_parent(tree, node), node.ref, node.kind)
        except ValidationError as e:
            QMessageBox.warning(self, _("Reread current position"), str(e))
            return
        node.xy = (offset_mm[0], offset_mm[1])
        node.polar = None
        if rotation is not None:
            node.rotation = rotation
        self._mark_dirty()
        self._rebuild_tabs()

    def _edit_node_flow(self, tree: Tree, node: TreeNode) -> None:
        """The first general node editor: the Add dialog reused with
        existing=node, then the built fields copied onto the EXISTING node in
        place (mutate, don't swap identity — other structures may hold a
        reference, e.g. _node_items)."""
        built = self._prompt_node(_("Edit node"), tree,
                                  parent_node=self._find_parent(tree, node),
                                  existing=node)
        if built is None:
            return
        node.ref = built.ref
        node.kind = built.kind
        node.xy = built.xy
        node.polar = built.polar
        node.rotation = built.rotation
        node.name = built.name
        node.group = built.group
        self._mark_dirty()
        self._rebuild_tabs()

    def _set_anchor_flow(self, tree: Tree) -> None:
        anchor = _AnchorDialog.prompt(self, self._all_ref_candidates())
        if anchor is not None:
            tree.anchor = anchor
            self._mark_dirty()
            self._rebuild_tabs()

    def _move_node_flow(self, tree: Tree, node: TreeNode) -> None:
        """FORK-C: a parent-picker dialog, no drag&drop. The candidate list
        excludes the node itself and its own descendants (a structural
        invariant — you cannot move a node into its own subtree)."""
        forbidden = self._collect_subtree(node)
        candidates: list[tuple[str, Optional[TreeNode]]] = [(_("(top level)"), None)]
        for top in tree.nodes:
            self._collect_move_candidates(top, forbidden, candidates)

        labels = [label for label, _t in candidates]
        choice, ok = QInputDialog.getItem(
            self, _("Move to…"), _("New parent:"), labels, 0, False)
        if not ok:
            return
        new_parent = candidates[labels.index(choice)][1]
        if new_parent is node or self._in_list(new_parent, forbidden):
            return  # structural invariant — the dialog never offered it
        parent = self._find_parent(tree, node)
        if parent is None:
            tree.nodes.remove(node)
        else:
            parent.children.remove(node)
        if new_parent is None:
            tree.nodes.append(node)
        else:
            new_parent.children.append(node)
        self._mark_dirty()
        self._rebuild_tabs()

    @staticmethod
    def _in_list(node: Optional[TreeNode], nodes: list[TreeNode]) -> bool:
        """Identity-based membership — TreeNode is not hashable (a dataclass
        with a list field), so set membership would fail; identity is what
        "is this node in this list" means for structural checks."""
        return any(n is node for n in nodes)

    def _collect_subtree(self, node: TreeNode) -> list[TreeNode]:
        """The node plus every descendant, in pre-order — the set of parents
        a move may NOT target (identity-based, not hash-based)."""
        out: list[TreeNode] = [node]
        for child in node.children:
            out.extend(self._collect_subtree(child))
        return out

    def _collect_move_candidates(self, node: TreeNode, forbidden: list[TreeNode],
                                 out: list) -> None:
        if not self._in_list(node, forbidden):
            out.append((node.ref, node))
        for child in node.children:
            self._collect_move_candidates(child, forbidden, out)

    def _on_add_tree(self) -> None:
        name, ok = QInputDialog.getText(self, _("Add tree"), _("Tree name:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(t.name == name for t in self._trees):
            QMessageBox.warning(self, _("Add tree"),
                                _("A tree named {name!r} already exists.").format(name=name))
            return
        anchor = _AnchorDialog.prompt(self, self._all_ref_candidates())
        if anchor is None:
            return
        self._trees.append(Tree(name=name, anchor=anchor, nodes=[]))
        self._mark_dirty()
        self._rebuild_tabs()
        self.tabs.setCurrentIndex(len(self._trees) - 1)

    def _on_rename_tree(self) -> None:
        tree = self._current_tree()
        if tree is None:
            return
        new_name, ok = QInputDialog.getText(self, _("Rename tree"), _("Tree name:"),
                                            text=tree.name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if any(t.name == new_name for t in self._trees if t is not tree):
            QMessageBox.warning(self, _("Rename tree"),
                                _("A tree named {name!r} already exists.").format(name=new_name))
            return
        tree.name = new_name
        self._mark_dirty()
        self._rebuild_tabs()

    # ── Checkbox subtree selection + Redraw (Phase 4) ────────────────────

    def _on_redraw_selected(self) -> None:
        """Collect the checked nodes' refs and run the curated redraw for the
        current tree in the background (start_long_op) — never blocks the UI
        thread, same worker pattern as AnchorTreeDock's cascade."""
        tree_name = self._current_tree_name()
        if tree_name is None:
            return
        selected_refs = {ref for ref, item in self._node_items.items()
                         if item.checkState(0) == Qt.CheckState.Checked}
        if not selected_refs:
            self._show_status(_("Nothing selected — check some nodes first."))
            return
        payload = {
            "config_path": str(self._root_path) if self._root_path else "",
            "cfg": self._cfg,
            "ctx": self._ctx,
            "trees": self._trees,
            "tree_name": tree_name,
            "selected_refs": selected_refs,
        }
        self._active_op = start_long_op(
            self._main_window.connection, (),
            run_curated_tree_redraw_worker, self._finish_redraw,
            self._on_redraw_failed, payload)

    def _finish_redraw(self, result) -> None:
        results, warnings = result
        ok = sum(1 for _n, good, _e in results if good)
        failed = len(results) - ok
        logger.info(_("Redraw: {ok}/{total} ok").format(ok=ok, total=len(results)))
        if failed:
            logger.warning(_("Redraw: {failed} record(s) failed — see the log above")
                           .format(failed=failed))
        if warnings:
            self._show_status(_("{count} warning(s) — see log").format(count=len(warnings)))
        else:
            self._show_status(_("Redraw: {ok}/{total} ok").format(ok=ok, total=len(results)))

    def _on_redraw_failed(self, message: str) -> None:
        logger.error(_("Redraw failed: {error}").format(error=message))
        self._show_status(_("Redraw failed — see log"))


class _NodeDialog(QDialog):
    """Modal dialog for adding/editing a node: ref + kind + offset (xy/polar
    via AnchorOriginWidget) + rotation/name/group, plus a "Read current
    position" button that fills offset/rotation from the LIVE board relative
    to the parent base (the parent is decided by the context-menu action that
    opened it, not here — design §3). `existing` switches to EDIT mode:
    every field is pre-filled and the "ref already used" check excludes the
    node's own ref."""

    def __init__(self, parent, ref_candidates: list[str], used_refs: set[str],
                 title: str, cfg=None, adapter=None, sheet_names=None,
                 tree=None, parent_node=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._ref_candidates = ref_candidates
        self._used_refs = used_refs
        self._cfg = cfg
        self._adapter = adapter
        self._sheet_names = sheet_names if sheet_names is not None else {}
        self._tree = tree
        self._parent_node = parent_node
        self._existing = existing

        form = QFormLayout(self)

        # kind — "auto" (None) + the 5 grammar kinds.
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(_("auto"), None)
        for k in KINDS:
            self.kind_combo.addItem(k, k)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addRow(_("Kind:"), self.kind_combo)

        # ref — searchable combo over the placeable record names (kind-filtered
        # live), free-typed for external; used refs get a "(used)" marker.
        self.ref_combo = QComboBox()
        configure_searchable(self.ref_combo)
        form.addRow(_("Ref:"), self.ref_combo)

        # offset block — xy/polar only, reused from the shared widget (design §3).
        self.offset_widget = AnchorOriginWidget(modes=["xy"], polar=True)
        form.addRow(_("Offset:"), self.offset_widget)

        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText(_("0"))
        form.addRow(_("Rotation (deg):"), self.rotation_edit)

        # "Read current position" — resolves the typed/picked ref's CURRENT
        # live position/rotation relative to the parent base and fills the
        # offset + rotation fields. Enabled only once ref + an explicit kind
        # are set (a live read must not silently guess the record's section).
        self.read_position_button = QPushButton(_("Read current position"))
        self.read_position_button.clicked.connect(self._on_read_position)
        form.addRow(self.read_position_button)
        self.read_status_label = QLabel("")
        self.read_status_label.setWordWrap(True)
        form.addRow("", self.read_status_label)

        self.name_edit = QLineEdit()
        form.addRow(_("Name (optional):"), self.name_edit)
        self.group_edit = QLineEdit()
        form.addRow(_("Group (optional):"), self.group_edit)

        buttons = QHBoxLayout()
        self.ok_button = QPushButton(_("OK"))
        self.ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton(_("Cancel"))
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(cancel_button)
        form.addRow(buttons)

        self.ref_combo.currentTextChanged.connect(self._update_read_button_state)
        self.kind_combo.currentIndexChanged.connect(self._update_read_button_state)

        if existing is not None:
            self._prefill(existing)   # calls _on_kind_changed() itself (kind
                                      # must be set BEFORE the ref combo is
                                      # repopulated; external clears its items)
        else:
            self._on_kind_changed()
        self._update_read_button_state()

    def _prefill(self, existing: TreeNode) -> None:
        """Edit mode: populate every field from an existing node. Called
        BEFORE _on_kind_changed() so the ref combo is repopulated for the
        pre-filled kind (external clears its candidates)."""
        kind_idx = self.kind_combo.findData(existing.kind)
        if kind_idx >= 0:
            self.kind_combo.setCurrentIndex(kind_idx)
        self._on_kind_changed()
        self.ref_combo.setCurrentText(existing.ref)
        if existing.xy is not None:
            self.offset_widget.load(x=existing.xy[0], y=existing.xy[1])
        elif existing.polar is not None:
            self.offset_widget.load(polar=True, radius=existing.polar[0],
                                    angle=existing.polar[1])
        else:
            self.offset_widget.load()
        self.rotation_edit.setText(str(existing.rotation))
        self.name_edit.setText(existing.name or "")
        self.group_edit.setText(existing.group or "")

    def _update_read_button_state(self) -> None:
        """Button enabled only once BOTH a ref and an explicit kind are set —
        a live position read needs the record's section to resolve against."""
        has_ref = bool(self.ref_combo.currentText().strip())
        has_kind = self.kind_combo.currentData() is not None
        self.read_position_button.setEnabled(has_ref and has_kind)

    def _on_read_position(self) -> None:
        """Resolve the typed/picked ref's current live position/rotation
        relative to the parent base and fill offset + rotation. Any resolution
        failure (no live connection, ref not on the board, ambiguous, broken
        tree state) is shown as a warning — never a silent partial write, never
        an uncaught exception in a GUI callback."""
        self.read_status_label.setText("")
        ref = self.ref_combo.currentText().strip()
        kind = self.kind_combo.currentData()
        if not ref:
            return
        if self._adapter is None:
            QMessageBox.warning(
                self, _("Read current position"),
                _("No live board connection — connect KiCad first."))
            return
        if self._cfg is None or self._tree is None:
            QMessageBox.warning(
                self, _("Read current position"),
                _("No root config loaded — cannot resolve the record."))
            return
        try:
            offset_mm, rotation = _resolve_live_offset(
                self._cfg, self._adapter, self._sheet_names,
                self._tree, self._parent_node, ref, kind)
        except ValidationError as e:
            QMessageBox.warning(self, _("Read current position"), str(e))
            return
        # Fill the Cartesian offset only — the offset widget's own xy/polar
        # toggle is the user's choice (never guess polar from a flat delta).
        self.offset_widget.x_edit.setText(f"{offset_mm[0]:.3f}")
        self.offset_widget.y_edit.setText(f"{offset_mm[1]:.3f}")
        if rotation is None:
            self.read_status_label.setText(
                _("rotation not available for this record kind"))
        else:
            self.rotation_edit.setText(f"{rotation:.3f}")

    def _on_kind_changed(self) -> None:
        """kind == "external" -> ref is a free-text external refdes (the combo
        is already searchable/editable, so just clear its candidates and hint);
        otherwise -> the placeable record names, filtered by kind if set."""
        kind = self.kind_combo.currentData()
        if kind == "external":
            self.ref_combo.clear()
            self.ref_combo.setPlaceholderText(_("external refdes (live board)"))
            return
        names = list(self._ref_candidates)
        if kind in _PLACEABLE_KINDS:
            names = [n for n in names]
        set_combo_items(self.ref_combo, names)
        self.ref_combo.setPlaceholderText(_("record name (from config)"))

    def build_node(self) -> Optional[TreeNode]:
        """Collect + validate the form into a TreeNode, or None (invalid —
        an error is shown via QMessageBox)."""
        ref = self.ref_combo.currentText().strip()
        if not ref:
            QMessageBox.warning(self, _("Add node"), _("Ref is required."))
            return None
        used_refs = self._used_refs
        if self._existing is not None:
            # Editing a node without changing its ref must not trip the
            # uniqueness check against itself (compare to the set MINUS the
            # node's own ref — the naive "exclude by value" could otherwise be
            # got backwards and let a DIFFERENT node's ref through).
            used_refs = {r for r in used_refs if r != self._existing.ref}
        if ref in used_refs:
            QMessageBox.warning(
                self, _("Add node"),
                _("Record {ref!r} already has a node in this file — a record's "
                  "position source must be exactly one.").format(ref=ref))
            return None

        fields, err = self.offset_widget.build()
        if err:
            QMessageBox.warning(self, _("Add node"), err)
            return None
        if "radius" in fields:
            polar = (fields["radius"], fields["angle"])
            xy = None
        else:
            xy = (fields["x"], fields["y"])
            polar = None

        try:
            rotation = float(self.rotation_edit.text()) if self.rotation_edit.text().strip() else 0.0
        except ValueError:
            QMessageBox.warning(self, _("Add node"), _("Rotation must be a number."))
            return None

        name = self.name_edit.text().strip() or None
        group = self.group_edit.text().strip() or None
        return TreeNode(ref=ref, kind=self.kind_combo.currentData(), xy=xy,
                        polar=polar, rotation=rotation, name=name, group=group)


class _AnchorDialog(QDialog):
    """Modal dialog for picking a tree anchor: (origin) / config record ref /
    free-text external refdes (design §4)."""

    def __init__(self, parent, ref_candidates: list[str]):
        super().__init__(parent)
        self.setWindowTitle(_("Set anchor"))
        self._result: Optional[TreeAnchor] = None

        form = QFormLayout(self)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(_("Origin (board 0,0)"), "origin")
        self.mode_combo.addItem(_("Config record"), "record")
        self.mode_combo.addItem(_("External refdes"), "external")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow(_("Anchor:"), self.mode_combo)

        self.ref_combo = QComboBox()
        configure_searchable(self.ref_combo)
        set_combo_items(self.ref_combo, ref_candidates)
        self.ref_combo.setPlaceholderText(_("record name (from config)"))
        form.addRow(_("Ref:"), self.ref_combo)

        buttons = QHBoxLayout()
        self.ok_button = QPushButton(_("OK"))
        self.ok_button.clicked.connect(self._accept)
        cancel_button = QPushButton(_("Cancel"))
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(cancel_button)
        form.addRow(buttons)

        self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        self.ref_combo.setEnabled(self.mode_combo.currentData() != "origin")

    def _accept(self) -> None:
        mode = self.mode_combo.currentData()
        if mode == "origin":
            self._result = TreeAnchor(ref=None, is_origin=True)
        else:
            ref = self.ref_combo.currentText().strip()
            if not ref:
                QMessageBox.warning(self, _("Set anchor"), _("Ref is required."))
                return
            self._result = TreeAnchor(ref=ref, is_origin=False)
        self.accept()

    @staticmethod
    def prompt(parent, ref_candidates: list[str]) -> Optional[TreeAnchor]:
        dlg = _AnchorDialog(parent, ref_candidates)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg._result
