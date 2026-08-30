# kicadstamp/tree_position.py
"""Position resolution + curated-redraw planning for the trees layer.

Two distinct concepts (design_2026_08_26_tree_position_resolution.md, Q1):

1. A node's OWN position is pure composition, no adapter:
   `node_position(node, parent_position, parent_rotation_deg)` = the parent's
   position + node_offset(node) rotated into the parent's frame.
   `node_offset` maps xy -> flat (x_mm, y_mm), polar -> the rotated offset
   vector via the existing local_to_absolute primitive (origin = 0 just
   extracts the rotated offset, nothing invented). A node's own `rotation`
   never feeds this — it rotates the node's own geometry later, never the
   offset vector. The PARENT's rotation IS applied to the child's offset
   (the offset is expressed in the parent's LOCAL frame, same convention as
   a ClonePlacement.xy shift inside a rotated parent frame) — REVERSED
   2026-08-29 by Denis's explicit request
   (plan_2026_08_29_tree_live_rigid_redraw.md §2; the pre-2026-08-29 design
   tree_position_resolution.md §1.3 "parent rotation NEVER applied to the
   child offset" is superseded — the old guard test was replaced by the
   opposite guarantee).

2. The LIVE position of a RECORD is only needed as a "base" (a tree anchor,
   or a parent node outside the curated selection). That is a thin kind
   dispatcher over the existing resolvers — the ONLY thing that talks to the
   live board here.

curated_redraw_plan() turns a LinkedTree + a set of selected refs into the
ordered name list run_cascade can apply (parent strictly before child), plus
the structural "parent not in selection" warnings.
"""
import dataclasses
import logging

from .anchor_graph import Record
from .exceptions import ValidationError, format_fatal_error
from .i18n import _
from .domain.geometry import Vector2
from .geometry.clone_geometry import clone_shift_mm
from .geometry.spoke_layout import local_to_absolute, rotate_local_offset
from .link_trees import LinkedNode, LinkedTree, inline_anchor_field
from .placement.services.clone_position_calculator import ClonePositionCalculator
from .placement.services.component_resolver import (
    ComponentResolver,
    resolve_anchor_pad_position,
    resolve_footprint_by_ref,
)
from .placement.services.coordinate_position_calculator import (
    _anchor_offset_mm,
    _has_external_anchor,
    _resolve_external_anchor,
    resolve_target_position,
)
from .placement.services.point_resolver import resolve_point_chain
from .trees import TreeNode
from .utils.units import MM

_ORIGIN = Vector2.from_xy(0, 0)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class PositionOverride:
    """Absolute placement override for ONE record during a single redraw run.

    Non-persistent by construction: it replaces the record's own
    anchor/position+rotation resolution for THIS run only — the saved config
    is never rewritten, the record's fields (anchor_ref/anchor_role/anchor_
    point/xy/polar/rotation_deg) are never mutated or replaced, so registry
    identity (clone_anchor_id built from the real record fields) is preserved.

    The choice of this mechanism over in-memory field substitution is
    documented in handoff_2026_08_29_tree_live_rigid_redraw_step0.md §3-§4
    (plan_2026_08_29_tree_live_rigid_redraw.md §3 — Option 1)."""
    position: Vector2
    rotation_deg: float


def node_offset(node: TreeNode) -> Vector2:
    """Pure geometry, no adapter. xy -> flat (x_mm, y_mm) in board units;
    polar -> local_to_absolute(origin=0, radius, 0, angle_deg) — the rotated
    offset vector only, nothing invented. Node's own `rotation` does NOT feed
    this (it rotates the node's own geometry later, never the offset vector)."""
    if node.xy is not None:
        return Vector2.from_xy(int(node.xy[0] * MM), int(node.xy[1] * MM))
    if node.polar is not None:
        radius_mm, angle_deg = node.polar
        return local_to_absolute(_ORIGIN, radius_mm, 0.0, angle_deg)
    return Vector2.from_xy(0, 0)


