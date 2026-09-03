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
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDockWidget,
                             QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QMenu, QMessageBox, QPushButton,
                             QRadioButton, QSizePolicy, QTabWidget,
                             QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
                             QVBoxLayout, QWidget)

from kicadstamp.anchor_graph import Record, build_records
from kicadstamp.config import TreeInstance, load_config, load_tree
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.config_writer import read_data, upsert_entity, write_data
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.link_trees import (
    _PLACEABLE_KINDS,
    _build_by_key_index,
    _build_by_name_index,
    _resolve_anchor_ref,
    _resolve_node_ref,
    link_trees,
)
from kicadstamp.tree_position import (
    _anchor_base_live_position,
    _root_entity_record,
    _root_entity_ref,
    resolve_base_live_position,
    resolve_base_rotation_deg,
    relative_rotation_deg,
)
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver,
    resolve_anchor_pad_position,
)
from kicadstamp.placement.services.point_resolver import resolve_point_chain
from kicadstamp.trees import KINDS, Tree, TreeAnchor, TreeNode, tree_to_dict
from kicadstamp.utils.units import MM

from .. import settings
from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (configure_searchable, highlight_stylesheet_for,
                      set_combo_items)
from .cascade import (run_curated_forest_redraw_worker, run_curated_tree_redraw_worker,
                      run_single_node_redraw_worker)
from .entity_delete import backup_file

logger = logging.getLogger(__name__)

# Short kind tags, shown next to a node's ref when the kind is set. "external"
# is included here (unlike anchor_tree's _KIND_TAGS, which has no external
# leaf — trees need it).
_KIND_TAGS = {
    "clone": _("clone"),
    "placement": _("placement"),
    "chain": _("chain"),
    # Legacy kind alias (2026-09-01 Rule -> Chain rename): a tree node still
    # carrying kind "rule" shows the chain tag too.
    "rule": _("chain"),
    "coordinate": _("coordinate"),
    "point": _("point"),
    "external": _("external"),
    "module": _("module"),
}


def _anchor_label(anchor: TreeAnchor) -> str:
    """Human-readable label for a tree's anchor pseudo-root — one branch per
    TreeAnchor mode; never renders "None" (2026-08-31, anchor-dialog GUI gap:
    auto/role/point anchors carry ref=None and would otherwise show "⚓ None").
    The exact tag per mode is a display convention only — the underlying
    TreeAnchor is unchanged."""
    if anchor.is_origin:
        return _("⚓ (origin)")
    if anchor.is_auto:
        return _("⚓ (auto)")
    if anchor.role:
        details = " / ".join(
            part for part in (anchor.anchor_sheet, anchor.anchor_cluster,
                              anchor.anchor_pad) if part)
        base = _("⚓ (role {role})").format(role=anchor.role)
        return f"{base} {details}" if details else base
    if anchor.point:
        return _("⚓ (point {point})").format(point=anchor.point)
    if anchor.ref:
        if anchor.is_external:
            return _("⚓ {ref} (external)").format(ref=anchor.ref)
        return f"⚓ {anchor.ref}"
    return _("⚓ (unknown)")


_ORIGIN = Vector2.from_xy(0, 0)


def collect_tree_refs(tree: "Tree") -> list[str]:
    """ALL node refs of a Tree, DFS parent-before-child, regardless of any
    checkbox state — the selection source for "Redraw whole tree"
    (plan_2026_08_29_fork1_rigid_redraw_override.md §5): the whole operation
    must not depend on the UI checkbox state, only on the tree structure."""
    refs: list[str] = []

    def walk(nodes: list) -> None:
        for node in nodes:
            refs.append(node.ref)
            walk(node.children)

    walk(tree.nodes)
    return refs


def _tree_net_trace_nets(tree: "Tree") -> set[str]:
    """Every net referenced by the tree's kind="net_trace" nodes (phase D/E,
    2026-09-01) — the net_traces: records that belong to this tree's captured
    inter-cluster copper (used by the delete-tree cascade to find orphans)."""
    nets: set[str] = set()

    def walk(nodes: list) -> None:
        for node in nodes:
            if node.kind == "net_trace" and node.ref:
                nets.add(node.ref)
            walk(node.children)

    walk(tree.nodes)
    return nets


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


def _resolve_live_offset(cfg, adapter, sheet_names, tree: Tree,
                         parent_node: Optional[TreeNode], ref: str, kind: str | None,
                         base_anchor: Optional[TreeAnchor] = None
                         ) -> tuple[tuple[float, float], Optional[float]]:
    """((offset_x_mm, offset_y_mm), relative_rotation_deg | None) for the
    "would-be" child `ref`/`kind` relative to `parent_node` (None = the tree's
    own anchor). Reuses the EXACT link_trees resolution rules via
    _resolve_probe_ref + the existing tree_position resolvers — nothing
    duplicated here. The tree's own anchor base is resolved by
    _anchor_base_live_position so EVERY anchor mode works (origin/auto/role/
    point/ref); a parent NODE is resolved the same single-ref way as before.
    Rotation is None when either side has no rotation concept (point kind) —
    the caller must leave the field blank, never write a fake 0. Raises
    ValidationError on any resolution failure (ref not found/ambiguous, adapter
    not connected, ref missing on the live board, a non-canonical auto tree)."""
    child_record, _is_external = _resolve_probe_ref(cfg, ref, kind)

    # No KeyError boundary here any more: bug #6 (2026-08-31) made
    # ClonePositionCalculator._resolve_anchor resolve its anchor_point LAZILY on
    # demand (resolve_point_chain), so a clone+anchor_point live read succeeds
    # even with the always-empty resolved_points dict this ad-hoc path passes —
    # the 2026-08-27 workaround that converted that KeyError into a warning is
    # superseded. Real resolution failures (a missing point, a ref not on the
    # board, ...) are ValidationErrors, caught by the callers
    # (_on_read_position / _reread_node_flow), which turn them into a warning.
    if base_anchor is not None:
        # The node's OWN anchor (plan tree_node_own_anchor §2.3): the read
        # offset is measured from the anchor's LIVE role frame, NOT the parent —
        # otherwise the button would diff against the wrong base for a node
        # positioned relative to a component. Same ComponentResolver resolution
        # the recursive walks use (shared semantics, no duplicated logic).
        resolver = ComponentResolver(adapter, cfg, sheet_names)
        fp = resolver.resolve_anchor_fp(
            None, base_anchor.role, base_anchor.anchor_sheet,
            base_anchor.anchor_cluster, label=base_anchor.role)
        parent_pos = fp.position
        parent_deg = fp.angle_deg
        if base_anchor.anchor_pad:
            parent_pos = resolve_anchor_pad_position(
                adapter, fp, base_anchor.anchor_pad, base_anchor.role)
    elif parent_node is None:
        # The base is the tree's own anchor — full anchor-mode support, not the
        # old origin-only/ref-only split (role/point/auto used to read ref=None).
        parent_pos, parent_deg = _anchor_base_live_position(
            adapter, cfg, tree, sheet_names)
    else:
        # The base is the parent NODE's own resolved record (external node ->
        # live refdes read, same as _resolve_probe_ref used to provide).
        parent_record, _is_external = _resolve_probe_ref(cfg, parent_node.ref, parent_node.kind)
        parent_pos = resolve_base_live_position(adapter, cfg, parent_node.ref, parent_record, {}, sheet_names)
        parent_deg = resolve_base_rotation_deg(adapter, cfg, parent_node.ref, parent_record, sheet_names)

    child_pos = resolve_base_live_position(adapter, cfg, ref, child_record, {}, sheet_names)
    child_deg = resolve_base_rotation_deg(adapter, cfg, ref, child_record, sheet_names)

    offset_mm = ((child_pos.x - parent_pos.x) / MM, (child_pos.y - parent_pos.y) / MM)
    rotation = (relative_rotation_deg(child_deg, parent_deg)
                if parent_deg is not None and child_deg is not None else None)
    return offset_mm, rotation


def _copy_node_onto(target: TreeNode, built: TreeNode) -> None:
    """Copy every editable field of a BUILT node onto an EXISTING node in place
    (mutate, don't swap identity — other structures may hold a reference, e.g.
    _node_items). The single copy routine shared by the node editor dialog's
    Apply button (Phase B) and the legacy _edit_node_flow commit path, so the
    two can never drift."""
    target.ref = built.ref
    target.kind = built.kind
    target.xy = built.xy
    target.polar = built.polar
    target.rotation = built.rotation
    target.name = built.name
    target.group = built.group
    target.pivot_xy = built.pivot_xy
    target.pivot_polar = built.pivot_polar
    target.own_anchor = built.own_anchor


