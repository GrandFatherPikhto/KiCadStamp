# kicadstamp/tree_position.py
"""Position resolution + curated-redraw planning for the trees layer.

Two distinct concepts (design_2026_08_26_tree_position_resolution.md, Q1):

1. A node's OWN position is pure composition, no adapter:
   `node_position(node, parent_position) = parent_position + node_offset(node)`.
   `node_offset` maps xy -> flat (x_mm, y_mm), polar -> the rotated offset
   vector via the existing local_to_absolute primitive (origin = 0 just
   extracts the rotated offset, nothing invented). A node's own `rotation`
   never feeds this — it rotates the node's own geometry later, never the
   offset vector, and a parent's rotation is NEVER applied to a child's
   offset (flat shift, same as ClonePlacement.xy with an anchor).

2. The LIVE position of a RECORD is only needed as a "base" (a tree anchor,
   or a parent node outside the curated selection). That is a thin kind
   dispatcher over the existing resolvers — the ONLY thing that talks to the
   live board here.

curated_redraw_plan() turns a LinkedTree + a set of selected refs into the
ordered name list run_cascade can apply (parent strictly before child), plus
the structural "parent not in selection" warnings.
"""
from .anchor_graph import Record
from .i18n import _
from .domain.geometry import Vector2
from .geometry.clone_geometry import clone_shift_mm
from .geometry.spoke_layout import local_to_absolute
from .link_trees import LinkedNode, LinkedTree
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


def node_position(node: TreeNode, parent_position: Vector2) -> Vector2:
    """parent_position + node_offset(node). That's it — no adapter, no kind
    dispatch, pure composition (design Q2)."""
    offset = node_offset(node)
    return Vector2.from_xy(parent_position.x + offset.x, parent_position.y + offset.y)


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


def curated_redraw_plan(linked_tree: LinkedTree, selected_refs: set[str]
                        ) -> tuple[list[str], list[str]]:  # (names, warnings)
    """DFS over the linked tree, parent strictly before child. A node emits
    into `names` (as record.name — record.name == node.ref by construction of
    link_trees's index) ONLY if record is not None and record.kind != "point"
    (external AND point nodes are walked — needed as a live base for
    children's position resolve — but never emit a name: apply_only_filter
    has no "points" support at all, see apply_pipeline.py, and external isn't
    a config record to redraw).

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
            names.append(linked_node.record.name)
        for child in linked_node.children:
            walk(child, parent_in_sel=is_selected, parent_label=ref)

    for top in linked_tree.nodes:
        walk(top, parent_in_sel=anchor_in_sel, parent_label=anchor_label)

    return names, warnings