def node_position(node: TreeNode, parent_position: Vector2,
                  parent_rotation_deg: float = 0.0) -> Vector2:
    """parent_position + node_offset(node), the offset rotated into the
    parent's frame first. Pure composition, no adapter, no kind dispatch
    (design Q2). parent_rotation_deg — the parent's rotation, default 0.0
    (flat composition, the original behavior). When the parent is rotated,
    the node's offset is expressed in the parent's LOCAL (unrotated) frame —
    the same convention apply_clone_geometry uses for a clone's xy shift
    inside a rotated parent frame — so it is rotated by parent_rotation_deg
    before being added, via the same rotate_local_offset primitive as the
    live path (plan_2026_08_29_tree_live_rigid_redraw.md §2). This REVERSES
    the pre-2026-08-29 design §1.3 ("parent rotation is NEVER applied to the
    child offset") by Denis's explicit request — the old guard test was
    replaced by the opposite guarantee."""
    offset = node_offset(node)
    if parent_rotation_deg:
        offset = rotate_local_offset(offset.x / MM, offset.y / MM, parent_rotation_deg)
    return Vector2.from_xy(parent_position.x + offset.x, parent_position.y + offset.y)


def child_local_offset(child_pos: Vector2, parent_pos: Vector2,
                       parent_rotation_deg: float) -> Vector2:
    """Pure capture half of the rigid-group mechanics (plan_2026_08_29_
    tree_live_rigid_redraw.md §1): the child's absolute offset from the
    parent, expressed in the parent's LOCAL (unrotated) frame. Inverse of
    child_absolute_position — together they are the "live capture -> apply
    with rotation" round-trip: capturing at the parent's OLD rotation and
    applying to the parent's NEW rotation re-projects the child's offset so
    it rotates WITH the parent."""
    delta_mm_x = (child_pos.x - parent_pos.x) / MM
    delta_mm_y = (child_pos.y - parent_pos.y) / MM
    return rotate_local_offset(delta_mm_x, delta_mm_y, -parent_rotation_deg)


def child_absolute_position(parent_pos: Vector2, parent_rotation_deg: float,
                            local_offset: Vector2) -> Vector2:
    """Pure apply half of the rigid-group mechanics (plan §1): the child's
    absolute position = the parent's position + local_offset rotated into the
    parent's frame. Inverse of child_local_offset."""
    offset = rotate_local_offset(local_offset.x / MM, local_offset.y / MM,
                                 parent_rotation_deg)
    return Vector2.from_xy(parent_pos.x + offset.x, parent_pos.y + offset.y)


