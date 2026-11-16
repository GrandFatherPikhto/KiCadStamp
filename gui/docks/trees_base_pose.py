# gui/docks/trees_base_pose.py
"""The frame a tree node's stored `xy`/`rotation` are expressed against — resolved LIVE.

Plan plan_2026_11_09_node_redraw_from_live_parent. Read ("Read current position") and
Redraw of ONE node used to take the base from TWO different places: Read resolved the
parent ON THE BOARD, while the Redraw button ran `ApplyPipeline(only=[ref])`, whose
placement walks the tree from its ANCHOR. Measured live (HP-Power-v099, tree `switch_a`,
node `conn_out_sw_A`): the read gave (69.994, 8.014) and was right, the redraw of the same
numbers put the pads below the board, and a hand-picked (102, -27.5) put them right on the
tree tab and off the edge in `main`. THE CONTRACT this module serves: **Read, then Redraw of
the same node does not move it**, and neither Grid Origin nor the tree anchor may influence
the result for a CHILD node.

WHY THE FUNCTIONS MOVED HERE. `_resolve_node_base_pose`, `_mount_anchor_pose` and
`_resolve_probe_ref` used to live in `trees_dock`. The WORKER must resolve the base (a board
read belongs off the UI thread), and `trees_dock` imports `cascade`, so `cascade` importing
`trees_dock` back would be a cycle. They are moved VERBATIM — same rules, same refusals — and
`trees_dock` re-imports them under their old names, so nothing that patches them breaks.

WHAT IS NEW HERE: `redraw_target` — the inverse of the Read. Read turns a live child pose into
the two numbers a node stores (`_resolve_live_offset`); this turns the stored numbers plus the
LIVE base back into the absolute pose the redraw must place the component at. The math is not
new either: `local_offset_to_board_mm` / `local_rotation_to_board_deg` are the project's one
board<->config frame pair, and the composition is the one `tree_position.apply_rigid_override`
already uses for a rigid-group redraw.

A REFUSAL IS ALWAYS BETTER THAN A FALLBACK. When the parent cannot be read, the old behaviour
was to lay the child out from the tree — and THAT is the defect (a module's offset, a Grid
Origin, a moved parent all made the numbers wrong while looking plausible). Every refusal here
raises `ValidationError` BEFORE anything is placed, so the caller logs one line and the board
is not touched.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from kicadstamp.anchor_graph import Record, build_records
from kicadstamp.component_address import resolve_component_footprint
from kicadstamp.exceptions import NonRigidClusterError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.link_trees import (_build_by_key_index, _build_by_name_index,
                                   _resolve_node_ref)
from kicadstamp.domain.board import BoardLayer
from kicadstamp.domain.geometry import Vector2
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver,
    resolve_anchor_pad_position,
)
from kicadstamp.tree_position import (MountFrames, _anchor_base_live_position,
                                      local_offset_to_board_mm,
                                      local_rotation_to_board_deg,
                                      mount_node_poses,
                                      resolve_base_live_position,
                                      resolve_base_rotation_deg,
                                      tree_layout_base)
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

from .live_position import read_record_live_pose

logger = logging.getLogger(__name__)

__all__ = [
    "child_live_pose",
    "node_offset_of",
    "redraw_target",
    "resolve_node_base_pose",
]

#: "resolve the child yourself from (ref, kind)" — the default of `child_live_pose`'s
#: `child_record`. `None` is a MEANINGFUL value (an external refdes, which has no record),
#: so it cannot double as "not given".
_PROBE_CHILD = object()


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


def _component_footprint_is_mirrored(fp) -> bool:
    """Whether the live footprint a COMPONENT node's address resolved to sits on
    the BACK copper side — the mirror the trees layer cannot store, refused by
    the caller exactly like a mirrored base or a mirrored record child
    (plan_2026_09_11 §2.3 discipline). A duck-typed double without a usable layer
    reads as "not mirrored", the same tolerance tree_position's
    _footprint_relative_mirror uses."""
    return getattr(fp, "layer", None) == BoardLayer.BL_B_Cu


def child_live_pose(cfg, adapter, sheet_names, ref: str, kind: str | None, *,
                    component_address: Optional[TreeAnchor] = None,
                    child_record=_PROBE_CHILD, snapshot=None,
                    verdict: str | None = None
                    ) -> tuple[Vector2, Optional[float], bool]:
    """(position_nm, rotation_deg | None, mirror) of the record or COMPONENT a node
    names — the child's pose ON THE BOARD, absolute, with no base subtracted.

    This is the "where does this thing really stand" half of
    `trees_dock._resolve_live_offset`, moved here (plan_2026_11_16_tree_anchor_read
    §1) so that ONE function answers it and the anchor read cannot grow a second
    copy: the offset read subtracts a base from it, the tree-anchor read compares it
    with the layout. Nothing else changed — the same dispatcher, the same
    `strict=True` on a placement Entity, the same two mirror refusals.

    A COMPONENT node's `component_address` replaces the config probe for the CHILD
    only: a component node's `ref` is a LOCAL NAME (link_trees resolves no record for
    kind "component"), so the component it names can only come from the node's own
    ADDRESS, resolved by component_address.resolve_component_footprint — the SAME
    resolver the materializer and the rigid-group capture use. A mirrored live
    component is refused here with an honest message instead of being silently read
    as an unmirrored pose.

    `child_record` — the record the caller already resolved, or `_PROBE_CHILD` (the
    default) to resolve it here. `trees_dock._resolve_live_offset` passes its own
    probe so the ORDER of its failures (probe -> base -> child) stays byte for byte
    what the cells pin; `None` is a legitimate value (an external refdes) and does
    NOT mean "resolve it yourself".

    `verdict` (Ч2 of plan_2026_10_15_extract_and_read_paths_cleanup): the closing
    line of the mirrored CHILD refusal. None keeps the historical footer; the READING
    callers pass read_only_verdict()."""
    if component_address is not None:
        # The component the node ADDRESSES, never a config record (see the
        # docstring). The address's `pad` takes no part in the resolution: a pad
        # says where the component is SEATED (the node's own anchor mode), not
        # which component it is — component_address's own rule.
        synthetic = TreeNode(ref=ref, kind="component", xy=None, polar=None,
                             rotation=0.0, name=None, group=None, children=[],
                             anchor=component_address)
        fp = resolve_component_footprint(adapter, synthetic, sheet_names)
        return fp.position, float(fp.angle_deg), _component_footprint_is_mirrored(fp)
    if child_record is _PROBE_CHILD:
        child_record, _is_external = _resolve_probe_ref(cfg, ref, kind)
    if child_record is not None and getattr(child_record, "kind", None) == "placement":
        # strict=True (Ч1-А): the child's offset/rotation are saved into the
        # config, so a non-rigid cluster of the CHILD's own instance refuses.
        child_pose = read_record_live_pose(adapter, cfg, ref, child_record,
                                          sheet_names, snapshot=snapshot,
                                          strict=True)
        return child_pose.position, child_pose.rotation_deg, child_pose.mirror
    # Historic path, kept on tree_position's names so the pass-through kinds
    # (clone/coordinate/point/external) answer exactly as before.
    child_pos = resolve_base_live_position(adapter, cfg, ref, child_record, {},
                                           sheet_names, snapshot=snapshot)
    child_deg = resolve_base_rotation_deg(adapter, cfg, ref, child_record,
                                          sheet_names, snapshot=snapshot)
    return child_pos, child_deg, False


def _mount_anchor_pose(cfg, adapter, sheet_names, tree: Tree,
                       mount_node: TreeNode, *, snapshot=None) -> MountFrames:
    """BOTH frames of ONE mount node, from ONE board read — the seam over
    tree_position.mount_node_poses (plan_2026_10_14_tree_group_move_and_mount_rebase).

    `anchor_pos`/`anchor_rot` — the node's OWN ANCHOR: the frame its own `xy`/`rotation` are
    stored in. `point_pos`/`point_rot` — the node's POINT: that anchor composed with the
    node's own xy/rotation, i.e. the frame EVERYTHING UNDER the node is stored against (and
    the frame the redraw lays a child from). The two travel together because the second is
    pure composition of the first — asking twice would mean two resolves and a second copy
    of the composition rule, which is how the dock and the redraw drifted apart on
    2026-10-14. A mount node's ref is a LOCAL NAME and is never resolved against the config
    (rule Б1), so this is its only path."""
    forest = {t.name: t for t in (getattr(cfg, "trees", None) or [])}
    tree_base_pos, tree_base_rot = tree_layout_base(
        adapter, cfg, tree, sheet_names, forest, snapshot=snapshot)
    return mount_node_poses(mount_node, tree, tree_base_pos, tree_base_rot,
                            adapter, cfg, sheet_names, forest,
                            snapshot=snapshot)


def resolve_node_base_pose(cfg, adapter, sheet_names, tree: Tree,
                           parent_node: Optional[TreeNode],
                           base_anchor: Optional[TreeAnchor], *, snapshot=None
                           ) -> tuple[Vector2, Optional[float], bool]:
    """(position_nm, rotation_deg | None, mirror) of the frame a node's stored xy/rotation
    are expressed against — THE ONE resolver, shared by the live read
    (`trees_dock._resolve_live_offset`), the node FORM's board-frame conversion and now the
    child-node Redraw worker, so the three can never disagree about where the base is
    (plan_2026_09_11 §3).

    - base_anchor (the node's OWN anchor, "Relative to component") — resolved live through
      the SAME ComponentResolver the recursive walks use. A role anchor names a COMPONENT,
      never a cell instance, so it has no mirror concept (mirror=False).
    - parent_node is None — the tree's OWN anchor, via `_anchor_base_live_position` (every
      anchor mode: origin/auto/role/point/ref).
    - a MOUNT parent node — the frame the CALLER's node is stored in is that mount node's
      POINT: its anchor composed with the node's own xy/rotation (`_mount_anchor_pose`),
      the same composition `layout_tree_from_base` uses. A mount node's ref is a LOCAL NAME
      and is never resolved against the config (rule Б1); mirror=False for the same reason
      as the base_anchor branch.
    - any other parent NODE — its live pose via read_record_live_pose: a placement Entity is
      read from its LIVE CLUSTER, not from the tree that places it (the BASE has the same
      disease as the child, plan §2.2).

    Raises ValidationError on any resolution failure — the callers turn it into a warning
    (read) / a raw+disabled form (no connection) / a Log line and no write (redraw), never a
    guess."""
    if base_anchor is not None:
        resolver = ComponentResolver(adapter, cfg, sheet_names, snapshot=snapshot)
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
            adapter, cfg, tree, sheet_names, snapshot=snapshot)
        return parent_pos, parent_deg, False
    if parent_node.kind == "mount":
        frames = _mount_anchor_pose(cfg, adapter, sheet_names, tree,
                                    parent_node, snapshot=snapshot)
        return frames.point_pos, frames.point_rot, False
    parent_record, _is_external = _resolve_probe_ref(
        cfg, parent_node.ref, parent_node.kind)
    # strict=True (Ч1-А): the base is WRITTEN into the node's numbers, so a non-rigid
    # PARENT cluster refuses here — and the refusal names the parent's cluster/cell (the
    # frame being read), never the caller's node.
    pose = read_record_live_pose(adapter, cfg, parent_node.ref, parent_record,
                                 sheet_names, snapshot=snapshot, strict=True)
    return pose.position, pose.rotation_deg, pose.mirror


def _base_label(parent_node: Optional[TreeNode],
                base_anchor: Optional[TreeAnchor]) -> str:
    """How a refusal NAMES the frame it could not read: the node's own anchor ROLE
    ("Relative to component"), else the parent node's ref, else the tree's anchor."""
    if base_anchor is not None:
        return base_anchor.role or "anchor"
    if parent_node is not None:
        return parent_node.ref or parent_node.kind or "?"
    # Not "the tree anchor": that msgid already exists in the catalogs meaning the ANCHOR
    # CHOICE in the form ("Set anchor"), and a fuzzy match would put the wrong sentence in
    # a refusal that names a frame.
    return _("this tree's own anchor")


def redraw_target(cfg, adapter, sheet_names, tree: Tree, node: TreeNode,
                  parent_node: Optional[TreeNode], *,
                  base_anchor: Optional[TreeAnchor] = None,
                  snapshot=None) -> tuple[Vector2, float]:
    """(absolute position_nm, absolute rotation_deg) the redraw must PLACE this node at.

    The inverse of the Read: the base is resolved LIVE (the SAME `resolve_node_base_pose`
    the read uses), then the node's stored `xy`/`rotation` are carried into the board frame
    with the project's one frame pair (`local_offset_to_board_mm`,
    `local_rotation_to_board_deg`) — the composition `apply_rigid_override` performs for a
    rigid-group redraw.

    REFUSES (always before anything is placed, never falling back to the tree layout):

    * the base cannot be resolved — the parent is not on the board, is ambiguous, or its
      cluster is not rigid. The tree-layout fallback IS the defect: it reads a Grid Origin
      and a tree anchor the board knows nothing about;
    * the base is MIRRORED — a tree node stores no mirror at all (neither tree_position nor
      link_trees nor entity_placement reads or writes one), so a mirrored base is refused
      instead of being silently imported unmirrored. The Read refuses it the same way.

    A top-level node whose TREE is embedded by a `module` node is refused by the CALLER
    (`TreesDock._redraw_edited_node`): only the dock knows the embedding, and only the dock
    knows the marker to name in the sentence."""
    label = _base_label(parent_node, base_anchor)
    try:
        base_pos, base_deg, mirrored = resolve_node_base_pose(
            cfg, adapter, sheet_names, tree, parent_node, base_anchor,
            snapshot=snapshot)
    except NonRigidClusterError:
        # Already names the CELL/CLUSTER that is not a rigid copy — that identity IS
        # the reason, so a prefix naming the parent would only bury it.
        raise
    except ValidationError as exc:
        raise ValidationError(_(
            "tree node {node!r}: its base ({base}) could not be read from the board — "
            "nothing is placed, and the tree layout is NOT used instead. {error}"
        ).format(node=node.ref, base=label, error=exc)) from exc
    if mirrored:
        raise ValidationError(_(
            "tree node {node!r}: its base ({base}) is MIRRORED on the board — a tree "
            "node stores no mirror, so nothing is placed").format(
            node=node.ref, base=label))
    deg = float(base_deg or 0.0)
    dx_mm, dy_mm = local_offset_to_board_mm(node_offset_of(node), deg)
    position = Vector2.from_xy(
        base_pos.x + int(round(dx_mm * MM)), base_pos.y + int(round(dy_mm * MM)))
    rotation_deg = local_rotation_to_board_deg(float(node.rotation or 0.0), deg)
    return position, rotation_deg


def node_offset_of(node: TreeNode) -> tuple[float, float]:
    """The node's stored offset in the base's own frame, mm — `xy` when it has one, else the
    `polar` pair converted to the same flat vector (`tree_position.node_offset` is the nm
    twin; this one is the mm pair the frame conversion takes)."""
    if node.xy is not None:
        return (float(node.xy[0]), float(node.xy[1]))
    if node.polar is not None:
        radius, angle_deg = float(node.polar[0]), float(node.polar[1])
        r = math.radians(angle_deg)
        return (radius * math.cos(r), radius * math.sin(r))
    return (0.0, 0.0)
