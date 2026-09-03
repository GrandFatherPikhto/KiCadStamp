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

A tree ANCHOR may itself point at an Entity (anchor (ref ...) resolving to a
record.kind == "placement") — the tree is then anchored on another tree's
placement node. Because an Entity carries no position, such an anchor base is
resolved RECURSIVELY: find the (single) tree whose placement node references
that Entity, resolve ITS anchor base (origin/ref/role/Entity-again), and
compose the found node's own offset on top (the same composition _walk()
uses). The recursion is cycle-guarded (a set of visited Entity names): an
Entity with no placement node, one referenced by more than one node, or a
chain that loops into a cycle is a CONFIG error — fatal for the whole run,
never a per-tree skip (that skip is reserved for live-board conditions like a
point/unresolvable-role anchor).

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
from ..link_trees import LinkedNode, LinkedTree, link_trees
from ..tree_position import (
    node_own_anchor_base,
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


class _EntityAnchorError(ValidationError):
    """A fatal Entity ref-anchor config error (the ref'd Entity is not placed
    in any tree, is placed in more than one node, or the entity-anchor chain
    loops into a cycle). Deliberately a ValidationError SUBCLASS so
    materialize_entity_placements can re-raise it (a real config bug, fatal
    for the whole run) instead of the per-tree live-board skip it applies to a
    point/unresolvable-role anchor."""


def _find_entity_node(forest: list[LinkedTree], entity_name: str
                      ) -> list[tuple[LinkedTree, list[LinkedNode]]]:
    """Every (tree, [LinkedNode path from the tree's top level to the node])
    where a kind="placement" node places entity_name (node.ref == entity.name,
    record resolved to an Entity). The path is the node chain from the tree's
    root down to the found node, so the caller composes the node's absolute
    position with the SAME node_position/node.rotation accumulation _walk()
    uses — correct for a nested node too. At most one match in a valid config
    (trees rule 2 / link_trees guarantee ref uniqueness); the caller treats
    2+ as fatal (defensive)."""
    matches: list[tuple[LinkedTree, list[LinkedNode]]] = []
    for tree in forest:
        def walk(nodes: list[LinkedNode], path: list[LinkedNode]) -> None:
            for ln in nodes:
                node_path = path + [ln]
                if (ln.node.kind == "placement" and ln.record is not None
                        and ln.record.kind == "placement"
                        and ln.record.name == entity_name):
                    matches.append((tree, node_path))
                walk(ln.children, node_path)
        walk(tree.nodes, [])
    return matches


def _auto_anchor_base(adapter: "KiCadBoardAdapter", cfg: "Config",
                      linked_tree: LinkedTree, sheet_names: dict) -> tuple[Vector2, float]:
    """Auto-derive a tree's anchor base when it has NO explicit (anchor ...)
    (2026-08-31, plan tree_self_anchor_from_entity): the single top-level
    kind="placement" node's Entity becomes the anchor subject — the ONE
    component of its cell sitting at local offset (0,0) (no
    offset_along_mm/offset_across_mm — the "zero", self-referencing slot, e.g.
    role "FPGA" in the fpga/fpga_supp cells) acts as the anchor role, narrowed
    by the Entity's OWN sheet/cluster, then resolved LIVE exactly like an
    explicit (role ...) anchor (Phase 4.2 — no new board-reading logic).

    Config errors (no/2+ zero slots, 0/2+ top-level placement nodes, a missing
    cell) are _EntityAnchorError — fatal for the whole run, NEVER a silent
    origin/guess. A LIVE error from the role resolution (role not found /
    ambiguous on the board) is a plain ValidationError — the SAME per-tree
    skip tolerance materialize_entity_placements already applies to explicit
    role anchors."""
    placement_roots = [ln for ln in linked_tree.nodes
                       if ln.node.kind == "placement" and ln.record is not None
                       and isinstance(ln.record.obj, Entity)]
    if len(linked_tree.nodes) != 1 or len(placement_roots) != 1:
        raise _EntityAnchorError(format_fatal_error(
            _("tree {name!r} has no explicit anchor and cannot auto-derive one")
            .format(name=linked_tree.name),
            [_("auto-anchor needs EXACTLY ONE top-level placement node on an Entity "
               "(found {n} top-level node(s)); add an explicit (anchor ...) to this "
               "tree instead").format(n=len(linked_tree.nodes))]))
    entity = placement_roots[0].record.obj
    # The "Entity -> its Cell -> single zero-offset component -> role -> live
    # read" core is shared with resolve_entity_live_position's fallback for an
    # Entity that is not placed by any tree — one source of truth, only the
    # label differs (tree-anchor context here, Entity's own context there).
    return _entity_own_zero_slot_live_position(
        adapter, cfg, entity, sheet_names,
        label=_("tree {name!r} auto-anchor").format(name=linked_tree.name))


def _find_entity_record(cfg: "Config", entity_name: str) -> Entity | None:
    """The cfg.entities record with this name, or None (defensive: a
    placement-kind record always resolves to an Entity via link_trees, so None
    is unreachable in practice — but the zero-slot fallback needs the actual
    Entity object to read its cell)."""
    for e in cfg.entities:
        if e.name == entity_name:
            return e
    return None


def _entity_own_zero_slot_live_position(adapter: "KiCadBoardAdapter",
                                        cfg: "Config",
                                        entity: Entity, sheet_names: dict,
                                        label: str | None = None
                                        ) -> tuple[Vector2, float]:
    """(position_nm, rotation_deg) of an Entity's OWN live position, read from
    its cell's single zero-offset (local 0,0) component's role, live-resolved
    via ComponentResolver exactly like an explicit (role ...) anchor (Phase
    4.2 — no new board-reading logic). This is the standalone core of the
    auto-anchor derivation (_auto_anchor_base, plan 2026-08-31
    tree_self_anchor_from_entity), extracted so resolve_entity_live_position
    can fall back to it for an Entity that is NOT (yet) placed by any tree —
    Denis's live case: "Add child" under fpga with ref fpga_flash and "Read
    current position" BEFORE saving the node (plan_2026_08_31_entity_live_
    position_zero_slot_fallback.md).

    Config errors (missing cell, 0/2+ zero slots) are _EntityAnchorError. A
    LIVE error from the role resolution (role not found / ambiguous on the
    board) is a plain ValidationError — the caller decides how to surface it
    (per-tree skip for the materializer, a warning for the GUI read)."""
    cell = cfg.cells.get(entity.cell)
    if cell is None:
        raise _EntityAnchorError(format_fatal_error(
            _("Entity {name!r} references missing cell {cell!r}")
            .format(name=entity.name, cell=entity.cell),
            [_("the zero-offset live read needs the Entity's cell to exist")]))
    zero = [c for c in cell.components
            if c.offset_along_mm == 0.0 and c.offset_across_mm == 0.0]
    if not zero:
        raise _EntityAnchorError(format_fatal_error(
            _("Entity {name!r} has no zero-offset component to read live")
            .format(name=entity.name),
            [_("cell {cell!r} has no component without offset_along_mm/"
               "offset_across_mm (local (0,0)) to act as the Entity's own "
               "anchor role").format(cell=entity.cell)]))
    if len(zero) > 1:
        raise _EntityAnchorError(format_fatal_error(
            _("Entity {name!r} has {n} zero-offset components in cell {cell!r} "
              "— live position is ambiguous")
            .format(name=entity.name, n=len(zero), cell=entity.cell),
            [_("a zero-offset live read needs EXACTLY ONE component without "
               "offset_along_mm/offset_across_mm; found {n}")
             .format(n=len(zero))]))
    slot = zero[0]
    resolver = ComponentResolver(adapter, cfg, sheet_names)
    fp = resolver.resolve_anchor_fp(
        None, slot.role, entity.sheet, entity.cluster,
        label=label or _("Entity {name!r} own zero-offset live position")
        .format(name=entity.name))
    return fp.position, fp.angle_deg


def _anchor_base(adapter: "KiCadBoardAdapter", cfg: "Config",
                 linked_tree: LinkedTree, sheet_names: dict,
                 forest: list[LinkedTree] | None = None,
                 visited: set[str] | None = None) -> tuple[Vector2, float]:
    """(position_nm, rotation_deg) for a tree's anchor base.
    AUTO (no explicit (anchor ...)) -> derived from the tree's own root Entity
    placement's cell zero slot (_auto_anchor_base) — live role resolution.
    (origin) -> the board origin (0,0), rotation 0.
    (ref ...) -> the referenced record's / live footprint's current position
    and rotation (external refdes handled by resolve_base_*). When the ref
    resolves to an ENTITY (record.kind == "placement"), the Entity carries NO
    position by design — its position comes from the tree that PLACES it (a
    node with node.ref == entity.name, kind "placement"). Such an anchor is
    therefore resolved RECURSIVELY: find that tree, resolve ITS anchor base
    (origin / ref / role / an Entity ref again — recursion), then compose the
    found node's offset (node_position/node.rotation — the same composition
    _walk() applies) on top. `forest` is the whole linked-trees forest (to
    search for the placing node); `visited` holds the Entity names on the
    current resolution chain so a cycle (tree A anchored on an Entity of tree
    B, B on an Entity of A) is a clear FATAL, not an infinite loop.
    (role ...) -> the Role-matching footprint's current position and rotation,
    resolved LIVE via ComponentResolver (Phase 4.2 — the SAME logic as
    resolve_record_live_position's kind == "rule" branch, tree_position.py);
    anchor_sheet/anchor_cluster narrow the same ambiguity cascade, anchor_pad
    moves the base onto that specific pad.
    (point ...) anchors are not live-resolvable for entity materialization
    yet — raise a clear error instead of guessing."""
    anchor = linked_tree.anchor
    if anchor.anchor.is_auto:
        # No explicit (anchor ...): derive the base from the tree's own root
        # Entity placement's cell zero slot, live-resolved like a role anchor.
        return _auto_anchor_base(adapter, cfg, linked_tree, sheet_names)
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
    if anchor.record is not None and anchor.record.kind == "placement":
        # ref anchor resolved to an Entity: the Entity's live position comes
        # from the tree that places it — find that tree, resolve its anchor
        # base RECURSIVELY, then compose the found node's own offset. This
        # branch is EXACTLY resolve_entity_live_position (the public entry
        # point the tree_position live-read of an Entity shares), so delegate
        # to it — one source of truth for the entity-ref recursion.
        if forest is None:
            raise _EntityAnchorError(format_fatal_error(
                _("internal error: Entity-anchored tree resolved without the "
                  "tree forest"),
                []))
        return resolve_entity_live_position(
            adapter, cfg, anchor.record.name, sheet_names,
            forest=forest, visited=visited)
    pos = resolve_base_live_position(adapter, cfg, anchor.anchor.ref,
                                     anchor.record, {}, sheet_names)
    rot = resolve_base_rotation_deg(adapter, cfg, anchor.anchor.ref,
                                    anchor.record, sheet_names) or 0.0
    return pos, rot


def resolve_entity_live_position(adapter: "KiCadBoardAdapter", cfg: "Config",
                                 entity_name: str, sheet_names: dict,
                                 forest: list[LinkedTree] | None = None,
                                 visited: set[str] | None = None
                                 ) -> tuple[Vector2, float]:
    """(position_nm, rotation_deg) of an Entity record itself — the SAME
    recursive resolution _anchor_base's Entity-ref branch applies when an
    Entity is a TREE ANCHOR, exposed as a standalone entry point for a caller
    that wants the Entity's OWN live position (tree_position's
    resolve_record_live_position/resolve_record_rotation_deg kind ==
    "placement" branches).

    An Entity carries no position by design (design_2026_08_30_entity_
    placement_grammar.md §3); its live position comes from the (single) tree
    that places it: find that tree (_find_entity_node), resolve ITS anchor
    base RECURSIVELY (_anchor_base), then compose the found node's node-path
    offset/rotation on top (the same node_position accumulation _walk()
    applies). `forest` defaults to the whole config forest
    (link_trees(cfg, cfg.trees)); `visited` holds the Entity names already on
    the current resolution chain so a cycle (a tree anchored on an Entity
    whose placing tree anchors back) is a clear _EntityAnchorError fatal,
    never an infinite loop. An Entity with NO placement node anywhere falls
    back to its OWN zero-offset component's live position
    (_entity_own_zero_slot_live_position — plan_2026_08_31_entity_live_
    position_zero_slot_fallback.md); an Entity referenced by 2+ nodes, a
    cyclic chain, or one with neither a placement node nor a readable
    zero-slot raises the SAME _EntityAnchorError as _anchor_base — the caller
    must decide how to surface it (fatal for the materializer, a ValidationError
    warning for the GUI read)."""
    if forest is None:
        forest = link_trees(cfg, cfg.trees)
    chain = visited if visited is not None else set()
    if entity_name in chain:
        raise _EntityAnchorError(format_fatal_error(
            _("tree anchor cycle through Entity placements"),
            [_("the tree-anchor chain loops back through Entity "
               "placements: {chain}").format(
                   chain=", ".join(sorted(chain | {entity_name})))]))
    chain = chain | {entity_name}
    matches = _find_entity_node(forest, entity_name)
    if not matches:
        # The Entity is not (yet) placed by any tree — but it may still have
        # a readable OWN live position: the single zero-offset (local 0,0)
        # component of its cell acts as its own anchor role (the same
        # derivation the auto-anchor uses for a root Entity). Denis's live
        # case: "Add child" under fpga with ref fpga_flash and "Read current
        # position" BEFORE saving the node — fpga_flash is not a placement
        # node anywhere yet, but its cell was extracted with a zero-offset
        # component (an Origin "By component role" extraction). Only when the
        # zero-slot read ALSO fails (no cell, 0/2+ zero slots) do we raise the
        # final fatal, honestly naming BOTH reasons. A LIVE role-resolution
        # failure (role not found / ambiguous) is a plain ValidationError from
        # resolve_anchor_fp and propagates as-is — a board condition, not a
        # config error.
        entity_rec = _find_entity_record(cfg, entity_name)
        if entity_rec is None:
            raise _EntityAnchorError(format_fatal_error(
                _("Entity {name!r} is not placed in any tree — nothing to "
                  "read live").format(name=entity_name),
                [_("no (kind placement) node places Entity {name!r}, and no "
                   "Entity record with that name exists to read a zero-offset "
                   "component from").format(name=entity_name)]))
        try:
            return _entity_own_zero_slot_live_position(
                adapter, cfg, entity_rec, sheet_names)
        except _EntityAnchorError as zero_err:
            raise _EntityAnchorError(format_fatal_error(
                _("Entity {name!r} is not placed in any tree and has no "
                  "readable own zero-offset component").format(name=entity_name),
                [_("no (kind placement) node places Entity {name!r}; add a "
                   "placement node for it, or read a record that is actually "
                   "placed by a tree").format(name=entity_name),
                 _("the own-cell zero-offset fallback also failed: "
                   "{reason}").format(reason=zero_err)]))
    if len(matches) > 1:
        raise _EntityAnchorError(format_fatal_error(
            _("Entity {name!r} is placed in more than one tree node")
            .format(name=entity_name),
            [_("trees rule 2 (a flat record ref may appear in at most one "
               "node) is violated — an Entity can stand in only one "
               "place")]))
    target_tree, node_path = matches[0]
    base_pos, base_rot = _anchor_base(adapter, cfg, target_tree, sheet_names,
                                      forest=forest, visited=chain)
    pos, rot = base_pos, base_rot
    for ln in node_path:
        # Same own_anchor base substitution as _walk: an intermediate node on
        # the path with its own (role) anchor breaks to THAT anchor's live
        # frame, and the composition continues from it — keeps the live Entity
        # read consistent with materialization (plan tree_node_own_anchor §2).
        if ln.node.own_anchor is not None:
            resolved = node_own_anchor_base(ln.node, adapter, cfg, sheet_names)
            if resolved is not None:
                pos, rot = resolved
        pos = node_position(ln.node, pos, rot)
        rot = rot + ln.node.rotation
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


def _walk(linked_nodes, parent_pos: Vector2, parent_rot: float, out: list[ClonePlacement],
          position_overrides: dict | None = None, *,
          adapter=None, cfg=None, sheet_names=None) -> None:
    """Depth-first over LinkedNode children. A node's absolute position =
    node_position(node, parent_pos, parent_rot) (parent + offset rotated into
    the parent's frame); its own rotation feeds its children's frame as
    parent_rot + node.rotation (the same accumulation the rigid-redraw
    relative_rotation does). Only kind "placement" nodes whose record is an
    Entity are materialized; legacy clone/rule/coordinate/point/external
    nodes are left to their existing paths.

    position_overrides — {record.name: PositionOverride} (tree rigid-group
    redraw, plan_2026_08_29_tree_live_rigid_redraw.md): an override REPLACES
    the structural pos/rot for THAT node (the same principle as
    ClonePositionCalculator.compute_raw_positions), closing the asymmetry
    where materialization recomputed the position without seeing the override
    (plan_2026_08_31_fpga_flash_rigid_redraw_not_following.md). Children keep
    walking from the STRUCTURAL pos/rot — a rigid redraw applies one node per
    run (only=[name]) and the override node is the only one that survives
    _filter_materialized_entities, so the child frame is never observed in
    that scenario; a full apply passes no overrides at all."""
    for ln in linked_nodes:
        node = ln.node
        # Own-anchor node: its offset is measured from the node's OWN (role)
        # anchor's live frame, not the parent's — a PER-NODE base substitution
        # (siblings keep the parent frame; children inherit the node's abs
        # frame below, exactly as _walk has always done). adapter/cfg/sheet_names
        # are threaded from materialize_entity_placements only for this case.
        base_pos, base_rot = parent_pos, parent_rot
        if node.own_anchor is not None:
            resolved = node_own_anchor_base(node, adapter, cfg, sheet_names)
            if resolved is None:
                # node_own_anchor_base returns None only when own_anchor is
                # None — defensive, unreachable above.
                raise ValidationError(_(
                    "node {ref!r}: own anchor needs a live board to resolve"
                    ).format(ref=node.ref))
            base_pos, base_rot = resolved
        pos = node_position(node, base_pos, base_rot)
        rot = base_rot + node.rotation
        if node.kind == "placement" and ln.record is not None \
                and isinstance(ln.record.obj, Entity):
            override = (position_overrides or {}).get(ln.record.name)
            if override is not None:
                out.append(_to_clone(ln.record.obj, override.position,
                                     override.rotation_deg))
            else:
                out.append(_to_clone(ln.record.obj, pos, rot))
        _walk(ln.children, pos, rot, out, position_overrides,
              adapter=adapter, cfg=cfg, sheet_names=sheet_names)


def materialize_entity_placements(adapter: "KiCadBoardAdapter", cfg: "Config",
                                  sheet_names=None,
                                  position_overrides: dict | None = None
                                  ) -> list[ClonePlacement]:
    """Walk cfg.trees and materialize every kind="placement" node (whose
    ref resolves to an Entity) into a transient absolute ClonePlacement.

    Purely in-memory; empty when there are no entities or no trees. The tree
    anchor (origin / ref / role) is read LIVE from the board via tree_position
    resolve_base_* / ComponentResolver — so an entity placement under a
    component ref anchor follows the anchor's current position, matching the
    curated-redraw model. A ref anchor that resolves to an ENTITY (kind
    "placement") is resolved RECURSIVELY through the tree that places that
    Entity (_anchor_base, cycle-guarded) — a tree can anchor on another tree's
    placement node (cross-tree entity anchoring).

    position_overrides — {record.name: PositionOverride} (tree rigid-group
    redraw): an override REPLACES the structural pos/rot for that placement
    node (see _walk), closing the asymmetry where this step recomputed the
    position without seeing the override (the override still applied later in
    ClonePositionCalculator.compute_raw_positions, but only after this step
    had already produced a possibly-different transient clone — plan_2026_08_31_
    fpga_flash_rigid_redraw_not_following.md). None/empty = the normal apply
    path, behavior unchanged.

    Per-tree tolerance (bug #4, 2026-08-30): a tree whose anchor is not
    resolvable (a (point ...) anchor — still unwired — or an unresolvable
    role/ref) is LOCAL to that tree (warning + skip), never fatal for the
    whole run. A real profile may have 21 of 22 trees role-anchored; without
    this tolerance Apply/Redraw died before materializing ANY entity
    placement. (role ...) anchors are LIVE-resolved since Phase 4.2 — only
    (point ...) remains unwired. This is the same per-item tolerance the
    Extract dock's Sub-placements catalog already applies at the call level
    (gui/docks/extract.py).

    EXCEPTION to the tolerance: an Entity ref-anchor whose Entity is not
    placed in any tree, is placed in more than one node, or whose entity-anchor
    chain forms a CYCLE is a CONFIG error — re-raised (_EntityAnchorError),
    fatal for the whole run, never silently skipped.
    """
    if not cfg.entities or not cfg.trees:
        return []
    sheet_names = sheet_names or {}
    linked = link_trees(cfg, cfg.trees)
    out: list[ClonePlacement] = []
    for tree in linked:
        try:
            anchor_pos, anchor_rot = _anchor_base(
                adapter, cfg, tree, sheet_names, forest=linked)
            tree_clones: list[ClonePlacement] = []
            _walk(tree.nodes, anchor_pos, anchor_rot, tree_clones,
                  position_overrides=position_overrides,
                  adapter=adapter, cfg=cfg, sheet_names=sheet_names)
        except _EntityAnchorError:
            # A ref anchor resolving to an Entity that is not placed / placed
            # twice / in a cycle is a CONFIG bug — fatal for the whole run,
            # never a local per-tree skip (a skip would silently drop the whole
            # tree's placement and mask the config error).
            raise
        except ValidationError as e:
            logger.warning(_("Entity materialization: tree {tree!r} skipped — "
                             "{error}").format(tree=tree.name, error=e))
            continue
        out.extend(tree_clones)
    return out