def resolve_record_live_position(adapter, cfg, rec: Record, resolved_points,
                                 sheet_names) -> Vector2:
    """Thin kind dispatcher, called ONLY for a base with a real record:
      - "clone": ClonePositionCalculator._resolve_anchor() + clone_shift_mm()
      - "point": resolve_point_chain()
      - "rule": ComponentResolver.resolve_anchor_fp() -> fp.position (or
        resolve_anchor_pad_position() if anchor_pad set)
      - "coordinate": resolve_target_position() (absolute) or
        _resolve_external_anchor() + _anchor_offset_mm() (anchor-relative)
    ("external" never reaches here — resolve_base_live_position handles it
    before this dispatcher.)
    """
    kind = rec.kind
    if kind == "clone":
        calc = ClonePositionCalculator(adapter, cfg, sheet_names, resolved_points)
        anchor = calc._resolve_anchor(rec.obj)  # Vector2 | None (None = absolute mode)
        shift_x_mm, shift_y_mm = clone_shift_mm(rec.obj)
        base = anchor if anchor is not None else _ORIGIN
        return Vector2.from_xy(base.x + int(shift_x_mm * MM),
                               base.y + int(shift_y_mm * MM))

    if kind == "placement":
        # Entity records carry NO position by design (design_2026_08_30_entity_
        # placement_grammar.md §3). Their live position as a tree base/anchor is
        # resolved from the TREE (anchor / parent node offset) at Phase 4 —
        # never from the record. Reaching this branch means a caller asked for
        # a record-only live position of an Entity, which is not wired yet.
        raise AssertionError(
            "entity placement live position is resolved from the tree at apply "
            "time (phase 4); the Entity record itself carries no anchor/xy")

    if kind == "point":
        resolved = resolve_point_chain(adapter, cfg.points, rec.name, sheet_names)
        return resolved.position

    if kind == "rule":
        resolver = ComponentResolver(adapter, cfg, sheet_names)
        fp = resolver.resolve_anchor_fp(
            rec.anchor_ref, rec.anchor_role, rec.anchor_sheet, rec.anchor_cluster,
            label=rec.name)
        anchor_pad = getattr(rec.obj, "anchor_pad", None)
        if anchor_pad is None:
            return fp.position
        return resolve_anchor_pad_position(adapter, fp, anchor_pad, rec.name)

    if kind == "coordinate":
        cp = rec.obj
        if _has_external_anchor(cp):
            anchor = _resolve_external_anchor(adapter, cp, cfg.points, sheet_names, rec.name)
            offset_x_mm, offset_y_mm = _anchor_offset_mm(cp)
            return Vector2.from_xy(anchor.x + int(offset_x_mm * MM),
                                   anchor.y + int(offset_y_mm * MM))
        target, _ = resolve_target_position(cp)
        return target

    raise AssertionError(f"unreachable record kind for live resolution: {kind!r}")


def resolve_base_live_position(adapter, cfg, ref: str, record: Record | None,
                               resolved_points, sheet_names) -> Vector2:
    """Entry point for a BASE (tree anchor, or a parent node outside the
    curated selection) — the only thing that needs a LIVE board position.
    record is None (external ref) -> resolve_footprint_by_ref(adapter, ref,
    ...).position directly, no kind dispatch (external has no Record.kind).
    record is not None -> resolve_record_live_position(...)."""
    if record is None:
        fp = resolve_footprint_by_ref(adapter, ref, ref)
        return fp.position
    return resolve_record_live_position(adapter, cfg, record, resolved_points, sheet_names)


def resolve_record_rotation_deg(adapter, cfg, rec: Record, sheet_names) -> float | None:
    """Kind dispatcher for a record's CURRENT rotation — NOT computed from a
    live footprint reading for clone/coordinate (they already store it
    explicitly in config, with well-established "relative to what" semantics
    per their own docstrings — see config/models.py ClonePlacement/
    CoordinatePlacement). Only rule/external genuinely need a LIVE read
    (their own record has no rotation field at all). point -> None (no
    rotation concept by design, config/points.py). Returns None when the
    kind has no rotation concept — the caller must treat None as "not
    available", never silently 0."""
    kind = rec.kind
    if kind == "clone":
        return rec.obj.rotation_deg
    if kind == "placement":
        # Entity has no rotation concept of its own (rotation lives in the tree
        # node) — same "not available" convention as point records; the caller
        # treats None as "assume 0.0 for composition" (never silently 0).
        return None
    if kind == "coordinate":
        cp = rec.obj
        # Anchor-relative mode — a coordinate-kind record may LEGALLY carry an
        # inline anchor now that FORK-1 lives at redraw-select time
        # (plan_2026_08_28_fork1_move_to_redraw_time.md), so it can be used as
        # a tree base. Its rotation is rotation_deg if set, else angle_deg in
        # polar-offset mode, else 0.0 — the SAME rule the move builder applies
        # at plan time (build_coordinate_moves) and the same anchor-awareness
        # resolve_record_live_position's coordinate branch already has.
        if _has_external_anchor(cp):
            return (cp.rotation_deg if cp.rotation_deg is not None
                    else (cp.angle_deg if cp.radius_mm is not None else 0.0))
        # Absolute mode — resolve_target_position already returns the rotation
        # for both Cartesian and fixed-centre polar.
        _, rotation_deg = resolve_target_position(cp)
        return rotation_deg
    if kind == "rule":
        resolver = ComponentResolver(adapter, cfg, sheet_names)
        fp = resolver.resolve_anchor_fp(
            rec.anchor_ref, rec.anchor_role, rec.anchor_sheet, rec.anchor_cluster,
            label=rec.name)
        return fp.angle_deg
    if kind == "point":
        return None
    raise AssertionError(f"unreachable record kind for rotation: {kind!r}")


