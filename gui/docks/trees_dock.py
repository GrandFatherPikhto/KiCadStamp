# gui/docks/trees_dock.py
"""TreesDock — hand-authored s-expr "trees" editor (design
techdocs/handoff/deepseek/design_2026_08_27_trees_gui_dock.md, then moved
into the root config as the trees: section — design_2026_08_27_trees_in_
config_file.md, FORK-5).

This dock edits the OPTIONAL manual trees: section of the ROOT config
(design_2026_08_27_trees_in_config_file.md): it follows the root via
root_changed (like ConfigTreeDock), has no file identity of its own,
per-tree tabs, structural editing, Save + dirty tracking through
the single config_writer chokepoint, checkbox subtree selection + background
curated Redraw through run_curated_tree_redraw_worker.
"""
import logging
import math
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QComboBox, QDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                             QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
                             QTabWidget, QTreeWidget, QTreeWidgetItem,
                             QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from kicadstamp.anchor_graph import Record, build_records
from kicadstamp.config import TreeInstance, load_config, load_tree
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.config_writer import read_data, upsert_entity, write_data
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
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
    _root_entity_ref,
    _snap_mm,
    board_offset_to_local_mm,
    board_rotation_to_local_deg,
    local_offset_to_board_mm,
    local_rotation_to_board_deg,
    mount_node_base,
    rotate_offset_mm,
    tree_layout_base,
    tree_pivot_offset,
    resolve_base_live_position,
    resolve_base_rotation_deg,
    relative_rotation_deg,
)
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver,
    resolve_anchor_pad_position,
)
from kicadstamp.placement.anchor_identity import entity_is_self_anchor
from kicadstamp.placement.services.point_resolver import resolve_point_chain
from kicadstamp.trees import (KINDS, Tree, TreeAnchor, TreeNode,
                              _mount_ancestor_of, _walk_nodes,
                              tree_pivot_ref_candidates,
                              tree_self_ref_candidates, tree_to_dict)
from kicadstamp.utils.units import MM

from .. import board_overlay, overlay_markers, settings
from ..ui_utils import (persist_dialog_size, restore_dialog_size,
                        wrap_in_scroll_area)
from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget, build_role_anchor_fields
from .live_position import read_record_live_pose
from ._common import (ERROR_STYLE as _ERROR_STYLE,
                      WARN_STYLE as _WARN_STYLE,
                      configure_searchable, confirm_first_run_adoption,
                      highlight_stylesheet_for, set_combo_items, show_message,
                      SplitterSizeKeeper)
from .cascade import (run_curated_forest_redraw_worker, run_curated_tree_redraw_worker,
                      run_single_node_redraw_worker)
from .entity_delete import backup_file

logger = logging.getLogger(__name__)

# Neutral informational accent for a tree node that duplicates its OWN explicit
# (role ...) anchor (plan_2026_09_05_tree_root_rotation_drift §2): the node and
# the anchor resolve the SAME physical part, so the node is a redundant
# no-op that can "rotate" the anchor on redraw. Amber-ish — informational
# "look here / safe to delete", NEVER the red reserved for real problems.
_ANCHOR_DUPLICATE_BG = QColor("#f5e6b8")
_ANCHOR_DUPLICATE_TOOLTIP = _(
    "this node duplicates the tree's own anchor (role {role}) — safe to "
    "delete; the anchor resolves independently of the node list")

# Accent + tag for the node a tree HANGS FROM — its pivot-ref / inner point
# (design §3.10, plan_2026_09_11_tree_settings_form §W.6). A cool tint,
# deliberately different from the duplicate-anchor amber so "this is the
# handle" never reads as "this is a redundant duplicate"; informational only,
# NEVER an error accent.
_PIVOT_HANDLE_BG = QColor("#cfe3f5")
_PIVOT_HANDLE_TAG = _("handle")
_PIVOT_HANDLE_TOOLTIP = _(
    "this node is the tree's suspension point (pivot-ref) — the tree is "
    "positioned and rotated around it")

# Short kind tags, shown next to a node's ref when the kind is set. "external"
# is included here — trees need it.
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
    # Display-only relabel (2026-09-07, Denis: "module -- это tree", not
    # discoverable as a label on its own) — the grammar/data value stays
    # "module" everywhere (files, KINDS, link_trees, ...); only what the user
    # reads here and in the Kind combo (NodeFormWidget, below) changes.
    "module": _("tree"),
    # Mount node (2026-09-11, plan_2026_09_11_tree_mount_nodes): a POINT OF
    # REFERENCE — it places nothing itself; its children hang from the live
    # component its anchor names.
    "mount": _("mount"),
}

# Node kinds the per-node Redraw button can actually act on. Most of these
# are a record kind ApplyPipeline's --only can resolve by name directly
# (_redraw_edited_node -> run_single_node_redraw_worker). "module" is the one
# exception: its ref names an EMBEDDED TREE, not a config record, so --only
# can never resolve it directly — but Denis pointed out (2026-09-07, live)
# that Redraw on a module node should still DO something: activate that
# module's own content, exactly like checking its marker does in the
# forest-wide "Full redraw" (curated_redraw_plan_forest/design P3 D2).
# _redraw_edited_node special-cases "module" to run_curated_forest_redraw_
# worker with selected_refs={node.ref} instead — scoping the SAME mechanism
# to just this one marker, so only ITS content moves (nothing else in the
# owning tree is touched). external/point nodes stay excluded: they are
# live-board-only bases with no content of their own to activate.
# Shared by _NodeDialog._update_redraw_state (modal Edit) and
# TreesDock._form_action_row (master-detail Node tab) so the two Redraw
# buttons can never drift out of sync (found live 2026-09-07: the
# master-detail button had no guard at all — clicking Redraw on a module node
# crashed run_single_node_redraw_worker with "--only: names not found").
_REDRAWABLE_NODE_KINDS = frozenset(
    {"placement", "clone", "chain", "rule", "coordinate", "net_trace", "module"})


def _anchor_label(anchor: TreeAnchor) -> str:
    """Human-readable label for a tree's anchor pseudo-root — one branch per
    TreeAnchor mode; never renders "None" (2026-08-31, anchor-dialog GUI gap:
    self/role/point anchors carry ref=None and would otherwise show "⚓ None").
    The exact tag per mode is a display convention only — the underlying
    TreeAnchor is unchanged."""
    if anchor.is_origin:
        return _("⚓ (origin)")
    if anchor.is_self:
        return _("⚓ (self)")
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

# ── Overlay circles for a tree's anchor and base (З, 2026-09-12) ──────────
# The overlay owner (gui/overlay_markers.py, task Е) owns the key -> uuid map;
# these two namespaced keys are what the tree-settings form draws through it.
# The slugs live HERE rather than in the owner because the owner reserves
# `tree-inner` / `tree-outer` — the design §О.3.1 pair this task deliberately
# REPLACES: the inner (suspension) point lands EXACTLY on the anchor by
# construction (resolve_module_effective_base makes the pivot land on the
# marker), so two circles there would coincide. The pair worth drawing is the
# ANCHOR (where the tree hangs — what it is moved and rotated by) and the BASE
# (the origin of its local frame, from which every node's xy is measured and
# which is invisible today); the vector between them IS the rotated suspension
# point made visible.
_TREE_ANCHOR_NS = "tree-anchor"
_TREE_BASE_NS = "tree-base"


def _tree_anchor_key(name) -> str:
    """Overlay key of the circle showing where tree `name` hangs."""
    return f"{_TREE_ANCHOR_NS}/{name}"


def _tree_base_key(name) -> str:
    """Overlay key of the circle showing the origin of tree `name`'s own local
    frame."""
    return f"{_TREE_BASE_NS}/{name}"


def _tree_marker_keys(name) -> tuple:
    """BOTH overlay keys one tree's toggle owns, anchor first."""
    return (_tree_anchor_key(name), _tree_base_key(name))


def _tree_marker_points(adapter, cfg, tree, sheet_names, forest) -> tuple:
    """(anchor_mm, base_mm) for `tree` — the two LIVE positions the circles
    show, in world mm. anchor = `_anchor_base_live_position` (every anchor
    mode); base = `tree_layout_base` (that same raw anchor pose plus the tree's
    own suspension point and angle). No new maths is introduced: both functions
    already exist and are only converted from nm to mm here. Raises
    ValidationError when the anchor cannot resolve — the caller turns that into
    one Log line."""
    anchor_pos, _anchor_rot = _anchor_base_live_position(
        adapter, cfg, tree, sheet_names)
    base_pos, _base_rot = tree_layout_base(
        adapter, cfg, tree, sheet_names, forest)
    return ((anchor_pos.x / MM, anchor_pos.y / MM),
            (base_pos.x / MM, base_pos.y / MM))


def _show_tree_markers_worker(payload) -> dict:
    """WORKER thread — no widget access anywhere in here.

    Draw ONE tree's anchor circle and (when it does not coincide with the
    anchor) a second circle at its local-frame base, through the shared overlay
    owner. The owner's ensure_marker is idempotent BY KEY (Е.2.2), so pressing
    the toggle again MOVES the two circles instead of stacking new ones.

    З.2.3: when base and anchor are closer than
    `overlay_markers.orphan_tolerance_mm()` (a zero suspension point at a zero
    angle — the first row of the task's own measurement) ONLY the anchor is
    drawn and a stale `tree-base` key is removed, so two circles can never end
    up in one spot, even if the user enabled the toggle at a different pivot
    first.

    Never raises (Е.2.6): a circle is a visualisation, so a failed anchor
    resolve or a disabled overlay layer degrades to one Log line returned in
    the result dict — never to an exception or a modal."""
    adapter = payload["adapter"]
    tree = payload["tree"]
    try:
        anchor_mm, base_mm = _tree_marker_points(
            adapter, payload["cfg"], tree, payload["sheet_names"],
            payload["forest"])
    except Exception as e:  # noqa: BLE001 — a resolve failure is a Log line
        return {"error": _("Tree {name!r}: the anchor did not resolve: {error}")
                .format(name=tree.name, error=e)}
    try:
        overlay_markers.owner.ensure_marker(
            adapter, _tree_anchor_key(tree.name), anchor_mm[0], anchor_mm[1])
    except Exception:  # noqa: BLE001 — drawing never breaks the caller
        return {"error": _(
            "Tree {name!r}: the anchor marker was not drawn — the overlay "
            "layer {layer!r} is not enabled on this board, or the board read "
            "failed.").format(name=tree.name,
                              layer=board_overlay.overlay_layer_name())}
    tol = overlay_markers.orphan_tolerance_mm()
    if (abs(base_mm[0] - anchor_mm[0]) <= tol
            and abs(base_mm[1] - anchor_mm[1]) <= tol):
        # No second circle in the same spot — and drop a base key left over
        # from an earlier pivot (З.2.3).
        overlay_markers.owner.remove_key(adapter, _tree_base_key(tree.name))
        return {"anchor_mm": anchor_mm, "base_mm": None}
    try:
        overlay_markers.owner.ensure_marker(
            adapter, _tree_base_key(tree.name), base_mm[0], base_mm[1])
    except Exception:  # noqa: BLE001 — the anchor circle is already down
        return {"anchor_mm": anchor_mm, "base_mm": None, "warning": _(
            "Tree {name!r}: the base marker was not drawn — the overlay layer "
            "{layer!r} is not enabled on this board, or the board read "
            "failed.").format(name=tree.name,
                              layer=board_overlay.overlay_layer_name())}
    return {"anchor_mm": anchor_mm, "base_mm": base_mm}


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


def _resolve_node_base_pose(cfg, adapter, sheet_names, tree: Tree,
                            parent_node: Optional[TreeNode],
                            base_anchor: Optional[TreeAnchor]
                            ) -> tuple[Vector2, Optional[float], bool]:
    """(position_nm, rotation_deg | None, mirror) of the frame a node's stored
    xy/rotation are expressed against — shared by the live read
    (_resolve_live_offset) and the node FORM's board-frame conversion, so the
    two can never disagree about where the base is (plan_2026_09_11 §3).

    - base_anchor (the node's OWN anchor, "Relative to component") — resolved
      live through the SAME ComponentResolver the recursive walks use (plan
      tree_node_own_anchor §2.3). A role anchor names a COMPONENT, never a cell
      instance, so it has no mirror concept (mirror=False).
    - parent_node is None — the tree's OWN anchor, via
      _anchor_base_live_position (every anchor mode: origin/auto/role/point/ref).
    - a MOUNT parent node — its OWN base via mount_node_base (plan
      plan_2026_09_12_node_form_mount_parent_base §Л.2.1), laid from the tree's
      own base (tree_layout_base) exactly like gui/docks/cascade.py. A mount
      node's ref is a LOCAL NAME and is never resolved against the config (rule
      Б1), so the record path below can never find it; mirror=False — the
      anchor names a COMPONENT, not a cell instance (the same reasoning as the
      base_anchor branch above).
    - any other parent NODE — its live pose via read_record_live_pose: a
      placement Entity is read from its LIVE CLUSTER, not from the tree that
      places it (the BASE has the same disease as the child, plan §2.2).

    Raises ValidationError on any resolution failure — the callers turn it into
    a warning (read) / a raw+disabled form (no connection), never a guess."""
    if base_anchor is not None:
        resolver = ComponentResolver(adapter, cfg, sheet_names)
        fp = resolver.resolve_anchor_fp(
            None, base_anchor.role, base_anchor.anchor_sheet,
            base_anchor.anchor_cluster, label=base_anchor.role)
        parent_pos = fp.position
        parent_deg = fp.angle_deg
        if base_anchor.anchor_pad:
            parent_pos = resolve_anchor_pad_position(
                adapter, fp, base_anchor.anchor_pad, base_anchor.role)
        return parent_pos, parent_deg, False
    if parent_node is None:
        parent_pos, parent_deg = _anchor_base_live_position(
            adapter, cfg, tree, sheet_names)
        return parent_pos, parent_deg, False
    if parent_node.kind == "mount":
        # Л.2.1: a mount node's ref is a local NAME (never a config record), so
        # the generic branch below would look for a refdes that cannot exist.
        # Its base is mount_node_base — THE single seam every recursive walk
        # uses — fed from the tree's own layout base (tree_layout_base), the
        # same pair gui/docks/cascade.py's stage-2 layout calls. mount_node_base
        # picks INTERNAL vs LIVE structurally, so a mount anchored to a role
        # this tree places itself needs no board here.
        #
        # The forest is derived from cfg.trees: the dock pins
        # `cfg.trees is self._trees` (trees_dock_cfg_trees_desync), so this is
        # the dictionary the form's own all_trees would give, without widening
        # this function's signature (and its monkeypatched test doubles).
        forest = {t.name: t for t in (getattr(cfg, "trees", None) or [])}
        tree_base_pos, tree_base_rot = tree_layout_base(
            adapter, cfg, tree, sheet_names, forest)
        parent_pos, parent_deg = mount_node_base(
            parent_node, tree, tree_base_pos, tree_base_rot, adapter, cfg,
            sheet_names, forest)
        return parent_pos, parent_deg, False
    parent_record, _is_external = _resolve_probe_ref(
        cfg, parent_node.ref, parent_node.kind)
    pose = read_record_live_pose(adapter, cfg, parent_node.ref, parent_record,
                                 sheet_names)
    return pose.position, pose.rotation_deg, pose.mirror


def _pivot_ref_mount_parent(tree: Tree, node: TreeNode,
                            candidate: Optional[TreeNode]) -> Optional[TreeNode]:
    """The mount node that WOULD pin `node`'s base to a live component if `node`
    were re-hung under `candidate` — None when that re-hang is legal.

    THE single expression of `kicadstamp.trees._pivot_ref_rejection`'s third
    branch ("mount-ancestor") for a HYPOTHETICAL parent
    (plan_2026_09_12_move_to_recalculates_offset §Э1). A re-hang under
    `candidate` gives `node` exactly candidate's ancestor chain plus `candidate`
    itself, so the load-time validator (_validate_tree_pivot_ref) would kill the
    config at the next load precisely when that chain holds a mount node: a node
    under a mount node is pinned to a LIVE component's position and does not
    follow the tree, so it can never be the tree's inner point. The walker
    itself (_mount_ancestor_of) is CONSULTED from kicadstamp.trees — the very
    one the loader and the pivot-ref picker use — never re-implemented here.

    Only a tree's OWN pivot-ref can be pinned this way; for any other node the
    question does not arise, and the answer is None without a single walk."""
    if tree.pivot_ref is None or tree.pivot_ref != node.ref:
        return None
    if candidate is None:
        return None                     # top level: the tree's own anchor
    if candidate.kind == "mount":
        return candidate
    return _mount_ancestor_of(candidate, tree.nodes)


def _reparented_offset(cfg, adapter, sheet_names, tree: Tree, node: TreeNode,
                       old_parent: Optional[TreeNode],
                       new_parent: Optional[TreeNode]
                       ) -> Optional[tuple[Optional[tuple[float, float]],
                                           Optional[tuple[float, float]],
                                           float]]:
    """The new (xy, polar, rotation) for `node` after re-hanging it from
    `old_parent` to `new_parent`, chosen so the node does NOT move on the board
    (plan_2026_09_12_move_to_recalculates_offset §Э2).

    A node's xy/polar are stored in its BASE's local frame and its rotation is
    RELATIVE to that base, so changing the parent silently changes what the
    stored numbers mean. The absolute board pose is preserved here: the board
    offset against the new base is the old one plus the base SHIFT (old - new),
    and the absolute board angle is untouched, so only the stored relative angle
    changes when the base turned. This is arithmetic on the TreeNode alone — no
    widgets, no board write — which is what the context menu needs (the form's
    own _refresh_for_new_anchor reads and writes widgets and cannot be called
    from there).

    Returns None when there is nothing to change:
    * a MOUNT node's offset is expressed against its OWN live anchor, not
      against its parent (_resolve_node_base_pose returns the anchor pose on its
      very first branch and never looks at parent_node), so a re-hang does not
      move it and recalculating would inject an error where there was none;
    * a node that stores no offset at all.
    The representation is preserved (Л.2): a polar node comes back polar, never
    silently rewritten as xy, so the s-expr keeps its shape. Rotation is
    recalculated together with the offset. Children are deliberately NOT touched:
    they are stored relative to THIS node, whose pose does not change, so the
    subtree follows on its own (Л.3).

    Raises whatever _resolve_node_base_pose raises when a base does not resolve
    (no live board, missing component): there is no "roughly" answer here and no
    silent 0° fallback — the caller decides between asking the user and
    refusing."""
    # Л.1: nothing to recalculate — the base does not depend on the parent.
    if node.kind == "mount" and node.anchor is not None:
        return None
    if node.xy is None and node.polar is None:
        return None

    old_pos, old_deg, _old_mirror = _resolve_node_base_pose(
        cfg, adapter, sheet_names, tree, old_parent, None)
    new_pos, new_deg, _new_mirror = _resolve_node_base_pose(
        cfg, adapter, sheet_names, tree, new_parent, None)
    # A base with no angle concept (origin/point anchor) stores rotation exactly
    # like the form does — 0.0 is the definition there, not a guess.
    old_rot = old_deg if old_deg is not None else 0.0
    new_rot = new_deg if new_deg is not None else 0.0

    # abs = base + board offset  =>  new board offset = old + (old base - new).
    # _snap_mm mirrors the offset widget's own round trip (the form stores what
    # it shows), so both re-hang paths land on bit-identical numbers.
    dx_mm = (old_pos.x - new_pos.x) / MM
    dy_mm = (old_pos.y - new_pos.y) / MM

    board_rot = local_rotation_to_board_deg(node.rotation, old_rot)
    new_rotation = board_rotation_to_local_deg(board_rot, new_rot)

    if node.polar is not None:
        # Л.2: polar stays polar. The offset takes the SAME board-frame hop the
        # form's widget does (rotate to the board frame, shift, read the radius
        # and angle back), so neither path rewrites the config's shape.
        bx, by = rotate_offset_mm(
            node.polar[0], 0.0,
            local_rotation_to_board_deg(node.polar[1], old_rot))
        bx = _snap_mm(bx + dx_mm)
        by = _snap_mm(by + dy_mm)
        radius = _snap_mm(math.hypot(bx, by))
        angle = _snap_mm(math.degrees(math.atan2(-by, bx)))
        return (None, (radius, board_rotation_to_local_deg(angle, new_rot)),
                new_rotation)

    bx, by = local_offset_to_board_mm(node.xy, old_rot)
    new_xy = board_offset_to_local_mm(
        (_snap_mm(bx + dx_mm), _snap_mm(by + dy_mm)), new_rot)
    return (new_xy, None, new_rotation)


