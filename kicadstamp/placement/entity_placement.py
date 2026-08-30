# kicadstamp/placement/entity_placement.py
"""Materialize Entity placements (Entity/Placement split, Phase 4.1).

An Entity carries no position by design (design_2026_08_30_entity_placement_
grammar.md §3) — its position comes from a trees: node with kind "placement".
This module walks cfg.trees, resolves each placement-node to an ABSOLUTE
board position (tree anchor + node offsets, reusing tree_position's
composition), and materializes a TRANSIENT ClonePlacement (Entity fields +
absolute xy/rotation) so the EXISTING clone machinery (dependency_order /
ClonePositionCalculator / clone_geometry) applies it unchanged.

The materialized clone is absolute (xy = resolved position, rotation = node
rotation), so its registry anchor id is the "name:" branch ==
entity_anchor_id(clone.name == entity.name) — exactly the id wired into
known_anchor_ids in phase 3.1.

Materialization is purely in-memory: the saved config is never rewritten,
and legacy clone_placements/rules/coordinate_placements are untouched. With
no entities and/or no trees the result is empty (all real profiles today).
"""
import logging
from typing import TYPE_CHECKING

from ..config import ClonePlacement, Entity
from ..domain.geometry import Vector2
from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _
from ..link_trees import LinkedTree, link_trees
from ..tree_position import (
    node_position,
    resolve_base_live_position,
    resolve_base_rotation_deg,
)
from ..utils.units import MM
from .services.component_resolver import (
    ComponentResolver,
    resolve_anchor_pad_position,
)

if TYPE_CHECKING:
    from ..config import Config
    from ..kicad.adapter import KiCadBoardAdapter

logger = logging.getLogger(__name__)

_ORIGIN = Vector2.from_xy(0, 0)


def _anchor_base(adapter: "KiCadBoardAdapter", cfg: "Config",
                 linked_tree: LinkedTree, sheet_names: dict) -> tuple[Vector2, float]:
    """(position_nm, rotation_deg) for a tree's anchor base.
    (origin) -> the board origin (0,0), rotation 0.
    (ref ...) -> the referenced record's / live footprint's current position
    and rotation (external refdes handled by resolve_base_*).
    (role ...) -> the Role-matching footprint's current position and rotation,
    resolved LIVE via ComponentResolver (Phase 4.2 — the SAME logic as
    resolve_record_live_position's kind == "rule" branch, tree_position.py);
    anchor_sheet/anchor_cluster narrow the same ambiguity cascade, anchor_pad
    moves the base onto that specific pad.
    (point ...) anchors are not live-resolvable for entity materialization
    yet — raise a clear error instead of guessing."""
    anchor = linked_tree.anchor
    if anchor.is_origin:
        return _ORIGIN, 0.0
    if anchor.anchor.role is not None:
        resolver = ComponentResolver(adapter, cfg, sheet_names)
        fp = resolver.resolve_anchor_fp(
            None, anchor.anchor.role, anchor.anchor.anchor_sheet,
            anchor.anchor.anchor_cluster,
            label=_("tree {name!r} anchor").format(name=linked_tree.name))
        pos = (resolve_anchor_pad_position(
                   adapter, fp, anchor.anchor.anchor_pad,
                   _("tree {name!r} anchor").format(name=linked_tree.name))
               if anchor.anchor.anchor_pad else fp.position)
        return pos, fp.angle_deg
    if anchor.anchor.point is not None:
        raise ValidationError(format_fatal_error(
            _("tree anchor (point ...) is not wired for entity placement "
              "materialization yet"),
            [_("entity placements under a point tree anchor are a future phase; "
               "use an (origin), (ref ...) or (role ...) anchor for now")]))
    pos = resolve_base_live_position(adapter, cfg, anchor.anchor.ref,
                                     anchor.record, {}, sheet_names)
    rot = resolve_base_rotation_deg(adapter, cfg, anchor.anchor.ref,
                                    anchor.record, sheet_names) or 0.0
    return pos, rot


def _to_clone(entity: Entity, pos_nm: Vector2, rot_deg: float) -> ClonePlacement:
    """Materialize a transient ClonePlacement from an Entity + absolute
    position (nm -> mm for the clone's xy). cluster falls back to the entity
    name (ClonePlacement.cluster is required; Entity.cluster is optional)."""
    return ClonePlacement(
        cluster=entity.cluster or entity.name,
        cell=entity.cell,
        xy=(pos_nm.x / MM, pos_nm.y / MM),
        rotation_deg=float(rot_deg),
        nets=entity.nets,
        params=entity.params,
        net_overrides=entity.net_overrides,
        retired=entity.retired,
        skip=entity.skip,
        ignore_selection=entity.ignore_selection,
        sheet=entity.sheet,
        name=entity.name,
        layer=entity.layer,
        mirror=entity.mirror,
        refs=entity.refs,
        by_selection=entity.by_selection,
        comment=entity.comment,
    )


def _walk(linked_nodes, parent_pos: Vector2, parent_rot: float, out: list[ClonePlacement]) -> None:
    """Depth-first over LinkedNode children. A node's absolute position =
    node_position(node, parent_pos, parent_rot) (parent + offset rotated into
    the parent's frame); its own rotation feeds its children's frame as
    parent_rot + node.rotation (the same accumulation the rigid-redraw
    relative_rotation does). Only kind "placement" nodes whose record is an
    Entity are materialized; legacy clone/rule/coordinate/point/external
    nodes are left to their existing paths."""
    for ln in linked_nodes:
        node = ln.node
        pos = node_position(node, parent_pos, parent_rot)
        rot = parent_rot + node.rotation
        if node.kind == "placement" and ln.record is not None \
                and isinstance(ln.record.obj, Entity):
            out.append(_to_clone(ln.record.obj, pos, rot))
        _walk(ln.children, pos, rot, out)


def materialize_entity_placements(adapter: "KiCadBoardAdapter", cfg: "Config",
                                  sheet_names=None) -> list[ClonePlacement]:
    """Walk cfg.trees and materialize every kind="placement" node (whose
    ref resolves to an Entity) into a transient absolute ClonePlacement.

    Purely in-memory; empty when there are no entities or no trees. The tree
    anchor (origin / ref) is read LIVE from the board via tree_position's
    resolve_base_* — so an entity placement under a component ref anchor
    follows the anchor's current position, matching the curated-redraw model.

    Per-tree tolerance (bug #4, 2026-08-30): a tree whose anchor is not
    resolvable (a (point ...) anchor — still unwired — or an unresolvable
    role/ref) is LOCAL to that tree (warning + skip), never fatal for the
    whole run. A real profile may have 21 of 22 trees role-anchored; without
    this tolerance Apply/Redraw died before materializing ANY entity
    placement. (role ...) anchors are LIVE-resolved since Phase 4.2 — only
    (point ...) remains unwired. This is the same per-item tolerance the
    Extract dock's Sub-placements catalog already applies at the call level
    (gui/docks/extract.py).
    """
    if not cfg.entities or not cfg.trees:
        return []
    sheet_names = sheet_names or {}
    linked = link_trees(cfg, cfg.trees)
    out: list[ClonePlacement] = []
    for tree in linked:
        try:
            anchor_pos, anchor_rot = _anchor_base(adapter, cfg, tree, sheet_names)
            tree_clones: list[ClonePlacement] = []
            _walk(tree.nodes, anchor_pos, anchor_rot, tree_clones)
        except ValidationError as e:
            logger.warning(_("Entity materialization: tree {tree!r} skipped — "
                             "{error}").format(tree=tree.name, error=e))
            continue
        out.extend(tree_clones)
    return out