def resolve_base_rotation_deg(adapter, cfg, ref: str, record: Record | None,
                              sheet_names) -> float | None:
    """Entry point for a BASE's rotation (tree anchor, or a parent/child node).
    record is None -> external ref, live footprint's own angle_deg. record is
    not None -> resolve_record_rotation_deg(...). is_origin (record=None AND
    ref=None, the tree's own (origin) anchor) is the caller's job to special-
    case as 0.0 BEFORE calling this — mirrors resolve_base_live_position's own
    convention (origin is handled by the caller, see _link_tree's anchor)."""
    if record is None:
        fp = resolve_footprint_by_ref(adapter, ref, ref)
        return fp.angle_deg
    return resolve_record_rotation_deg(adapter, cfg, record, sheet_names)


def relative_rotation_deg(child_deg: float, parent_deg: float) -> float:
    """Relative rotation of a child angle w.r.t. a parent angle, normalized to
    (-180, 180] — the SAME (a - b + 180) % 360 - 180 normalization already
    used by position_tracker.py:48 and channel_copy.py:394, not reinvented."""
    return (child_deg - parent_deg + 180.0) % 360.0 - 180.0


@dataclasses.dataclass
class RigidCapture:
    """Captured rigid-group state for one selected node, taken BEFORE anything
    moved (plan_2026_08_29_tree_live_rigid_redraw.md §1): the node's offset
    from its parent in the parent's OLD local frame (so it can be re-projected
    into the parent's NEW frame at apply time), plus the node's rotation
    relative to its parent (preserved across the parent's rotation change)."""
    local_offset: Vector2
    relative_rotation: float


def _tree_node_index(tree: LinkedTree) -> dict[str, LinkedNode]:
    """node.ref -> LinkedNode for every node in the tree (trees are shallow —
    a plain walk, no recursion concerns)."""
    index: dict[str, LinkedNode] = {}

    def walk(nodes: list[LinkedNode]) -> None:
        for ln in nodes:
            index[ln.node.ref] = ln
            walk(ln.children)

    walk(tree.nodes)
    return index


def _node_parent_map(tree: LinkedTree) -> dict[str, tuple[str | None, Record | None, bool]]:
    """node.ref -> (parent_ref, parent_record, parent_is_anchor). The tree
    anchor is the parent of every top-level node (parent_is_anchor=True); a
    nested node's parent is its enclosing LinkedNode. An origin anchor has
    parent_ref=None AND parent_record=None — its position is the absolute
    origin (0,0) and its rotation 0.0 (callers special-case it before calling
    resolve_base_*)."""
    parent_map: dict[str, tuple[str | None, Record | None, bool]] = {}
    anchor = tree.anchor
    anchor_ref = anchor.anchor.ref
    anchor_record = anchor.record
    anchor_is_origin = anchor.is_origin

    def walk(nodes: list[LinkedNode], parent_ref, parent_record, parent_is_anchor) -> None:
        for ln in nodes:
            parent_map[ln.node.ref] = (parent_ref, parent_record, parent_is_anchor)
            walk(ln.children, ln.node.ref, ln.record, False)

    walk(tree.nodes, anchor_ref, anchor_record, not anchor_is_origin)
    return parent_map