def _resolve_live_offset(cfg, adapter, sheet_names, tree: Tree,
                         parent_node: Optional[TreeNode], ref: str, kind: str | None,
                         base_anchor: Optional[TreeAnchor] = None
                         ) -> tuple[tuple[float, float], Optional[float]]:
    """((local_offset_x_mm, local_offset_y_mm), relative_rotation_deg | None) for
    the "would-be" child `ref`/`kind` relative to its base (parent_node None =
    the tree's own anchor) — expressed in the BASE'S LOCAL (config) frame, i.e.
    EXACTLY the two numbers a tree node stores (plan_2026_09_11 §3.1).

    The child's live pose comes from the "where does this record stand right
    now" dispatcher (read_record_live_pose): a placement Entity is read from its
    LIVE CLUSTER, never from the tree that places it — a cluster the user moved
    by hand far from where the config records put it is now visible (bug 0.1).

    Reuses the EXACT link_trees resolution rules via _resolve_probe_ref, and the
    shared base resolver (_resolve_node_base_pose) for the mount anchor / tree
    anchor / parent node.

    Rotation is None when the CHILD has no rotation concept (point kind) — the
    caller must leave the field blank, never write a fake 0. Raises
    ValidationError on any resolution failure, INCLUDING a MIRRORED live
    instance: the trees layer has no mirror storage at all (neither
    tree_position.py nor link_trees.py nor entity_placement.py reads or writes
    one), so a mirrored read is refused with an honest message instead of being
    silently imported as an unmirrored pose (plan §2.3).

    A note on the historic path: `resolve_base_live_position` /
    `resolve_base_rotation_deg` stay on THIS module's names, so the existing
    tests' monkeypatches of them keep driving the pass-through kinds (clone/
    chain/coordinate/point/external/rule)."""
    child_record, _is_external = _resolve_probe_ref(cfg, ref, kind)

    # No KeyError boundary here any more: bug #6 (2026-08-31) made
    # ClonePositionCalculator._resolve_anchor resolve its anchor_point LAZILY on
    # demand (resolve_point_chain), so a clone+anchor_point live read succeeds
    # even with the always-empty resolved_points dict this ad-hoc path passes —
    # the 2026-08-27 workaround that converted that KeyError into a warning is
    # superseded. Real resolution failures (a missing point, a ref not on the
    # board, ...) are ValidationErrors, caught by the callers
    # (_on_read_position / _reread_node_flow), which turn them into a warning.
    base_pos, base_deg, base_mirror = _resolve_node_base_pose(
        cfg, adapter, sheet_names, tree, parent_node, base_anchor)
    if base_mirror:
        raise ValidationError(format_fatal_error(
            _("the base of this node is MIRRORED on the live board"),
            [_("a tree node cannot store a mirror — unmirror the component in "
               "KiCad (or pick another base), then read the position again")]))
    base_rot = base_deg if base_deg is not None else 0.0

    if child_record is not None and getattr(child_record, "kind", None) == "placement":
        child_pose = read_record_live_pose(adapter, cfg, ref, child_record,
                                          sheet_names)
        child_pos = child_pose.position
        child_deg = child_pose.rotation_deg
        child_mirror = child_pose.mirror
    else:
        # Historic path, kept on THIS module's names for the monkeypatch seam.
        child_pos = resolve_base_live_position(adapter, cfg, ref, child_record,
                                               {}, sheet_names)
        child_deg = resolve_base_rotation_deg(adapter, cfg, ref, child_record,
                                              sheet_names)
        child_mirror = False
    if child_mirror:
        raise ValidationError(format_fatal_error(
            _("this node's instance is MIRRORED on the live board"),
            [_("a tree node cannot store a mirror — unmirror the component in "
               "KiCad, then read the position again")]))

    board_offset = ((child_pos.x - base_pos.x) / MM,
                    (child_pos.y - base_pos.y) / MM)
    offset_mm = board_offset_to_local_mm(board_offset, base_rot)
    rotation = (relative_rotation_deg(child_deg, base_rot)
                if child_deg is not None else None)
    return offset_mm, rotation


def _copy_node_onto(target: TreeNode, built: TreeNode) -> None:
    """Copy every editable field of a BUILT node onto an EXISTING node in place
    (mutate, don't swap identity — other structures may hold a reference, e.g.
    _node_items). The single copy routine shared by every node-edit apply path
    (the master-detail Node tab's apply()/redraw() and the dialog forms), so
    they can never drift."""
    target.ref = built.ref
    target.kind = built.kind
    target.xy = built.xy
    target.polar = built.polar
    target.rotation = built.rotation
    target.name = built.name
    target.group = built.group
    # NOTE (2026-09-11, plan_2026_09_11_tree_inner_point_and_rotation §V.3): the
    # pivot_xy/pivot_polar copy that used to sit here is GONE — a node carries no
    # pivot any more (the inner point belongs to the TREE). The tree-level
    # editor arrives in stage Б2.1.
    target.anchor = built.anchor