class TreesDock(QDockWidget):
    """QDockWidget hosting the hand-authored trees editor for the root
    config's trees: section (design_2026_08_27_trees_in_config_file.md).
    Since 2026-09-03 (plan plan_2026_09_03_trees_menu_tools.md) the
    whole-tree actions (Create/Rename/Delete tree, Anchor position, Redraw
    selected/whole) live in the top-level menu Tools → Trees; the dock itself
    keeps the per-tree tabs, the per-node context menus and the read-only
    status row. dock_hub adds and tabifies it like the other tree docks. No
    file identity of its own — the trees live in the root config (cfg.trees),
    read via root_changed and saved through config_writer."""

    def __init__(self, main_window):
        super().__init__(_("Trees"), main_window)
        # Stable QDockWidget identity for QMainWindow.saveState()/restoreState()
        # (handoff sync_skip_message_and_view_menu §0) — without a unique
        # objectName Qt cannot reliably map a saved layout blob back to this
        # dock between runs.
        self.setObjectName("trees_dock")
        self._main_window = main_window
        self._trees: list[Tree] = []
        # tree_instances (2026-09-02, P1): the materialized instance trees live
        # in cfg.trees — and therefore in self._trees, so redraw/embedding/
        # forest treat them as ordinary trees — but the raw tree_instances:
        # declarations (cfg.tree_instances) mark them as GENERATED: an instance
        # tree is read-only here (geometry comes from template + declaration)
        # and is NEVER persisted by _do_save/_stage_trees (regenerated on every
        # load from the declaration). name -> TreeInstance.
        self._instances: dict[str, TreeInstance] = {}
        self._root_path: Optional[Path] = None   # for link_trees + Save, via root_changed
        self._cfg = None
        self._ctx = None
        self._dirty: bool = False                # used from Phase 2, field kept from the start
        self._active_op = None
        # Phase E (2026-09-01): net_traces: records orphaned by a deleted tree's
        # net_trace nodes — removed from the working set on the next stage.
        self._orphan_net_nets: set[str] = set()
        # ref -> QTreeWidgetItem, rebuilt on every render — needed for the
        # checkbox selection (Phase 4) and for the move "not into own
        # descendant" guard (Phase 2).
        self._node_items: dict[str, QTreeWidgetItem] = {}
        # (P1/P2, 2026-09-03, plan tree_ui_state_persistence): active-tab and
        # per-tree expand/collapse state. _pending_active_name is a NAME set by
        # set_root_file (the persisted active_tab) and consumed by the next
        # _rebuild_tabs(); _rebuilding_tabs suppresses the persistence signal
        # handlers during a rebuild (their intermediate events are not user
        # state — the rebuild persists its final state itself).
        self._pending_active_name: Optional[str] = None
        self._rebuilding_tabs = False

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        self.setWidget(container)

        # ── No whole-tree toolbar (2026-09-03, plan
        #    plan_2026_09_03_trees_menu_tools.md): every whole-tree action —
        #    Create/Rename/Delete tree, Anchor position, Redraw selected and
        #    Redraw whole tree — lives in the top-level menu Tools → Trees
        #    (gui/main_window.py + DockHub delegates). The dock is a pure
        #    per-tree editor: tabs, checkbox subtree selection, per-node
        #    context menus, and the read-only indicators in the status row
        #    below. The handlers stay here as the single call points for the
        #    Tools-menu QActions (see _on_create_tree/_on_rename_tree/...).

        # ── Per-tree tabs ────────────────────────────────────────────────
        self.tabs = QTabWidget()
        # (P1) Persist the active tab by tree name on every switch — the
        # rebuild-time events are filtered by _rebuilding_tabs.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        # ── Bottom status row: read-only indicators ──────────────────────
        # The whole-tree action buttons are gone (Tools → Trees owns them);
        # only the two read-only labels stay — the anchor live-position
        # readout and the unsaved-changes ●.
        status_row = QHBoxLayout()
        # Labels must never floor the dock width (their text can be wide, e.g.
        # a live anchor position) — Ignored lets them shrink below the text.
        self.anchor_pos_label = QLabel("")
        self.anchor_pos_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                            QSizePolicy.Policy.Preferred)
        status_row.addWidget(self.anchor_pos_label)
        status_row.addStretch(1)
        self.dirty_label = QLabel("")
        self.dirty_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                       QSizePolicy.Policy.Preferred)
        status_row.addWidget(self.dirty_label)
        layout.addLayout(status_row)

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
        self._instances = {}
        if path is None:
            self._dirty = False
            self._rebuild_tabs()
            self._update_status_row()
            return
        try:
            self._cfg, self._ctx = load_config(str(path))
            self._trees = list(self._cfg.trees)
            # Invariant (2026-09-03, plan trees_dock_cfg_trees_desync.md):
            # self._cfg.trees MUST be the SAME list object as self._trees — the
            # redraw payloads pass "cfg" (whose trees ApplyPipeline reads), so
            # cfg.trees and the working buffer must never diverge. In scope here
            # they already match by value; this just pins the shared identity
            # from the very start of the dock's life.
            self._cfg.trees = self._trees
        except (ValidationError, OSError) as e:
            # A broken root config must not crash the trees dock — cfg stays
            # None, trees empty, and Save's link_trees round-trip is skipped
            # until a good root is loaded.
            logger.warning(_("Trees: root config failed to load: {error}")
                           .format(error=e))
        # tree_instances read-only index (cleared above on no-root; rebuilt from
        # the loaded cfg — empty when the config has no tree_instances:).
        self._rebuild_instance_index()
        self._dirty = False
        # (P1) A fresh root load applies the remembered active tab (by NAME)
        # from gui_state.json; _rebuild_tabs consumes it. An absent/foreign
        # name is fatal-safe — the rebuild falls back to tab 0.
        self._pending_active_name = self._persisted_active_tab_name()
        self._rebuild_tabs()
        self._update_status_row()

    def refresh_ref_candidates(self) -> None:
        """Lightweight cfg re-read — the TreesDock half of DockHub's
        _refresh_graph_dependent_choices (wired in gui/dock_hub.py, plan
        2026-08-31_trees_dock_stale_after_entity_add.md). The include graph's
        shape or an entry's name changed (a new Entity/Cell/Rule/... saved by
        another dock, or a rename/delete/add-file in ConfigTreeDock), so the
        ref candidates behind the node/anchor dialogs must reflect it without
        waiting for a root change or an app restart.

        Unlike set_root_file this NEVER touches self._trees or self._dirty:
        trees already loaded/edited stay exactly as they are (unsaved edits
        preserved) — only self._cfg/self._ctx are re-read from the SAME root,
        so the next _all_ref_candidates()/_live_roles()/_live_clusters() and
        every combo populated at dialog-open time see the fresh graph. The
        dialogs fetch their candidates lazily when opened (see _prompt_node/
        _set_anchor_flow/_on_create_tree), so no tab rebuild is needed here — the
        opposite of the other docks, whose set_root_path refresh_file_combo_
        choices repopulates live combos, and which are safe to call because
        they never reset loaded form state.

        No-op when no root is loaded. On a load failure the PREVIOUS cfg/ctx
        are kept (a transiently broken file must not wipe the candidate
        combos; set_root_file remains the only full-teardown path, on a real
        root change) and the failure is logged."""
        if self._root_path is None:
            return
        try:
            cfg, ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            logger.warning(_("Trees: root config failed to load: {error}")
                           .format(error=e))
            return
        self._cfg = cfg
        self._ctx = ctx
        # Invariant (2026-09-03, plan trees_dock_cfg_trees_desync.md): this
        # method re-reads cfg/ctx but deliberately NEVER touches _trees (dirty
        # edits preserved) — rebind cfg.trees to the working buffer so the two
        # lists cannot diverge and a later redraw reads the ACTUAL nodes.
        self._cfg.trees = self._trees
        self._rebuild_instance_index()
        if self._dirty:
            logger.debug("Trees: ref candidates refreshed; unsaved tree "
                         "edits were left untouched (trees stay stale until "
                         "saved)")

    def reload_trees(self) -> None:
        """Re-read the root config's trees: section after an EXTERNAL write —
        the TreesDock half of "Tools -> Trees -> Extract tree..." (2026-09-01, plan
        extract_selection_as_tree.md), which saves the new tree through
        config_writer directly, BYPASSING this dock's own Save. Without this
        the dock would keep showing the pre-action tree until the root was
        reassigned.

        Rebuilds cfg/ctx/_trees from the same root like set_root_file, but
        NEVER wipes an in-progress dirty edit: when _dirty, the current buffer
        stays exactly as it is and externally-added trees (present on disk, not
        in the buffer) are appended by name — so an unsaved tree the user is
        hand-editing is preserved and the new "Extract tree..." tab appears.
        No-op when no root is loaded; on a load failure the PREVIOUS cfg/ctx/
        _trees are kept and the failure is logged (same discipline as
        refresh_ref_candidates)."""
        if self._root_path is None:
            return
        try:
            cfg, ctx = load_config(str(self._root_path))
        except (ValidationError, OSError) as e:
            logger.warning(_("Trees: root config failed to reload: {error}")
                           .format(error=e))
            return
        self._cfg = cfg
        self._ctx = ctx
        self._rebuild_instance_index()
        fresh = list(cfg.trees)
        if self._dirty:
            # Unsaved edits stay untouched; append whatever appeared AFTER the
            # current buffer. Index-based (not name-based): an unsaved RENAME
            # makes the on-disk old name look like a brand-new tree and would
            # duplicate it — external writers ("Extract tree...") only ever
            # APPEND, so the buffer's tail is exactly what is new.
            self._trees = self._trees + fresh[len(self._trees):]
            logger.debug("Trees: external trees reloaded; unsaved tree edits "
                         "were left untouched")
        else:
            self._trees = fresh
        # Invariant (2026-09-03, plan trees_dock_cfg_trees_desync.md): `fresh`
        # was captured from cfg.trees ABOVE, before this rebind. Only now that
        # _trees holds its FINAL value (dirty-merged or fresh) do we rebind
        # cfg.trees to it — so redraw payloads and Save's link_trees round-trip
        # always see the same tree objects the dock is showing/editing.
        self._cfg.trees = self._trees
        self._rebuild_tabs()
        self._update_status_row()

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
        list is empty (Tools → Trees → Create tree… fills it)."""
        # (P1, 2026-09-03, plan tree_ui_state_persistence): remember the
        # CURRENT active tree by NAME before clear() so the rebuild keeps the
        # user on the same tab instead of unconditionally jumping to tab 0 (the
        # pre-fix behavior). The explicitly pending name (the persisted
        # active_tab, applied by set_root_file on a fresh load) wins; otherwise
        # the previously active tree when it survives this rebuild; only then
        # tab 0. Tab indexes are not stable across a rebuild — trees get
        # added/removed/renamed — hence the name-based restore.
        previous_name = self._current_tab_tree_name()
        # Suppress the persistence signal handlers while repopulating: the
        # intermediate currentChanged events (clear -> addTab ->
        # setCurrentIndex) are not user state. The final active tab is
        # persisted below, once it is restored.
        self._rebuilding_tabs = True
        self.tabs.clear()
        self._node_items = {}
        if not self._trees:
            # Nothing to apply a pending active tab to — drop it so a stale
            # name from a previous root cannot leak into a later load.
            self._pending_active_name = None
            placeholder = QTreeWidget()
            # Explicit minimum width (2026-08-30, Denis: TreesDock can't be
            # narrowed after being widened once) — QTreeWidget's natural
            # minimumSizeHint() floors the dock's width the same way
            # QPlainTextEdit's floored LogDock's height (commit 9d8ddff).
            # Same proven value: 1, NOT 0 — Qt treats an explicit minimum of
            # exactly 0 as "unset" and silently falls back to minimumSizeHint()
            # (see tests/gui/test_log_panel.py::
            # test_text_view_minimum_height_is_explicitly_overridden).
            placeholder.setMinimumWidth(1)
            self.tabs.addTab(placeholder, _("(no trees)"))
            self._rebuilding_tabs = False
            return
        # (P2) Saved per-tree expansion map — the whole "trees" sub-key is read
        # ONCE per rebuild (not once per tree). An absent/foreign name means "no
        # saved state": that tree renders at the Qt default (collapsed), exactly
        # like today (design §1.2 — not a regression, just no data to restore).
        saved_tree_state = self._saved_trees_state()
        for tree in self._trees:
            tree_widget = QTreeWidget()
            # Same 2026-08-30 width-floor fix as the placeholder above: the
            # tree scrolls long node refs (e.g. Entity names) horizontally on
            # its own, so it must never force the dock wider than the layout
            # wants. 1, not 0 (see the placeholder comment / log_panel test).
            tree_widget.setMinimumWidth(1)
            tree_widget.setHeaderHidden(True)
            tree_widget.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))
            tree_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tree_widget.customContextMenuRequested.connect(self._on_context_menu)
            tree_widget.itemSelectionChanged.connect(self._on_selection_changed)
            tree_widget.itemDoubleClicked.connect(self._on_node_activated)
            # (P2) itemExpanded/itemCollapsed are per-tree-widget signals, so
            # each handler is bound to ITS tree's name (the handler must know
            # which tree the item belongs to in order to update the right
            # trees_dock.trees entry). Events fired while _rebuild_tabs() is
            # applying the SAVED state are filtered by the _rebuilding_tabs
            # guard inside _on_item_expand_changed.
            tree_widget.itemExpanded.connect(
                lambda item, name=tree.name: self._on_item_expand_changed(name, item))
            tree_widget.itemCollapsed.connect(
                lambda item, name=tree.name: self._on_item_expand_changed(name, item))
            self._render_tree(tree_widget, tree, saved_tree_state.get(tree.name, {}))
            self.tabs.addTab(tree_widget, tree.name)
        # (P1) Restore the active tab by name.
        desired = self._pending_active_name
        self._pending_active_name = None
        if desired is None:
            desired = previous_name
        for idx, tree in enumerate(self._trees):
            if tree.name == desired:
                self.tabs.setCurrentIndex(idx)
                break
        else:
            # The desired tree is gone (deleted/renamed by this very edit) or
            # nothing was active before — today's behavior, tab 0.
            self.tabs.setCurrentIndex(0)
        self._rebuilding_tabs = False
        # Keep gui_state.json in sync with the restored tab (also covers a
        # rebuild that dropped the previously active tree -> tab 0).
        self._persist_active_tab()

    # ── UI-state persistence (2026-09-03, plan tree_ui_state_persistence) ──
    #
    # gui_state.json["trees_dock"] is a NESTED dict with two independent parts:
    # "active_tab" (P1) and the per-tree "trees" expansion map (P2). Settings
    # merges only TOP-LEVEL keys (gui/settings.py), so P1 and P2 must merge
    # with each other INSIDE that one value — _update_trees_dock_state is the
    # single read-merge-mutate-write helper both phases go through.

    def _update_trees_dock_state(self, mutate) -> None:
        """Read-merge-mutate-write of the nested "trees_dock" settings key.
        `mutate(dock_state)` edits the current dict value in place; the whole
        dict is then written back under "trees_dock". Lets P1 ("active_tab")
        and P2 (per-tree "trees") update their own sub-key without clobbering
        each other."""
        data = settings.state.get("trees_dock")
        if not isinstance(data, dict):
            data = {}
        mutate(data)
        settings.state.set("trees_dock", data)

    def _persisted_active_tab_name(self) -> Optional[str]:
        """The persisted trees_dock.active_tab if it names one of the CURRENTLY
        loaded trees, else None — fatal-safe: a missing/foreign/stale name (a
        tree deleted or renamed since it was saved) just falls back to tab 0."""
        active = settings.state.get("trees_dock", {}).get("active_tab")
        if isinstance(active, str) and any(t.name == active for t in self._trees):
            return active
        return None

    def _current_tab_tree_name(self) -> Optional[str]:
        """Name of the tree behind the CURRENT tab, or None when the tab widget
        is not in a state consistent with self._trees (no trees, or a stale tab
        count right after a structural edit changed the tree list)."""
        idx = self.tabs.currentIndex()
        if (idx < 0 or idx >= len(self._trees)
                or self.tabs.count() != len(self._trees)):
            return None
        return self._trees[idx].name

    def _persist_active_tab(self) -> None:
        """Write the CURRENT active tab (by tree name) into gui_state.json's
        trees_dock.active_tab. No-op while no real trees are loaded."""
        if not self._trees:
            return
        name = self._current_tab_tree_name()
        if name is not None:
            self._update_trees_dock_state(lambda d: d.update({"active_tab": name}))

    def _on_tab_changed(self, _index: int) -> None:
        """Active tab switched (by the user or programmatically) -> persist by
        tree name. Ignored while _rebuild_tabs() is repopulating the widget —
        those intermediate currentChanged events are not user state; the
        rebuild persists its final active tab itself."""
        if self._rebuilding_tabs:
            return
        self._persist_active_tab()

    def _saved_trees_state(self) -> dict:
        """The persisted per-tree expansion map (trees_dock.trees): a plain
        dict of tree_name -> {anchor_expanded, expanded_refs}. {} when the key
        is absent or not a dict — fatal-safe, callers fall back to Qt defaults
        (collapsed), which is also what a first run / foreign file gets."""
        trees = settings.state.get("trees_dock", {}).get("trees")
        return trees if isinstance(trees, dict) else {}

    def _update_tree_ui_state(self, tree_name: str, mutate) -> None:
        """Read-merge-mutate-write of ONE tree's expansion entry inside
        trees_dock.trees. Several trees each own their own entry AND P1's
        sibling "active_tab" key must survive — this nests under the shared
        _update_trees_dock_state, which handles the outer merge."""
        def _fn(dock_state: dict) -> None:
            trees = dock_state.get("trees")
            if not isinstance(trees, dict):
                trees = {}
                dock_state["trees"] = trees
            entry = trees.get(tree_name)
            if not isinstance(entry, dict):
                entry = {}
                trees[tree_name] = entry
            mutate(entry)
        self._update_trees_dock_state(_fn)

    def _on_item_expand_changed(self, tree_name: str,
                                item: QTreeWidgetItem) -> None:
        """A user expanded/collapsed a node or the anchor pseudo-root -> persist
        that tree's expansion state. Ignored while _rebuild_tabs() is
        repopulating — those events come from APPLYING the saved state and are
        not user actions. Pseudo navigation items ("⇐ embedded in" / "⇐
        instance of" / "→ instance: …") carry a str in UserRole and have no
        children — nothing to persist."""
        if self._rebuilding_tabs:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, TreeNode):
            ref = data.ref
            expanded = bool(item.isExpanded())
            def _fn(entry: dict) -> None:
                refs = entry.get("expanded_refs")
                if not isinstance(refs, list):
                    refs = []
                if expanded:
                    if ref not in refs:
                        refs.append(ref)
                else:
                    refs = [r for r in refs if r != ref]
                entry["expanded_refs"] = refs
            self._update_tree_ui_state(tree_name, _fn)
        elif data is None:
            # The anchor pseudo-root carries no UserRole data.
            expanded = bool(item.isExpanded())
            self._update_tree_ui_state(
                tree_name,
                lambda entry: entry.update({"anchor_expanded": expanded}))

    def _capture_tree_expansion(self, tree_widget: QTreeWidget) -> dict:
        """Read the CURRENT expansion of one rendered tree into the persisted
        entry shape {anchor_expanded, expanded_refs} — the final-flush path
        (per-event handlers cover individual changes as they happen)."""
        anchor_expanded = False
        expanded_refs: list = []
        it = QTreeWidgetItemIterator(tree_widget)
        while it.value():
            item = it.value()
            if item.childCount() and item.isExpanded():
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(data, TreeNode):
                    expanded_refs.append(data.ref)
                elif data is None:
                    anchor_expanded = True
            it += 1
        return {"anchor_expanded": anchor_expanded,
                "expanded_refs": expanded_refs}

    def persist_ui_state(self) -> None:
        """Final flush — called by MainWindow._persist_settings() on quit/
        close. Re-reads the CURRENT widget state so an interaction that
        happened after the last _rebuild_tabs (a pure tab switch or a manual
        expand/collapse with no structural edit) is still captured."""
        if not self._trees:
            return
        self._persist_active_tab()
        # (P2) Also flush every rendered tree's expansion, from the widgets
        # themselves (authoritative — drops refs whose nodes are gone).
        def _fn(dock_state: dict) -> None:
            trees = dock_state.get("trees")
            if not isinstance(trees, dict):
                trees = {}
                dock_state["trees"] = trees
            for i, tree in enumerate(self._trees):
                widget = self.tabs.widget(i)
                if isinstance(widget, QTreeWidget):
                    trees[tree.name] = self._capture_tree_expansion(widget)
        self._update_trees_dock_state(_fn)

    def _embedded_in(self, tree: Tree) -> list[str]:
        """Names of every OTHER tree that embeds `tree` through a module node
        (plan 2026-09-02 P4 п.4) — computed live by reverse-scanning all trees
        for kind=="module" nodes whose ref == tree.name; nothing is cached."""
        return [t.name for t in self._trees
                if t.name != tree.name and tree.name in TreesDock._module_targets(t)]

    def _render_tree(self, tree_widget: QTreeWidget, tree: Tree,
                     saved_entry: Optional[dict] = None) -> None:
        """Read-only render: (for a generated instance) one "⇐ instance of
        {template}" pseudo-root at the very top; then a pseudo-root item for
        the anchor and the tree's top-level nodes recursively; then "embedded
        in X" pseudo items per module-embedding parent AND (for a template
        tree) one "→ instance: {name}" pseudo item per tree_instances:
        declaration that references it. Every pseudo item is non-selectable and
        carries the target TREE NAME (a plain str) in UserRole for double-click
        navigation.

        saved_entry is the P2 per-tree expansion state from gui_state.json
        ({anchor_expanded, expanded_refs}) — when present it is re-applied to
        the freshly built items; when absent (new/renamed tree, first run) the
        items keep the Qt default (collapsed), which is today's behavior."""
        # A generated instance points back at its template (P2).
        inst = self._instance_of(tree)
        if inst is not None:
            back_item = QTreeWidgetItem(tree_widget.invisibleRootItem())
            back_item.setText(
                0, _("⇐ instance of {template} (sheet={sheet})")
                .format(template=inst.template, sheet=inst.sheet))
            back_item.setFlags(back_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            back_item.setData(0, Qt.ItemDataRole.UserRole, inst.template)
        # (P2) Saved expansion for THIS tree. expanded_refs keys are node.ref —
        # a globally unique string (rule 2 in trees.py), so no Path/identity
        # conversion is needed (unlike ConfigTreeDock, design §1.4/§3.1).
        entry = saved_entry if isinstance(saved_entry, dict) else {}
        anchor_expanded = bool(entry.get("anchor_expanded", False))
        raw_refs = entry.get("expanded_refs")
        expanded_refs = ({r for r in raw_refs if isinstance(r, str)}
                         if isinstance(raw_refs, list) else set())
        # Pseudo-root showing the anchor, visually distinct (not selectable).
        anchor_item = QTreeWidgetItem(tree_widget.invisibleRootItem())
        anchor_item.setText(0, _anchor_label(tree.anchor))
        anchor_item.setFlags(anchor_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        for node in tree.nodes:
            self._render_node(anchor_item, node, expanded_refs)
        # The anchor pseudo-root is the one item that shows/hides the tree's
        # ENTIRE content — persist/restore its expansion separately from nodes.
        anchor_item.setExpanded(anchor_expanded)
        # "встроено в:" pseudo items — non-selectable, carry the PARENT tree
        # name (a plain str) in UserRole for double-click navigation.
        for parent_name in self._embedded_in(tree):
            emb_item = QTreeWidgetItem(tree_widget.invisibleRootItem())
            emb_item.setText(0, _("⇐ embedded in {parent}").format(parent=parent_name))
            emb_item.setFlags(emb_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            emb_item.setData(0, Qt.ItemDataRole.UserRole, parent_name)
        # A template tree (Q3: still an ordinary editable tree) shows its
        # instances — one "→ instance: {name}" pseudo item each (P2).
        for child_inst in self._instances_of(tree.name):
            inst_item = QTreeWidgetItem(tree_widget.invisibleRootItem())
            inst_item.setText(0, _("→ instance: {name}").format(name=child_inst.name))
            inst_item.setFlags(inst_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            inst_item.setData(0, Qt.ItemDataRole.UserRole, child_inst.name)

    def _on_node_activated(self, item: QTreeWidgetItem, column: int) -> None:
        """Double-click (plan 2026-09-02 P4 п.3/п.4 + Phase B
        design_2026_09_03... §3): a MODULE node switches the current tab to its
        referenced (child) tree; an "embedded in X"/"instance: X" pseudo item
        switches tabs; EVERY OTHER tree node opens its EDIT dialog
        (_edit_node_flow — Apply/Redraw/Close), so double-clicking a placement
        node gets you straight to editing and a live Redraw. The anchor
        pseudo-root (no TreeNode) does nothing."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, str):
            self._switch_to_tree(data)
            return
        if not isinstance(data, TreeNode):
            return  # anchor pseudo-root
        if data.kind == "module":
            self._switch_to_tree(data.ref)
            return
        tree = self._tree_of_node(data) or self._current_tree()
        if tree is not None:
            self._edit_node_flow(tree, data)

    def _switch_to_tree(self, name: str) -> None:
        """Activate the tab of the tree named `name` (no-op if not loaded)."""
        for idx, t in enumerate(self._trees):
            if t.name == name:
                self.tabs.setCurrentIndex(idx)
                return

    def _render_node(self, parent_item: QTreeWidgetItem, node: TreeNode,
                     expanded_refs: Optional[set] = None) -> None:
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
            self._render_node(item, child, expanded_refs)
        # (P2) Re-apply the saved expansion for this node — done after the
        # children exist (setExpanded is only meaningful on a populated parent).
        if expanded_refs is None:
            expanded_refs = set()
        item.setExpanded(node.ref in expanded_refs)

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

    # ── Status / dirty state helpers ─────────────────────────────────────

    def _update_status_row(self) -> None:
        """Refresh the read-only status row (2026-09-03, plan
        plan_2026_09_03_trees_menu_tools.md): the dirty indicator reflects
        _dirty. The per-dock Save button is gone since 2026-09-01 (structural
        edits auto-stage via _mark_dirty -> _stage_trees) and the whole-tree
        action buttons are gone since 2026-09-03 (they live in Tools → Trees),
        so this only drives the two read-only labels."""
        self.dirty_label.setText(_("●") if self._dirty else "")

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
        self._enforce_no_self_ref()
        backup_file(self._root_path)
        # tree_instances (P1/F3): generated instance trees are never persisted
        # as literal trees: — only hand-written (incl. template) trees go to
        # disk; the untouched tree_instances: section regenerates instances on
        # the next load.
        trees_dict = [tree_to_dict(t) for t in self._trees
                      if t.name not in self._instances]
        write_data(self._root_path, {**read_data(self._root_path), "trees": trees_dict})
        try:
            reloaded = [load_tree(t) for t in trees_dict]
            if self._cfg is not None:
                link_trees(self._cfg, reloaded)
        except ValidationError as e:
            QMessageBox.warning(self, _("Save"), str(e))
            return  # file written, .bak is fresh — report, don't roll back
        self._dirty = False
        self._update_status_row()

    @staticmethod
    def _is_self_ref_anchor(tree: Tree) -> bool:
        """True when the tree's explicit ref anchor points at its OWN single
        top-level placement node — the self-reference combination that can
        never resolve (plan 2026-08-31 anchor_self_ref_guard §3). Only an
        explicit, non-external ref anchor is a candidate: origin/auto/role/
        point anchors carry no ref, an external refdes is not an Entity record
        by construction, and _root_entity_ref already enforces the EXACTLY ONE
        rule (empty / multi-top-level / non-placement roots are untouched)."""
        anchor = tree.anchor
        if (anchor is None or anchor.is_auto or anchor.is_origin
                or anchor.role is not None or anchor.point is not None
                or not anchor.ref or anchor.is_external):
            return False
        return anchor.ref == _root_entity_ref(tree)

    def _enforce_no_self_ref(self) -> None:
        """Save-time catch-all for the self-reference anchor (plan §3). The
        dialog filter can't cover every path — an anchor can be set while the
        tree is EMPTY (a legitimate candidate then), and the (ref X) root node
        added afterwards (or edited/moved to top level, or loaded from a
        hand-edited .sexp). The combination is ALWAYS fatal at materialization
        (never "sometimes useful"), so silently switch such an anchor to Auto
        (Denis: quiet auto-replace as the fallback) + a non-intrusive
        log/status-bar notice instead of a modal."""
        for tree in self._trees:
            if not self._is_self_ref_anchor(tree):
                continue
            ref = tree.anchor.ref
            message = _("Anchor for tree {name!r}: a ref anchor pointing at "
                        "its own root Entity {ref!r} never resolves "
                        "(self-reference) — switched to Auto.").format(
                            name=tree.name, ref=ref)
            logger.info(message)
            self._show_status(message)
            tree.anchor = TreeAnchor(is_auto=True)

    def _stage_trees(self) -> None:
        """Auto-stage the trees: section into the working set after every
        structural edit (2026-09-01, plan project_save_model) — File > Save
        commits it. No per-edit backup (the flush backs up to history/) and no
        modal on an invalid intermediate state (the flush's load_config
        validation catches it before anything is written)."""
        if self._root_path is None:
            return
        data = read_data(self._root_path)
        # tree_instances (P1/F3): generated instance trees are NEVER staged/
        # saved as literal trees: — they are derived records, regenerated on
        # every load from the untouched tree_instances: declarations. Only
        # hand-written (incl. template) trees are persisted here.
        data["trees"] = [tree_to_dict(t) for t in self._trees
                         if t.name not in self._instances]
        # Phase E cascade: remove the net_traces orphaned by a deleted tree's
        # net_trace nodes (see _on_delete_tree).
        if self._orphan_net_nets:
            data["net_traces"] = [
                e for e in data.get("net_traces", [])
                if not (isinstance(e, dict) and e.get("net") in self._orphan_net_nets)]
        write_data(self._root_path, data)

    def _mark_dirty(self) -> None:
        """Central dirty setter — every structural mutator (Phase 2) calls
        this instead of setting _dirty inline, so the indicator can never be
        forgotten. Since 2026-09-01 the mutation ALSO auto-stages the trees:
        section into the working set (the per-dock Save button is gone)."""
        self._stage_trees()
        self._dirty = True
        self._update_status_row()

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

    # ── tree_instances: read-only marker (2026-09-02, P1) ────────────────

    def _rebuild_instance_index(self) -> None:
        """(Re)build the name -> TreeInstance map from cfg.tree_instances after
        any (re)load of the root config. Instance trees are ORDINARY cfg.trees
        entries (redraw/embedding see them — that is the point), but this index
        marks them as generated: read-only in the node/tree editors and EXCLUDED
        from _do_save/_stage_trees (the raw declarations regenerate them)."""
        if self._cfg is None:
            self._instances = {}
            return
        self._instances = {ti.name: ti for ti in self._cfg.tree_instances}

    def _instance_of(self, tree: Tree) -> Optional[TreeInstance]:
        """The TreeInstance declaration behind `tree`, or None when `tree` is a
        hand-written (or template) tree, i.e. editable normally."""
        return self._instances.get(tree.name)

    def _instances_of(self, template_name: str) -> list[TreeInstance]:
        """Every tree_instances: declaration that instantiates `template_name`,
        sorted by generated tree name — the template tab's "→ instance" list
        (P2 reverse-scan; a tree with ≥1 of these IS a template)."""
        return sorted((ti for ti in self._instances.values()
                       if ti.template == template_name),
                      key=lambda ti: ti.name)

    def _warn_read_only_instance(self, tree: Tree) -> bool:
        """True (and shows a message) when `tree` is a generated instance that
        may not be edited or deleted — the read-only guard for the tree-level
        actions (Tools → Trees). Returns False for editable (hand-written/template)
        trees, so callers just `return` on True."""
        inst = self._instance_of(tree)
        if inst is None:
            return False
        QMessageBox.information(
            self, _("Read-only instance"),
            _("This tree is an instance of template {template!r} (read-only) — "
              "its geometry comes from the template; edit the template tree to "
              "change it, or manage the instance in Tools → Trees → Instances.")
            .format(template=inst.template))
        return True

    def _used_refs(self) -> set[str]:
        """Every RECORD node ref already used anywhere in the current file —
        the grammar's "a ref appears in at most one node" invariant, surfaced
        as a "(used)" marker in the node dialog's ref combo. kind=="module"
        refs are EXCLUDED (plan 2026-09-02 P4 п.1b): a module ref is a CHILD
        TREE NAME, not a record, and multiple parents may embed the same child
        tree (design §2.3) — module refs must never be flagged "(used)" or
        auto-numbered; the narrow within-one-parent duplicate is guarded by
        link_trees at Save (P1 п.3), not by this set."""
        used: set[str] = set()
        for tree in self._trees:
            for node in tree.nodes:
                self._collect_refs(node, used)
        return used

    @staticmethod
    def _collect_refs(node: TreeNode, into: set[str]) -> None:
        if node.kind != "module":
            into.add(node.ref)
        for child in node.children:
            TreesDock._collect_refs(child, into)

    @staticmethod
    def _module_targets(tree: Tree) -> set[str]:
        """The refs of every kind=="module" node in `tree` at ANY depth — the
        names of the trees this tree embeds (module refs are tree names)."""
        out: set[str] = set()

        def walk(nodes: list) -> None:
            for n in nodes:
                if n.kind == "module":
                    out.add(n.ref)
                walk(n.children)

        walk(tree.nodes)
        return out

    def _module_tree_candidates(self, current: Tree) -> list[str]:
        """Tree names a NEW module node under `current` may reference (plan
        2026-09-02 P4 п.1): every OTHER tree, minus the ones `current` already
        embeds (a within-one-parent duplicate is a config fatal, P1 п.3), minus
        any tree that would close a module cycle — i.e. a tree that already
        reaches `current` transitively through modules."""
        by_name = {t.name: t for t in self._trees}
        targets = {t.name: TreesDock._module_targets(t) for t in self._trees}
        already_embedded = targets.get(current.name, set())

        def reaches(name: str, goal: str, _seen: set[str]) -> bool:
            if name == goal:
                return True
            if name in _seen:
                return False
            _seen.add(name)
            return any(reaches(n, goal, _seen) for n in targets.get(name, ()))

        return [name for name in by_name
                if name != current.name
                and name not in already_embedded
                and not reaches(name, current.name, set())]

    def _all_ref_candidates(self) -> list[tuple[str, str]]:
        """Kind-aware ref candidates for the node dialog: (kind, name) pairs
        for the 4 placeable kinds' record names from build_records(cfg), in
        build_records' stable section order, NOT deduped by name — two sections
        may share a name (record_key distinguishes them), and the dialog's auto
        mode shows such collisions prefixed (plan_2026_08_29_trees_node_kind_
        filtered_combo.md). Empty when no root config is loaded (dialog still
        works via free text / external)."""
        if self._cfg is None:
            return []
        return [(r.kind, r.name) for r in build_records(self._cfg)
                if r.kind in _PLACEABLE_KINDS]

    def _all_ref_names(self) -> list[str]:
        """Plain unique ref names for the ANCHOR dialog — an anchor auto-
        resolves by name (a section collision is fatal there, see link_trees),
        so a colliding name must appear once, not once per section."""
        seen: set[str] = set()
        names: list[str] = []
        for _kind, name in self._all_ref_candidates():
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names

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

        inst = self._instance_of(tree)
        if inst is not None:
            # A generated instance (tree_instances:, P1/F3) is read-only: no
            # structural actions at all — its geometry is owned by the template
            # + the declaration. A single disabled note explains why; double-
            # clicking the tab just shows the read-only preview.
            menu = QMenu(tree_widget)
            note = menu.addAction(
                _("Instance of {template} — read-only: edit the template tree "
                  "to change the geometry").format(template=inst.template))
            note.setEnabled(False)
            menu.exec(tree_widget.viewport().mapToGlobal(pos))
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
            # "Instantiate from Cell..." (2026-09-03, plan instantiate_from_
            # entity) — add a NEW group reusing an EXISTING Cell into THIS
            # tree. Routed through DockHub so it has the live board selection.
            menu.addAction(_("Instantiate from Cell…")).triggered.connect(
                lambda: self._main_window._dock_hub.instantiate_from_cell())
        menu.exec(tree_widget.viewport().mapToGlobal(pos))

    # ── Node dialog helpers ──────────────────────────────────────────────

    def _live_adapter(self):
        """The live KiCad board adapter (or None when not connected) — the
        same main_window.connection.board.adapter access pattern every other
        dock uses (PlacerDock, RoleClusterTreeDock, ...)."""
        board = getattr(self._main_window.connection, "board", None)
        return getattr(board, "adapter", None)

    def _live_roles(self) -> list[str]:
        """Distinct Role values from the current live-board snapshot, sorted —
        the same populate-don't-restrict source every dock's
        refresh_known_roles uses. Empty when not connected: the dialog's role
        combo is still a searchable picker where free text is accepted."""
        snapshot = getattr(getattr(self._main_window, "connection", None), "snapshot", None)
        return sorted({s.role for s in (snapshot or []) if s.role})

    def _live_clusters(self) -> list[str]:
        """Distinct Cluster values from the current live-board snapshot, sorted
        (same source/empty-tolerant rules as _live_roles)."""
        snapshot = getattr(getattr(self._main_window, "connection", None), "snapshot", None)
        return sorted({s.cluster for s in (snapshot or []) if s.cluster})

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
            # Position-tab (own_anchor) candidate lists — the same live sources
            # the anchor dialog uses (plan tree_node_own_anchor §3.1).
            role_candidates=self._live_roles(),
            sheet_candidates=self._live_sheets(),
            cluster_candidates=self._live_clusters(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        node = dialog.build_node()
        if node is None:
            # build_node() already reported the problem (empty/used ref, bad
            # offset/rotation) via QMessageBox and returned None — treat it as
            # a cancel, never dereference it below. The node dialog's OK button
            # accept()s unconditionally, so validation runs HERE, after exec();
            # a None node used to crash on node.ref (found live 2026-09-02,
            # AttributeError in _add_node_flow -> whole GUI died).
            return None
        # Phase 5.5 auto-numbering: a NEW node whose free-typed ref (not a
        # placeable record — those are shown "(used)" and stay strict) collides
        # with an existing node is auto-numbered (ref_1, ref_2, ...) so the
        # next Save doesn't fatal with link_trees' "already has a node
        # elsewhere". Add-mode only: editing a node must never rename it.
        # kind=="module" ALWAYS bypasses this (plan 2026-09-02 P4 п.1a): a
        # module ref is a child TREE NAME chosen explicitly from the dialog's
        # tree-name list, never a free-typed record needing dedup. The bypass
        # is CONSTRUCTIVE (by kind) — even if the tree name coincides with a
        # DIFFERENT ordinary record's node ref elsewhere (e.g. a tree named
        # "GND"), a module node must keep its exact tree name, or the next
        # Save would fatal on "unknown tree GND_1" (P1). _used_refs() also
        # excludes module refs (P4 п.1b), but that only removes module refs
        # from the "used" set; it does NOT protect a module ref that happens
        # to equal an unrelated record's ref, which is why the kind guard here
        # is the real fix.
        if existing is None and node.kind != "module":
            placeable = {name for _kind, name in self._all_ref_candidates()}
            if node.ref not in placeable:
                node.ref = self._unique_ref(node.ref, self._used_refs())
        return node

    @staticmethod
    def _unique_ref(base: str, used: set) -> str:
        """The first free auto-numbered variant of a colliding ref: base if
        free, else base_1, base_2, ... (phase 5.5 auto-numbering)."""
        if base not in used:
            return base
        i = 1
        while f"{base}_{i}" in used:
            i += 1
        return f"{base}_{i}"

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
                tree, self._find_parent(tree, node), node.ref, node.kind,
                # An own-anchor node's xy/polar are defined relative to its OWN
                # anchor, not the parent — reread against the same base the
                # node is authored against (plan tree_node_own_anchor §2.3).
                base_anchor=node.own_anchor)
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
        """The node editor (Phase B — design_2026_09_03... §3): the dialog opens
        in EDIT mode (existing=node) with Apply/Redraw/Close buttons, so the
        user can apply edits explicitly and keep the dialog open. When the
        dialog's own Apply already mutated `node` in place, Close returns None
        here and nothing more is copied — the tree is just refreshed. The
        legacy commit path (built != None — the Add dialog's caller pattern) is
        kept for tests/compat and funnels through the shared _copy_node_onto."""
        built = self._prompt_node(_("Edit node"), tree,
                                  parent_node=self._find_parent(tree, node),
                                  existing=node)
        if built is None:
            # Dialog closed via Close; any applied edits were already written
            # onto `node` by the dialog's Apply button (marking dirty). Refresh
            # the tree view so those edits are visible.
            self._rebuild_tabs()
            return
        _copy_node_onto(node, built)
        self._mark_dirty()
        self._rebuild_tabs()

    @staticmethod
    def _contains_node(candidate: TreeNode, target: TreeNode) -> bool:
        """Identity-based subtree containment (TreeNode is unhashable)."""
        if candidate is target:
            return True
        return any(TreesDock._contains_node(c, target) for c in candidate.children)

    def _tree_of_node(self, node: TreeNode) -> Optional[Tree]:
        """The tree whose node-subtree holds `node` (identity), or None."""
        for t in self._trees:
            if any(TreesDock._contains_node(top, node) for top in t.nodes):
                return t
        return None

    def _redraw_edited_node(self, node: TreeNode) -> None:
        """The node editor dialog's **Redraw** button (Phase B): place the REAL
        record on the live board at its (edited) CONFIG position — ONE
        ApplyPipeline --only run for node.ref over the in-memory cfg/trees (the
        dialog just applied to the same Tree/TreeNode objects), in a background
        worker (start_long_op, never blocks the UI). NOT the rigid curated
        redraw: after an offset edit the component must move to the new offset.
        Inherits the documented ApplyPipeline boundary (design §0) — a node that
        can't be resolved by the pipeline fails exactly as it would on a full
        Apply, this phase does not soften it."""
        if node is None or not node.ref or self._cfg is None or self._ctx is None:
            return
        payload = {
            "config_path": str(self._root_path) if self._root_path else "",
            "cfg": self._cfg,
            "ctx": self._ctx,
            "ref": node.ref,
        }
        self._active_op = start_long_op(
            self._main_window.connection, (),
            run_single_node_redraw_worker, self._finish_redraw,
            self._on_redraw_failed, payload)

    def _set_anchor_flow(self, tree: Tree) -> None:
        anchor = _AnchorDialog.prompt(
            self, self._all_ref_candidates(),
            cfg=self._cfg,
            sheet_names=self._ctx.sheet_names if self._ctx is not None else {},
            role_candidates=self._live_roles(),
            cluster_candidates=self._live_clusters(),
            existing=tree.anchor,
            tree=tree)
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

    def _on_create_tree(self) -> None:
        """Tools → Trees → Create tree… (2026-09-03, plan
        plan_2026_09_03_trees_menu_tools.md): create a NEW empty (manual)
        tree in the dock's buffer — name + the six-mode anchor dialog, then an
        empty nodes=[] tree is appended, marked dirty (auto-staged), and its
        fresh tab is focused. Nothing is written until File > Save (the same
        staged model as Rename/Delete). Was the dock's "Add tree…" handler."""
        name, ok = QInputDialog.getText(self, _("Create tree"), _("Tree name:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(t.name == name for t in self._trees):
            QMessageBox.warning(self, _("Create tree"),
                                _("A tree named {name!r} already exists.").format(name=name))
            return
        anchor = _AnchorDialog.prompt(
            self, self._all_ref_candidates(),
            cfg=self._cfg,
            sheet_names=self._ctx.sheet_names if self._ctx is not None else {},
            role_candidates=self._live_roles(),
            cluster_candidates=self._live_clusters())
        if anchor is None:
            return
        self._trees.append(Tree(name=name, anchor=anchor, nodes=[]))
        self._mark_dirty()
        self._rebuild_tabs()
        self.tabs.setCurrentIndex(len(self._trees) - 1)

    # ── Instantiate from Cell… (2026-09-03, plan instantiate_from_entity) ──

    def _live_sheets(self) -> list[str]:
        """Distinct sheet segments from the current live snapshot, sorted —
        the editable Sheet combo candidates for the instantiate dialog."""
        snapshot = getattr(getattr(self._main_window, "connection", None),
                           "snapshot", None) or []
        return sorted({seg for s in snapshot for seg in (s.sheet or ()) if seg})

    @staticmethod
    def _tree_anchor_ready(tree: Tree) -> bool:
        """A tree has a REAL anchor when one is written (role/ref/point/origin)
        — an is_auto anchor (no (anchor ...) at all) has no resolvable base, so
        "position relative to the tree anchor" is meaningless for it. The
        Instantiate-from-Cell flow must refuse an auto/absent anchor in ANY
        positioning mode (plan instantiate_from_entity §1.5)."""
        return tree.anchor is not None and not tree.anchor.is_auto

    def _anchor_base_mm(self, tree: Tree) -> Optional[tuple[float, float]]:
        """Live base (mm) of the tree's own anchor, or None when it cannot be
        resolved (not connected / anchor unresolvable) — needed only for the
        "from selection" placement mode (node xy = group center - anchor base)."""
        adapter = self._live_adapter()
        if adapter is None or self._cfg is None:
            return None
        try:
            sheet_names = self._ctx.sheet_names if self._ctx else {}
            pos, _rot = _anchor_base_live_position(
                adapter, self._cfg, tree, sheet_names)
        except Exception:  # noqa: BLE001 — live read, best-effort
            return None
        return (pos.x / MM, pos.y / MM)

    def _instantiate_from_cell(self, selected) -> None:
        """Add ONE new group into the CURRENT tree by reusing an EXISTING Cell
        (2026-09-03, plan instantiate_from_entity). Collects the decision in
        the InstantiateCellDialog, stages a NEW Entity on that Cell (no refs —
        roles resolve at Apply by cluster/sheet) through config_writer, and
        appends a top-level placement node (xy relative to the tree anchor).
        Everything is staged via WORKING_SET — nothing reaches disk until the
        global Save (see the plan's §3)."""
        tree = self._current_tree()
        if tree is None:
            QMessageBox.warning(self, _("Instantiate from Cell"),
                                _("Open a tree first — a new group needs a "
                                  "tree to be added to."))
            return
        if self._instance_of(tree) is not None:
            QMessageBox.warning(
                self, _("Instantiate from Cell"),
                _("A generated instance is read-only — add the new group to "
                  "its template tree instead."))
            return
        if not self._tree_anchor_ready(tree):
            # Plan §1.5: a new group is positioned RELATIVE to the tree anchor —
            # an auto/absent anchor makes manual AND from-selection placement
            # meaningless alike, so refuse before the dialog (soft, with the
            # "Set anchor…" hint), in every positioning mode.
            QMessageBox.warning(
                self, _("Instantiate from Cell"),
                _("Set the tree anchor first — a new group is positioned "
                  "relative to the tree anchor (anchor → Set anchor…)."))
            return
        if self._cfg is None:
            return
        from .instantiate_cell_dialog import InstantiateCellDialog
        from .tree_from_selection import (build_instantiated_entity,
                                          selected_center_mm)
        snapshot = getattr(getattr(self._main_window, "connection", None),
                           "snapshot", None) or []
        dialog = InstantiateCellDialog(
            self, self._cfg,
            cells=sorted(self._cfg.cells),
            sheets=self._live_sheets(),
            clusters=self._live_clusters(),
            selected=list(selected or []),
            snapshot=list(snapshot))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        cell_name = dialog.result_cell()
        entity_name = dialog.entity_name()
        cluster = dialog.cluster()
        sheet = dialog.sheet()
        if dialog.from_selection():
            center = selected_center_mm(selected)
            base = self._anchor_base_mm(tree)
            if center is None or base is None:
                QMessageBox.warning(
                    self, _("Instantiate from Cell"),
                    _("Cannot derive the node offset from the selection (no "
                      "selected footprint / cannot resolve the tree anchor "
                      "live). Enter the xy manually instead."))
                return
            xy = (center[0] - base[0], center[1] - base[1])
        else:
            xy = dialog.manual_xy()
            if xy is None:
                return
        if self._root_path is None:
            return
        upsert_entity(self._root_path,
                      build_instantiated_entity(cell_name, entity_name,
                                                cluster, sheet))
        tree.nodes.append(TreeNode(
            ref=entity_name, kind="placement", xy=xy, polar=None,
            rotation=0.0, name=None, group=None, children=[]))
        self._mark_dirty()
        self._rebuild_tabs()
        self._show_status(_("Added {entity!r} (cell {cell!r}) to tree {tree!r} "
                            "— Save to persist.")
                          .format(entity=entity_name, cell=cell_name,
                                  tree=tree.name))

    def _on_rename_tree(self) -> None:
        tree = self._current_tree()
        if tree is None:
            return
        if self._warn_read_only_instance(tree):
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

    def _on_delete_tree(self) -> None:
        """Remove the CURRENT tree from self._trees entirely — the whole-tree
        counterpart of the per-node "Delete node" context-menu action. Like
        Add/Rename, nothing is written until Save: the deletion is just part of
        the unsaved state, and _do_save persists it (plus the .bak backup).
        Confirmed via QMessageBox with No as the safe default button."""
        tree = self._current_tree()
        if tree is None:
            return
        if self._warn_read_only_instance(tree):
            return
        ret = QMessageBox.question(
            self, _("Delete tree"),
            _("Delete tree {name!r}? This cannot be undone (until you Save).")
            .format(name=tree.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        # Phase E cascade (2026-09-01): net_traces captured by this tree's
        # net_trace nodes and NOT referenced by any other remaining tree become
        # orphaned — offer to remove them too (No is the safe default).
        deleted_nets = _tree_net_trace_nets(tree)
        remaining_nets: set[str] = set()
        for other in self._trees:
            if other is tree:
                continue
            remaining_nets |= _tree_net_trace_nets(other)
        orphaned = deleted_nets - remaining_nets
        if orphaned:
            ret = QMessageBox.question(
                self, _("Delete tree"),
                _("Also delete the net traces now only referenced by this tree: "
                  "{nets}?").format(nets=", ".join(sorted(orphaned))),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.Yes:
                self._orphan_net_nets |= orphaned
        self._trees.remove(tree)
        self._mark_dirty()
        self._rebuild_tabs()

    # ── Checkbox subtree selection + Redraw (Phase 4) ────────────────────

    def _run_curated_redraw(self, selected_refs: set) -> None:
        """Shared worker invocation for "Redraw selected" and "Redraw whole
        tree" (plan_2026_08_29_fork1_rigid_redraw_override.md §5) — one
        implementation, only the selection source differs. start_long_op keeps
        it off the UI thread, same worker pattern as AnchorTreeDock's cascade."""
        tree_name = self._current_tree_name()
        if tree_name is None:
            return
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

    def _on_redraw_selected(self) -> None:
        """Collect the CHECKED nodes' refs and run the curated redraw for the
        current tree in the background."""
        selected_refs = {ref for ref, item in self._node_items.items()
                         if item.checkState(0) == Qt.CheckState.Checked}
        self._run_curated_redraw(selected_refs)

    def _on_redraw_whole_tree(self) -> None:
        """Redraw EVERY node of the current tree in one click — the SAME
        run_curated_tree_redraw_worker as "Redraw selected", but the refs are
        collected DIRECTLY from the Tree structure (collect_tree_refs), not
        from checkbox state, so no manual check-marking is needed even on a
        multi-branch/large tree (plan_2026_08_29_fork1_rigid_redraw_override.md
        §5)."""
        tree = self._current_tree()
        if tree is None:
            return
        self._run_curated_redraw(set(collect_tree_refs(tree)))

    def _run_forest_redraw(self) -> None:
        """Forest-wide curated redraw — the module-aware FULL redraw (plan
        2026-09-02 tree_module_embedding P3 п.2/п.3, design P3 D5): collects
        EVERY node ref of EVERY tree (records AND module markers — checking the
        markers activates their content) and runs run_curated_forest_redraw_
        worker in the background, which stage-2-places active module content
        from the flow roots' live anchors. Exposed ONLY through the Tools menu
        (DockHub.run_forest_full_redraw) — NO new dock button."""
        if not self._trees or not self._root_path or self._cfg is None:
            self._show_status(_("Nothing to redraw."))
            return
        refs: set[str] = set()
        for tree in self._trees:
            refs.update(collect_tree_refs(tree))
        if not refs:
            self._show_status(_("Nothing to redraw."))
            return
        payload = {
            "config_path": str(self._root_path),
            "cfg": self._cfg,
            "ctx": self._ctx,
            "trees": self._trees,
            "selected_refs": refs,
        }
        self._active_op = start_long_op(
            self._main_window.connection, (),
            run_curated_forest_redraw_worker, self._finish_redraw,
            self._on_redraw_failed, payload)

    def _refresh_anchor_live_position(self) -> None:
        """§5.1 (plan_2026_08_29_fork1_rigid_redraw_override.md) — a READ-ONLY
        indicator of the current tree anchor's live absolute position/rotation,
        via _anchor_base_live_position — which supports EVERY anchor mode
        (origin/auto/role/point/ref; 2026-09-02: role/point/auto used to show
        "unavailable" because a ref-less anchor read was never implemented
        here). Not cached: reads the board on demand (button/on-open). An
        origin anchor is trivially (0,0)/0°; a live KiCad IPC failure just
        shows "unavailable" — the indicator never crashes the dock."""
        tree = self._current_tree()
        if tree is None:
            self.anchor_pos_label.setText("")
            return
        if tree.anchor.is_origin:
            self.anchor_pos_label.setText(_("anchor (origin): (0, 0) mm @ 0°"))
            return
        try:
            adapter = KiCadBoardAdapter(timeout_ms=20000)
            adapter.refresh_board()
            sheet_names = self._ctx.sheet_names if self._ctx else {}
            pos, rot = _anchor_base_live_position(
                adapter, self._cfg, tree, sheet_names)
        except Exception as exc:  # noqa: BLE001 — read-only indicator, never crash
            logger.warning(_("anchor live position unavailable: {error}")
                           .format(error=exc))
            self.anchor_pos_label.setText(_("anchor: live position unavailable"))
            return
        rot_s = f"{rot:.1f}" if rot is not None else "—"
        if tree.anchor.ref:
            self.anchor_pos_label.setText(
                _("anchor {ref!r}: ({x:.3f}, {y:.3f}) mm @ {rot}°")
                .format(ref=tree.anchor.ref, x=pos.x / MM, y=pos.y / MM,
                        rot=rot_s))
        else:
            # auto/role/point anchor — no ref to name, a mode-generic readout.
            self.anchor_pos_label.setText(
                _("anchor: ({x:.3f}, {y:.3f}) mm @ {rot}°")
                .format(x=pos.x / MM, y=pos.y / MM, rot=rot_s))

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

    def __init__(self, parent, ref_candidates: list[tuple[str, str]], used_refs: set[str],
                 title: str, cfg=None, adapter=None, sheet_names=None,
                 tree=None, parent_node=None, existing=None,
                 module_candidates=None, all_trees=None,
                 role_candidates=None, sheet_candidates=None,
                 cluster_candidates=None):
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
        # kind=="module": tree-name candidates (other trees minus per-parent
        # duplicates / cycle risks) + the full tree list for the pivot sugar
        # (plan 2026-09-02 P4 п.1/п.2). `parent` is the TreesDock; the dock
        # computes both before opening the dialog.
        self._module_candidates = list(module_candidates or [])
        self._all_trees = list(all_trees or [])

        self._role_candidates = list(role_candidates or [])
        self._sheet_candidates = list(sheet_candidates or [])
        self._cluster_candidates = list(cluster_candidates or [])

        # Two-tab node editor (plan tree_node_own_anchor §3): the old single
        # form becomes the "General" tab (everything below is moved verbatim —
        # same `form` name so no other line changes); the "Position" tab is
        # assembled after the general rows, and the buttons live at the bottom
        # of a top-level layout, not inside the form.
        self.tabs = QTabWidget(self)
        general_widget = QWidget()
        form = QFormLayout(general_widget)

        # kind — "auto" (None) + every grammar kind (KINDS incl. "module").
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
        # Picking a PREFIXED collision entry in auto mode auto-specializes the
        # Kind (see _on_ref_selected) — a node with kind=None and a colliding
        # ref would be fatal at link_trees ("0 or 2+ matches").
        self.ref_combo.currentIndexChanged.connect(self._on_ref_selected)
        form.addRow(_("Ref:"), self.ref_combo)

        # offset block — xy/polar only, reused from the shared widget (design §3).
        self.offset_widget = AnchorOriginWidget(modes=["xy"], polar=True)
        form.addRow(_("Offset:"), self.offset_widget)

        # pivot block (kind=="module" only; hidden otherwise, plan P4 п.1) —
        # which point INSIDE the referenced tree's own local offset frame must
        # land on this marker. The offset above stays the MARKER's own offset
        # in the parent; pivot is a second, independent field.
        self.pivot_widget = AnchorOriginWidget(modes=["xy"], polar=True)
        form.addRow(_("Pivot (child frame):"), self.pivot_widget)
        self.pivot_from_node_button = QPushButton(_("From child node..."))
        self.pivot_from_node_button.clicked.connect(self._on_use_child_offset)
        form.addRow(self.pivot_from_node_button)
        self.pivot_widget.setVisible(False)
        self.pivot_from_node_button.setVisible(False)

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

        # ── Position tab: the offset base — parent (default, = today's node
        # semantics) or a chosen live component (own_anchor, plan
        # tree_node_own_anchor §3). Role/Sheet/Cluster/Pad mirror the
        # _AnchorDialog role section (configure_searchable + set_combo_items),
        # so no new widget pattern is invented.
        position_widget = QWidget()
        position_form = QFormLayout(position_widget)
        self.own_anchor_group = QButtonGroup(self)
        self.relative_to_parent_radio = QRadioButton(_("Relative to parent"))
        self.relative_to_component_radio = QRadioButton(_("Relative to component"))
        self.own_anchor_group.addButton(self.relative_to_parent_radio)
        self.own_anchor_group.addButton(self.relative_to_component_radio)
        self.relative_to_parent_radio.setChecked(True)
        position_form.addRow(self.relative_to_parent_radio)
        position_form.addRow(self.relative_to_component_radio)

        self.own_anchor_role_combo = QComboBox()
        configure_searchable(self.own_anchor_role_combo)
        set_combo_items(self.own_anchor_role_combo, self._role_candidates)
        position_form.addRow(_("Role:"), self.own_anchor_role_combo)
        self.own_anchor_sheet_combo = QComboBox()
        configure_searchable(self.own_anchor_sheet_combo)
        set_combo_items(self.own_anchor_sheet_combo, self._sheet_candidates)
        self.own_anchor_sheet_combo.lineEdit().setPlaceholderText(
            _("sheet name (narrows an ambiguous Role, optional)"))
        position_form.addRow(_("Sheet:"), self.own_anchor_sheet_combo)
        self.own_anchor_cluster_combo = QComboBox()
        configure_searchable(self.own_anchor_cluster_combo)
        set_combo_items(self.own_anchor_cluster_combo, self._cluster_candidates)
        position_form.addRow(_("Cluster:"), self.own_anchor_cluster_combo)
        self.own_anchor_pad_edit = QLineEdit()
        self.own_anchor_pad_edit.setPlaceholderText(_("pad (optional)"))
        position_form.addRow(_("Pad:"), self.own_anchor_pad_edit)

        self.relative_to_component_radio.toggled.connect(self._update_own_anchor_enabled)
        self._update_own_anchor_enabled()

        self.tabs.addTab(general_widget, _("General"))
        self.tabs.addTab(position_widget, _("Position"))

        # Phase B (design_2026_09_03... §3): the EDIT dialog (existing) closes
        # ONLY via Close — Apply writes the form onto the node (staying open,
        # edits are explicit, nothing auto-commits), Redraw applies AND re-places
        # the real component live; unsaved changes since the last Apply are lost
        # on Close. The ADD dialog (no existing node to apply to) keeps the modal
        # OK/Cancel commit through the caller (_prompt_node).
        self._dock = parent if getattr(parent, "_mark_dirty", None) else None
        self.apply_status_label = QLabel("")
        self.apply_status_label.setWordWrap(True)
        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        buttons = QHBoxLayout()
        if existing is not None:
            self.apply_button = QPushButton(_("Apply"))
            self.apply_button.clicked.connect(self._on_apply)
            self.redraw_button = QPushButton(_("Redraw"))
            self.redraw_button.clicked.connect(self._on_redraw)
            self.close_button = QPushButton(_("Close"))
            self.close_button.clicked.connect(self.reject)
            buttons.addWidget(self.apply_button)
            buttons.addWidget(self.redraw_button)
            buttons.addWidget(self.close_button)
            root.addWidget(self.apply_status_label)
        else:
            self.ok_button = QPushButton(_("OK"))
            self.ok_button.clicked.connect(self.accept)
            cancel_button = QPushButton(_("Cancel"))
            cancel_button.clicked.connect(self.reject)
            buttons.addWidget(self.ok_button)
            buttons.addWidget(cancel_button)
        root.addLayout(buttons)

        self.ref_combo.currentTextChanged.connect(self._update_read_button_state)
        self.kind_combo.currentIndexChanged.connect(self._update_read_button_state)
        if existing is not None:
            self.kind_combo.currentIndexChanged.connect(self._update_redraw_state)

        if existing is not None:
            self._prefill(existing)   # calls _on_kind_changed() itself (kind
                                      # must be set BEFORE the ref combo is
                                      # repopulated; external clears its items)
        else:
            self._on_kind_changed()
        self._update_read_button_state()
        self._update_redraw_state()

    def _update_own_anchor_enabled(self) -> None:
        """The Role/Sheet/Cluster/Pad fields are enabled ONLY in "Relative to
        component" mode (own_anchor is meaningful only then); "Relative to
        parent" keeps them disabled so a stray typed Role cannot silently change
        the node's base."""
        component_mode = self.relative_to_component_radio.isChecked()
        for w in (self.own_anchor_role_combo, self.own_anchor_sheet_combo,
                  self.own_anchor_cluster_combo, self.own_anchor_pad_edit):
            w.setEnabled(component_mode)

    def own_anchor(self) -> TreeAnchor | None:
        """The Position tab's value: None when "Relative to parent" (the
        default), else the filled role-only TreeAnchor. An empty Role under
        "Relative to component" is returned as None here — the caller's
        validation (build_node) turns it into a warning, never a silent parent."""
        if self.relative_to_parent_radio.isChecked():
            return None
        role = self.own_anchor_role_combo.currentText().strip()
        if not role:
            return None
        return TreeAnchor(
            role=role,
            is_origin=False,
            anchor_sheet=self.own_anchor_sheet_combo.currentText().strip() or None,
            anchor_cluster=self.own_anchor_cluster_combo.currentText().strip() or None,
            anchor_pad=self.own_anchor_pad_edit.text().strip() or None,
        )

    def _update_redraw_state(self) -> None:
        """Phase B: Redraw only makes sense in EDIT mode with a dock (live board
        + config) and a record kind ApplyPipeline can re-place by name — a
        module marker, an external live refdes or an auto node cannot."""
        redraw = getattr(self, "redraw_button", None)
        if redraw is None:
            return
        kind = self.kind_combo.currentData()
        redraw.setEnabled(self._dock is not None
                          and kind in ("placement", "clone", "chain", "rule",
                                       "coordinate", "net_trace"))

    def _on_apply(self) -> bool:
        """Phase B Apply: validate the form and write the fields onto the EDITED
        node in place — explicit, does NOT close the dialog (edits are explicit
        actions; the config still reaches disk only through the caller's Save).
        Returns True when applied."""
        if self._existing is None:
            return False
        built = self.build_node()
        if built is None:
            return False  # build_node already warned
        _copy_node_onto(self._existing, built)
        if self._dock is not None:
            self._dock._mark_dirty()
        self.apply_status_label.setText(_("Applied — keep editing or Redraw."))
        return True

    def _on_redraw(self) -> None:
        """Phase B Redraw: Apply first, then place the node's REAL record on the
        live board at its (edited) config position — a background worker via the
        dock, never blocking the dialog or the UI."""
        if not self._on_apply():
            return
        if self._dock is not None:
            self._dock._redraw_edited_node(self._existing)
            self.apply_status_label.setText(_("Applied — Redraw started."))
        else:
            self.apply_status_label.setText(
                _("Applied — no live board Redraw available."))

    def _prefill(self, existing: TreeNode) -> None:
        """Edit mode: populate every field from an existing node. Called
        BEFORE _on_kind_changed() so the ref combo is repopulated for the
        pre-filled kind (external clears its candidates)."""
        kind_idx = self.kind_combo.findData(existing.kind)
        if kind_idx >= 0:
            self.kind_combo.setCurrentIndex(kind_idx)
        self._on_kind_changed()
        self.ref_combo.setCurrentText(existing.ref)
        # Position tab: restore the node's own_anchor (or "Relative to parent").
        if existing.own_anchor is not None:
            self.relative_to_component_radio.setChecked(True)
            self.own_anchor_role_combo.setCurrentText(existing.own_anchor.role)
            self.own_anchor_sheet_combo.setCurrentText(
                existing.own_anchor.anchor_sheet or "")
            self.own_anchor_cluster_combo.setCurrentText(
                existing.own_anchor.anchor_cluster or "")
            self.own_anchor_pad_edit.setText(existing.own_anchor.anchor_pad or "")
        else:
            self.relative_to_parent_radio.setChecked(True)
        if existing.xy is not None:
            self.offset_widget.load(x=existing.xy[0], y=existing.xy[1])
        elif existing.polar is not None:
            self.offset_widget.load(polar=True, radius=existing.polar[0],
                                    angle=existing.polar[1])
        else:
            self.offset_widget.load()
        if existing.kind == "module":
            # pivot round-trips through Edit (plan 2026-09-02 P4 п.1).
            if existing.pivot_xy is not None:
                self.pivot_widget.load(x=existing.pivot_xy[0], y=existing.pivot_xy[1])
            elif existing.pivot_polar is not None:
                self.pivot_widget.load(polar=True, radius=existing.pivot_polar[0],
                                       angle=existing.pivot_polar[1])
            else:
                self.pivot_widget.load()
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
        # When "Relative to component" is selected the offset is defined from
        # that component's live frame — the read must diff against it, not the
        # parent (plan tree_node_own_anchor §2.3/§3).
        base_anchor = self.own_anchor() if self.relative_to_component_radio.isChecked() else None
        if self.relative_to_component_radio.isChecked() and base_anchor is None:
            QMessageBox.warning(
                self, _("Read current position"),
                _("Pick a component (Role) or switch back to Relative to parent."))
            return
        try:
            offset_mm, rotation = _resolve_live_offset(
                self._cfg, self._adapter, self._sheet_names,
                self._tree, self._parent_node, ref, kind,
                base_anchor=base_anchor)
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

    def _set_ref_items(self, items: list[tuple[str, Optional[str], str]]) -> None:
        """Repopulate ref_combo with (display_text, kind, name) triples,
        preserving the current text and blocking signals (the same
        preserve-current-text rule as set_combo_items) plus per-item itemData
        for the auto-mode Kind specialization. A None `kind` means "plain auto
        entry" — picking it must NOT touch the Kind combo; a concrete kind
        means a PREFIXED collision entry — picking it auto-specializes."""
        current_text = self.ref_combo.currentText()
        self.ref_combo.blockSignals(True)
        self.ref_combo.clear()
        for text, kind, name in items:
            self.ref_combo.addItem(text, (kind, name))
        self.ref_combo.setCurrentText(current_text)
        self.ref_combo.blockSignals(False)

    def _on_kind_changed(self) -> None:
        """kind == "external" -> ref is a free-text external refdes (combo
        cleared, hint shown); kind is None (auto) -> ALL placeable names — one
        unique to a section shown plain, one shared by 2+ sections shown once
        per section as {kind}:{name}; a concrete kind -> only that section's
        names, plain (plan_2026_08_29_trees_node_kind_filtered_combo.md)."""
        kind = self.kind_combo.currentData()
        is_module = kind == "module"
        # Module-only rows: pivot + its convenience sugar. Everything else:
        # the "Read current position" row (a live read of a module ref — a
        # tree, not a record — is meaningless).
        self.pivot_widget.setVisible(is_module)
        self.pivot_from_node_button.setVisible(is_module)
        self.read_position_button.setVisible(not is_module)
        self.read_status_label.setVisible(not is_module)
        if kind == "module":
            # Ref = a child TREE NAME (not a record) — the dialog's separate
            # tree-name candidate list, minus self/dups/cycle risks.
            self.ref_combo.clear()
            self._set_ref_items([(name, None, name)
                                 for name in self._module_candidates])
            self.ref_combo.setPlaceholderText(_("child tree name"))
            return
        if kind == "external":
            self.ref_combo.clear()
            self.ref_combo.setPlaceholderText(_("external refdes (live board)"))
            return
        if kind is None:
            section_count: dict[str, int] = {}
            for _k, name in self._ref_candidates:
                section_count[name] = section_count.get(name, 0) + 1
            items = []
            for k, name in self._ref_candidates:
                if section_count[name] > 1:
                    items.append((f"{k}:{name}", k, name))
                else:
                    items.append((name, None, name))
        else:
            items = [(name, kind, name)
                     for k, name in self._ref_candidates if k == kind]
        self._set_ref_items(items)
        self.ref_combo.setPlaceholderText(_("record name (from config)"))

    def _on_ref_selected(self, index: int) -> None:
        """Auto-specialize the Kind when the user picks a PREFIXED collision
        entry in auto mode (itemData = (kind, name) with a concrete kind):
        switch the Kind combo to that section (its change handler repopulates
        the ref list for it) and put the CLEAN name in the ref combo — a node
        with kind=None and a colliding ref would be fatal at link_trees ("0 or
        2+ matches"), so picking one must carry the explicit kind along. Plain
        entries carry (None, name) and leave the Kind untouched."""
        data = self.ref_combo.itemData(index)
        if data is None:
            return
        kind, name = data
        if kind is None:
            return
        kind_idx = self.kind_combo.findData(kind)
        if kind_idx < 0:
            return
        self.kind_combo.setCurrentIndex(kind_idx)
        self.ref_combo.setCurrentText(name)

    def _on_use_child_offset(self) -> None:
        """P4 п.2 convenience (pure UI sugar over the pivot field, no extra
        logic): pick a node of the currently referenced (child) tree and put
        its static offset from the child tree's origin into the pivot fields —
        computed by ordinary composition inside the child tree at zero anchor
        rotation (a plain read, nothing is written anywhere)."""
        from kicadstamp.tree_position import node_position
        from kicadstamp.domain.geometry import Vector2

        ref = self.ref_combo.currentText().strip()
        if not ref or not self._all_trees:
            return
        child = next((t for t in self._all_trees if t.name == ref), None)
        if child is None:
            QMessageBox.warning(
                self, _("Add node"),
                _("No tree named {name!r} is loaded.").format(name=ref))
            return
        refs: list[str] = []

        def collect(nodes: list) -> None:
            for n in nodes:
                refs.append(n.ref)
                collect(n.children)

        collect(child.nodes)
        if not refs:
            QMessageBox.warning(self, _("Add node"),
                                _("The referenced tree has no nodes."))
            return
        choice, ok = QInputDialog.getItem(self, _("Use child node"),
                                          _("Child node:"), refs, 0, False)
        if not ok:
            return
        origin = Vector2.from_xy(0, 0)

        def find_abs(nodes: list, px, prot):
            for n in nodes:
                pos = node_position(n, px, prot)
                if n.ref == choice:
                    return pos
                found = find_abs(n.children, pos, prot + n.rotation)
                if found is not None:
                    return found
            return None

        abs_nm = find_abs(child.nodes, origin, 0.0)
        if abs_nm is not None:
            self.pivot_widget.load(x=abs_nm.x / MM, y=abs_nm.y / MM)

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
        kind = self.kind_combo.currentData()
        pivot_xy = pivot_polar = None
        if kind == "module":
            pfields, perr = self.pivot_widget.build()
            if perr:
                QMessageBox.warning(self, _("Add node"), perr)
                return None
            if "radius" in pfields:
                pivot_polar = (pfields["radius"], pfields["angle"])
            else:
                pivot_xy = (pfields["x"], pfields["y"])
        # Position tab: "Relative to component" with no Role is a hard refusal
        # (never silently downgrade to the parent base) — same explicit style as
        # the other guards above.
        own_anchor = self.own_anchor()
        if self.relative_to_component_radio.isChecked() and own_anchor is None:
            QMessageBox.warning(
                self, _("Add node"),
                _("Pick a component (Role) or switch back to Relative to parent."))
            return None
        return TreeNode(ref=ref, kind=kind, xy=xy, polar=polar, rotation=rotation,
                        name=name, group=group, pivot_xy=pivot_xy,
                        pivot_polar=pivot_polar, own_anchor=own_anchor)


class _AnchorDialog(QDialog):
    """Modal dialog for picking a tree anchor, covering ALL six TreeAnchor
    modes (see kicadstamp/trees.py):
      - origin   -> (anchor (origin)): absolute board origin (0,0)
      - record   -> (anchor (ref "...")): a config record name, narrowed by a
                    kind filter (Entity/Rule/Coordinate/Point/Clone + All) —
                    a PICKER AID only: the anchor grammar has no kind (a name
                    shared across sections is fatal at link_trees either way)
      - external -> (anchor (ref "...") (external)): live-board-only refdes
      - auto     -> NO (anchor ...): derived from the root Entity's own cell
                    zero slot at materialization (is_auto=True) — the only
                    way to get an auto anchor through the GUI
      - role     -> (anchor (role "...") [(sheet ...) (cluster ...) (pad ...)])
      - point    -> (anchor (point "...")): a points: entry name
    `existing` (a TreeAnchor) switches to EDIT mode: the mode and every field
    are pre-filled (symmetric to _NodeDialog's existing=), so a user can just
    tweak e.g. the sheet of a role anchor instead of rebuilding it.
    "External refdes" is STORED as an is_external anchor — the resolver then
    never matches it against a config record name (collision impossible;
    note_2026_08_28_tree_anchor_name_collision)."""

    # User-facing labels for the record-mode kind filter (populate the Kind
    # combo in the same order the placeable sections are documented).
    _KIND_LABELS = {
        "placement": _("Entity"),
        "chain": _("Chain"),
        # Legacy kind alias (2026-09-01 Rule -> Chain rename).
        "rule": _("Chain"),
        "coordinate": _("Coordinate"),
        "point": _("Point"),
        "clone": _("Clone"),
    }

    def __init__(self, parent, ref_candidates, *, cfg=None, sheet_names=None,
                 role_candidates=None, cluster_candidates=None, existing=None,
                 tree=None):
        super().__init__(parent)
        self.setWindowTitle(_("Set anchor"))
        self._ref_candidates = list(ref_candidates or [])
        self._cfg = cfg
        self._sheet_names = dict(sheet_names or {})
        self._role_candidates = list(role_candidates or [])
        self._cluster_candidates = list(cluster_candidates or [])
        self._result: Optional[TreeAnchor] = None

        # Self-reference guard (plan 2026-08-31 anchor_self_ref_guard): a tree
        # whose OWN single top-level node is a placement record must never be
        # offered THAT record as its own ref anchor — a ref anchor pointing at
        # its own root Entity can never resolve (cycle-fatal). Drop the
        # (placement, self_ref) candidate here so _on_kind_changed, _prefill
        # and the collision-prefix counting all operate on the filtered list.
        self._self_entity_ref = _root_entity_ref(tree)
        if self._self_entity_ref is not None:
            self._had_self_entity = any(
                k == "placement" and n == self._self_entity_ref
                for k, n in self._ref_candidates)
            self._ref_candidates = [
                (k, n) for k, n in self._ref_candidates
                if not (k == "placement" and n == self._self_entity_ref)]
        else:
            self._had_self_entity = False

        form = QFormLayout(self)

        # Mode combo — the six TreeAnchor modes. The first three keep their
        # historic indices (0/1/2) so nothing that drives the combo by index
        # regresses; auto/role/point are appended after them.
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(_("Origin (board 0,0)"), "origin")
        self.mode_combo.addItem(_("Config record"), "record")
        self.mode_combo.addItem(_("External refdes"), "external")
        self.mode_combo.addItem(_("Auto (derive from Entity's own cell)"), "auto")
        self.mode_combo.addItem(_("Role"), "role")
        self.mode_combo.addItem(_("Point"), "point")
        form.addRow(_("Anchor:"), self.mode_combo)

        # record / external rows: a kind filter (picker aid) + the ref combo.
        self.record_row = QWidget()
        record_form = QFormLayout(self.record_row)
        record_form.setContentsMargins(0, 0, 0, 0)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(_("All kinds"), None)
        for kind, label in self._KIND_LABELS.items():
            self.kind_combo.addItem(label, kind)
        record_form.addRow(_("Kind:"), self.kind_combo)
        self.ref_combo = QComboBox()
        configure_searchable(self.ref_combo)
        self.ref_combo.setPlaceholderText(_("record name (from config)"))
        record_form.addRow(_("Ref:"), self.ref_combo)
        # Self-reference hint (§2 of plan_2026_08_31_anchor_self_ref_guard): a
        # static label (never a modal) shown when the Entity section emptied
        # BECAUSE of the self-ref exclusion, pointing the user at the Auto mode
        # instead of a bare empty combo. Hidden by default; _update_hint drives
        # it.
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.hide()
        record_form.addRow(self.hint_label)
        form.addRow(self.record_row)

        # role rows: role/sheet/cluster searchable combos + pad free text.
        self.role_row = QWidget()
        role_form = QFormLayout(self.role_row)
        role_form.setContentsMargins(0, 0, 0, 0)
        self.role_edit = QComboBox()
        configure_searchable(self.role_edit)
        set_combo_items(self.role_edit, self._role_candidates)
        role_form.addRow(_("Role:"), self.role_edit)
        self.sheet_edit = QComboBox()
        configure_searchable(self.sheet_edit)
        set_combo_items(self.sheet_edit, list(self._sheet_names.values()))
        self.sheet_edit.lineEdit().setPlaceholderText(
            _("sheet name (narrows an ambiguous Role, optional)"))
        role_form.addRow(_("Sheet:"), self.sheet_edit)
        self.cluster_edit = QComboBox()
        configure_searchable(self.cluster_edit)
        set_combo_items(self.cluster_edit, self._cluster_candidates)
        role_form.addRow(_("Cluster:"), self.cluster_edit)
        self.pad_edit = QLineEdit()
        self.pad_edit.setPlaceholderText(_("pad (optional)"))
        role_form.addRow(_("Pad:"), self.pad_edit)
        form.addRow(self.role_row)

        # point row: searchable combo over the cfg.points names.
        self.point_row = QWidget()
        point_form = QFormLayout(self.point_row)
        point_form.setContentsMargins(0, 0, 0, 0)
        self.point_edit = QComboBox()
        configure_searchable(self.point_edit)
        if self._cfg is not None:
            set_combo_items(self.point_edit, sorted(getattr(self._cfg, "points", {}) or {}))
        point_form.addRow(_("Point:"), self.point_edit)
        form.addRow(self.point_row)

        buttons = QHBoxLayout()
        self.ok_button = QPushButton(_("OK"))
        self.ok_button.clicked.connect(self._accept)
        cancel_button = QPushButton(_("Cancel"))
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(cancel_button)
        form.addRow(buttons)

        # Connect only AFTER every widget exists so no handler fires mid-
        # construction (adding the first combo item triggers a spurious
        # currentIndexChanged before the rows are built).
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self.ref_combo.currentIndexChanged.connect(self._on_ref_selected)

        if existing is not None:
            self._prefill(existing)
        else:
            self._on_mode_changed()

    def _on_mode_changed(self) -> None:
        mode = self.mode_combo.currentData()
        self.record_row.setVisible(mode in ("record", "external"))
        self.role_row.setVisible(mode == "role")
        self.point_row.setVisible(mode == "point")
        if mode in ("record", "external"):
            self._on_kind_changed()

    def _on_kind_changed(self) -> None:
        """Populate the ref combo for the current kind filter. External mode
        clears it (free-text live refdes); "All kinds" shows every placeable
        name — one unique to a section plain, one shared by 2+ sections once
        per section as {kind}:{name}; a concrete kind shows only that section's
        names (mirrors _NodeDialog._on_kind_changed)."""
        if self.mode_combo.currentData() == "external":
            self.ref_combo.clear()
            self.ref_combo.setPlaceholderText(_("external refdes (live board)"))
            self._update_hint()
            return
        kind = self.kind_combo.currentData()
        if kind is None:
            section_count: dict[str, int] = {}
            for _k, name in self._ref_candidates:
                section_count[name] = section_count.get(name, 0) + 1
            items = []
            for k, name in self._ref_candidates:
                if section_count[name] > 1:
                    items.append((f"{k}:{name}", k, name))
                else:
                    items.append((name, None, name))
        else:
            items = [(name, kind, name) for k, name in self._ref_candidates if k == kind]
        self._set_ref_items(items)
        self.ref_combo.setPlaceholderText(_("record name (from config)"))
        self._update_hint()

    def _update_hint(self) -> None:
        """Show the self-reference hint when the record-mode ref list emptied
        BECAUSE of the self-ref exclusion (§2 of plan_2026_08_31_anchor_self_
        ref_guard): the tree's own root Entity was a real candidate
        (_had_self_entity) and no other Entity record is left. A static label,
        never a modal — it tells the user where to switch (Auto) instead of
        leaving them staring at an empty combo."""
        mode = self.mode_combo.currentData()
        kind = self.kind_combo.currentData()
        entity_empty = not any(k == "placement" for k, _n in self._ref_candidates)
        if (mode == "record" and self._had_self_entity
                and (kind is None or kind == "placement") and entity_empty):
            self.hint_label.setText(_(
                "This tree's own root Entity {ref!r} can't anchor itself — use Auto.")
                .format(ref=self._self_entity_ref))
            self.hint_label.show()
        else:
            self.hint_label.hide()

    def _set_ref_items(self, items: list[tuple[str, Optional[str], str]]) -> None:
        """Repopulate ref_combo with (display_text, kind, name) triples,
        preserving the current text and blocking signals (the same rule as
        set_combo_items); a concrete `kind` means a PREFIXED collision entry —
        picking it auto-narrows the kind filter (_on_ref_selected)."""
        current_text = self.ref_combo.currentText()
        self.ref_combo.blockSignals(True)
        self.ref_combo.clear()
        for text, kind, name in items:
            self.ref_combo.addItem(text, (kind, name))
        self.ref_combo.setCurrentText(current_text)
        self.ref_combo.blockSignals(False)

    def _on_ref_selected(self, index: int) -> None:
        """Auto-narrow the kind filter when the user picks a PREFIXED collision
        entry in "All kinds" mode (itemData = (kind, name) with a concrete
        kind): switch the kind combo to that section and put the CLEAN name in
        the ref combo. Plain entries carry (None, name) and leave it alone."""
        data = self.ref_combo.itemData(index)
        if data is None:
            return
        kind, name = data
        if kind is None:
            return
        kind_idx = self.kind_combo.findData(kind)
        if kind_idx < 0:
            return
        self.kind_combo.setCurrentIndex(kind_idx)
        self.ref_combo.setCurrentText(name)

    def _prefill(self, existing: TreeAnchor) -> None:
        """Edit mode: select the mode matching `existing` and pre-fill every
        field (symmetric to _NodeDialog._prefill). The mode handler runs even
        when the index did not change (a fresh dialog defaults to origin), so
        the right rows are shown and the ref list is built for record/external."""
        if existing.is_origin:
            mode = "origin"
        elif existing.is_auto:
            mode = "auto"
        elif existing.role is not None:
            mode = "role"
        elif existing.point is not None:
            mode = "point"
        else:
            mode = "external" if existing.is_external else "record"
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self._on_mode_changed()

        if existing.role is not None:
            self.role_edit.setCurrentText(existing.role)
            self.sheet_edit.setCurrentText(existing.anchor_sheet or "")
            self.cluster_edit.setCurrentText(existing.anchor_cluster or "")
            self.pad_edit.setText(existing.anchor_pad or "")
        elif existing.point is not None:
            self.point_edit.setCurrentText(existing.point)
        elif existing.ref is not None:
            if not existing.is_external:
                # Narrow the kind filter when the ref is unambiguous (a name
                # unique to one section); ambiguous names stay on "All kinds".
                kinds = sorted({k for k, name in self._ref_candidates
                                if name == existing.ref})
                if len(kinds) == 1:
                    kind_idx = self.kind_combo.findData(kinds[0])
                    if kind_idx >= 0:
                        self.kind_combo.setCurrentIndex(kind_idx)
            self._on_kind_changed()
            self.ref_combo.setCurrentText(existing.ref)

    def _accept(self) -> None:
        mode = self.mode_combo.currentData()
        if mode == "origin":
            self._result = TreeAnchor(ref=None, is_origin=True, is_external=False)
        elif mode == "auto":
            self._result = TreeAnchor(is_auto=True)
        elif mode == "role":
            role = self.role_edit.currentText().strip()
            if not role:
                QMessageBox.warning(self, _("Set anchor"), _("Role is required."))
                return
            self._result = TreeAnchor(
                role=role, is_origin=False,
                anchor_sheet=self.sheet_edit.currentText().strip() or None,
                anchor_cluster=self.cluster_edit.currentText().strip() or None,
                anchor_pad=self.pad_edit.text().strip() or None)
        elif mode == "point":
            point = self.point_edit.currentText().strip()
            if not point:
                QMessageBox.warning(self, _("Set anchor"), _("Point name is required."))
                return
            self._result = TreeAnchor(point=point, is_origin=False)
        else:  # record / external
            ref = self.ref_combo.currentText().strip()
            if not ref:
                QMessageBox.warning(self, _("Set anchor"), _("Ref is required."))
                return
            # "external" mode = live-board refdes, never a config record name —
            # carry it as is_external so the resolver can't hit a name collision.
            self._result = TreeAnchor(ref=ref, is_origin=False,
                                      is_external=(mode == "external"))
        self.accept()

    @staticmethod
    def prompt(parent, ref_candidates, *, cfg=None, sheet_names=None,
               role_candidates=None, cluster_candidates=None, existing=None,
               tree=None):
        dlg = _AnchorDialog(parent, ref_candidates, cfg=cfg, sheet_names=sheet_names,
                            role_candidates=role_candidates,
                            cluster_candidates=cluster_candidates, existing=existing,
                            tree=tree)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg._result