def _base_position_or_origin(adapter, cfg, ref, record, resolved_points, sheet_names) -> Vector2:
    """resolve_base_live_position with the origin-anchor special case: an
    origin anchor (ref=None AND record=None) is the absolute (0,0) point."""
    if ref is None and record is None:
        return _ORIGIN
    return resolve_base_live_position(adapter, cfg, ref, record, resolved_points, sheet_names)


def _base_rotation_or_zero(adapter, cfg, ref, record, sheet_names) -> float:
    """resolve_base_rotation_deg with the origin-anchor special case (0.0) and
    the None -> 0.0 assumption LOGGED (plan §1: a parent/base with no rotation
    concept is treated as 0.0 for this composition, but the assumption is never
    silent)."""
    if ref is None and record is None:
        return 0.0
    rot = resolve_base_rotation_deg(adapter, cfg, ref, record, sheet_names)
    if rot is None:
        logger.debug(_("base {ref!r}: no rotation concept — assumed 0.0 for "
                       "rigid-group composition").format(ref=ref))
        return 0.0
    return rot


def capture_rigid_state(adapter, cfg, tree: LinkedTree, names: list[str], sheet_names
                        ) -> tuple[dict[str, RigidCapture],
                                   dict[str, tuple[str | None, Record | None, bool]]]:
    """Capture half of the rigid-group redraw (plan_2026_08_29_
    tree_live_rigid_redraw.md §1): for every selected node (in `names`,
    topological order), read its CURRENT live position/rotation and its
    parent's, BEFORE anything is moved, and store the node's offset in the
    parent's LOCAL frame + the node's rotation relative to the parent. The
    apply half (cascade.run_curated_tree_redraw) re-projects these into the
    parent's NEW frame at apply time. Returns (captures, parent_map)."""
    index = _tree_node_index(tree)
    parent_map = _node_parent_map(tree)
    resolved_points: dict = {}
    captures: dict[str, RigidCapture] = {}
    for name in names:
        ln = index.get(name)
        if ln is None or ln.record is None:
            continue  # external/point never emit names; defensive only
        try:
            child_pos_old = resolve_base_live_position(adapter, cfg, ln.node.ref, ln.record,
                                                       resolved_points, sheet_names)
            child_rot_old = _base_rotation_or_zero(adapter, cfg, ln.node.ref, ln.record, sheet_names)
            parent_ref, parent_record, _is_anchor = parent_map[name]
            parent_pos_old = _base_position_or_origin(adapter, cfg, parent_ref, parent_record,
                                                      resolved_points, sheet_names)
            parent_rot_old = _base_rotation_or_zero(adapter, cfg, parent_ref, parent_record,
                                                    sheet_names)
        except Exception as exc:  # noqa: BLE001 — one node without a live base
            logger.warning(_("tree redraw: node {name!r} has no resolvable live "
                             "position ({error}) — redrawn from its own record "
                             "fields, not rigidly").format(name=name, error=exc))
            continue
        captures[name] = RigidCapture(
            local_offset=child_local_offset(child_pos_old, parent_pos_old, parent_rot_old),
            relative_rotation=relative_rotation_deg(child_rot_old, parent_rot_old),
        )
    return captures, parent_map


def apply_rigid_override(adapter, cfg, parent_ref, parent_record, capture: RigidCapture,
                         sheet_names) -> PositionOverride:
    """Apply half of the rigid-group redraw (plan §1): re-project the captured
    local offset into the parent's CURRENT (post-move) frame and preserve the
    node's rotation relative to the parent. Returns the PositionOverride to
    feed into the ApplyPipeline."""
    parent_pos_new = _base_position_or_origin(adapter, cfg, parent_ref, parent_record,
                                              {}, sheet_names)
    parent_rot_new = _base_rotation_or_zero(adapter, cfg, parent_ref, parent_record, sheet_names)
    child_pos_new = child_absolute_position(parent_pos_new, parent_rot_new, capture.local_offset)
    child_rot_new = parent_rot_new + capture.relative_rotation
    return PositionOverride(position=child_pos_new, rotation_deg=child_rot_new)