class TreesDock(QWidget):
    """Editor for the root config's trees: section (design_2026_08_27_trees_
    in_config_file.md) — one page of DockHub's central QTabWidget (it used to
    be a QDockWidget; 2026-09-10, task T).
    Since 2026-09-03 (plan plan_2026_09_03_trees_menu_tools.md) the
    whole-tree actions (Create/Rename/Delete tree, Anchor position, Redraw
    selected/whole) live in the top-level menu Tools → Trees; this widget itself
    keeps the per-tree tabs, the per-node context menus and the read-only
    status row. No file identity of its own — the trees live in the root config
    (cfg.trees), read via root_changed and saved through config_writer."""

    def __init__(self, main_window):
        super().__init__(main_window)
        # Stable widget identity for diagnostics/findChild. This is NO LONGER a
        # QDockWidget (2026-09-10, task T): it is one page of DockHub's central
        # QTabWidget, so saveState()/restoreState() never sees it.
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
        # Master-detail (plan §3): the ref of the node currently shown in the
        # ACTIVE page's Node tab, or None when the hint is shown. Lets the
        # selection handler skip a rebuild when the SAME node is re-selected
        # (e.g. a "Redraw selected" checkbox toggle — plan §7.1.5).
        self._current_node_ref: Optional[str] = None
        # (P1/P2, 2026-09-03, plan tree_ui_state_persistence): active-tab and
        # per-tree expand/collapse state. _pending_active_name is a NAME set by
        # set_root_file (the persisted active_tab) and consumed by the next
        # _rebuild_tabs(); _rebuilding_tabs suppresses the persistence signal
        # handlers during a rebuild (their intermediate events are not user
        # state — the rebuild persists its final state itself).
        self._pending_active_name: Optional[str] = None
        self._rebuilding_tabs = False
        # S.3 (techdocs/me/scroll.md): one SplitterSizeKeeper per page splitter,
        # keyed by tree name. Every non-active tab page is hidden ALWAYS, so a
        # naive sizes() read at quit wrote [0, 0] for each of them.
        self._page_splitter_keepers: dict = {}

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        # A page of DockHub's central QTabWidget, not a dock (task T): the
        # QTabWidget is the elastic centre that makes the Log separator
        # draggable (S.1's make_dock_grow_vertically was measured inert and
        # removed).
        dock_layout = QVBoxLayout(self)
        dock_layout.setContentsMargins(0, 0, 0, 0)
        dock_layout.addWidget(container)

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
        self.tree_tabs = QTabWidget()
        # (P1) Persist the active tab by tree name on every switch — the
        # rebuild-time events are filtered by _rebuilding_tabs.
        self.tree_tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tree_tabs, 1)

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
        previous_path = self._root_path
        if path != previous_path:
            # З.2.5: an ACTUAL root switch invalidates every tree the circles
            # belonged to — clear BOTH tree-marker namespaces now (state; the
            # shapes go on a worker). A repeat call with the SAME path (DockHub
            # broadcasts root_changed on graph refreshes) must NOT wipe circles
            # the user is looking at — the same guard PointsDock uses.
            self._clear_all_tree_markers()
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
        dialogs/forms fetch their candidates lazily when opened (see _prompt_node/
        _build_anchor_form/_build_node_form/_on_create_tree), so no tab rebuild
        is needed here — the
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
        for i in range(self.tree_tabs.count()):
            tree = self._tree_widget_of_page(self.tree_tabs.widget(i))
            if tree is not None:
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
        # §7.1.2/design §9.4: EVERY rebuild clears every page, including the
        # active one's Node/Anchor forms — warn when one of them holds unapplied
        # edits (non-blocking; the rebuild proceeds and the draft is lost).
        self._warn_rebuild_discard()
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
        self.tree_tabs.clear()
        # Fresh page splitters replace the old ones — drop their keepers too.
        self._page_splitter_keepers = {}
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
            self.tree_tabs.addTab(placeholder, _("(no trees)"))
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
            # Master-detail page (plan §3.1 + Z.3): a QSplitter — the SAME tree
            # on the left (widget(0), so _tree_widget_of_page finds it), and ONE
            # form panel on the right (widget(1)). The panel is a QStackedWidget
            # used as a single-visible-page container (the project's master-detail
            # idiom, as in ChainDock/ConfigTreeDock): there is NO Anchor|Node tab
            # bar any more — WHICH form is shown follows the SELECTION (Z.3.2),
            # never a tab the user has to pick. The content is filled lazily for
            # the active page (Z.3.4, `_panel_built`); the placeholder page below
            # keeps the splitter structure stable from the first build.
            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setChildrenCollapsible(False)
            splitter.addWidget(tree_widget)
            form_panel = QStackedWidget()
            form_panel.setObjectName(f"tree_form_panel_{tree.name}")
            form_panel.setProperty("_panel_built", False)
            form_panel.addWidget(QWidget())
            # 2026-09-12 (plan plan_2026_09_12_no_widget_squeezing.md): the
            # panel goes into the splitter WRAPPED, exactly like a Config right
            # page. This dock has no QScrollArea of its own, and the central
            # QTabWidget is pinned to a minimum height of 1 (dock_hub.py:166) so
            # the Log dock can be grown past its content — which is precisely
            # the setup that made Qt squeeze this form's combos to 0 px instead
            # of clipping the container (measured:
            # diagnostics/probe_trees_dock_form_squeeze.py). The wrap takes the
            # squeeze, the form keeps its own field heights and scrolls.
            splitter.addWidget(wrap_in_scroll_area(form_panel))
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 0)
            # Per-tree splitter persistence (2026-09-05, same as the Config
            # master-detail): re-apply the saved handle position to the fresh
            # page QSplitter. The form panel (widget(1)) has stretch 0, so Qt
            # holds it at the saved width once the page is laid out and the
            # tree (widget(0), stretch 1) absorbs the dock-width difference —
            # applying setSizes() before the page is even shown is reliable
            # (unlike an equal-stretch splitter, where pre-show sizes would
            # target a zero-width widget).
            saved_entry = saved_tree_state.get(tree.name)
            if isinstance(saved_entry, dict):
                splitter_sizes = saved_entry.get("splitter_sizes")
                if isinstance(splitter_sizes, list) and len(splitter_sizes) == splitter.count():
                    try:
                        splitter.setSizes([int(v) for v in splitter_sizes])
                    except (TypeError, ValueError):
                        logger.warning("Ignoring invalid saved splitter sizes for %r", tree.name)
            # S.3: remember this page splitter's last good size while it is laid
            # out — a hidden page reports [0, 0].
            self._page_splitter_keepers[tree.name] = SplitterSizeKeeper(splitter)
            self.tree_tabs.addTab(splitter, tree.name)
        # (P1) Restore the active tab by name.
        desired = self._pending_active_name
        self._pending_active_name = None
        if desired is None:
            desired = previous_name
        for idx, tree in enumerate(self._trees):
            if tree.name == desired:
                self.tree_tabs.setCurrentIndex(idx)
                break
        else:
            # The desired tree is gone (deleted/renamed by this very edit) or
            # nothing was active before — today's behavior, tab 0.
            self.tree_tabs.setCurrentIndex(0)
        self._rebuilding_tabs = False
        # Keep gui_state.json in sync with the restored tab (also covers a
        # rebuild that dropped the previously active tree -> tab 0).
        self._persist_active_tab()
        # Master-detail (§3.2): fill the right-hand Anchor/Node panel of the
        # now-active page lazily (after the active tab is final).
        self._rebuild_active_form_panel()

    # ── UI-state persistence (2026-09-03, plan tree_ui_state_persistence) ──
    #
    # gui_state.json["trees_dock"] is a NESTED dict with two independent parts:
    # "active_tab" (P1) and the per-tree "trees" expansion map (P2). Settings
    # merges only TOP-LEVEL keys (gui/settings.py), so P1 and P2 must merge
    # with each other INSIDE that one value — _update_trees_dock_state is the
    # single read-merge-mutate-write helper both phases go through. Each P2
    # per-tree entry also carries "splitter_sizes" (2026-09-05): the page's
    # master-detail splitter position, flushed on quit and re-applied when the
    # page splitter is (re)built.

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
        idx = self.tree_tabs.currentIndex()
        if (idx < 0 or idx >= len(self._trees)
                or self.tree_tabs.count() != len(self._trees)):
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
        rebuild persists its final active tab itself. Also (re)build the lazy
        master-detail form panel for the newly active tree (§3.2)."""
        if self._rebuilding_tabs:
            return
        # З.2.5: leaving a tree takes ITS circles down — only the tree being
        # looked at may keep its anchor/base keys.
        self._clear_other_tree_markers()
        self._persist_active_tab()
        self._rebuild_active_form_panel()

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

    def _good_page_splitter_sizes(self, name: str,
                                  splitter: QSplitter) -> Optional[list]:
        """The page splitter's last GOOD size list (S.3 of techdocs/me/scroll.md):
        the keeper's remembered value when it has one, else the live sizes only
        when they are non-degenerate. None when neither exists — the caller then
        keeps the previously persisted value instead of writing [0, 0]."""
        keeper = self._page_splitter_keepers.get(name)
        if keeper is not None:
            return keeper.capture()
        sizes = list(splitter.sizes())
        if len(sizes) == splitter.count() and any(size > 0 for size in sizes):
            return sizes
        return None

    def persist_ui_state(self) -> None:
        """Final flush — called by MainWindow._persist_settings() on quit/
        close. Re-reads the CURRENT widget state so an interaction that
        happened after the last _rebuild_tabs (a pure tab switch or a manual
        expand/collapse with no structural edit) is still captured."""
        if not self._trees:
            return
        self._persist_active_tab()
        # (P2) Also flush every rendered tree's expansion AND its page
        # splitter's handle position, from the widgets themselves
        # (authoritative — drops refs whose nodes are gone).
        def _fn(dock_state: dict) -> None:
            trees = dock_state.get("trees")
            if not isinstance(trees, dict):
                trees = {}
                dock_state["trees"] = trees
            for i, tree in enumerate(self._trees):
                widget = self.tree_tabs.widget(i)
                tree_widget = self._tree_widget_of_page(widget)
                if tree_widget is not None:
                    entry = self._capture_tree_expansion(tree_widget)
                    # Splitter position (2026-09-05): the tree | form-panel
                    # divider of THIS tree's page (the page is the QSplitter).
                    if isinstance(widget, QSplitter):
                        sizes = self._good_page_splitter_sizes(tree.name, widget)
                        if sizes is not None:
                            entry["splitter_sizes"] = sizes
                        else:
                            # No good size this session (the page was never laid
                            # out) — keep the previously saved handle position
                            # rather than dropping the key or writing [0, 0].
                            previous = trees.get(tree.name)
                            if (isinstance(previous, dict)
                                    and "splitter_sizes" in previous):
                                entry["splitter_sizes"] = previous["splitter_sizes"]
                    trees[tree.name] = entry
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
        # The tree's ROOT row IS its anchor (Z.2): SELECTABLE, so the user can
        # pick the one row that answers "where does the whole tree stand" and
        # edit the anchor in the single right-hand panel. Every OTHER pseudo-root
        # (instance-of / embedded-in / instance:) stays unselectable.
        anchor_item = QTreeWidgetItem(tree_widget.invisibleRootItem())
        anchor_item.setText(0, _anchor_label(tree.anchor))
        dup_refs = self._anchor_duplicate_refs(tree)
        dup_tooltip = (_ANCHOR_DUPLICATE_TOOLTIP.format(role=tree.anchor.role)
                       if dup_refs else None)
        for node in tree.nodes:
            self._render_node(anchor_item, node, expanded_refs,
                              dup_refs=dup_refs, dup_tooltip=dup_tooltip,
                              pivot_ref=tree.pivot_ref)
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
        """Double-click (plan 2026-09-02 P4 п.3/п.4 + master-detail §3.2): a
        MODULE node / an "embedded in X" / "instance: X" pseudo item switches
        the current tab to the referenced tree (unchanged — navigation, not
        editing). EVERY OTHER real node no longer opens the modal editor: a
        single click already shows that node's editor on the master-detail Node
        tab, so the double-click is a convenience that just makes sure the node
        is selected and brings the Node tab to the front. The anchor pseudo-root
        (no TreeNode) does nothing."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, str):
            self._switch_to_tree(data)
            return
        if not isinstance(data, TreeNode):
            return  # anchor pseudo-root
        if data.kind == "module":
            self._switch_to_tree(data.ref)
            return
        tree_widget = self._current_tree_widget()
        if tree_widget is not None:
            tree_widget.setCurrentItem(item)
        # Z.3: selecting the node is enough — the single right-hand panel follows
        # the selection, so there is no Node tab to bring to the front any more.

    def _switch_to_tree(self, name: str) -> None:
        """Activate the tab of the tree named `name` (no-op if not loaded)."""
        for idx, t in enumerate(self._trees):
            if t.name == name:
                self.tree_tabs.setCurrentIndex(idx)
                return

    def activate_tree(self, name: str) -> None:
        """Public: switch to the tree named `name` AND make sure the dock is
        visible/raised — the Config Entity page's "jump to placement" target
        (2026-09-05, design config_qview_chain_entity_pages §8.6)."""
        self._switch_to_tree(name)
        self.show()
        self.raise_()

    def _edit_in_panel(self, tree: Tree, node: TreeNode) -> None:
        """Z.3: 'Edit node…' is a shortcut — a single click already shows the
        node's editor in the single right-hand panel. This makes sure the node is
        selected (a right-click does not select by default); the panel then
        follows the selection. No modal, no separate edit action, no tab."""
        tree_widget = self._tree_widget_for(tree) or self._current_tree_widget()
        if tree_widget is not None:
            item = self._node_items.get(node.ref)
            if item is not None:
                tree_widget.setCurrentItem(item)

    def _anchor_duplicate_refs(self, tree: Tree) -> set[str]:
        """Refs of `tree`'s top-level kind="placement" nodes that duplicate the
        tree's OWN EXPLICIT (role ...) anchor (plan_2026_09_05_tree_root_
        rotation_drift §2): node and anchor resolve the SAME physical part, so
        the node is a redundant no-op that can "rotate" the anchor on redraw.
        The dock marks such nodes (neutral accent + tooltip) so a legacy/hand-
        made duplicate like conn_pm5v_power in "power" is visible; deleting it
        is safe (the anchor resolves independently of the node list).

        Only an EXPLICIT role anchor is considered — a self tree's single
        top-level node is its anchor SOURCE by construction (never a duplicate)
        and origin/ref/point anchors carry no role. Empty when the config is
        not loaded or no node matches."""
        if self._cfg is None or tree is None or tree.anchor is None \
                or tree.anchor.is_self or tree.anchor.role is None:
            return set()
        by_name = {e.name: e for e in self._cfg.entities}
        dup: set[str] = set()
        for node in tree.nodes:
            if node.kind != "placement" or not node.ref:
                continue
            entity = by_name.get(node.ref)
            if entity is None:
                continue
            if entity_is_self_anchor(entity, self._cfg, tree.anchor):
                dup.add(node.ref)
        return dup

    @staticmethod
    def _node_item_text(node: TreeNode, *, is_handle: bool = False) -> str:
        """One row's text: the ref, plus the short kind tag (_KIND_TAGS) and, for
        the tree's own suspension node, the handle tag — the SAME single-column
        tag idiom, so no second column is added (plan §W.6). Shared by the
        initial render and _refresh_tree_marks."""
        text = node.ref
        if node.kind is not None:
            tag = _KIND_TAGS.get(node.kind)
            if tag:
                text = f"{text} ({tag})"
        if is_handle:
            text = f"{text} ({_PIVOT_HANDLE_TAG})"
        return text

    @staticmethod
    def _apply_node_marks(item: QTreeWidgetItem, node: TreeNode, *,
                          is_handle: bool, dup_refs: Optional[set],
                          dup_tooltip: Optional[str]) -> None:
        """Set the informational accent + tooltip of ONE row. The suspension
        handle always WINS over the duplicate-anchor accent (a handle is never
        'just a redundant duplicate'). Shared by the initial render and the
        in-place _refresh_tree_marks, so the two can never disagree.

        A top-level placement node duplicating its tree's own EXPLICIT (role
        ...) anchor (plan_2026_09_05_tree_root_rotation_drift §2) gets the amber
        accent + tooltip: informational, not an error — the node is safe to
        delete (the anchor resolves independently of the node list)."""
        item.setBackground(0, QBrush())
        item.setToolTip(0, "")
        if is_handle:
            item.setBackground(0, QBrush(_PIVOT_HANDLE_BG))
            item.setToolTip(0, _PIVOT_HANDLE_TOOLTIP)
        elif dup_refs and node.ref in dup_refs and dup_tooltip:
            item.setBackground(0, QBrush(_ANCHOR_DUPLICATE_BG))
            item.setToolTip(0, dup_tooltip)

    def _render_node(self, parent_item: QTreeWidgetItem, node: TreeNode,
                     expanded_refs: Optional[set] = None,
                     dup_refs: Optional[set] = None,
                     dup_tooltip: Optional[str] = None,
                     pivot_ref: Optional[str] = None) -> None:
        item = QTreeWidgetItem(parent_item)
        is_handle = pivot_ref is not None and node.ref == pivot_ref
        item.setText(0, self._node_item_text(node, is_handle=is_handle))
        self._apply_node_marks(item, node, is_handle=is_handle,
                               dup_refs=dup_refs, dup_tooltip=dup_tooltip)
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
            self._render_node(item, child, expanded_refs,
                              dup_refs=dup_refs, dup_tooltip=dup_tooltip,
                              pivot_ref=pivot_ref)
        # (P2) Re-apply the saved expansion for this node — done after the
        # children exist (setExpanded is only meaningful on a populated parent).
        if expanded_refs is None:
            expanded_refs = set()
        item.setExpanded(node.ref in expanded_refs)

    def _refresh_tree_marks(self, tree: Optional[Tree]) -> None:
        """Re-apply the informational ROW MARKS (suspension handle, duplicate
        anchor) of an ALREADY BUILT tree widget IN PLACE — no rebuild, so
        selection, expansion and focus survive. Called after the tree settings
        form applies a new pivot-ref/rotation (plan §W.6/W.8.5 item 14)."""
        if tree is None:
            return
        dup_refs = self._anchor_duplicate_refs(tree)
        dup_tooltip = (_ANCHOR_DUPLICATE_TOOLTIP.format(role=tree.anchor.role)
                       if dup_refs else None)
        for node in _walk_nodes(tree.nodes):
            item = self._node_items.get(node.ref)
            if item is None:
                continue
            is_handle = tree.pivot_ref is not None and node.ref == tree.pivot_ref
            item.setText(0, self._node_item_text(node, is_handle=is_handle))
            self._apply_node_marks(item, node, is_handle=is_handle,
                                   dup_refs=dup_refs, dup_tooltip=dup_tooltip)

    # ── Static preview (Phase 1, §5) ─────────────────────────────────────

    @staticmethod
    def _tree_widget_of_page(widget) -> Optional[QTreeWidget]:
        """Unwrap a page widget (a QSplitter after §3.1, or historically a bare
        QTreeWidget in tests/older callers) to the tree it hosts — always
        widget(0) by construction (§3.1: tree first, form panel second)."""
        if isinstance(widget, QSplitter):
            return widget.widget(0) if isinstance(widget.widget(0), QTreeWidget) else None
        return widget if isinstance(widget, QTreeWidget) else None

    def _current_tree_widget(self) -> Optional[QTreeWidget]:
        return self._tree_widget_of_page(self.tree_tabs.currentWidget())

    def _on_selection_changed(self) -> None:
        tree_widget = self._current_tree_widget()
        if tree_widget is None:
            return
        # Master-detail (Z.3): reflect the current selection in the single
        # right-hand panel — a real node shows its editor, the root row / an
        # empty selection shows the tree anchor form. Skipped while
        # _rebuild_tabs is repopulating — its own final
        # _rebuild_active_form_panel() already covers the restored page.
        discarded = False
        if not self._rebuilding_tabs:
            discarded = self._refresh_form_panel()
        # design §9.4: when switching away from a node whose editor held
        # UNAPPLIED edits, the notice wins over the new node's static preview —
        # it is the one thing the user must see (the preview returns on the
        # next click).
        if discarded:
            self._show_status(_("Unapplied changes were discarded."))
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

    # ── Master-detail right panel (plan §3 + Z.3) ────────────────────────
    #
    # Each tree page (Z.3.1) is a QSplitter: the tree on the left (widget(0)),
    # ONE form panel on the right (widget(1)) — a QStackedWidget used as a
    # single-visible-page container (the project's master-detail idiom, as in
    # ChainDock/ConfigTreeDock). There is NO Anchor|Node tab bar: WHICH form is
    # shown follows the SELECTION (Z.3.2), so the panel is re-filled on a tree
    # switch (new tree) and on a selection change (which node/root row). The
    # forms are built LAZILY for the ACTIVE page only — candidates are collected
    # at build time (the same populate-don't-restrict idiom _prompt_node/
    # _build_anchor_form/_build_node_form use), so inactive pages stay cheap and
    # no N live widget trees are kept around.

    def _active_page_splitter(self) -> Optional[QSplitter]:
        """The QSplitter of the CURRENT page, or None (placeholder/no tree)."""
        page = self.tree_tabs.currentWidget()
        return page if isinstance(page, QSplitter) else None

    @staticmethod
    def _form_panel_of_page(widget) -> Optional[QStackedWidget]:
        """The right-hand form PANEL of ANY page widget — the page-level
        counterpart of _tree_widget_of_page (splitter -> widget(1)): by
        construction (§3.1) the page hosts the tree first and ONE form panel
        second. Since 2026-09-12 widget(1) is the scroll area wrapping that
        panel (see _rebuild_tabs), so the wrap is unwrapped here. Returns None
        for a bare/placeholder page (or a historical bare form wrapper), so
        every caller goes through the SAME page -> panel unwrap instead of
        guessing."""
        if not isinstance(widget, QSplitter):
            return None
        right = widget.widget(1)
        if isinstance(right, QScrollArea):
            right = right.widget()
        return right if isinstance(right, QStackedWidget) else None

    def _active_form_panel(self) -> Optional[QStackedWidget]:
        """The right-hand form PANEL (a one-visible-page QStackedWidget) of the
        current page, or None (the placeholder page / no tree)."""
        return self._form_panel_of_page(self.tree_tabs.currentWidget())

    @staticmethod
    def _panel_page(panel: QStackedWidget) -> Optional[QWidget]:
        """The content page currently shown by `panel` — a _form_action_row
        wrapper, a read-only instance stub (QLabel) or the empty placeholder —
        or None when the panel holds no page."""
        return panel.currentWidget() if panel.count() else None

    def _active_form_page(self) -> Optional[QWidget]:
        """The widget currently shown in the active page's form panel, or None.
        The single-panel successor of the retired _active_form_tabs(): callers
        that want "the form on screen" read this."""
        panel = self._active_form_panel()
        return self._panel_page(panel) if panel is not None else None

    @staticmethod
    def _set_panel_content(panel: QStackedWidget, widget: QWidget) -> None:
        """Replace the panel's ONE page in place — the single-panel successor of
        _replace_tab. The previous page is dropped (deleteLater): its content is
        decided by the selection, never kept as a hidden tab."""
        while panel.count():
            old = panel.widget(0)
            panel.removeWidget(old)
            old.deleteLater()
        panel.addWidget(widget)

    def _form_action_row(self, form) -> QWidget:
        """Wrap a modal-agnostic form (NodeFormWidget/AnchorFormWidget) with
        the Apply/Redraw button row the master-detail panel needs — the forms
        own apply()/redraw() (plan §1.3/§2.3), the buttons belong to the
        embedding context (this panel). No Close: a permanent tab has nothing
        to close."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(form, 1)
        row = QHBoxLayout()
        apply_btn = QPushButton(_("Apply"))
        apply_btn.clicked.connect(form.apply)
        redraw_btn = QPushButton(_("Redraw"))
        redraw_btn.clicked.connect(form.redraw)
        row.addWidget(apply_btn)
        row.addWidget(redraw_btn)
        row.addStretch(1)
        lay.addLayout(row)
        # A NodeFormWidget's Redraw only makes sense for a kind
        # _redraw_edited_node actually knows how to place — direct --only for
        # most kinds, forest-content-activation for "module"
        # (_REDRAWABLE_NODE_KINDS) — same guard _NodeDialog's modal Redraw
        # button has (_update_redraw_state). This embedded row lacked it
        # entirely until 2026-09-07 (found live: clicking Redraw on a module
        # node crashed run_single_node_redraw_worker with "--only: names not
        # found" before the forest-content routing existed). AnchorFormWidget
        # has no kind_combo — its own Redraw is a different mechanism,
        # untouched here.
        if isinstance(form, NodeFormWidget):
            def _sync_redraw_enabled() -> None:
                kind = form.kind_combo.currentData()
                redraw_btn.setEnabled(form._dock is not None
                                      and kind in _REDRAWABLE_NODE_KINDS)
            form.kind_combo.currentIndexChanged.connect(_sync_redraw_enabled)
            _sync_redraw_enabled()
        return page

    def _build_anchor_form(self, tree: Tree) -> "AnchorFormWidget":
        """An AnchorFormWidget for `tree` — parent is the DOCK (not the page
        widget) so _dock resolves to the dock that owns _mark_dirty; addWidget
        below reparents visually without touching _dock."""
        return AnchorFormWidget(
            self, self._all_ref_candidates(), cfg=self._cfg,
            sheet_names=self._ctx.sheet_names if self._ctx is not None else {},
            role_candidates=self._live_roles(),
            cluster_candidates=self._live_clusters(),
            existing=tree.anchor, tree=tree,
            adapter=self._live_adapter(), all_trees=self._trees)

    def _build_node_form(self, tree: Tree, node: TreeNode) -> "NodeFormWidget":
        """A NodeFormWidget (EDIT mode, existing=node) for the master-detail
        Node tab — same candidate wiring as _prompt_node's Edit branch, plus the
        Parent combo's rows (Э1, plan_2026_09_12_node_dialog_usability)."""
        return NodeFormWidget(
            self, self._all_ref_candidates(), self._used_refs(), _("Edit node"),
            cfg=self._cfg, adapter=self._live_adapter(),
            sheet_names=self._ctx.sheet_names if self._ctx is not None else {},
            tree=tree, parent_node=self._find_parent(tree, node), existing=node,
            module_candidates=self._module_tree_candidates(tree),
            all_trees=self._trees,
            role_candidates=self._live_roles(),
            cluster_candidates=self._live_clusters(),
            parent_candidates=self._node_parent_candidates(tree, node))

    @staticmethod
    def _embedded_form_of(page: Optional[QWidget]) -> Optional[QWidget]:
        """The modal-agnostic form inside a _form_action_row wrapper page (the
        wrapper's top VBox puts the form at itemAt(0), the Apply/Redraw row at
        itemAt(1)), or None when `page` is not a wrapper (the read-only instance
        stub QLabel, or the §3a placeholder QWidget)."""
        if page is None:
            return None
        lay = page.layout()
        if lay is None:
            return None
        item = lay.itemAt(0)
        return item.widget() if item is not None else None

    @staticmethod
    def _discard_if_touched(form: Optional[QWidget]) -> bool:
        """design §9.4: True when `form` (the editor currently shown in the
        panel) carries unapplied edits and is about to be replaced/discarded.
        The caller surfaces the non-blocking notice; the replacement still
        happens."""
        return bool(form is not None and getattr(form, "_touched", False))

    def _current_panel_node_ref(self, panel: QStackedWidget) -> Optional[str]:
        """The ref of the real node whose editor the panel currently shows, or
        None for the anchor form / a stub / the placeholder — keeps the §7.1.5
        rebuild guard in sync with the form actually on screen."""
        form = self._embedded_form_of(self._panel_page(panel))
        existing = getattr(form, "_existing", None) if form is not None else None
        return existing.ref if isinstance(existing, TreeNode) else None

    def _select_anchor_row(self, tree: Optional[Tree] = None) -> None:
        """Z.3: 'Set anchor…' is a shortcut — select the tree's ROOT row (now
        selectable, Z.2) so the single right-hand panel shows the tree ANCHOR
        form. Falls back to re-filling the panel directly when the row is not
        built yet (headless/partial state)."""
        tree = tree if tree is not None else self._current_tree()
        tree_widget = (self._tree_widget_for(tree) if tree is not None else None) \
            or self._current_tree_widget()
        root = tree_widget.invisibleRootItem() if tree_widget is not None else None
        if root is not None and root.childCount():
            tree_widget.setCurrentItem(root.child(0))
            return
        panel = self._active_form_panel()
        if panel is not None and tree is not None:
            self._set_panel_content(
                panel, self._form_action_row(self._build_anchor_form(tree)))
            self._current_node_ref = None
            panel.setProperty("_panel_built", True)

    def _read_only_stub(self, inst: TreeInstance) -> QLabel:
        """The read-only instance notice (plan §7.1.4) — the SAME wording as the
        instance context menu, shown as the panel's content instead of an editor
        for a generated tree."""
        label = QLabel(
            _("Instance of {template} — read-only: edit the template tree to "
              "change the geometry").format(template=inst.template))
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _warn_rebuild_discard(self) -> None:
        """design §9.4/§7.1.2: _rebuild_tabs() is about to clear every page, so
        a touched form on the ACTIVE page is discarded — surface a non-blocking
        notice BEFORE clear() takes it away (the lazy panel only ever hosts ONE
        form for the active page, so this is one cheap read)."""
        panel = self._active_form_panel()
        if panel is None:
            return
        if self._discard_if_touched(
                self._embedded_form_of(self._panel_page(panel))):
            self._show_status(_("Unapplied changes were discarded."))

    def _rebuild_active_form_panel(self) -> None:
        """(Re)build the right-hand FORM PANEL of the CURRENT page. Lazy: only
        ever touches the active page, and a page that was ALREADY built is NOT
        rebuilt on re-activation — its form lives inside the page and survives a
        tree switch, so an unapplied draft is never lost by merely tabbing away
        (a structural edit that calls _rebuild_tabs() is what actually discards
        it, and _warn_rebuild_discard() reports that at the point of loss).

        The panel shows ONE form, chosen by the SELECTION (Z.3.2): a real node ->
        that node's editor; the root row / nothing selected -> the tree anchor
        form (it always exists); a generated instance -> the read-only stub."""
        panel = self._active_form_panel()
        if panel is None:
            return
        if bool(panel.property("_panel_built")):
            self._current_node_ref = self._current_panel_node_ref(panel)
            return
        self._current_node_ref = self._fill_form_panel(panel)
        panel.setProperty("_panel_built", True)

    def _fill_form_panel(self, panel: QStackedWidget) -> Optional[str]:
        """Set `panel`'s content to the form the CURRENT tree + selection call
        for; returns the ref of the node whose editor is shown (None for the
        anchor form / the read-only stub / no tree)."""
        tree = self._current_tree()
        if tree is None:
            return None
        inst = self._instance_of(tree)
        if inst is not None:
            # Read-only instance (plan §7.1.4): never an editor — a generated
            # tree's anchor/nodes belong to the template + the declaration.
            self._set_panel_content(panel, self._read_only_stub(inst))
            return None
        node = self._selected_real_node(tree)
        if node is not None:
            self._set_panel_content(
                panel, self._form_action_row(self._build_node_form(tree, node)))
            return node.ref
        self._set_panel_content(
            panel, self._form_action_row(self._build_anchor_form(tree)))
        return None

    def _selected_real_node(self, tree: Tree) -> Optional[TreeNode]:
        """The currently selected REAL TreeNode of `tree`'s widget, or None —
        pseudo-roots (anchor, "⇐ embedded in", "→ instance:") carry a str /
        nothing and are not editable nodes."""
        tree_widget = self._tree_widget_for(tree)
        if tree_widget is None:
            return None
        items = tree_widget.selectedItems()
        if not items:
            return None
        node = items[0].data(0, Qt.ItemDataRole.UserRole)
        return node if isinstance(node, TreeNode) else None

    def _tree_widget_for(self, tree: Tree) -> Optional[QTreeWidget]:
        """The left QTreeWidget of the page whose tab index matches `tree`."""
        try:
            idx = self._trees.index(tree)
        except ValueError:
            return None
        return self._tree_widget_of_page(self.tree_tabs.widget(idx))

    def _refresh_form_panel(self) -> bool:
        """Selection changed on the active tree -> show the form the new
        selection calls for (a real node's editor, or the tree anchor form for
        the root row / an empty selection). Returns True when a TOUCHED editor
        was discarded (design §9.4) so the caller can surface the non-blocking
        notice after its own status write.

        §7.1.5: a refresh is skipped when the newly selected REAL node is the one
        already shown (`self._current_node_ref`) — a "Redraw selected" checkbox
        toggle re-selects the same row and must not tear the form down (or flash
        a discard notice) on every click. The None→None case (root row / cleared
        selection) is likewise left alone, so clicking the root does not rebuild
        the anchor form under the user.

        §7.1.4: a node of a generated INSTANCE tree is read-only — the panel gets
        the same stub as the instance context menu, never an editor."""
        panel = self._active_form_panel()
        if panel is None:
            return False
        tree = self._current_tree()
        if tree is None:
            return False
        node = self._selected_real_node(tree)
        new_ref = node.ref if node is not None else None
        if new_ref == self._current_node_ref:
            return False  # same node / still the anchor form — keep the form
        discarded = self._discard_if_touched(
            self._embedded_form_of(self._panel_page(panel)))
        self._current_node_ref = self._fill_form_panel(panel)
        panel.setProperty("_panel_built", True)
        return discarded

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
        """True when the tree's explicit (anchor (ref "...")) RECORD anchor
        points at its OWN single top-level placement node — the self-reference
        combination that can never resolve (plan 2026-08-31 anchor_self_ref_
        guard §3). Only an explicit, non-external ref anchor is a candidate:
        origin/self/role/point anchors carry no ref, an external refdes is not
        an Entity record by construction, and _root_entity_ref already enforces
        the EXACTLY ONE rule (empty / multi-top-level / non-placement roots are
        untouched). The SANCTIONED way to anchor on that component is the
        dedicated (self) mode, which resolves it LIVE."""
        anchor = tree.anchor
        if (anchor is None or anchor.is_self or anchor.is_origin
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
        (never "sometimes useful"), so silently switch such an anchor to Self
        (Denis: quiet auto-replace as the fallback) + a non-intrusive
        log/status-bar notice instead of a modal."""
        for tree in self._trees:
            if not self._is_self_ref_anchor(tree):
                continue
            ref = tree.anchor.ref
            message = _("Anchor for tree {name!r}: a ref anchor pointing at "
                        "its own root Entity {ref!r} never resolves "
                        "(self-reference) — switched to Self.").format(
                            name=tree.name, ref=ref)
            logger.info(message)
            self._show_status(message)
            tree.anchor = TreeAnchor(is_self=True)

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
        idx = self.tree_tabs.currentIndex()
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
            # §3.3: 'Edit node…' no longer opens the modal — a single click on
            # the node already shows its editor on the master-detail Node tab;
            # this action is a shortcut that selects the node and focuses that
            # tab (a right-click does not select by default).
            menu.addAction(_("Edit node…")).triggered.connect(
                lambda: self._edit_in_panel(tree, node))
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
            # Z.3: 'Set anchor…' no longer opens a modal picker — it selects the
            # tree's ROOT row (now selectable, Z.2), which makes the single
            # right-hand panel show the active tree's anchor form.
            menu.addAction(_("Set anchor…")).triggered.connect(
                lambda: self._select_anchor_row(tree))
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

    # ── Overlay circles of the tree's anchor / base (З, 2026-09-12) ────────
    # The DRAWING half lives on AnchorFormWidget (the form owns the toggle
    # button, next to the anchor/pivot/angle rows it visualises); these are the
    # CLEANUP entry points the dock needs because a circle must go when the
    # thing it points at is replaced (a root switch, a rename, leaving the
    # tree). Every one of them derives what to drop from the OWNER'S MAP, never
    # from a widget flag that could drift, and drops state on the UI thread
    # while the shapes go on a worker (the cell_anchor_view.cleanup() split).

    def _refresh_markers_button(self) -> None:
        """Ask the ACTIVE anchor form (when one is shown) to re-read the map —
        its toggle label follows the fact, and a root switch / rename changes
        the fact without the form being re-created."""
        form = self._embedded_form_of(self._active_form_page())
        if isinstance(form, AnchorFormWidget):
            form._refresh_show_markers_button()

    def _clear_tree_markers(self, name: str) -> None:
        """Drop ONE tree's two overlay keys (a rename, or leaving the tree):
        state on the UI thread, the shapes themselves on a worker. A tree whose
        markers were never shown is a no-op; without a live board the keys are
        still dropped and the leftover shapes become orphans — exactly the
        accepted outcome of Ж.2.3 (the owner's reconcile counts them)."""
        if getattr(self._main_window.connection, "long_op_active", False):
            return  # never interleave two IPC ops on the shared kipy REQ socket
        uuids: list = []
        for key in _tree_marker_keys(name):
            uuid = overlay_markers.owner.forget_key(key)
            if uuid:
                uuids.append(uuid)
        if not uuids:
            return
        self._refresh_markers_button()
        adapter = self._live_adapter()
        if adapter is None:
            return
        self._active_op = start_long_op(
            self._main_window.connection, (), board_overlay.remove_overlay,
            lambda _result: None, lambda _message: None, adapter, uuids)

    def _clear_other_tree_markers(self) -> None:
        """Keep only the CURRENT tree's circles (З.2.5, a tree switch): every
        other tree's anchor/base keys are dropped. What to drop is derived from
        the owner's MAP, so a tree whose form was never opened here is covered
        too."""
        if getattr(self._main_window.connection, "long_op_active", False):
            return
        current = self._current_tab_tree_name()
        keep = set(_tree_marker_keys(current)) if current is not None else set()
        doomed = [
            key for key in overlay_markers.owner.keys()
            if key not in keep
            and (key.startswith(_TREE_ANCHOR_NS + "/")
                 or key.startswith(_TREE_BASE_NS + "/"))]
        uuids: list = []
        for key in doomed:
            uuid = overlay_markers.owner.forget_key(key)
            if uuid:
                uuids.append(uuid)
        self._refresh_markers_button()
        adapter = self._live_adapter()
        if not uuids or adapter is None:
            return
        self._active_op = start_long_op(
            self._main_window.connection, (), board_overlay.remove_overlay,
            lambda _result: None, lambda _message: None, adapter, uuids)

    def _clear_all_tree_markers(self) -> None:
        """Drop BOTH tree-marker namespaces entirely (an actual root switch,
        З.2.5): every key belonged to the previous project. State now, shapes
        on a worker."""
        if getattr(self._main_window.connection, "long_op_active", False):
            return
        uuids: list = []
        for namespace in (_TREE_ANCHOR_NS, _TREE_BASE_NS):
            uuids.extend(overlay_markers.owner.forget_scope(namespace))
        self._refresh_markers_button()
        adapter = self._live_adapter()
        if not uuids or adapter is None:
            return
        self._active_op = start_long_op(
            self._main_window.connection, (), board_overlay.remove_overlay,
            lambda _result: None, lambda _message: None, adapter, uuids)

    def set_snapshot_refresher(self, refresher) -> None:
        """Injected once by DockHub at construction (S.3.2,
        plan_2026_09_11_stale_snapshot_role_lists.md):
        ``DockHub.refresh_snapshot_and_push`` — the ONE "rebuild the board
        snapshot, then distribute the fresh lists" operation. Injected rather
        than reached for through ``main_window._dock_hub``, so this dock keeps
        talking only to ``main_window.connection``."""
        self._snapshot_refresher = refresher

    def _refresh_snapshot_then(self, on_ready) -> None:
        """T1 — for the TREE surfaces the trigger is the DIALOG open: every
        flow below fills Role/Cluster CANDIDATES by reading
        ``main_window.connection.snapshot`` lazily at the moment the dialog is
        built. That snapshot freezes at connect/manual-refresh time (the
        automatic poll tick is a deliberate no-op once connected), so it is
        rebuilt FIRST — on the worker thread, never a direct adapter call on
        the UI thread (the Commit H hang) — and ``on_ready`` (which builds and
        execs the dialog) then runs on the UI thread with fresh candidates.

        No refresher injected (a standalone dock), or no live board behind the
        connection (the docks' own stand-ins, an offline session): ``on_ready``
        runs at once on the cached snapshot — the exact previous behaviour."""
        refresher = getattr(self, "_snapshot_refresher", None)
        if refresher is None:
            on_ready()
            return
        refresher(on_ready)

    def refresh_known_lists(self) -> None:
        """Feed the LIVE known-value lists into the Role/Cluster suggestion
        combos of the embedded node/anchor FORMS currently on screen — the
        lists this dock owns. Called by ``DockHub.push_known_lists`` (and
        therefore by ``push_snapshot`` too): S.3.2 — this dock used to take no
        part in the snapshot distribution at all and read
        ``connection.snapshot`` directly.

        No snapshot argument: it re-reads the same live cache through
        ``_live_roles()``/``_live_clusters()`` (its documented source), exactly
        as the dialogs do — so a caller only has to guarantee the rebuild
        happened first (DockHub.refresh_snapshot_and_push).

        Deliberately does NOT rebuild any page: the embedded forms are edited
        in place, and a rebuild would discard an in-progress edit (design §9.4,
        ``_discard_if_touched``). The combos are repopulated through
        ``set_combo_items``, which preserves whatever the user already typed
        ("populate, don't restrict")."""
        roles = self._live_roles()
        clusters = self._live_clusters()
        for index in range(self.tree_tabs.count()):
            # P.3 (plan_2026_09_11_pivot_ref_mount_ancestor): the tab widget is
            # the PAGE QSplitter, NOT the form wrapper — reaching the form means
            # page -> panel -> current page -> embedded form, the same unwrap
            # _active_form_panel/_panel_page use. Passing the splitter straight
            # to _embedded_form_of found nothing, so set_candidates never ran
            # (the lists stayed frozen at connect time).
            panel = self._form_panel_of_page(self.tree_tabs.widget(index))
            if panel is None:
                continue
            form = self._embedded_form_of(self._panel_page(panel))
            if isinstance(form, (NodeFormWidget, AnchorFormWidget)):
                form.set_candidates(roles, clusters)

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
            # kind=="module" candidate wiring (2026-09-07 fix): mirrors
            # _build_node_form's Edit-mode wiring exactly — without these two,
            # the module Ref combo and "From child node..." are always empty,
            # regardless of how many trees actually exist (found live).
            module_candidates=self._module_tree_candidates(tree),
            all_trees=self._trees,
            # Position-tab (mount anchor) candidate lists — the same live
            # sources the mount anchor picker uses (plan tree_node_own_anchor
            # §3.1 — the plan NAME is historical, the mechanism is "mount" now).
            role_candidates=self._live_roles(),
            cluster_candidates=self._live_clusters(),
            # Э1 (plan_2026_09_12_node_dialog_usability): the Parent combo's
            # rows — EDIT mode only. A new node's parent is decided by the
            # context-menu action that opened this dialog (Add node / Add child
            # / Add sibling), so Add mode has nothing to re-hang.
            parent_candidates=(self._node_parent_candidates(tree, existing)
                               if existing is not None else None),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        # Э2 (plan_2026_09_12_node_dialog_usability): the OK button validated the
        # form BEFORE accepting, so this is the node it built — validation does
        # NOT run here any more and the user gets no second QMessageBox.
        node = dialog.build_node()
        if node is None:
            # Still honoured for a caller that bypassed OK (exec() stubbed by a
            # test): build_node() reports the problem itself and returns None —
            # treat it as a cancel, never dereference it below. A None node used
            # to crash on node.ref (found live 2026-09-02, AttributeError in
            # _add_node_flow -> whole GUI died).
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
        """T1 (S.3.2): the node dialog lists live Role/Cluster candidates, so
        rebuild the snapshot before it opens (see _refresh_snapshot_then)."""
        self._refresh_snapshot_then(
            lambda: self._add_child_flow_now(tree, parent))

    def _add_child_flow_now(self, tree: Tree, parent: TreeNode) -> None:
        node = self._prompt_node(_("Add child"), tree, parent_node=parent)
        if node is not None:
            parent.children.append(node)
            self._mark_dirty()
            self._rebuild_tabs()

    def _add_sibling_flow(self, tree: Tree, sibling: TreeNode) -> None:
        """T1 — same as _add_child_flow (fresh Role/Cluster candidates)."""
        self._refresh_snapshot_then(
            lambda: self._add_sibling_flow_now(tree, sibling))

    def _add_sibling_flow_now(self, tree: Tree, sibling: TreeNode) -> None:
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
        """T1 — same as _add_child_flow (fresh Role/Cluster candidates)."""
        self._refresh_snapshot_then(lambda: self._add_node_flow_now(tree))

    def _add_node_flow_now(self, tree: Tree) -> None:
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
            # Connection state, not user input — a Log line, never a modal
            # (plan_2026_09_11_no_modals_and_busy_kicad X.1). The node is
            # still left untouched (nothing below runs).
            show_message(
                _("No live board connection — connect KiCad first."),
                _ERROR_STYLE, logger)
            return
        try:
            offset_mm, rotation = _resolve_live_offset(
                self._cfg, adapter,
                self._ctx.sheet_names if self._ctx is not None else {},
                tree, self._find_parent(tree, node), node.ref, node.kind,
                # A MOUNT node's xy/polar are defined relative to its OWN
                # anchor, not the parent — reread against the same base the
                # node is authored against (plan_2026_09_11_tree_mount_nodes).
                base_anchor=node.anchor if node.kind == "mount" else None)
        except ValidationError as e:
            QMessageBox.warning(self, _("Reread current position"), str(e))
            return
        node.xy = (offset_mm[0], offset_mm[1])
        node.polar = None
        if rotation is not None:
            node.rotation = rotation
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
        Apply, this phase does not soften it.

        "module" is special-cased (2026-09-07, Denis: Redraw on a module node
        should activate its content, not just be blocked): node.ref names an
        EMBEDDED TREE, which --only can never resolve, so this routes through
        the SAME forest-content-activation machinery the "Full redraw" menu
        action uses (run_curated_forest_redraw_worker), scoped to just this
        one marker via selected_refs={node.ref} — its content is placed from
        the owning tree's LIVE anchor, nothing else in that tree moves."""
        if node is None or not node.ref or self._cfg is None or self._ctx is None:
            return
        if node.kind == "module":
            payload = {
                "config_path": str(self._root_path) if self._root_path else "",
                "cfg": self._cfg,
                "ctx": self._ctx,
                "trees": self._trees,
                "selected_refs": {node.ref},
            }
            self._active_op = start_long_op(
                self._main_window.connection, (),
                run_curated_forest_redraw_worker, self._finish_redraw,
                self._on_redraw_failed, payload)
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

    def _move_node_flow(self, tree: Tree, node: TreeNode) -> None:
        """FORK-C: a parent-picker dialog, no drag&drop. The candidate list
        excludes the node itself and its own descendants (a structural
        invariant — you cannot move a node into its own subtree) and, when the
        node IS the tree's pivot-ref, every mount node with its whole subtree:
        a re-hang there pins the node's base to a LIVE component and the LOADER
        would refuse the config at the next load (kicadstamp.trees
        _validate_tree_pivot_ref, reason "mount-ancestor"). Both rules are
        enforced by CONSTRUCTION through the shared _pivot_ref_mount_parent —
        the same single expression of the pivot-ref rule the form's combo uses,
        never a second copy of it."""
        forbidden = self._collect_subtree(node)
        candidates: list[tuple[str, Optional[TreeNode]]] = [(_("(top level)"), None)]
        for top in tree.nodes:
            self._collect_move_candidates(top, forbidden, candidates)
        candidates = [row for row in candidates
                      if _pivot_ref_mount_parent(tree, node, row[1]) is None]

        labels = [label for label, _t in candidates]
        choice, ok = QInputDialog.getItem(
            self, _("Move to…"), _("New parent:"), labels, 0, False)
        if not ok:
            return
        new_parent = candidates[labels.index(choice)][1]
        if new_parent is node or self._in_list(new_parent, forbidden):
            return  # structural invariant — the dialog never offered it
        pinned = _pivot_ref_mount_parent(tree, node, new_parent)
        if pinned is not None:
            # Never offered above, so reaching this means the dialog was
            # bypassed: refuse instead of writing a tree the loader will kill.
            logger.warning(
                "Refusing to move %r under mount node %r: %r is the tree's "
                "pivot-ref and a node under a mount node does not follow the "
                "tree", node.ref, pinned.ref, node.ref)
            return
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

    # ── Node form's Parent combo (Э1, plan_2026_09_12_node_dialog_usability) ──

    def _node_parent_candidates(self, tree: Tree, node: TreeNode
                                ) -> list[tuple[str, Optional[TreeNode]]]:
        """The (label, parent) rows of the node form's Parent combo: "(top
        level)", every MOUNT node of this tree (by ref — a mount node is the
        re-hang target the plan asks for) and the node's CURRENT parent when it
        is none of those.

        Both structural rules are enforced by CONSTRUCTION — a row the combo
        never offers cannot be picked:

        * the node itself and its whole subtree are excluded (a node cannot
          become its own ancestor);
        * when the node is the tree's `pivot_ref`, NO mount node is offered: a
          node under a mount ancestor has a LIVE base (mount_node_base), does
          not follow the tree, and `_validate_tree_pivot_ref` would refuse the
          config at the next load (plan_2026_09_11_pivot_ref_mount_ancestor
          §P.1.3 — the SAME predicate, `_pivot_ref_rejection`, is consulted
          here, never a second copy of the rule).

        The current parent is listed even when it is not a mount node, or the
        combo would lie about where the node hangs today and the next Apply
        would silently move it."""
        forbidden = self._collect_subtree(node)
        barred_mounts = tree.pivot_ref is not None and tree.pivot_ref == node.ref
        candidates: list[TreeNode] = []
        for top in tree.nodes:
            self._collect_parent_candidates(top, forbidden, barred_mounts, candidates)
        out: list[tuple[str, Optional[TreeNode]]] = [(_("(top level)"), None)]
        out.extend((candidate.ref, candidate) for candidate in candidates)
        current = self._find_parent(tree, node)
        if (current is not None and not self._in_list(current, candidates)
                and not self._in_list(current, forbidden)):
            out.append((_("{ref} (current parent)").format(ref=current.ref), current))
        return out

    @classmethod
    def _collect_parent_candidates(cls, node: TreeNode, forbidden: list[TreeNode],
                                   barred_mounts: bool, out: list[TreeNode]) -> None:
        """Pre-order collect of the MOUNT nodes of a tree that may serve as a
        parent — see _node_parent_candidates for the two rules `forbidden` and
        `barred_mounts` carry."""
        if (node.kind == "mount" and not barred_mounts
                and not cls._in_list(node, forbidden)):
            out.append(node)
        for child in node.children:
            cls._collect_parent_candidates(child, forbidden, barred_mounts, out)

    def _reparent_node(self, tree: Tree, node: TreeNode,
                       new_parent: Optional[TreeNode]) -> bool:
        """Move `node` (with its whole subtree) under `new_parent` (None = the
        top level) in `tree` — the STRUCTURAL half of the Node form's Parent
        combo (Э1); the offset that keeps the node physically still belongs to
        the form, which has already re-expressed it through the new parent's
        base.

        Removal is by IDENTITY: TreeNode is a dataclass with value equality, so
        list.remove() could drop a different-but-equal sibling (every other
        structural mutator has the same latent trap; this one does not).

        The rebuild is DEFERRED by one event-loop turn: this runs from inside
        the embedded form's apply(), and rebuilding the page here would destroy
        that very form mid-call (the panel's content is replaced wholesale).
        One turn later the apply has returned and the form is no longer
        `_touched`, so the rebuild is silent — no discard warning.

        Both structural invariants are re-checked here, whoever the caller is
        (the combo enforces them by construction, so a violation means a
        programming error): the node's own subtree (a cycle) and, for the
        tree's pivot-ref, any parent that would pin its base to a LIVE
        component (the loader's own `_pivot_ref_rejection` rule, times a
        hypothetical parent). Returns True when the tree really changed, False
        when the call was refused or was a structural no-op — the caller must
        not touch the node's stored coordinates on False."""
        if new_parent is not None and self._contains_node(node, new_parent):
            # A programming error, not a user path: the combo never offers the
            # node's own subtree. Refuse structurally rather than corrupt the
            # tree into a cycle.
            logger.warning(
                "Refusing to re-hang %r under its own descendant %r",
                node.ref, new_parent.ref)
            return False
        pinned = _pivot_ref_mount_parent(tree, node, new_parent)
        if pinned is not None:
            # Same discipline: writing this tree would make the NEXT config
            # load a fatal ("mount-ancestor"), silently until then.
            logger.warning(
                "Refusing to re-hang %r under mount node %r: %r is the tree's "
                "pivot-ref and a node under a mount node does not follow the "
                "tree", node.ref, pinned.ref, node.ref)
            return False
        old_parent = self._find_parent(tree, node)
        if old_parent is new_parent:
            return False
        siblings = tree.nodes if old_parent is None else old_parent.children
        for index, candidate in enumerate(siblings):
            if candidate is node:
                del siblings[index]
                break
        if new_parent is None:
            tree.nodes.append(node)
        else:
            new_parent.children.append(node)
        self._mark_dirty()
        QTimer.singleShot(0, self._rebuild_tabs)
        return True

    def _on_create_tree(self) -> None:
        """T1 (S.3.2, plan_2026_09_11_stale_snapshot_role_lists.md): the anchor
        dialog below lists live Role/Cluster candidates, so the snapshot is
        rebuilt on the worker thread BEFORE it opens — see
        _refresh_snapshot_then."""
        self._refresh_snapshot_then(self._on_create_tree_now)

    def _on_create_tree_now(self) -> None:
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
        self.tree_tabs.setCurrentIndex(len(self._trees) - 1)

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
        — a self anchor (a component the tree places itself, incl. an absent
        (anchor ...)) is the tree's own reference, not a fixed external point,
        so "position relative to the tree anchor" is meaningless for it. The
        Instantiate-from-Cell flow must refuse a self anchor in ANY positioning
        mode (plan instantiate_from_entity §1.5)."""
        return tree.anchor is not None and not tree.anchor.is_self

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

    def _instantiate_from_cell(self, selected, raw_items=()) -> None:
        """T1 (S.3.2, plan_2026_09_11_stale_snapshot_role_lists.md): the
        Instantiate dialog lists the current selection's fully-selected
        clusters AND the live Cluster candidates, so the snapshot is rebuilt on
        the worker thread BEFORE it opens — see _refresh_snapshot_then."""
        self._refresh_snapshot_then(
            lambda: self._instantiate_from_cell_now(selected, raw_items))

    def _instantiate_from_cell_now(self, selected, raw_items=()) -> None:
        """Add ONE new group into the CURRENT tree (2026-09-03, plan
        instantiate_from_entity; second tab 2026-09-04, plan
        instantiate_new_cell_from_selection). The group's internal layout comes
        either from an EXISTING Cell (tab 1) or from a NEW Cell extracted right
        from the current selection (tab 2, STRICT full-selection semantics —
        only a FULLY selected cluster is ever extracted, a partial selection is
        never captured silently). Collects the decision in the
        InstantiateCellDialog, stages the NEW Entity (no refs — roles resolve
        at Apply by cluster/sheet) and, on tab 2, the NEW Cell, through
        config_writer, and appends a top-level placement node (xy relative to
        the tree anchor). Everything is staged via WORKING_SET — nothing
        reaches disk until the global Save (see the plan's §3)."""
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
        # Strict tab-2 source: the FULLY-selected clusters of the current
        # selection (the same detection "Extract cluster..." uses). A partial
        # selection is never offered for extraction (2026-09-04, Denis's
        # decision — cf. plan_2026_09_03_fpga_oscill_missing_copper_and_
        # cell_import.md).
        fully_selected: list = []
        fully_selected_diagnostics = ""
        if self._ctx is not None:
            from .extract_diagnostics import format_cluster_rejections
            from .reead import fully_selected_clusters
            sheet_names = self._ctx.sheet_names or {}
            rejections: list = []
            fully_selected = fully_selected_clusters(
                list(selected or []), list(snapshot),
                list(self._cfg.entities), (), sheet_names=sheet_names,
                rejections=rejections)
            fully_selected = [c for c in fully_selected
                              if c.cluster and "\n" not in c.cluster]
            # V.2 (plan_2026_09_11_extract_selection_diagnostics): the SAME
            # concrete cause the two Extract flows show, so tab 2's strict gate
            # no longer blames the selection alone.
            fully_selected_diagnostics = format_cluster_rejections(rejections)
        dialog = InstantiateCellDialog(
            self, self._cfg,
            cells=sorted(self._cfg.cells),
            sheets=self._live_sheets(),
            clusters=self._live_clusters(),
            selected=list(selected or []),
            snapshot=list(snapshot),
            fully_selected=[(c.cluster, c.sheet) for c in fully_selected],
            fully_selected_diagnostics=fully_selected_diagnostics)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entity_name = dialog.entity_name()
        cell_name = dialog.result_cell()
        cluster = dialog.cluster()
        sheet = dialog.sheet()
        if dialog.is_new_cell():
            # Tab 2 — extract a NEW Cell right from the current selection's ONE
            # fully-selected cluster (the dialog only enables OK in that case).
            adapter = self._live_adapter()  # lazy: tab 1 stays usable offline
            if adapter is None:
                QMessageBox.warning(self, _("Instantiate from Cell"),
                                    _("Not connected."))
                return
            if len(fully_selected) != 1:
                # Defensive — the dialog gates OK on exactly one; nothing staged.
                QMessageBox.warning(
                    self, _("Instantiate from Cell"),
                    _("Select exactly ONE fully selected Cluster — the new Cell "
                      "is extracted from exactly one cluster."))
                return
            c = fully_selected[0]
            if cell_name in self._cfg.cells:
                # A same-named Cell would be silently overwritten — refuse.
                QMessageBox.warning(
                    self, _("Instantiate from Cell"),
                    _("A cell named {name!r} already exists.").format(name=cell_name))
                return
            from .tree_from_selection import extract_new_cell_for_instantiation
            origin_role, origin_pad = dialog.origin_override()
            cell_dict = extract_new_cell_for_instantiation(
                adapter, c, cell_name, selected, raw_items,
                absolute=dialog.absolute_origin(),
                origin_role=origin_role, origin_pad=origin_pad)
            if cell_dict is None:
                QMessageBox.warning(
                    self, _("Instantiate from Cell"),
                    _("Failed to extract the new Cell from the selection — "
                      "see the log."))
                return
            # Strict addressing: the new Entity points at the cluster the Cell
            # was extracted from (the dialog validated the shared combos match
            # the detected cluster — see InstantiateCellDialog.validate).
            cluster = c.cluster
            sheet = c.sheet
            # Stage the NEW Cell before the entity write — the same
            # read_data/write_data read-merge-write path _stage_trees uses
            # (WORKING_SET-aware; no per-edit backup — the flush backs up to
            # history/, cf. _stage_trees's docstring).
            try:
                data = read_data(self._root_path)
                data.setdefault("cells", {})[cell_name] = cell_dict[cell_name]
                write_data(self._root_path, data)
            except Exception as e:  # noqa: BLE001 — history/.bak is fresh; report
                QMessageBox.warning(
                    self, _("Instantiate from Cell"),
                    _("Failed to save the new Cell: {error}").format(error=e))
                return
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
        old_name = tree.name
        tree.name = new_name
        if old_name != new_name:
            # З.2.5: the overlay keys are named after the tree, so the old
            # name's circles point at a tree that no longer exists.
            self._clear_tree_markers(old_name)
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

    def _confirm_first_run_redraw(self) -> bool:
        """Bug 3 (2026-09-05) heads-up before a tree redraw: when this profile's
        registries are empty but the live board already carries copper, confirm
        that the redraw registers matching existing copper as owned (always-on
        adoption) — and hint to run once WITHOUT moving first. Returns True to
        proceed; silent True (no dialog) on steady-state runs, headless/GUI
        tests and disconnected adapters (see confirm_first_run_adoption)."""
        config_path = str(self._root_path) if self._root_path else ""
        return confirm_first_run_adoption(self, config_path,
                                          adapter=self._live_adapter())

    def _run_curated_redraw(self, selected_refs: set) -> None:
        """Shared worker invocation for "Redraw selected" and "Redraw whole
        tree" (plan_2026_08_29_fork1_rigid_redraw_override.md §5) — one
        implementation, only the selection source differs. start_long_op keeps
        it off the UI thread, same worker pattern as run_cascade_worker."""
        tree_name = self._current_tree_name()
        if tree_name is None:
            return
        if not selected_refs:
            self._show_status(_("Nothing selected — check some nodes first."))
            return
        if not self._confirm_first_run_redraw():
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
        if not self._confirm_first_run_redraw():
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


class NodeFormWidget(QWidget):
    """The node add/edit FORM (no modal wrapper): ref + kind + offset (xy/
    polar via AnchorOriginWidget) + rotation/name/group, plus a "Read current
    position" button that fills offset/rotation from the LIVE board relative
    to the parent base (the parent is decided by the caller, not here — design
    §3). `existing` switches to EDIT mode: every field is pre-filled and the
    "ref already used" check excludes the node's own ref. Modal-agnostic:
    phase B (design §9.4) — apply()/redraw() mutate the node in place and stay
    open; the OK/Cancel or Apply/Redraw/Close button row is added by the
    embedding context (_NodeDialog's thin wrapper, or the master-detail Node
    tab of plan_2026_09_04_trees_dock_master_detail.md §3).

    Extracted from the former _NodeDialog (plan §1.1); the class keeps the
    exact constructor signature and every field/method the Add/Edit flows use,
    just as a plain QWidget."""
    _dock: Optional["TreesDock"] = None
    _touched: bool = False

    def __init__(self, parent, ref_candidates: list[tuple[str, str]], used_refs: set[str],
                 title: str, cfg=None, adapter=None, sheet_names=None,
                 tree=None, parent_node=None, existing=None,
                 module_candidates=None, all_trees=None,
                 role_candidates=None, cluster_candidates=None,
                 parent_candidates=None):
        super().__init__(parent)
        # `title` is accepted for signature compatibility with the former
        # QDialog (its caller set the window title); a plain QWidget form has
        # no window of its own, so nothing is set here.
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
        self._cluster_candidates = list(cluster_candidates or [])
        # Э1 (plan_2026_09_12_node_dialog_usability): (label, TreeNode | None)
        # rows the Parent combo offers — collected by the DOCK
        # (_node_parent_candidates), the only place that knows the tree
        # structure and the pivot-ref rule. Empty in ADD mode.
        self._parent_candidates = list(parent_candidates or [])

        # Board-frame form state (plan_2026_09_11 §3): the base pose the
        # displayed offset/rotation are expressed against, resolved LIVE
        # LAZILY and INVALIDATED on every real anchor change (plan_2026_09_11_
        # node_form_base_frame_follows_anchor). None = no live base -> the form
        # shows the RAW stored values and (Edit mode) disables the fields, so a
        # rename+save offline can never corrupt them.
        self._base_resolved: bool = False
        # (Vector2 position_nm, float rotation_deg) of the current base, or None.
        self._base_pose_value = None
        # WHY the base did not resolve, as the exception's own text (Л.2.2) —
        # the form used to swallow the cause and blame the connection. None
        # means "not tried yet" or "resolved".
        self._base_error: Optional[str] = None
        # The pose the form was showing BEFORE the current anchor change,
        # captured once per change burst so the refresh can hold the node still
        # (U.1) without re-reading the old anchor.
        self._base_pose_before_change = None
        # The anchor the cached base was resolved for — a no-op guard so the
        # debounced refresh (and the mode-change + synthetic-field double fire)
        # never re-resolves the same anchor.
        self._last_anchor_sig = None

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
            # Display label from _KIND_TAGS when one exists (2026-09-07:
            # "module" reads as "tree" here too) — data stays the raw kind
            # value `k`, so currentData()/build_node() are unaffected.
            self.kind_combo.addItem(_KIND_TAGS.get(k, k), k)
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

        # ── Parent combo (Э1, plan_2026_09_12_node_dialog_usability) ────────
        # Re-hang an EXISTING node under another parent. The rows come from the
        # dock (this tree's mount nodes + the node's current parent + "(top
        # level)"); an ADD-mode form has none — which parent a NEW node gets is
        # decided by the context-menu action that opened this dialog, and there
        # is nothing to re-hang yet.
        self.parent_combo: Optional[QComboBox] = None
        if existing is not None and self._parent_candidates:
            self.parent_combo = QComboBox()
            for _label, _candidate in self._parent_candidates:
                self.parent_combo.addItem(_label, _candidate)
            index = self._parent_index_of(self._parent_node)
            if index < 0:
                # The caller's list did not carry the node's CURRENT parent:
                # add it, or the combo would claim the node hangs at the top
                # level and the next Apply would really move it there.
                self.parent_combo.addItem(
                    _("{ref} (current parent)").format(ref=self._parent_node.ref),
                    self._parent_node)
                index = self.parent_combo.count() - 1
            self.parent_combo.setCurrentIndex(index)
            form.addRow(_("Parent:"), self.parent_combo)

        # offset block — xy/polar only, reused from the shared widget (design §3).
        # The value is shown in the BOARD frame (x right, y down); the config
        # stores it in the BASE's local frame. The conversion lives in _prefill
        # (load) and build_node (save) — nowhere else (plan_2026_09_11 §3.1).
        self.offset_widget = AnchorOriginWidget(modes=["xy"], polar=True)
        form.addRow(_("Offset (board frame):"), self.offset_widget)
        # Why the offset/rotation fields below may be read-only (no live base).
        self.offset_frame_label = QLabel("")
        self.offset_frame_label.setWordWrap(True)
        self.offset_frame_label.setVisible(False)
        form.addRow("", self.offset_frame_label)

        # (2026-09-11, plan_2026_09_11_tree_settings_form §W.5): the per-NODE
        # pivot block that used to live here is GONE — the inner point is a
        # property of the TREE and is edited by AnchorFormWidget's "Tree
        # settings" group. `build_node` carries no pivot field at all.

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

        # ── Position tab: the offset base. 2026-09-11 (plan_2026_09_11_tree_
        # mount_nodes §Y.1/Y.2): a node's base is ALWAYS its parent — the old
        # per-node own_anchor picker is gone. The picker below now belongs to a
        # kind "mount" node ONLY (a POINT OF REFERENCE whose children hang from
        # the live component it names), so it is hidden for every other kind by
        # _on_kind_changed. The same shared widget as the tree anchor's role
        # mode: Role + optional Sheet/Cluster/Pad; Ref is meaningless for an
        # anchor base, so show_ref=False.
        position_widget = QWidget()
        position_form = QFormLayout(position_widget)
        self.mount_anchor_widget = AnchorOriginWidget(
            modes=("parent", "anchor"), anchor_fields=("sheet", "cluster", "pad"),
            show_ref=False, mode_labels={"anchor": _("Mount anchor (role)")})
        position_form.addRow(self.mount_anchor_widget)
        self.mount_anchor_widget.setVisible(False)
        self.mount_anchor_widget.set_known_roles(
            self._role_candidates, self._cluster_candidates)
        # J.3 (2026-09-10): the Sheet combo is fed from the CONFIG's sheet map
        # (RuntimeContext.sheet_names, built from the schematics), exactly like
        # AnchorFormWidget does — NOT from the ~2s board snapshot, whose
        # Selected.sheet was empty for every footprint (measured: 325
        # footprints, 0 sheet names), leaving this combo permanently blank.
        self.mount_anchor_widget.set_known_sheets(list(self._sheet_names.values()))

        self.tabs.addTab(general_widget, _("General"))
        self.tabs.addTab(position_widget, _("Position"))
        # The Position tab's index, for the ONE show/hide condition in
        # _on_kind_changed (Л.2.3). The tab is never REMOVED — only hidden — so
        # the index and tabs.count() stay stable for every caller/test walking
        # the tabs by index.
        self._position_tab_index = self.tabs.indexOf(position_widget)

        # The button row is NOT part of the form — plan
        # plan_2026_09_04_trees_dock_master_detail.md §1.1: a plain QWidget form
        # is modal-agnostic, the OK/Cancel (Add) or Apply/Redraw/Close (Edit)
        # row is added by the embedding context (_NodeDialog's thin wrapper, or
        # the master-detail Node tab of §3). What stays here: the form layout
        # (General/Position tabs) + the non-blocking apply-status label that
        # apply()/redraw() write to (design §9.4).
        self._dock = parent if getattr(parent, "_mark_dirty", None) else None
        self._touched = False
        self.apply_status_label = QLabel("")
        self.apply_status_label.setWordWrap(True)
        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        root.addWidget(self.apply_status_label)

        self.ref_combo.currentTextChanged.connect(self._update_read_button_state)
        self.kind_combo.currentIndexChanged.connect(self._update_read_button_state)

        if existing is not None:
            self._prefill(existing)   # calls _on_kind_changed() itself (kind
                                      # must be set BEFORE the ref combo is
                                      # repopulated; external clears its items)
        else:
            self._on_kind_changed()
            # Э4 (plan_2026_09_12_node_dialog_usability): ADD mode starts the
            # Cartesian offset at the parent's own origin — "the node sits
            # exactly on its base" is the common case, and an EMPTY xy field is
            # an ERROR (build() -> "X is required."), so expressing it used to
            # cost two hand-typed zeros. Three things stay untouched: EDIT mode
            # (the node's own values, loaded by _prefill above), the POLAR pair
            # (radius/angle stay blank — only the Cartesian fields are
            # pre-filled) and PlacerDock's for_highlight placeholder, which is
            # build()'s own (0, 0) substitution, in another consumer entirely.
            self.offset_widget.x_edit.setText("0")
            self.offset_widget.y_edit.setText("0")
        self._update_read_button_state()
        # design §9.4: after _prefill/_on_kind_changed populated the fields the
        # form is clean — _touched reflects only USER edits since the last
        # load()/Apply (the same _mark_touched signals fired by prefill must
        # not mark the fresh form dirty).
        self._touched = False
        # design §9.4 wiring (§3c — note_2026_09_04_touched_wiring_still_open.md):
        # every user-facing field flags the form as having unapplied changes.
        # Kind/ref/free-text edits feed _mark_touched directly; the three
        # AnchorOriginWidget sub-widgets (offset / pivot / own_anchor) expose a
        # single fieldChanged signal each (they are wired AFTER prefill, so a
        # freshly opened form stays clean until the user actually edits).
        self.kind_combo.currentIndexChanged.connect(self._mark_touched)
        self.ref_combo.currentTextChanged.connect(self._mark_touched)
        for _edit in (self.rotation_edit, self.name_edit, self.group_edit):
            _edit.textChanged.connect(self._mark_touched)
        for _origin in (self.offset_widget, self.mount_anchor_widget):
            _origin.fieldChanged.connect(self._mark_touched)
        # Э1 (plan_2026_09_12_node_dialog_usability): the Parent combo is a
        # FRAME change exactly like the mount anchor — a discrete action, so it
        # invalidates the cached base and re-expresses the displayed offset at
        # once (the same U.1 machinery that holds a node still when its base
        # changes). Wired HERE, after the prefill, so no signal fires while the
        # form is still being built.
        if self.parent_combo is not None:
            self.parent_combo.currentIndexChanged.connect(self._mark_touched)
            self.parent_combo.currentIndexChanged.connect(self._on_anchor_mode_changed)

        # Anchor change -> the frame the offset/rotation are expressed against
        # changes too (plan_2026_09_11_node_form_base_frame_follows_anchor).
        # modeChanged is a discrete action -> refresh right away; fieldChanged
        # fires per keystroke, so it only INVALIDATES the cache immediately
        # (cheap, no adapter call) and coalesces the actual re-resolve + display
        # refresh behind a short single-shot timer — one resolution per
        # committed anchor change, never one per character.
        self.mount_anchor_widget.modeChanged.connect(self._on_anchor_mode_changed)
        self.mount_anchor_widget.fieldChanged.connect(self._on_anchor_field_changed)
        self._anchor_refresh_timer = QTimer(self)
        self._anchor_refresh_timer.setSingleShot(True)
        self._anchor_refresh_timer.setInterval(250)
        self._anchor_refresh_timer.timeout.connect(self._refresh_for_new_anchor)

    def _mark_touched(self) -> None:
        """design §9.4: any user field edit flags the form as having unapplied
        changes — the discard-warning source for a constantly-open panel (the
        master-detail Node tab). Connected per-widget in the embedding
        context/§3; the flag itself is owned here."""
        self._touched = True

    def set_candidates(self, role_candidates=None, cluster_candidates=None) -> None:
        """S.3.2 (plan_2026_09_11_stale_snapshot_role_lists.md): refresh the
        Role/Cluster SUGGESTION lists of this form IN PLACE from a freshly
        rebuilt board snapshot. Nothing is rebuilt or staged and the
        typed/picked values are kept (set_known_roles -> set_combo_items
        preserves the current text), so an in-progress edit survives — this is
        the whole point of updating the lists instead of re-creating the form
        (design §9.4, _discard_if_touched)."""
        self._role_candidates = list(role_candidates or [])
        self._cluster_candidates = list(cluster_candidates or [])
        self.mount_anchor_widget.set_known_roles(
            self._role_candidates, self._cluster_candidates)

    def mount_anchor(self) -> TreeAnchor | None:
        """The MOUNT node's anchor (plan §Y.1.2): None when the picker is not
        in "anchor" mode or its validation failed, else the filled role-only
        TreeAnchor. This value is meaningful ONLY for kind == "mount" —
        build_node requires it there and ignores it otherwise (a non-mount
        node's base is simply its parent)."""
        fields, err = self.mount_anchor_widget.build()
        if err or not fields or fields.get("mode") != "anchor":
            return None
        if not fields.get("role"):
            # An incomplete mount anchor (no Role yet) counts as "no anchor" —
            # build_node turns it into a refusal, never a silently-filled one.
            return None
        return TreeAnchor(
            role=fields["role"], is_origin=False,
            anchor_sheet=fields.get("sheet"),
            anchor_cluster=fields.get("cluster"),
            anchor_pad=fields.get("pad"),
        )

    def _parent_index_of(self, node: Optional[TreeNode]) -> int:
        """The combo row that means `node`, or -1 when the list does not carry
        it. Identity, never equality: two nodes of one tree may hold equal field
        values, and the "(top level)" row's data is None."""
        if self.parent_combo is None:
            return -1
        for index in range(self.parent_combo.count()):
            if self.parent_combo.itemData(index) is node:
                return index
        return -1

    def _selected_parent_node(self) -> Optional[TreeNode]:
        """The parent the form is working against RIGHT NOW: the Parent combo's
        choice when there is one, else the parent the caller opened the form
        with (ADD mode, or a tree with no mount nodes at all).

        Everything that resolves a base goes through this — the offset frame,
        the "Read current position" button and the frame a save is converted
        against (_base_pose/build_node) — so the numbers on screen and the
        numbers stored can never disagree about which parent they belong to."""
        if self.parent_combo is None:
            return self._parent_node
        return self.parent_combo.currentData()

    def _apply_parent_change(self) -> bool:
        """Э1 (plan_2026_09_12_node_dialog_usability): re-hang the edited node
        when the Parent combo points at a different parent. Returns False to
        abort the whole Apply (nothing is written).

        The offset is NOT recalculated here: the moment the combo changed,
        _refresh_for_new_anchor() re-expressed the DISPLAYED board offset
        through the new parent's base (board offset = old offset + old base -
        new base) and build_node() has just converted that back into the new
        parent's LOCAL frame. The physical position therefore does not change —
        this method only moves the node in the STRUCTURE.

        The one case that cannot be held still is an UNRESOLVABLE new base (no
        live board, component not found): nothing was re-expressed, the fields
        show the RAW stored values, and re-hanging really would move the node.
        That is said in the Log and confirmed with the user explicitly, never
        done silently (plan §Э1.3, option 2)."""
        if self.parent_combo is None or self._dock is None or self._tree is None:
            return True
        chosen = self._selected_parent_node()
        if chosen is self._parent_node:
            return True
        if self._base_pose() is None:
            show_message(
                _("Node {ref!r}: the base of the new parent did not resolve on "
                  "the live board — the offset can NOT be recalculated.").format(
                      ref=self._existing.ref),
                _ERROR_STYLE, logger)
            answer = QMessageBox.question(
                self, _("Parent"),
                _("Re-hang {ref!r} WITHOUT recalculating its offset? It keeps "
                  "the stored coordinates, which now mean a different place."
                  ).format(ref=self._existing.ref))
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._dock._reparent_node(self._tree, self._existing, chosen)
        # From here on the form edits the node in its NEW parent's frame.
        self._parent_node = chosen
        return True

    def apply(self) -> bool:
        """Phase B Apply (plan §1.3): validate the form and write the fields
        onto the EDITED node in place — explicit, does NOT close anything (the
        caller owns the button row; edits are explicit actions and the config
        still reaches disk only through the caller's Save). Returns True when
        applied. Resets _touched (design §9.4) on success.

        Э1 (plan_2026_09_12_node_dialog_usability): a changed Parent combo
        re-hangs the node as part of the same Apply — see _apply_parent_change,
        which runs first and may refuse the whole operation."""
        if self._existing is None:
            return False
        built = self.build_node()
        if built is None:
            return False  # build_node already warned
        if not self._apply_parent_change():
            return False
        _copy_node_onto(self._existing, built)
        if self._dock is not None:
            self._dock._mark_dirty()
        self._touched = False
        self.apply_status_label.setText(_("Applied — keep editing or Redraw."))
        return True

    def redraw(self) -> None:
        """Phase B Redraw (plan §1.3): apply() first, then place the node's
        REAL record on the live board at its (edited) config position — a
        background worker via the dock, never blocking the UI."""
        if not self.apply():
            return
        if self._dock is not None:
            self._dock._redraw_edited_node(self._existing)
            self.apply_status_label.setText(_("Applied — Redraw started."))
        else:
            self.apply_status_label.setText(
                _("Applied — no live board Redraw available."))

    # ── Board frame <-> config frame (plan_2026_09_11 §3) ─────────────────

    def _base_pose(self) -> Optional[tuple]:
        """(position_nm, rotation_deg) of the frame the displayed offset/
        rotation are expressed against, live-resolved LAZILY and cached until
        the user changes the anchor.

        None means "no live base": no connection, the tree/config is missing,
        the anchor is incomplete ("Relative to component" with no Role yet —
        never a silent fall back to the parent, see build_node's own guard), or
        the base itself cannot be resolved (a role anchor the board does not
        carry, an Entity no tree places, a point anchor with no live chain, a
        mount anchor whose role is not on the board). The form then shows the
        RAW stored values and, in Edit mode, disables the fields (plan §3.4) —
        never a silent conversion against an assumed 0°.

        Л.2.2: a failed resolve KEEPS its cause — `_base_error` carries the
        exception text into the Log (here) and into the reason under the fields
        (_no_live_base_reason). Only the CONNECTION case may say "no live board
        connection"; everything else must name what did not resolve."""
        if self._base_resolved:
            return self._base_pose_value
        self._base_resolved = True
        self._base_pose_value = None
        self._base_error = None
        if self._adapter is None or self._cfg is None or self._tree is None:
            return None
        try:
            if self.kind_combo.currentData() == "mount":
                base_anchor = self.mount_anchor()
                if base_anchor is None:
                    return None
            else:
                base_anchor = None
            _pos, rot, _mirror = _resolve_node_base_pose(
                self._cfg, self._adapter, self._sheet_names, self._tree,
                self._selected_parent_node(), base_anchor)
            self._base_pose_value = (_pos, rot if rot is not None else 0.0)
        except Exception as e:  # noqa: BLE001 — "no base" is a UI state, not a crash
            # The exception is a UI STATE, never a crash — but it is also never
            # SILENT (Л.2.2): the cause goes to the Log and to the text under
            # the fields, so no failure is ever reported as "not connected".
            self._base_error = str(e)
            logger.warning(
                _("Node form: the base of {ref!r} did not resolve on the live "
                  "board ({error})").format(
                      ref=self.ref_combo.currentText().strip() or "?", error=e))
            self._base_pose_value = None
        return self._base_pose_value

    def _base_rotation_deg(self) -> Optional[float]:
        """The node's BASE rotation — the board-frame conversion half. A thin
        view over the cached _base_pose(): None when there is no live base."""
        pose = self._base_pose()
        return None if pose is None else pose[1]

    def _no_live_base_reason(self) -> str:
        """WHY the offset/rotation fields are disabled — the honest text under
        them (Л.2.2).

        The old form blamed the live connection for EVERY failure, so a mount
        parent, an unresolved role or an Entity no tree places all read as "No
        live board connection" with KiCad plainly connected. Three cases, in
        order of specificity:

        * no adapter/cfg/tree — the connection really IS the reason;
        * the base did not resolve — the exception's own text, which names the
          role/ref that failed (captured by _base_pose);
        * the node's OWN anchor is still incomplete (a mount node with an empty
          Role — build_node refuses to save one) — there is nothing to name
          yet, so the anchor text is the honest one."""
        if self._adapter is None or self._cfg is None or self._tree is None:
            return _("No live board connection — showing the STORED values in "
                     "the base's own frame; editing the offset and rotation is "
                     "disabled until KiCad is connected.")
        if self._base_error:
            return _("The node's base did not resolve on the live board: "
                     "{error}. Showing the STORED values in the base's own "
                     "frame; editing the offset and rotation is disabled until "
                     "it resolves.").format(error=self._base_error)
        return _("The selected anchor does not resolve on the live board — "
                 "showing the STORED values in the anchor's own frame; editing "
                 "the offset and rotation is disabled until it resolves.")

    # ── Anchor change: the displayed frame follows the anchor ─────────────

    def _anchor_signature(self) -> tuple:
        """The anchor the base frame depends on — mode + every anchor field.
        Compared against the last resolved one so a keystroke that does not
        (yet) change the anchor, and the debounced timer's second fire after a
        mode change, never trigger a redundant adapter call.

        `is not None`, NOT truthiness: an empty QComboBox/QLineEdit is FALSY in
        this PyQt build, so a truthiness guard reads every field as blank (the
        same reason AnchorOriginWidget::build/own_anchor use explicit None
        checks)."""
        w = self.mount_anchor_widget
        return (
            # Э1: the Parent combo is part of the frame — switching it must look
            # like a real change to _refresh_for_new_anchor (and a second fire
            # with the same index stays the no-op it is).
            (self.parent_combo.currentIndex()
             if self.parent_combo is not None else None),
            w.mode,
            (w.anchor_role_edit.currentText().strip()
             if w.anchor_role_edit is not None else ""),
            (w.anchor_sheet_edit.currentText().strip()
             if w.anchor_sheet_edit is not None else ""),
            (w.anchor_cluster_edit.currentText().strip()
             if w.anchor_cluster_edit is not None else ""),
            (w.anchor_pad_edit.text().strip()
             if w.anchor_pad_edit is not None else ""),
        )

    def _invalidate_base(self) -> None:
        """Cheap, adapter-free: drop the cached base pose so the NEXT resolve
        uses the anchor now in the form. Called on EVERY anchor signal,
        including per keystroke — it only marks the cache stale, no resolve.

        The frame the form is CURRENTLY showing is stashed once per change
        burst (after the first call the cache is already unresolved), so the
        refresh can hold the node still across the change (U.1)."""
        if self._base_resolved:
            # Keep only a REAL pose — a failed resolve (None) must not clobber a
            # good pre-change frame still waiting to be consumed.
            if self._base_pose_value is not None:
                self._base_pose_before_change = self._base_pose_value
            self._base_resolved = False
            self._base_pose_value = None
            # The OLD failure reason is stale too (Л.2.2) — the next resolve
            # must record its own, not inherit the previous one.
            self._base_error = None

    def _on_anchor_mode_changed(self) -> None:
        """parent <-> component is a discrete action: invalidate + refresh now."""
        self._invalidate_base()
        self._refresh_for_new_anchor()

    def _on_anchor_field_changed(self) -> None:
        """A field edit can be per keystroke: invalidate (cheap) now, coalesce
        the resolve + refresh behind the single-shot timer."""
        self._invalidate_base()
        self._anchor_refresh_timer.start()

    def _offset_widget_is_polar(self) -> bool:
        w = self.offset_widget
        return bool(w._polar and w._polar_combo is not None
                    and w._polar_combo.currentIndex() == 1)

    def _current_board_offset_mm(self) -> Optional[tuple]:
        """The offset the form is showing as a board-frame (x, y) mm vector,
        whichever coordinate style (Cartesian or Polar) is active — None when
        the fields are not a complete pair. Polar is converted with the exact
        rotate_offset_mm, the same primitive the load/save pair uses."""
        fields, err = self.offset_widget.build()
        if err or not fields:
            return None
        if "radius" in fields:
            return rotate_offset_mm(fields["radius"], 0.0, fields["angle"])
        return (fields["x"], fields["y"])

    def _load_board_offset_mm(self, bx: float, by: float, *, polar: bool) -> None:
        """Show a board-frame (x, y) mm vector in the offset widget, keeping
        the coordinate style it already had (Cartesian -> x/y, Polar -> radius/
        angle)."""
        if polar:
            radius = math.hypot(bx, by)
            angle = math.degrees(math.atan2(-by, bx))
            self.offset_widget.load(polar=True, radius=round(radius, 9),
                                    angle=round(angle, 9))
        else:
            self.offset_widget.load(x=round(bx, 9), y=round(by, 9))

    def _restore_raw_stored_values(self) -> None:
        """Show a node's RAW stored (base-frame) values — used when the base is
        unavailable, so a save can never re-interpret board-frame numbers as a
        different frame (§3.4). No-op in Add mode (nothing stored yet)."""
        existing = self._existing
        if existing is None:
            return
        if existing.xy is not None:
            self.offset_widget.load(x=existing.xy[0], y=existing.xy[1])
        elif existing.polar is not None:
            self.offset_widget.load(polar=True, radius=existing.polar[0],
                                    angle=existing.polar[1])
        else:
            self.offset_widget.load()
        self.rotation_edit.setText(str(existing.rotation))

    def _refresh_for_new_anchor(self) -> None:
        """Re-resolve the base for the anchor now in the form and re-express the
        DISPLAYED offset through it so the NODE STAYS WHERE IT IS (U.1).

        The absolute board position is preserved: abs = old_base + old_board_
        offset, then the newly displayed offset = abs - new_base. The shown
        ROTATION is an absolute board angle and therefore does NOT change; only
        the RELATIVE angle the config stores does (implicitly, because
        build_node converts with the NEW base).

        If the new base cannot be resolved live, the fields are disabled with a
        reason and (Edit mode) the RAW stored values are restored — never
        numbers whose meaning silently changed."""
        if (self.kind_combo.currentData() == "mount"
                and self.mount_anchor() is None):
            # Incomplete anchor (no Role yet) — a transient state while the user
            # is still filling the picker. Never resolve a missing anchor as the
            # parent: disable the fields with the same wording build_node uses,
            # but KEEP the pre-change frame and the displayed values, so once the
            # Role arrives the node can still be held still (U.1). Saving is
            # already refused by build_node, so the shown numbers cannot be
            # written with a silently different meaning.
            self._last_anchor_sig = self._anchor_signature()
            self._set_offset_editable(False, reason=_("Anchor: Role is required."))
            return
        sig = self._anchor_signature()
        if sig == self._last_anchor_sig:
            return
        old_pose = self._base_pose_before_change
        self._base_pose_before_change = None
        # Board-frame offset + coordinate style the form is showing right now,
        # captured before any reload (a real reload fires widget signals).
        old_board_offset = self._current_board_offset_mm()
        old_polar = self._offset_widget_is_polar()
        new_pose = self._base_pose()   # cache was invalidated by the signal
        self._last_anchor_sig = sig
        if new_pose is None:
            self._restore_raw_stored_values()
            # Л.2.2: the reason names what actually failed (an unresolved mount
            # parent, role, Entity, ...) — "no connection" is only shown when
            # there really is none.
            self._set_offset_editable(
                False, reason=self._no_live_base_reason())
            return
        if old_pose is not None and old_board_offset is not None:
            old_pos, _old_rot = old_pose
            new_pos, _new_rot = new_pose
            bx = old_board_offset[0] + (old_pos.x - new_pos.x) / MM
            by = old_board_offset[1] + (old_pos.y - new_pos.y) / MM
            self._load_board_offset_mm(bx, by, polar=old_polar)
        # The absolute rotation shown in the field is unchanged — it is a board
        # angle, independent of the base (U.1).
        self._set_offset_editable(True)

    def _set_offset_editable(self, editable: bool, *, reason: str = "") -> None:
        """Enable/disable the offset + rotation fields as a group. Disabled
        means the form is showing the RAW stored (config-frame) values because
        the board frame is unavailable — editing them would silently change
        their meaning, so the user must reconnect first (plan §3.4)."""
        self.offset_widget.setEnabled(editable)
        self.rotation_edit.setEnabled(editable)
        self.offset_frame_label.setText(reason)
        self.offset_frame_label.setVisible(bool(reason))

    def _prefill(self, existing: TreeNode) -> None:
        """Edit mode: populate every field from an existing node. Called
        BEFORE _on_kind_changed() so the ref combo is repopulated for the
        pre-filled kind (external clears its candidates)."""
        kind_idx = self.kind_combo.findData(existing.kind)
        if kind_idx >= 0:
            self.kind_combo.setCurrentIndex(kind_idx)
        self._on_kind_changed()
        self.ref_combo.setCurrentText(existing.ref)
        # Position tab: restore a MOUNT node's anchor (every other kind's base
        # is its parent, so the picker stays in its empty "parent" mode).
        if existing.kind == "mount" and existing.anchor is not None:
            self.mount_anchor_widget.load(
                mode="anchor", role=existing.anchor.role,
                sheet=existing.anchor.anchor_sheet or "",
                cluster=existing.anchor.anchor_cluster or "",
                pad=existing.anchor.anchor_pad or "")
        else:
            self.mount_anchor_widget.load(mode="parent")
        # Board frame (plan §3): the form shows the offset/rotation in the BOARD
        # frame, the config stores them in the base's local frame. The
        # conversion happens HERE (load) and in build_node (save) — twice,
        # nowhere else. Without a live base the RAW stored values are shown and
        # the fields are disabled (§3.4), so an offline rename+save is safe.
        base_rot = self._base_rotation_deg()
        online = base_rot is not None
        if existing.xy is not None:
            if online:
                bx, by = local_offset_to_board_mm(existing.xy, base_rot)
                self.offset_widget.load(x=bx, y=by)
            else:
                self.offset_widget.load(x=existing.xy[0], y=existing.xy[1])
        elif existing.polar is not None:
            if online:
                # Polar is converted EXACTLY (radius untouched, angle shifted by
                # the base) — never via Cartesian, which would add microns.
                self.offset_widget.load(
                    polar=True, radius=existing.polar[0],
                    angle=local_rotation_to_board_deg(existing.polar[1], base_rot))
            else:
                self.offset_widget.load(polar=True, radius=existing.polar[0],
                                        angle=existing.polar[1])
        else:
            self.offset_widget.load()
        self._set_offset_editable(
            online,
            reason="" if online else self._no_live_base_reason())
        self.rotation_edit.setText(str(
            local_rotation_to_board_deg(existing.rotation, base_rot)
            if online else existing.rotation))
        self.name_edit.setText(existing.name or "")
        self.group_edit.setText(existing.group or "")
        # The anchor the base was just resolved for; the first user change is
        # therefore a real change, and the debounced double fires are no-ops.
        self._last_anchor_sig = self._anchor_signature()
        self._base_pose_before_change = None

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
            # Connection state, not user input — a Log line, never a modal
            # (plan_2026_09_11_no_modals_and_busy_kicad X.1). NodeFormWidget
            # has no _show_message (the inline read_status_label is the READ
            # READOUT, not a log), so this uses the shared helper. Fields stay
            # untouched below.
            show_message(
                _("No live board connection — connect KiCad first."),
                _ERROR_STYLE, logger)
            return
        if self._cfg is None or self._tree is None:
            QMessageBox.warning(
                self, _("Read current position"),
                _("No root config loaded — cannot resolve the record."))
            return
        # A MOUNT node's offset is defined from its anchor's live frame — the
        # read must diff against it, not the parent (plan_2026_09_11_tree_mount_
        # nodes). Every other kind reads against the parent base.
        if kind == "mount":
            base_anchor = self.mount_anchor()
            if base_anchor is None:
                QMessageBox.warning(
                    self, _("Read current position"),
                    _("Mount anchor: Role is required."))
                return
        else:
            base_anchor = None
        try:
            offset_mm, rotation = _resolve_live_offset(
                self._cfg, self._adapter, self._sheet_names,
                self._tree, self._selected_parent_node(), ref, kind,
                base_anchor=base_anchor)
        except ValidationError as e:
            QMessageBox.warning(self, _("Read current position"), str(e))
            return
        # The read returns the CONFIG-frame pair (exactly what a node stores);
        # the form displays the BOARD frame. Convert with the SAME cached base
        # rotation the fields would be saved back with (0.0 when no live base is
        # known — then the two frames coincide in every case that reaches here).
        base_rot = self._base_rotation_deg()
        if base_rot is None:
            base_rot = 0.0
        bx, by = local_offset_to_board_mm(offset_mm, base_rot)
        # Fill the Cartesian offset only — the offset widget's own xy/polar
        # toggle is the user's choice (never guess polar from a flat delta).
        self.offset_widget.x_edit.setText(f"{bx:.3f}")
        self.offset_widget.y_edit.setText(f"{by:.3f}")
        if rotation is None:
            self.read_status_label.setText(
                _("rotation not available for this record kind"))
        else:
            self.rotation_edit.setText(
                f"{local_rotation_to_board_deg(rotation, base_rot):.3f}")

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
        is_mount = kind == "mount"
        # The "Read current position" row (a live read of a module ref — a tree,
        # not a record — is meaningless; a MOUNT node's position IS its anchor,
        # so a read is meaningless there too).
        self.read_position_button.setVisible(not is_module and not is_mount)
        self.read_status_label.setVisible(not is_module and not is_mount)
        # The mount anchor picker belongs to a MOUNT node only (plan §Y.1/Y.2)
        # and is REQUIRED there, so it switches itself to "anchor" mode.
        self.mount_anchor_widget.setVisible(is_mount)
        # Л.2.3: the Position tab holds ONLY that picker, so it is shown exactly
        # when the picker is — ONE condition in ONE place, so the label can
        # never drift from its content. A tab with nothing behind it is an
        # interface defect (the user opens it and gets nothing); the tab is
        # HIDDEN, never removed, so its index stays stable for every caller and
        # test that walks the tabs by index.
        self.tabs.setTabVisible(self._position_tab_index, is_mount)
        if not is_mount and self.tabs.currentIndex() == self._position_tab_index:
            # Never leave a hidden tab as the CURRENT one — Qt would otherwise
            # keep a tab the user cannot see active.
            self.tabs.setCurrentIndex(0)
        if is_mount and self.mount_anchor_widget.mode != "anchor":
            self.mount_anchor_widget.load(mode="anchor")
        if kind == "module":
            # Ref = a child TREE NAME (not a record) — the dialog's separate
            # tree-name candidate list, minus self/dups/cycle risks.
            self.ref_combo.clear()
            self._set_ref_items([(name, None, name)
                                 for name in self._module_candidates])
            self.ref_combo.setPlaceholderText(_("child tree name"))
            return
        if kind == "mount":
            # A mount node's ref is a local NAME (unique within the tree), not
            # a config record — free text, like external (plan §Y.1.3).
            self.ref_combo.clear()
            self.ref_combo.setPlaceholderText(_("mount node name (unique in tree)"))
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
        # Board frame -> config frame: the save half of the pair _prefill opens.
        # What the user typed is a board-frame offset / absolute angle; the node
        # stores the base's LOCAL offset / RELATIVE angle. base_rot is the SAME
        # cached value _prefill displayed — no re-resolution, no drift.
        base_rot = self._base_rotation_deg()
        online = base_rot is not None
        if "radius" in fields:
            # Polar is exact: radius untouched, angle shifted by the base.
            polar = (fields["radius"],
                     (board_rotation_to_local_deg(fields["angle"], base_rot)
                      if online else fields["angle"]))
            xy = None
        else:
            xy = (board_offset_to_local_mm((fields["x"], fields["y"]), base_rot)
                  if online else (fields["x"], fields["y"]))
            polar = None

        try:
            rotation = float(self.rotation_edit.text()) if self.rotation_edit.text().strip() else 0.0
        except ValueError:
            QMessageBox.warning(self, _("Add node"), _("Rotation must be a number."))
            return None
        if online:
            rotation = board_rotation_to_local_deg(rotation, base_rot)

        name = self.name_edit.text().strip() or None
        group = self.group_edit.text().strip() or None
        kind = self.kind_combo.currentData()
        # No pivot on a node any more (2026-09-11, plan §V.3): the tree's inner
        # point is edited on the tree (stage Б2.1); the widget above is hidden.
        # A mount node WITHOUT a Role anchor is a hard refusal (a mount node is
        # a point of reference — with nothing to reference it is meaningless);
        # every other kind carries no anchor at all (its base is its parent).
        if kind == "mount":
            node_anchor = self.mount_anchor()
            if node_anchor is None:
                QMessageBox.warning(
                    self, _("Add node"),
                    _("A mount node needs a Role anchor."))
                return None
        else:
            node_anchor = None
        return TreeNode(ref=ref, kind=kind, xy=xy, polar=polar, rotation=rotation,
                        name=name, group=group, anchor=node_anchor)


class _NodeDialog(QDialog):
    """Thin modal wrapper around a single NodeFormWidget (plan
    plan_2026_09_04_trees_dock_master_detail.md §1.4): the form is
    modal-agnostic (a plain QWidget); THIS class adds the button row on top —
    OK/Cancel in Add mode (existing=None, the _prompt_node master flow) or
    Apply/Redraw/Close in Edit mode (existing=node, Phase B), mirroring the
    pre-refactor dialog exactly. build_node()/own_anchor() and every form
    field are reachable on the dialog (via the embedded form) so the existing
    callers/tests keep working until §6 ports them onto NodeFormWidget."""
    _form: Optional[NodeFormWidget] = None
    # Э2 (plan_2026_09_12_node_dialog_usability): the node the Add-mode OK
    # button validated and accepted with — see _on_ok/build_node. A class-level
    # default (not set in __init__) so the attribute is found by normal lookup,
    # never by __getattr__ (which forwards unknown names to the embedded form).
    _built_node: Optional[TreeNode] = None

    def __init__(self, parent, ref_candidates: list[tuple[str, str]], used_refs: set[str],
                 title: str, cfg=None, adapter=None, sheet_names=None,
                 tree=None, parent_node=None, existing=None,
                 module_candidates=None, all_trees=None,
                 role_candidates=None, cluster_candidates=None,
                 parent_candidates=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        # The FORM is built with the original `parent` (the TreesDock, not this
        # dialog) so its _dock resolves to the dock that owns _mark_dirty —
        # addWidget below reparents the form visually without touching _dock.
        self._form = NodeFormWidget(
            parent, ref_candidates, used_refs, title, cfg=cfg, adapter=adapter,
            sheet_names=sheet_names, tree=tree, parent_node=parent_node,
            existing=existing, module_candidates=module_candidates,
            all_trees=all_trees, role_candidates=role_candidates,
            cluster_candidates=cluster_candidates,
            # Э1: the Parent combo's rows (dock-computed; Edit mode only).
            parent_candidates=parent_candidates)
        layout = QVBoxLayout(self)
        layout.addWidget(self._form)
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
        else:
            # Э2 (plan_2026_09_12_node_dialog_usability): OK validates FIRST and
            # accepts only when the form built a node — a failed build leaves the
            # dialog OPEN with everything the user typed. Until 2026-09-12 OK was
            # wired straight to accept(), so the window closed unconditionally
            # and validation had to run after exec() in _prompt_node, by which
            # time the input was already gone (the 2026-09-02 workaround this
            # replaces; the edit mode's Apply/Close pair already behaved right).
            self.ok_button = QPushButton(_("OK"))
            self.ok_button.clicked.connect(self._on_ok)
            cancel_button = QPushButton(_("Cancel"))
            cancel_button.clicked.connect(self.reject)
            buttons.addWidget(self.ok_button)
            buttons.addWidget(cancel_button)
        layout.addLayout(buttons)
        # Redraw-enabled state tracks the form's kind combo (the form no longer
        # owns a redraw button — the wrapper does, so the wiring lives here).
        self._form.kind_combo.currentIndexChanged.connect(self._update_redraw_state)
        self._update_redraw_state()
        # Э3 (plan_2026_09_12_node_dialog_usability): the node dialog's size is
        # remembered between launches, keyed by the class name. No default
        # constant here — Qt's own size until the user resizes it once.
        restore_dialog_size(self)
        persist_dialog_size(self)

    @property
    def _adapter(self):
        """Read/write through to the embedded form — tests and _prompt_node
        reassign the live adapter after construction."""
        return self._form._adapter

    @_adapter.setter
    def _adapter(self, value):
        self._form._adapter = value

    def __getattr__(self, name):
        # Delegate any form field/method to the embedded NodeFormWidget (the
        # dialog keeps the QDialog surface only). Only reached when normal
        # attribute lookup fails — form-only names like kind_combo/tabs/etc.
        form = self.__dict__.get("_form")
        if form is not None:
            return getattr(form, name)
        raise AttributeError(name)

    def _update_redraw_state(self) -> None:
        """Phase B: Redraw only makes sense in EDIT mode with a dock (live board
        + config) and a kind _redraw_edited_node knows how to place — most
        kinds directly by name, "module" via forest-content-activation; an
        external live refdes or a point base cannot
        (_REDRAWABLE_NODE_KINDS, shared with the master-detail Node tab's own
        Redraw button — see _form_action_row)."""
        redraw = getattr(self, "redraw_button", None)
        if redraw is None:
            return
        kind = self._form.kind_combo.currentData()
        redraw.setEnabled(self._form._dock is not None
                          and kind in _REDRAWABLE_NODE_KINDS)

    def _on_apply(self) -> bool:
        """Edit-mode Apply: delegate to the form's apply() (Phase B — mutates
        the node in place, marks the dock dirty, stays open)."""
        return self._form.apply()

    def _on_redraw(self) -> None:
        """Edit-mode Redraw: delegate to the form's redraw() (apply first, then
        place the real record on the live board via the dock)."""
        self._form.redraw()

    def _on_ok(self) -> None:
        """Add-mode OK: validate the form and accept ONLY when it built a node.

        build_node() reports the problem itself (a QMessageBox naming the empty/
        used ref or the bad offset) and returns None; the dialog then stays open
        and unchanged, so the fix is one edit away (plan
        plan_2026_09_12_node_dialog_usability Э2). Cancel/Close stays the
        unconditional reject()."""
        node = self._form.build_node()
        if node is None:
            return
        self._built_node = node
        self.accept()

    def build_node(self):
        """The node the OK button already validated, or — when OK never ran
        (a caller driving the form directly, a test stubbing exec()) — the
        form's own build_node().

        The cache is what keeps the Add flow from validating twice: after Э2
        (plan_2026_09_12_node_dialog_usability) _prompt_node reads the node OK
        built instead of running build_node() again after exec(), so the same
        QMessageBox can never appear twice."""
        if self._built_node is not None:
            return self._built_node
        return self._form.build_node()

    def mount_anchor(self):
        """The form's mount_anchor() (Position-tab value, kind "mount")."""
        return self._form.mount_anchor()


# The tree-anchor MODE TABLE (design §3.2; plan_2026_09_11_external_point_
# materialization §X.3.2). DATA, not code: the combo, the per-mode row
# visibility (_on_mode_changed) and any future append read THIS table, so
# adding a mode is adding one entry here (plus its build/prefill line) — never
# a re-plumbing of hand-wired mode switches. The mode list is deliberately
# OPEN: 2026-09-11 replaced the old `auto` flag with the explicit `self` mode
# ("my own live root", plan tree_self_anchor task Д) — one entry below.
#
# `rows` names the field groups the mode shows: "record" = the kind-filtered
# ref row, "role" = the role/sheet/pad/cluster row, "point" = the point row,
# "self" = the node/pad row (a component this tree places itself).
# The order is FROZEN: existing tests drive the combo by INDEX (0 origin,
# 1 record, 2 external, ...), so new modes append AFTER point. The former
# `auto` mode became the explicit `self` (2026-09-11, plan tree_self_anchor,
# task Д) at the SAME index 3.
_TREE_ANCHOR_MODES = (
    ("origin", _("Origin (board 0,0)"), ()),
    ("record", _("Config record"), ("record",)),
    ("external", _("External refdes"), ("record",)),
    ("self", _("Self (component this tree places)"), ("self",)),
    ("role", _("Role"), ("role",)),
    ("point", _("Point"), ("point",)),
)


class AnchorFormWidget(QWidget):
    """The tree-anchor picker/edit FORM (no modal wrapper): every TreeAnchor
    mode in _TREE_ANCHOR_MODES (see kicadstamp/trees.py):
      - origin   -> (anchor (origin)): absolute board origin (0,0)
      - record   -> (anchor (ref "...")): a config record name, narrowed by a
                    kind filter (Entity/Rule/Coordinate/Point/Clone + All) —
                    a PICKER AID only: the anchor grammar has no kind (a name
                    shared across sections is fatal at link_trees either way)
      - external -> (anchor (ref "...") (external)): live-board-only refdes
      - self     -> (anchor (self [(ref "...") (pad "...")])): the tree hangs on
                    a component it places ITSELF — the optional ref names one of
                    THIS tree's kind "placement" nodes, else the single
                    top-level placement node, and the optional pad moves the
                    base onto that pad; the base is read LIVE
      - role     -> (anchor (role "...") [(sheet ...) (cluster ...) (pad ...)])
      - point    -> (anchor (point "...")): a points: entry name
    Every mode also carries an OPTIONAL own (shift x y) in LOCAL mm of the
    base (§X.2).

    `existing` (a TreeAnchor) switches to EDIT mode: the mode and every field
    are pre-filled (symmetric to NodeFormWidget's existing=), so a user can
    just tweak e.g. the sheet of a role anchor instead of rebuilding it.
    "External refdes" is STORED as an is_external anchor — the resolver then
    never matches it against a config record name (collision impossible;
    note_2026_08_28_tree_anchor_name_collision).

    Modal-agnostic: build_anchor() returns a (value, error) pair and the Phase
    B apply()/redraw() (design §9.3) write tree.anchor in place; the OK/Cancel
    or Apply/Redraw button row is added by the embedding context
    (_AnchorDialog's thin wrapper, or the master-detail Anchor tab of plan
    plan_2026_09_04_trees_dock_master_detail.md §3).

    Extracted from the former _AnchorDialog (plan §2.1); the class keeps the
    exact constructor signature and every field/method the picker/edit flows
    use, just as a plain QWidget."""
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
                 tree=None, adapter=None, all_trees=None):
        super().__init__(parent)
        self._ref_candidates = list(ref_candidates or [])
        self._cfg = cfg
        self._sheet_names = dict(sheet_names or {})
        self._role_candidates = list(role_candidates or [])
        self._cluster_candidates = list(cluster_candidates or [])
        self._tree = tree
        # Board-frame conversion base (plan §W.4): the anchor is resolved LIVE
        # from the anchor rows of THIS form, and all_trees lets a pivot-ref
        # snapshot lay this tree out (tree_pivot_offset needs the forest).
        self._adapter = adapter
        self._all_trees = list(all_trees or [])
        self._dock = parent if getattr(parent, "_mark_dirty", None) else None
        self._touched = False
        # The overlay-marker toggle launches a long op (every position is a
        # live board read) — keep the controller alive here, like every dock.
        self._active_op = None

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

        # The form's own vertical layout: the picker rows + the non-blocking
        # apply-status label (the button row is the embedding context's job).
        root = QVBoxLayout(self)
        form = QFormLayout()

        # Mode combo — built from the DATA table _TREE_ANCHOR_MODES, not
        # hand-listed here. Its order keeps the historic indices (0 origin,
        # 1 record, 2 external, ...) so nothing that drives the combo by index
        # regresses; a new mode is one table entry.
        self.mode_combo = QComboBox()
        for _mode_key, _mode_label, _rows in _TREE_ANCHOR_MODES:
            self.mode_combo.addItem(_mode_label, _mode_key)
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
        # BECAUSE of the self-ref exclusion, pointing the user at the Self mode
        # instead of a bare empty combo. Hidden by default; _update_hint drives
        # it.
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.hide()
        record_form.addRow(self.hint_label)
        form.addRow(self.record_row)

        # role rows: role/sheet/pad/cluster — built by the SAME shared builder
        # the mount-node picker and the other docks use (design §3.2, one
        # dictionary). show_ref=False: the tree's Ref lives in the separate
        # kind-filtered record/external row above, not a bare line edit.
        self.role_row = QWidget()
        role_form = QFormLayout(self.role_row)
        role_form.setContentsMargins(0, 0, 0, 0)
        _role_w = build_role_anchor_fields(
            role_form, show_ref=False, anchor_fields=("sheet", "pad", "cluster"))
        self.role_edit = _role_w["role"]
        self.sheet_edit = _role_w["sheet"]
        self.pad_edit = _role_w["pad"]
        self.cluster_edit = _role_w["cluster"]
        set_combo_items(self.role_edit, self._role_candidates)
        set_combo_items(self.sheet_edit, list(self._sheet_names.values()))
        set_combo_items(self.cluster_edit, self._cluster_candidates)
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

        # self rows (2026-09-11, plan tree_self_anchor, task Д.7): the tree hangs
        # on a component it places ITSELF — read live. The Node combo lists this
        # tree's OWN kind "placement" nodes; both fields are OPTIONAL (a bare
        # self uses the single top-level placement node, today's rule).
        self.self_row = QWidget()
        self_form = QFormLayout(self.self_row)
        self_form.setContentsMargins(0, 0, 0, 0)
        self.self_combo = QComboBox()
        configure_searchable(self.self_combo)
        self.self_combo.setPlaceholderText(_("node of this tree (optional)"))
        set_combo_items(self.self_combo, tree_self_ref_candidates(tree))
        self_form.addRow(_("Node:"), self.self_combo)
        self.self_pad_edit = QLineEdit()
        self.self_pad_edit.setPlaceholderText(_("pad (optional)"))
        self_form.addRow(_("Pad:"), self.self_pad_edit)
        form.addRow(self.self_row)

        # Shift row (§X.2.3): the anchor's OWN (shift x y). Stored in LOCAL mm
        # of the base, SHOWN in board mm — the same five conversion functions
        # as the tree settings, converted at the ANCHOR's own angle (never a
        # cached one). Applies to every mode; disabled with a reason when the
        # anchor does not resolve on the live board.
        self.shift_row = QWidget()
        shift_layout = QHBoxLayout(self.shift_row)
        shift_layout.setContentsMargins(0, 0, 0, 0)
        shift_layout.addWidget(QLabel(_("Shift X:")))
        self.shift_x_edit = QLineEdit()
        self.shift_x_edit.setPlaceholderText(_("shift X mm (0)"))
        shift_layout.addWidget(self.shift_x_edit)
        shift_layout.addWidget(QLabel(_("Shift Y:")))
        self.shift_y_edit = QLineEdit()
        self.shift_y_edit.setPlaceholderText(_("shift Y mm (0)"))
        shift_layout.addWidget(self.shift_y_edit)
        form.addRow(self.shift_row)
        self.shift_reason_label = QLabel("")
        self.shift_reason_label.setWordWrap(True)
        self.shift_reason_label.setVisible(False)
        form.addRow(self.shift_reason_label)

        # The button row is NOT part of the form — plan
        # plan_2026_09_04_trees_dock_master_detail.md §2.1: a plain QWidget form
        # is modal-agnostic, the OK/Cancel (Create-tree/Set-anchor master) or
        # Apply/Redraw (master-detail Anchor tab of §3) row is added by the
        # embedding context. What stays here: the picker rows + the non-blocking
        # apply-status label that apply()/redraw() write to (design §9.4).
        root.addLayout(form)

        # ── Tree settings (plan_2026_09_11_tree_settings_form §W.2): the tree's
        # INNER point (pivot-*) and its own angle, grouped in one box so the
        # right-hand panel stays compact. Hidden for the create-tree dialog
        # (no tree yet) by _load_settings.
        self.settings_box = QGroupBox(_("Tree settings"))
        settings_form = QFormLayout(self.settings_box)
        self.pivot_mode_combo = QComboBox()
        self.pivot_mode_combo.addItem(_("Tree origin (0,0)"), "origin")
        self.pivot_mode_combo.addItem(_("Coordinate (xy/polar)"), "coordinate")
        self.pivot_mode_combo.addItem(_("Node of this tree"), "node")
        settings_form.addRow(_("Suspension point:"), self.pivot_mode_combo)
        # xy/polar coordinate in the tree's OWN frame, shown in the BOARD frame
        # (mm) exactly like the node offset widget — same shared widget, same
        # five conversion functions (§W.4).
        self.pivot_widget = AnchorOriginWidget(modes=["xy"], polar=True)
        settings_form.addRow(self.pivot_widget)
        self.pivot_from_node_button = QPushButton(_("Use node's offset..."))
        self.pivot_from_node_button.setToolTip(_(
            "Fill the coordinate with the LOCAL offset of the selected node — "
            "a static snapshot of this tree's own layout."))
        settings_form.addRow(self.pivot_from_node_button)
        self.pivot_ref_combo = QComboBox()
        configure_searchable(self.pivot_ref_combo)
        settings_form.addRow(_("Node:"), self.pivot_ref_combo)
        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText(_("0"))
        settings_form.addRow(_("Angle (board deg):"), self.rotation_edit)
        # W.3.1: the empty candidate list is a NORMAL state (e.g. ch0_dac_buf) —
        # explained here rather than left as a broken-looking empty combo.
        self.pivot_hint_label = QLabel("")
        self.pivot_hint_label.setWordWrap(True)
        self.pivot_hint_label.setVisible(False)
        settings_form.addRow(self.pivot_hint_label)
        # Why the settings may be read-only (anchor does not resolve live, §W.4.2).
        self.settings_frame_label = QLabel("")
        self.settings_frame_label.setWordWrap(True)
        self.settings_frame_label.setVisible(False)
        settings_form.addRow(self.settings_frame_label)
        # The toggle that puts the tree's anchor and the origin of its LOCAL
        # frame on the board overlay (З.2.4) — exactly the two positions this
        # group edits. ONE button, and its state is read from the owner's MAP
        # (see _refresh_show_markers_button), never a widget flag that could
        # drift from what is really on the board. Hidden with the group for a
        # tree-less (create-tree) form.
        self.show_markers_button = QPushButton(_("Show tree markers"))
        self.show_markers_button.setToolTip(_(
            "Draw the tree's anchor (where it hangs) and the origin of its "
            "local frame on the board overlay — the vector between the two "
            "circles is the suspension point."))
        self.show_markers_button.clicked.connect(self._on_show_markers)
        settings_form.addRow(self.show_markers_button)
        root.addWidget(self.settings_box)
        # Form state kept in the CONFIG frame (the tree's own frame / dovоrот):
        # the board-frame DISPLAY is derived from it, never the other way round,
        # so an angle edit re-expresses the display without rewriting the stored
        # values (§W.4.1 — the 9887468 trap).
        self._stored_pivot_xy = None
        self._stored_pivot_polar = None
        self._stored_pivot_ref = None
        self._stored_rotation = 0.0
        # The anchor's OWN shift, in the CONFIG frame (LOCAL mm of the base),
        # or None (§X.2). Same rule as the pivot: the board-frame display is
        # derived, never the stored truth.
        self._stored_anchor_shift = None
        # Reentrancy counter for programmatic widget loads (they emit signals).
        self._loading_settings = 0

        self.apply_status_label = QLabel("")
        self.apply_status_label.setWordWrap(True)
        root.addWidget(self.apply_status_label)

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
        # Tree settings (inner point + angle): seed the config-frame state from
        # the tree and show it in the board frame. Runs BEFORE the touched wiring
        # so a programmatic load never marks the fresh form dirty.
        self._load_settings()
        # design §9.4: after prefill/initial populate the form is clean —
        # _touched reflects only USER edits since the last load()/Apply.
        self._touched = False
        # design §9.4 wiring (§3c — note_2026_09_04_touched_wiring_still_open.md):
        # every user-facing picker/edit marks the form as having unapplied
        # changes; apply()/redraw() reset the flag on success. Wired AFTER
        # prefill, so opening the form for an existing anchor stays clean.
        self.mode_combo.currentIndexChanged.connect(self._mark_touched)
        self.kind_combo.currentIndexChanged.connect(self._mark_touched)
        self.ref_combo.currentTextChanged.connect(self._mark_touched)
        self.role_edit.currentTextChanged.connect(self._mark_touched)
        self.sheet_edit.currentTextChanged.connect(self._mark_touched)
        self.cluster_edit.currentTextChanged.connect(self._mark_touched)
        self.point_edit.currentTextChanged.connect(self._mark_touched)
        self.pad_edit.textChanged.connect(self._mark_touched)
        self.shift_x_edit.textChanged.connect(self._on_shift_edited)
        self.shift_y_edit.textChanged.connect(self._on_shift_edited)
        self.shift_x_edit.textChanged.connect(self._mark_touched)
        self.shift_y_edit.textChanged.connect(self._mark_touched)

        # ── Tree settings wiring (plan_2026_09_11_tree_settings_form) ──────
        self.pivot_mode_combo.currentIndexChanged.connect(self._on_pivot_mode_changed)
        self.pivot_ref_combo.currentIndexChanged.connect(self._on_pivot_ref_changed)
        self.pivot_widget.fieldChanged.connect(self._on_pivot_coordinate_edited)
        self.pivot_widget.fieldChanged.connect(self._mark_touched)
        self.pivot_from_node_button.clicked.connect(self._on_use_node_offset)
        self.rotation_edit.textChanged.connect(self._on_rotation_edited)
        self.rotation_edit.textChanged.connect(self._mark_touched)
        # The board frame the settings are shown in follows the ANCHOR part of
        # this same form (W.4.1): a mode switch refreshes at once, a field edit
        # coalesces behind a short timer (an anchor resolve is an adapter call).
        self._settings_base_timer = QTimer(self)
        self._settings_base_timer.setSingleShot(True)
        self._settings_base_timer.setInterval(250)
        self._settings_base_timer.timeout.connect(self._on_settings_base_refresh)
        for _sig in (self.kind_combo.currentIndexChanged,
                     self.ref_combo.currentTextChanged,
                     self.role_edit.currentTextChanged,
                     self.sheet_edit.currentTextChanged,
                     self.cluster_edit.currentTextChanged,
                     self.point_edit.currentTextChanged,
                     self.pad_edit.textChanged):
            _sig.connect(self._schedule_settings_base_refresh)
        self.mode_combo.currentIndexChanged.connect(self._on_settings_base_refresh)

    def _mark_touched(self) -> None:
        """design §9.4: any user field edit flags the form as having unapplied
        changes — the discard-warning source for a constantly-open panel (the
        master-detail Anchor tab). Connected per-widget in the embedding
        context/§3; the flag itself is owned here."""
        self._touched = True

    @staticmethod
    def _mode_rows(mode: str) -> tuple:
        """The field-group tuple _TREE_ANCHOR_MODES declares for `mode` (empty
        for a mode with no extra rows). THE only place the table is read."""
        for key, _label, rows in _TREE_ANCHOR_MODES:
            if key == mode:
                return rows
        return ()

    def _on_mode_changed(self) -> None:
        # Row visibility is DATA-driven off _TREE_ANCHOR_MODES: a new mode gets
        # its rows for free. "record" covers both the record and external modes
        # (the same kind-filtered ref row, only is_external differs on save).
        rows = self._mode_rows(self.mode_combo.currentData())
        self.record_row.setVisible("record" in rows)
        self.role_row.setVisible("role" in rows)
        self.point_row.setVisible("point" in rows)
        self.self_row.setVisible("self" in rows)
        if "record" in rows:
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
        never a modal — it tells the user where to switch (Self) instead of
        leaving them staring at an empty combo."""
        mode = self.mode_combo.currentData()
        kind = self.kind_combo.currentData()
        entity_empty = not any(k == "placement" for k, _n in self._ref_candidates)
        if (mode == "record" and self._had_self_entity
                and (kind is None or kind == "placement") and entity_empty):
            self.hint_label.setText(_(
                "This tree's own root Entity {ref!r} can't anchor itself — use Self.")
                .format(ref=self._self_entity_ref))
            self.hint_label.show()
        else:
            self.hint_label.hide()

    def set_candidates(self, role_candidates=None, cluster_candidates=None) -> None:
        """S.3.2 (plan_2026_09_11_stale_snapshot_role_lists.md) — the anchor
        form's half of NodeFormWidget.set_candidates: the embedded anchor page
        is long-lived, so its Role/Cluster combos are refreshed IN PLACE
        (set_combo_items keeps the current text) instead of re-creating the
        form, which would throw away an unsaved edit."""
        self._role_candidates = list(role_candidates or [])
        self._cluster_candidates = list(cluster_candidates or [])
        set_combo_items(self.role_edit, self._role_candidates)
        set_combo_items(self.cluster_edit, self._cluster_candidates)

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
        # The mode comes from THE single predicate on the anchor
        # (TreeAnchor.mode): origin/record/external/self/role/point — never a
        # hand-rolled field chain (plan Д.3).
        mode = existing.mode
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self._on_mode_changed()

        if mode == "self":
            if existing.self_ref is not None:
                ci = self.self_combo.findText(existing.self_ref)
                if ci >= 0:
                    self.self_combo.setCurrentIndex(ci)
                else:
                    self.self_combo.setCurrentText(existing.self_ref)
            self.self_pad_edit.setText(existing.self_pad or "")
        elif existing.role is not None:
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

    # ── Tree settings: inner point + own angle (plan §W.2–W.4) ────────────

    def _pivot_mode(self) -> str:
        """The suspension-point source currently selected (origin/coordinate/
        node)."""
        return self.pivot_mode_combo.currentData()

    def _apply_pivot_mode_visibility(self) -> None:
        mode = self._pivot_mode()
        self.pivot_widget.setVisible(mode == "coordinate")
        self.pivot_from_node_button.setVisible(mode == "coordinate")
        self.pivot_ref_combo.setVisible(mode == "node")

    def _set_pivot_mode(self, mode: str) -> None:
        """Select a suspension-point mode; runs the visibility even when the
        index does not change (a programmatic switch back to the same mode)."""
        idx = self.pivot_mode_combo.findData(mode)
        if idx >= 0 and idx != self.pivot_mode_combo.currentIndex():
            self.pivot_mode_combo.setCurrentIndex(idx)
        else:
            self._apply_pivot_mode_visibility()

    def _on_pivot_mode_changed(self) -> None:
        """3-way mutex: origin (0,0) / coordinate / node. Switching CLEARS the
        sources that no longer apply, so two can never ride together (§W.8.1
        item 6)."""
        self._apply_pivot_mode_visibility()
        if self._loading_settings:
            return
        mode = self._pivot_mode()
        if mode != "coordinate":
            self._stored_pivot_xy = None
            self._stored_pivot_polar = None
        if mode != "node":
            self._stored_pivot_ref = None
        if mode == "coordinate":
            self._redisplay_pivot()
        elif mode == "node" and not self.pivot_ref_combo.currentData():
            # Default to the first usable node, so the mode is never "on" but
            # empty on a tree that HAS candidates.
            if (self.pivot_ref_combo.count()
                    and self.pivot_ref_combo.itemData(0) is not None):
                self.pivot_ref_combo.setCurrentIndex(0)
        self._mark_touched()

    def _on_pivot_ref_changed(self) -> None:
        if self._loading_settings:
            return
        ref = self.pivot_ref_combo.currentData()
        self._stored_pivot_ref = ref or None
        if ref:
            self._stored_pivot_xy = None
            self._stored_pivot_polar = None
            self._set_pivot_mode("node")
        self._mark_touched()

    def _on_pivot_coordinate_edited(self) -> None:
        """A coordinate edit updates the CONFIG-frame state using the angle in
        force RIGHT NOW (§W.4.1) — never a cached base."""
        if self._loading_settings or self._pivot_mode() != "coordinate":
            return
        base = self._conversion_base_deg()
        if base is None:
            return
        eff_rot = base[0]
        fields, err = self.pivot_widget.build()
        if err or not fields:
            return  # incomplete input — build_settings reports it on Apply
        if "radius" in fields:
            self._stored_pivot_xy = None
            self._stored_pivot_polar = (
                fields["radius"],
                board_rotation_to_local_deg(fields["angle"], eff_rot))
        else:
            self._stored_pivot_xy = board_offset_to_local_mm(
                (fields["x"], fields["y"]), eff_rot)
            self._stored_pivot_polar = None
        self._stored_pivot_ref = None
        self._mark_touched()

    def _on_rotation_edited(self) -> None:
        """Angle edit: the STORED dovоrот changes, and the coordinate DISPLAY is
        re-expressed through the new angle while its stored value does NOT
        (§W.4.1 — the 9887468 trap)."""
        if self._loading_settings:
            return
        base = self._conversion_base_deg()
        if base is None:
            return
        anchor_rot = base[1]
        text = self.rotation_edit.text().strip()
        if text == "":
            self._stored_rotation = board_rotation_to_local_deg(0.0, anchor_rot)
        else:
            try:
                shown = float(text)
            except ValueError:
                return  # incomplete input — build_settings reports it on Apply
            self._stored_rotation = board_rotation_to_local_deg(shown, anchor_rot)
        self._mark_touched()
        self._redisplay_pivot()

    def _redisplay_pivot(self) -> None:
        """Re-show the stored inner-point coordinate in the board frame using
        the CURRENT angle — the STORED value is never touched here."""
        base = self._conversion_base_deg()
        if base is None:
            return
        eff_rot = base[0]
        self._loading_settings += 1
        try:
            if self._stored_pivot_xy is not None:
                bx, by = local_offset_to_board_mm(self._stored_pivot_xy, eff_rot)
                self.pivot_widget.load(x=bx, y=by)
            elif self._stored_pivot_polar is not None:
                radius, angle = self._stored_pivot_polar
                self.pivot_widget.load(
                    polar=True, radius=radius,
                    angle=local_rotation_to_board_deg(angle, eff_rot))
        finally:
            self._loading_settings -= 1

    def _on_use_node_offset(self) -> None:
        """'Use node's offset…': snapshot the selected node's LOCAL offset (this
        tree laid out from zero) into the coordinate field as a pivot-xy."""
        if self._tree is None:
            return
        ref = self.pivot_ref_combo.currentData()
        if not ref:
            return
        forest = {t.name: t for t in self._all_trees}
        probe = replace(self._tree, pivot_ref=ref)
        try:
            offset = tree_pivot_offset(probe, forest, adapter=self._adapter,
                                       cfg=self._cfg, sheet_names=self._sheet_names)
        except Exception as exc:  # noqa: BLE001 — a UI action, report not crash
            self.apply_status_label.setText(str(exc))
            return
        self._stored_pivot_xy = (offset.x / MM, offset.y / MM)
        self._stored_pivot_polar = None
        self._stored_pivot_ref = None
        self._set_pivot_mode("coordinate")
        self._reload_settings_display()
        self._mark_touched()

    def _schedule_settings_base_refresh(self, *_args) -> None:
        if self._loading_settings:
            return
        self._settings_base_timer.start()

    def _on_settings_base_refresh(self, *_args) -> None:
        if self._loading_settings:
            return
        self._reload_settings_display()

    def _conversion_base_deg(self) -> Optional[tuple]:
        """(eff_rot_deg, anchor_rot_deg) for the ANCHOR COLUMN'S CURRENT values,
        or None when the anchor does not resolve (§W.4.2).

        eff_rot == anchor_rot + tree.rotation is exactly the tree content frame
        orientation tree_effective_base yields; anchor_rot is what the tree's own
        rotation is stored RELATIVE to. The form's anchor may differ from the
        saved tree.anchor (an unsaved edit) — that is the point of §W.4.1.

        No adapter is NOT by itself "unresolvable": an `origin` anchor resolves
        offline to 0 deg, so an offline rename+save round-trips. Only a real
        resolution failure disables the fields."""
        if self._cfg is None or self._tree is None:
            return None
        # The IDENTITY part only — the shift is a translation and cannot change
        # the anchor's angle, and build_anchor() would need THIS base to convert
        # the shift, which would recurse.
        anchor, err = self._anchor_identity()
        if err or anchor is None:
            return None
        probe = replace(self._tree, anchor=anchor)
        try:
            _pos, anchor_rot = _anchor_base_live_position(
                self._adapter, self._cfg, probe, self._sheet_names)
        except Exception:  # noqa: BLE001 — "no base" is a UI state, not a crash
            return None
        anchor_rot = 0.0 if anchor_rot is None else anchor_rot
        return anchor_rot + self._stored_rotation, anchor_rot

    def _refresh_pivot_candidates(self) -> None:
        combo = self.pivot_ref_combo
        combo.blockSignals(True)
        combo.clear()
        refs = (tree_pivot_ref_candidates(self._tree)
                if self._tree is not None else [])
        for ref in refs:
            combo.addItem(ref, ref)
        if not refs:
            combo.addItem(_("(no node can be a handle)"), None)
        combo.blockSignals(False)

    def _update_pivot_hint(self) -> None:
        refs = (tree_pivot_ref_candidates(self._tree)
                if self._tree is not None else [])
        if self._tree is None or refs:
            self.pivot_hint_label.setVisible(False)
            return
        self.pivot_hint_label.setText(_(
            "No node of this tree can be a suspension point: every positioned "
            "node hangs from a live component through a mount node. Use a "
            "coordinate instead."))
        self.pivot_hint_label.setVisible(True)

    def _set_settings_editable(self, editable: bool, *, reason: str = "") -> None:
        """Enable/disable the whole tree-settings group. Disabled means the
        board frame is unavailable, so the fields hold RAW stored values —
        editing them would silently change their meaning (§W.4.2)."""
        self.pivot_mode_combo.setEnabled(editable)
        self.pivot_widget.setEnabled(editable)
        self.pivot_from_node_button.setEnabled(editable)
        refs = (tree_pivot_ref_candidates(self._tree)
                if self._tree is not None else [])
        self.pivot_ref_combo.setEnabled(editable and bool(refs))
        self.rotation_edit.setEnabled(editable)
        self.settings_frame_label.setText(reason)
        self.settings_frame_label.setVisible(bool(reason))

    def _set_shift_editable(self, editable: bool, *, reason: str = "") -> None:
        """Enable/disable the anchor-shift row (§X.2.3). Disabled means the
        board frame is unavailable, so the fields hold the RAW stored value and
        editing them would silently change their meaning — the same rule
        _set_settings_editable applies to the tree settings."""
        self.shift_x_edit.setEnabled(editable)
        self.shift_y_edit.setEnabled(editable)
        self.shift_reason_label.setText(reason)
        self.shift_reason_label.setVisible(bool(reason))

    def _show_raw_settings(self) -> None:
        """Show the RAW stored (config-frame) settings — used when no live base
        is available, so a save can never re-interpret board-frame numbers."""
        self._loading_settings += 1
        try:
            self.rotation_edit.setText(str(self._stored_rotation))
            if self._stored_pivot_xy is not None:
                self.pivot_widget.load(x=self._stored_pivot_xy[0],
                                       y=self._stored_pivot_xy[1])
            elif self._stored_pivot_polar is not None:
                self.pivot_widget.load(polar=True,
                                       radius=self._stored_pivot_polar[0],
                                       angle=self._stored_pivot_polar[1])
            else:
                self.pivot_widget.load()
            # The RAW (config-frame, LOCAL-mm) shift — the same "show the stored
            # truth when the board frame is unavailable" rule as the pivot.
            if self._stored_anchor_shift is not None:
                self.shift_x_edit.setText(str(self._stored_anchor_shift[0]))
                self.shift_y_edit.setText(str(self._stored_anchor_shift[1]))
            else:
                self.shift_x_edit.setText("")
                self.shift_y_edit.setText("")
        finally:
            self._loading_settings -= 1

    def _reload_settings_display(self) -> None:
        """Express the stored settings in the board frame (angle absolute, pivot
        coordinate as a board-frame vector). No live base -> the fields are
        disabled with a reason and the RAW values are shown (§W.4.2)."""
        self._loading_settings += 1
        try:
            base = self._conversion_base_deg()
            if base is None:
                self._show_raw_settings()
                self._set_settings_editable(False, reason=_(
                    "The selected anchor does not resolve on the live board — "
                    "the suspension point and angle are shown disabled until it "
                    "resolves."))
                self._set_shift_editable(False, reason=_(
                    "The selected anchor does not resolve on the live board — "
                    "the shift is shown as the raw config value and disabled "
                    "until it resolves."))
                return
            eff_rot, anchor_rot = base
            self.rotation_edit.setText(str(
                local_rotation_to_board_deg(self._stored_rotation, anchor_rot)))
            if self._stored_pivot_xy is not None:
                bx, by = local_offset_to_board_mm(self._stored_pivot_xy, eff_rot)
                self.pivot_widget.load(x=bx, y=by)
            elif self._stored_pivot_polar is not None:
                radius, angle = self._stored_pivot_polar
                self.pivot_widget.load(
                    polar=True, radius=radius,
                    angle=local_rotation_to_board_deg(angle, eff_rot))
            else:
                self.pivot_widget.load()
            self._set_settings_editable(True)
            # The shift is converted at the ANCHOR's own angle (§X.2.3) — NOT
            # eff_rot (which adds the tree's dovоrот): the shift lives in the
            # base frame, before the tree turns.
            self._set_shift_editable(True)
            if self._stored_anchor_shift is not None:
                sx, sy = local_offset_to_board_mm(self._stored_anchor_shift,
                                                  anchor_rot)
                self.shift_x_edit.setText(str(sx))
                self.shift_y_edit.setText(str(sy))
            else:
                self.shift_x_edit.setText("")
                self.shift_y_edit.setText("")
        finally:
            self._loading_settings -= 1

    def _load_settings(self) -> None:
        """Seed the config-frame state from the tree and refresh the picker list,
        mode, visibility and board-frame display. Hidden for the create-tree
        dialog (tree is None)."""
        self._refresh_pivot_candidates()
        self._update_pivot_hint()
        # The toggle's label follows the map, which survives a GUI restart —
        # so it is (re)read whenever the form (re)loads a tree.
        self._refresh_show_markers_button()
        if self._tree is None:
            self.settings_box.setVisible(False)
            # No tree yet (create-tree dialog): there is no stored anchor to
            # carry a shift, and no live base to express one in board mm.
            self._set_shift_editable(False, reason=_(
                "The anchor shift is stored on an existing tree — create the "
                "tree first."))
            return
        self._loading_settings += 1
        try:
            self._stored_rotation = self._tree.rotation
            self._stored_pivot_xy = self._tree.pivot_xy
            self._stored_pivot_polar = self._tree.pivot_polar
            self._stored_pivot_ref = self._tree.pivot_ref
            self._stored_anchor_shift = getattr(self._tree.anchor, "shift_xy", None)
            if self._tree.pivot_ref is not None:
                mode = "node"
            elif (self._tree.pivot_xy is not None
                  or self._tree.pivot_polar is not None):
                mode = "coordinate"
            else:
                mode = "origin"
            idx = self.pivot_mode_combo.findData(mode)
            if idx >= 0:
                self.pivot_mode_combo.setCurrentIndex(idx)
            if self._tree.pivot_ref is not None:
                ci = self.pivot_ref_combo.findData(self._tree.pivot_ref)
                if ci >= 0:
                    self.pivot_ref_combo.setCurrentIndex(ci)
            self._apply_pivot_mode_visibility()
        finally:
            self._loading_settings -= 1
        self._reload_settings_display()

    def build_settings(self) -> tuple[Optional[dict], Optional[str]]:
        """(dict, error) — the tree's inner point + own angle in CONFIG-frame
        values, converted from the board frame the form shows (§W.4). Keys:
        pivot_xy / pivot_polar / pivot_ref / rotation. Writes nothing; apply()
        commits. Returns (None, None) for a tree-less (create-tree) form."""
        if self._tree is None:
            return None, None
        mode = self._pivot_mode()
        if mode == "node":
            ref = self.pivot_ref_combo.currentData()
            if not ref:
                return None, _(
                    "Suspension point: choose a node of this tree, or switch to "
                    "a coordinate.")
            if ref not in tree_pivot_ref_candidates(self._tree):
                return None, _(
                    "Suspension point: {ref!r} cannot be a handle of this "
                    "tree.").format(ref=ref)
            pivot_xy = pivot_polar = None
            pivot_ref = ref
        elif mode == "coordinate":
            base = self._conversion_base_deg()
            if base is None:
                return None, _(
                    "The anchor does not resolve — cannot convert the "
                    "suspension point.")
            eff_rot = base[0]
            fields, err = self.pivot_widget.build()
            if err:
                return None, err
            if "radius" in fields:
                pivot_xy = None
                pivot_polar = (
                    fields["radius"],
                    board_rotation_to_local_deg(fields["angle"], eff_rot))
            else:
                pivot_xy = board_offset_to_local_mm(
                    (fields["x"], fields["y"]), eff_rot)
                pivot_polar = None
            pivot_ref = None
        else:  # origin
            pivot_xy = pivot_polar = pivot_ref = None
        base = self._conversion_base_deg()
        if base is None:
            return None, _("The anchor does not resolve — cannot convert the angle.")
        anchor_rot = base[1]
        text = self.rotation_edit.text().strip()
        if text == "":
            shown = 0.0
        else:
            try:
                shown = float(text)
            except ValueError:
                return None, _("Angle: {text!r} is not a number.").format(text=text)
        rotation = board_rotation_to_local_deg(shown, anchor_rot)
        return ({"pivot_xy": pivot_xy, "pivot_polar": pivot_polar,
                 "pivot_ref": pivot_ref, "rotation": rotation}, None)

    def _build_shift(self) -> tuple[Optional[tuple], Optional[str]]:
        """The anchor's own (shift x y) in CONFIG (LOCAL) mm, or None, plus an
        optional error (§X.2.3). When the base resolves, the board-mm field is
        converted by the ANCHOR angle in force RIGHT NOW (never a cached one);
        when it does not resolve, the field is disabled and the STORED
        config-frame value is returned UNCHANGED — a disabled form can never
        reinterpret (and silently rewrite) the numbers."""
        if self._tree is None:
            return None, None
        base = self._conversion_base_deg()
        if base is None:
            return self._stored_anchor_shift, None
        anchor_rot = base[1]
        tx = self.shift_x_edit.text().strip()
        ty = self.shift_y_edit.text().strip()
        if tx == "" and ty == "":
            return None, None
        try:
            bx = float(tx) if tx != "" else 0.0
            by = float(ty) if ty != "" else 0.0
        except ValueError:
            return None, _("Shift: X and Y must be numbers.")
        if bx == 0.0 and by == 0.0:
            return None, None
        return board_offset_to_local_mm((bx, by), anchor_rot), None

    def _on_shift_edited(self) -> None:
        """A shift edit updates the CONFIG-frame state using the anchor angle in
        force RIGHT NOW (§X.2.3) — never a cached base (the 9887468 trap)."""
        if self._loading_settings or self._tree is None:
            return
        base = self._conversion_base_deg()
        if base is None:
            return
        anchor_rot = base[1]
        tx = self.shift_x_edit.text().strip()
        ty = self.shift_y_edit.text().strip()
        if tx == "" and ty == "":
            self._stored_anchor_shift = None
            return
        try:
            bx = float(tx) if tx != "" else 0.0
            by = float(ty) if ty != "" else 0.0
        except ValueError:
            return  # incomplete — build_anchor reports it on Apply
        if bx == 0.0 and by == 0.0:
            self._stored_anchor_shift = None
        else:
            self._stored_anchor_shift = board_offset_to_local_mm((bx, by), anchor_rot)

    def _anchor_identity(self) -> tuple[Optional[TreeAnchor], Optional[str]]:
        """The anchor from the mode/field rows, WITHOUT its own shift — the part
        the live-base probe needs (_conversion_base_deg). build_anchor() adds
        the shift on top. Adding a mode means one _TREE_ANCHOR_MODES entry and
        one branch HERE."""
        mode = self.mode_combo.currentData()
        if mode == "origin":
            return (TreeAnchor(ref=None, is_origin=True, is_external=False), None)
        if mode == "self":
            return (TreeAnchor(
                is_self=True,
                self_ref=self.self_combo.currentText().strip() or None,
                self_pad=self.self_pad_edit.text().strip() or None), None)
        if mode == "role":
            role = self.role_edit.currentText().strip()
            if not role:
                return (None, _("Role is required."))
            return (TreeAnchor(
                role=role, is_origin=False,
                anchor_sheet=self.sheet_edit.currentText().strip() or None,
                anchor_cluster=self.cluster_edit.currentText().strip() or None,
                anchor_pad=self.pad_edit.text().strip() or None), None)
        if mode == "point":
            point = self.point_edit.currentText().strip()
            if not point:
                return (None, _("Point name is required."))
            return (TreeAnchor(point=point, is_origin=False), None)
        # record / external
        ref = self.ref_combo.currentText().strip()
        if not ref:
            return (None, _("Ref is required."))
        # "external" mode = live-board refdes, never a config record name —
        # carry it as is_external so the resolver can't hit a name collision.
        return (TreeAnchor(ref=ref, is_origin=False,
                           is_external=(mode == "external")), None)

    def build_anchor(self) -> tuple[Optional[TreeAnchor], Optional[str]]:
        """Collect + validate the form into a TreeAnchor (including its own
        (shift x y), §X.2), or an error string — the same (value, error) idiom
        as AnchorOriginWidget.build()/build_node() (plan §2.2). Pure — no
        QMessageBox, no self.accept(); the caller (Apply in the Anchor tab, or
        _AnchorDialog's OK) decides how to surface an error (apply_status_label
        vs a modal warning)."""
        anchor, err = self._anchor_identity()
        if err:
            return (None, err)
        shift_xy, shift_err = self._build_shift()
        if shift_err:
            return (None, shift_err)
        anchor.shift_xy = shift_xy
        return (anchor, None)

    def apply(self) -> bool:
        """Anchor + tree-settings Phase B Apply (plan §2.3, §W.2): write the
        anchor, the inner point and the tree's own angle onto the tree in place
        and mark the dock dirty — stays open (the caller owns the button row).
        Resets _touched (design §9.4)."""
        anchor, err = self.build_anchor()
        if err:
            self.apply_status_label.setText(err)
            return False
        settings, serr = self.build_settings()
        if serr:
            self.apply_status_label.setText(serr)
            return False
        if self._tree is not None:
            self._tree.anchor = anchor
            self._tree.pivot_xy = settings["pivot_xy"]
            self._tree.pivot_polar = settings["pivot_polar"]
            self._tree.pivot_ref = settings["pivot_ref"]
            self._tree.rotation = settings["rotation"]
        if self._dock is not None:
            self._dock._mark_dirty()
            refresh = getattr(self._dock, "_refresh_tree_marks", None)
            if refresh is not None:
                refresh(self._tree)
        if self._tree is not None:
            # Re-read the committed tree so the display reflects exactly what
            # was written (and a no-op Apply round-trips bit-for-bit, §W.8.1).
            self._load_settings()
        self._touched = False
        self.apply_status_label.setText(_("Applied — keep editing or Redraw."))
        return True

    # ── Overlay circles of the anchor and the local-frame base (З) ────────
    # plan_2026_09_12_tree_point_markers.md. Both positions are LIVE reads, so
    # everything below runs on a worker — never IPC on the UI thread. The
    # toggle's state is READ FROM THE OWNER'S MAP (`tree-anchor/<this tree>`),
    # never a widget flag that could drift from what is really on the board —
    # the same discipline PointsDock's own toggle uses.

    def _tree_markers_shown(self) -> bool:
        """True when the owner holds THIS tree's anchor key — the toggle's
        state, read from the fact."""
        if self._tree is None:
            return False
        return overlay_markers.owner.has_key(_tree_anchor_key(self._tree.name))

    def _refresh_show_markers_button(self) -> None:
        """The label follows the fact: with no `tree-anchor/<this tree>` key
        the button offers to draw the circles, with one it offers to take them
        down."""
        self.show_markers_button.setText(
            _("Hide tree markers") if self._tree_markers_shown()
            else _("Show tree markers"))

    def _marker_connection(self):
        """The live connection, reached through the owning dock — None for a
        standalone form (the create-tree dialog, or a headless test form)."""
        dock = self._dock
        return getattr(getattr(dock, "_main_window", None), "connection", None)

    def _marker_adapter(self):
        """The LIVE board adapter at the moment the button is pressed (not the
        one cached when the form was built), or None without a board."""
        connection = self._marker_connection()
        board = getattr(connection, "board", None) if connection is not None else None
        return getattr(board, "adapter", None) if board is not None else None

    def _marker_payload(self) -> dict:
        """The worker payload — plain data only, no widgets. The forest lets
        tree_layout_base lay a pivot-ref tree out."""
        return {"adapter": self._marker_adapter(), "cfg": self._cfg,
                "tree": self._tree, "sheet_names": self._sheet_names,
                "forest": {t.name: t for t in self._all_trees}}

    def _on_show_markers(self) -> None:
        """The ONE toggle button (З.2.4). With no board it writes one Log line
        and does nothing else — never a modal, and the button never "sticks"
        (З.2.2/Е.2.6). Both halves run on a worker under start_long_op, the same
        lock the whole-tree redraw uses."""
        if self._tree is None:
            return
        if self._marker_adapter() is None:
            show_message(_("Not connected."), _ERROR_STYLE, logger)
            return
        if self._tree_markers_shown():
            self._hide_tree_markers()
            return
        self._active_op = start_long_op(
            self._marker_connection(), (self.show_markers_button,),
            _show_tree_markers_worker, self._finish_show_tree_markers,
            self._on_show_markers_failed, self._marker_payload())

    def _finish_show_tree_markers(self, result: dict) -> None:
        if result.get("error"):
            show_message(result["error"], _ERROR_STYLE, logger)
        elif result.get("warning"):
            show_message(result["warning"], _WARN_STYLE, logger)
        self._refresh_show_markers_button()

    def _on_show_markers_failed(self, message: str) -> None:
        show_message(
            _("Show tree markers failed: {error}").format(error=message),
            _ERROR_STYLE, logger)

    def _forget_tree_marker_keys(self) -> list:
        """Pop this tree's two keys from the owner's map and return their
        uuids — state only, no board call at all."""
        if self._tree is None:
            return []
        uuids: list = []
        for key in _tree_marker_keys(self._tree.name):
            uuid = overlay_markers.owner.forget_key(key)
            if uuid:
                uuids.append(uuid)
        return uuids

    def _hide_tree_markers(self) -> None:
        """UI thread: forget both keys AT ONCE (so the button flips
        immediately) and delete the shapes on a worker — the split
        cell_anchor_view.cleanup() uses. OUR two keys only: shapes of other
        namespaces/consumers on the same layer are never touched."""
        uuids = self._forget_tree_marker_keys()
        self._refresh_show_markers_button()
        adapter = self._marker_adapter()
        if not uuids or adapter is None:
            return
        self._active_op = start_long_op(
            self._marker_connection(), (self.show_markers_button,),
            board_overlay.remove_overlay,
            lambda _result: self._refresh_show_markers_button(),
            self._on_show_markers_failed, adapter, uuids)

    def _do_toggle_markers(self) -> None:
        """Synchronous composition of the toggle — for tests and any headless
        caller that must not return until the circles are on/off (the same
        `_do_*` idiom PointsDock uses for its own toggle)."""
        if self._tree is None:
            return
        adapter = self._marker_adapter()
        if adapter is None:
            show_message(_("Not connected."), _ERROR_STYLE, logger)
            return
        if self._tree_markers_shown():
            uuids = self._forget_tree_marker_keys()
            if uuids:
                board_overlay.remove_overlay(adapter, uuids)
            self._refresh_show_markers_button()
            return
        self._finish_show_tree_markers(
            _show_tree_markers_worker(self._marker_payload()))

    def redraw(self) -> None:
        """Anchor-tab Phase B Redraw (plan §2.3): apply() first, then the
        existing whole-tree redraw — an anchor moves the WHOLE tree, a
        point-redraw for it has no meaning (design §9.3)."""
        if not self.apply():
            return
        if self._dock is not None:
            self._dock._on_redraw_whole_tree()
            self.apply_status_label.setText(_("Applied — whole-tree redraw started."))


class _AnchorDialog(QDialog):
    """Thin modal wrapper around a single AnchorFormWidget (plan
    plan_2026_09_04_trees_dock_master_detail.md §2.4): the form is
    modal-agnostic (a plain QWidget); THIS class adds a pure OK/Cancel row and
    exec()s it. Kept because _on_create_tree (Tools → Trees → Create tree) —
    the master flow for a NEW tree that does not exist yet — still needs a
    modal anchor picker (a persistent tab cannot host the anchor of a tree
    that is not created). Every form field/method is reachable on the dialog
    (delegated to the embedded form) so the existing tests keep working until
    §6 ports them onto AnchorFormWidget directly."""
    _form: Optional[AnchorFormWidget] = None

    def __init__(self, parent, ref_candidates, *, cfg=None, sheet_names=None,
                 role_candidates=None, cluster_candidates=None, existing=None,
                 tree=None):
        super().__init__(parent)
        self.setWindowTitle(_("Set anchor"))
        self._result: Optional[TreeAnchor] = None
        # The FORM is built with the original `parent` (the TreesDock, not this
        # dialog) so its _dock resolves to the dock that owns _mark_dirty —
        # addWidget below reparents the form visually without touching _dock.
        self._form = AnchorFormWidget(
            parent, ref_candidates, cfg=cfg, sheet_names=sheet_names,
            role_candidates=role_candidates,
            cluster_candidates=cluster_candidates, existing=existing,
            tree=tree)
        layout = QVBoxLayout(self)
        layout.addWidget(self._form)
        buttons = QHBoxLayout()
        self.ok_button = QPushButton(_("OK"))
        self.ok_button.clicked.connect(self._accept)
        cancel_button = QPushButton(_("Cancel"))
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    def __getattr__(self, name):
        # Delegate any form field/method to the embedded AnchorFormWidget (the
        # dialog keeps the QDialog surface only). Only reached when normal
        # attribute lookup fails — form-only names like mode_combo/ref_combo/
        # role_edit/tabs/etc.
        form = self.__dict__.get("_form")
        if form is not None:
            return getattr(form, name)
        raise AttributeError(name)

    def _accept(self) -> None:
        anchor, err = self._form.build_anchor()
        if err:
            QMessageBox.warning(self, _("Set anchor"), err)
            return
        self._result = anchor
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