def curated_redraw_plan(linked_tree: LinkedTree, selected_refs: set[str]
                        ) -> tuple[list[str], list[str]]:  # (names, warnings)
    """DFS over the linked tree, parent strictly before child. A node emits
    into `names` (as record.name — record.name == node.ref by construction of
    link_trees's index) if record is not None and record.kind != "point" —
    REGARDLESS of whether its record carries an inline anchor (external AND
    point nodes are walked — needed as a live base for children's position
    resolve — but never emit a name: apply_only_filter has no "points" support
    at all, see apply_pipeline.py, and external isn't a config record to
    redraw).

    A SELECTED node whose record already carries an inline anchor
    (anchor_ref/anchor_role/anchor_point/anchor_origin) is STILL emitted — it
    gets an INFORMATIONAL (non-blocking) warning noting the record has its own
    position mechanism, which this tree redraw temporarily overrides via the
    NON-persistent PositionOverride (plan_2026_08_29_fork1_rigid_redraw_override.md).
    REVERSED 2026-08-29: the pre-2026-08-29 rule skipped such nodes ("not
    redrawn from this tree; remove the inline anchor"), justified by FORK-1's
    "one record must not have two competing PERSISTENT position sources".
    rigid-redraw's PositionOverride is non-persistent by construction — it
    moves the live footprint for one redraw pass and never rewrites the saved
    config — so the record's anchor_role keeps working for the regular
    (non-tree) Apply/Redraw exactly as before and no persistent conflict
    exists. link_trees.py (Save/Load) is unchanged.

    Warning: a selected node whose parent (another node, OR the tree's own
    anchor) is NOT in the selection — covers both plain nesting and the
    top-level-node-with-config-anchor case where the anchor "wasn't just
    redrawn"."""
    names: list[str] = []
    warnings: list[str] = []

    anchor = linked_tree.anchor
    # Top-level nodes: the "parent" is the tree anchor. A config anchor not in
    # the selection triggers the warning; origin/external anchors never do
    # (origin is an absolute point, external is always a live board position).
    if anchor.record is None:
        anchor_in_sel = True
        anchor_label = anchor.anchor.ref or "(origin)"
    else:
        anchor_in_sel = anchor.anchor.ref in selected_refs
        anchor_label = anchor.anchor.ref or "(origin)"

    def walk(linked_node: LinkedNode, parent_in_sel: bool, parent_label: str) -> None:
        ref = linked_node.node.ref
        is_selected = ref in selected_refs
        if is_selected and not parent_in_sel:
            warnings.append(
                _("Node {ref!r} will be redrawn from the current position of "
                  "{parent!r} (not in selection); if {parent!r} moved, {ref!r} "
                  "will land from the old point")
                .format(ref=ref, parent=parent_label))
        if (is_selected and linked_node.record is not None
                and linked_node.record.kind != "point"):
            conflict_field = inline_anchor_field(linked_node.record)
            if conflict_field is not None:
                warnings.append(
                    _("Node {ref!r} also has its own {field} — the regular "
                      "(non-tree) Apply/Redraw keeps using it; this tree redraw "
                      "moves it TEMPORARILY, without touching the record")
                    .format(ref=ref, field=conflict_field))
            names.append(linked_node.record.name)
        for child in linked_node.children:
            walk(child, parent_in_sel=is_selected, parent_label=ref)

    for top in linked_tree.nodes:
        walk(top, parent_in_sel=anchor_in_sel, parent_label=anchor_label)

    return names, warnings


def _forest_index(linked_trees: list[LinkedTree]):
    """Combine every tree's node index and parent map into one forest-wide
    index: {ref: LinkedNode} and {ref: parent_ref}. A top-level node's parent
    is its TREE ANCHOR's ref (None for an origin anchor); a nested node's
    parent is its enclosing node's ref. Because the anchor ref is the parent
    of the top-level nodes, a tree whose anchor points at a node of ANOTHER
    tree gets a cross-tree edge for free — the unified forest parent map is
    what plan 3.2 (§9.3 cross-tree anchoring) needs."""
    node_index: dict[str, LinkedNode] = {}
    parent_map: dict[str, str | None] = {}
    for tree in linked_trees:
        anchor_ref = tree.anchor.anchor.ref

        def walk(nodes: list[LinkedNode], parent_ref: str | None) -> None:
            for ln in nodes:
                node_index[ln.node.ref] = ln
                parent_map[ln.node.ref] = parent_ref
                walk(ln.children, ln.node.ref)

        walk(tree.nodes, anchor_ref)
    return node_index, parent_map


def curated_redraw_plan_forest(linked_trees: list[LinkedTree],
                               selected_refs: set[str]) -> tuple[list[str], list[str]]:
    """Global curated-redraw order over a FOREST of linked trees, with
    cross-tree anchor edges (plan 3.2 / design_2026_08_30_entity_placement_
    grammar.md §6).

    Within each tree a parent is strictly before its child (the per-tree
    rule), and every tree's top-level nodes are ordered after their TREE
    ANCHOR's record when that anchor is itself a SELECTED placement node —
    possibly in ANOTHER tree (cross-tree anchoring, §9.3). Because the anchor
    ref is the parent of the top-level nodes in _forest_index, both the
    within-tree and the cross-tree edges are expressed by one unified parent
    map, and the result is a single Kahn's topological order over the whole
    forest. External/origin/point anchors contribute no edge (read live).

    Returns (names, warnings): names is the global application order (each
    node's record.name == its ref, by link_trees's index construction);
    point/external nodes are walked as bases but never emitted (same rule as
    curated_redraw_plan). Raises ValidationError on a cross-tree cycle (two
    trees whose anchors point into each other's selected nodes)."""
    node_index, parent_map = _forest_index(linked_trees)

    selected: dict[str, LinkedNode] = {}
    for ref, ln in node_index.items():
        if ref in selected_refs and ln.record is not None \
                and ln.record.kind != "point":
            selected[ref] = ln

    # parent -> children edges (within-tree AND cross-tree anchor), indegrees
    children: dict[str, list[str]] = {}
    indeg: dict[str, int] = {ref: 0 for ref in selected}
    for ref in selected:
        p = parent_map.get(ref)
        if p is not None and p in selected:
            children.setdefault(p, []).append(ref)
            indeg[ref] += 1

    # warnings: a selected node whose parent/anchor is not redrawn (read live)
    warnings: list[str] = []
    for ref in selected:
        p = parent_map.get(ref)
        parent_label = p if p is not None else "(origin)"
        if p is None or p not in selected:
            warnings.append(
                _("Node {ref!r} will be redrawn from the current position of "
                  "{parent!r} (not in selection); if {parent!r} moved, {ref!r} "
                  "will land from the old point")
                .format(ref=ref, parent=parent_label))

    # Kahn's algorithm (deterministic: lexicographic queue)
    queue = sorted(ref for ref in selected if indeg[ref] == 0)
    names: list[str] = []
    while queue:
        ref = queue.pop(0)
        names.append(ref)
        for child in children.get(ref, []):
            indeg[child] -= 1
            if indeg[child] == 0:
                queue.append(child)
                queue.sort()
    if len(names) != len(selected):
        remaining = sorted(set(selected) - set(names))
        raise ValidationError(format_fatal_error(
            _("cross-tree anchor cycle in curated redraw forest"),
            [_("these nodes form a cycle through tree anchors: {items}")
             .format(items=", ".join(remaining))]))
    return names, warnings
