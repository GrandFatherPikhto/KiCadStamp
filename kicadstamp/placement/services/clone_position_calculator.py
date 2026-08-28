# kicadstamp/placement/services/clone_position_calculator.py
"""
clone_position_calculator.py — counterpart to manual_position_calculator.py, but
for ClonePlacement (TemplatePlacer). The two calculators share no base class
because they work fundamentally differently (pad‑anchored spokes vs cell‑
based section cloning), so they have independent signatures.

anchor_id for the registry (see registry.py) is built from PHYSICAL binding
(anchor_ref/anchor_pad, PLUS the xy offset — see
clone_anchor_id), not from the clone's identity — for the same reason that
refdes is not a reliable key: the identity is arbitrary and can change
(renaming a clone_placement should not erase vias/tracks if the physical anchor
and offset remain the same). Only if anchor_ref is not set at all (absolute
coordinate mode, a rare case) — we have no choice but to use
clone_placement_effective_name(clone), the only available identifier.
"""
import dataclasses
import logging

from ...domain.geometry import Vector2
from ...domain.geometry import BoardLayer

from ...config import (Config, ClonePlacement, CellPlacement, Cell,
                       TemplateComponentSlot, clone_placement_effective_name)
from ...exceptions import ValidationError, format_fatal_error
from ...kicad.adapter import KiCadBoardAdapter
from ...geometry.clone_geometry import apply_clone_geometry, clone_shift_mm
from ...net_resolution import resolve_net_from_role
from ...registry import make_registry_key
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from .clone_role_resolver import (
    resolve_roles_by_selection,
    resolve_roles_by_nets,
    clone_uses_selection_mode,
    resolve_anchor_by_role,
)
from .component_resolver import (
    resolve_anchor_identity,
    resolve_anchor_pad_position,
    resolve_footprint_by_ref,
)
from ...i18n import _

logger = logging.getLogger(__name__)


def resolve_clone_anchor_ref(adapter: KiCadBoardAdapter, cfg: Config, clone: ClonePlacement,
                             sheet_names=None) -> str | None:
    """
    Resolves clone's anchor to a concrete ref, WITHOUT resolving anchor_pad
    position — used by dependency_order.py to build the producer/consumer
    graph for a whole apply run before any planning happens. Mirrors the
    anchor branch of ClonePositionCalculator._resolve_anchor, but only needs
    identity here, not position; pad existence is still checked later, at real
    plan time, same as before (a deliberate read-twice trade-off — this module
    is explicitly not performance sensitive, see dependency_order.py).
    """
    _sn = sheet_names or {}
    return resolve_anchor_identity(
        clone.anchor_ref, clone.anchor_role, clone.anchor_point,
        lambda: resolve_anchor_by_role(adapter, clone, _sn),
    )


def clone_anchor_id(clone: ClonePlacement) -> str:
    """
    Identity of clone_placement for the registry — physical binding, not name.
    Priority order matches anchor resolution order (_resolve_anchor: anchor_point
    is checked FIRST, then anchor_ref, then anchor_role — this function mirrors
    that order so every anchored clone gets a physical-binding key, not the
    name: fallback):
      anchor_point set -> "point:{anchor_point}:{origin_x}:{origin_y}"
        (found 2026-08-06: this branch was missing entirely — a Point-anchored
        clone fell all the way through to name:{clone.name}, same as absolute
        coordinates, silently losing the rename-safety this whole function
        exists for — renaming a Point-anchored clone_placement, or just editing
        its xy: while keeping the same name, orphaned its own vias/tracks in
        the registry with no protection from known_anchor_ids, since the OLD
        name: key stops being produced the moment cfg no longer has that exact
        name)
      anchor_ref set -> "anchor:{ref}:{pad}:{origin_x}:{origin_y}"
      anchor_role set -> "role:{anchor_role}:{anchor_sheet}:{pad}:{origin_x}:{origin_y}"
        (anchor_role is also resilient to renaming/re‑annotation, like anchor_ref
        to clone.name changes; anchor_sheet is included because it is part of the
        anchor search conditions — changing anchor_sheet also changes the physical
        placement)
      neither (absolute coordinates) -> "name:{clone_placement_effective_name(clone)}",
        the only available identifier in this mode (the effective identity —
        name if set, else the Cluster tag).

    xy is included in the anchor_ref/anchor_role branches
    (found 2026-07-27): two clone_placements can legitimately share the same
    physical anchor point and differ only by this flat offset — e.g. a positive
    and a negative power-filter instance both anchored to the same connector
    pad, mirrored to opposite sides. Without the offset in the key, both
    resolved to the identical registry identity, so placing the second one
    made the registry think its vias/tracks had merely "moved" and dragged the
    first instance's already-placed items onto itself instead of creating
    independent ones. check_no_duplicate_clone_anchors (validation.py) uses
    the same extended key, for the same reason.

    anchor_cluster is included in the anchor_role branch (found 2026-07-28,
    same class of bug as above): p5v_led_spoke/n5v_led_spoke share identical
    anchor_role/anchor_sheet/anchor_pad/origin (both anchor on C_OUT_BYPASS
    pad '1', offset 3/0 mm) and differ ONLY by anchor_cluster (In_Pi_Filter_Pos
    vs In_Pi_Filter_Neg) — the one field that actually picks which physical C9
    vs C12 the anchor resolves to. Without it in the key, they too would
    collapse to one registry identity.
    """
    ox, oy = clone_shift_mm(clone)
    if clone.anchor_point is not None:
        return f"point:{clone.anchor_point}:{ox:.4f}:{oy:.4f}"
    if clone.anchor_ref is not None:
        return f"anchor:{clone.anchor_ref}:{clone.anchor_pad or ''}:{ox:.4f}:{oy:.4f}"
    if clone.anchor_role is not None:
        return (f"role:{clone.anchor_role}:{clone.anchor_sheet or ''}:{clone.anchor_cluster or ''}"
                f":{clone.anchor_pad or ''}:{ox:.4f}:{oy:.4f}")
    return f"name:{clone_placement_effective_name(clone)}"


class ClonePositionCalculator:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, sheet_names=None,
                 resolved_points=None):
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names or {}
        # name -> ResolvedPoint, for anchor_point: — see planner.py's
        # PlacementPlanner.resolved_points (owns/shares this dict).
        self.resolved_points = resolved_points if resolved_points is not None else {}

    def _resolve_role_nets(self, cell: Cell, role_to_ref: dict[str, str]) -> dict:
        """Resolve every net_from_role-bearing via/track net against the live
        board, BEFORE geometry — the "geometry does not touch the live board"
        boundary (apply_clone_geometry docstring) is preserved by doing the
        live read here, outside the geometry layer.

        Returns {(role, pad): net} for each distinct net_from_role in the
        cell (cell-level vias/tracks and every component slot's vias). Each
        resolve_net_from_role is fatal if the role/pad cannot be resolved on
        THIS instance — apply stops, it does not guess.
        """
        items: list = list(cell.vias) + list(cell.tracks)
        for slot in cell.components:
            items += list(slot.vias)

        resolved: dict = {}
        for item in items:
            role = getattr(item, "net_from_role", None)
            if role is None:
                continue
            key = (role, getattr(item, "net_from_role_pad", None))
            if key in resolved:
                continue
            resolved[key] = resolve_net_from_role(role, key[1], role_to_ref, self.adapter)
        return resolved

    def _resolve_anchor(self, clone: ClonePlacement) -> Vector2 | None:
        """
        anchor_ref/anchor_pad OR anchor_role(+anchor_sheet)/anchor_pad ->
        absolute anchor point. None if no anchor is set (absolute coordinate mode).
        A missing/ambiguous anchor is FATAL: the anchor is explicitly set, so
        placing the section "somewhere" or silently skipping it is worse than failing.
        """
        if clone.anchor_point is not None:
            # Guaranteed already resolved — dependency_order.py orders this
            # clone_placement's Item after the point's (see
            # resolve_clone_anchor_ref's anchor_point branch). Only ever
            # needs a coordinate, unlike Rule/thermal_via_array — a shifted
            # or xy-literal point is fine here.
            logger.debug(_("  [{name}] anchor: point {point!r}")
                         .format(name=clone_placement_effective_name(clone),
                                 point=clone.anchor_point))
            return self.resolved_points[clone.anchor_point].position
        if clone.anchor_ref is not None:
            fp = resolve_footprint_by_ref(self.adapter, clone.anchor_ref,
                                          clone_placement_effective_name(clone))
        elif clone.anchor_role is not None:
            fp = resolve_anchor_by_role(self.adapter, clone, self.sheet_names)
        else:
            return None

        if clone.anchor_pad is None:
            logger.debug(_("  [{name}] anchor: centre of {ref} ({x:.3f}, {y:.3f}) mm")
                         .format(name=clone_placement_effective_name(clone),
                                 ref=fp.ref,
                                 x=fp.position.x/1e6, y=fp.position.y/1e6))
            return fp.position
        position = resolve_anchor_pad_position(self.adapter, fp, clone.anchor_pad,
                                               clone_placement_effective_name(clone))
        logger.debug(_("  [{name}] anchor: pad {ref}.{pad} ({x:.3f}, {y:.3f}) mm")
                     .format(name=clone_placement_effective_name(clone),
                             ref=fp.ref,
                             pad=clone.anchor_pad,
                             x=position.x/1e6, y=position.y/1e6))
        return position

    def _resolve_content(self, cell_ref: str | None, role_ref: str | None,
                         label: str) -> tuple[Cell | None, str]:
        """Resolve a placement's content to a Cell. Top-level ClonePlacement
        (2026-08-12, Group 0 consolidation: cell: is now mandatory — the
        role:/cluster: single-component modes migrated to coordinate_placements'
        anchor-relative mode) always comes with cell_ref set, so this is a
        plain cfg.cells lookup; only a nested CellPlacement may still use
        role: (a one-component recipe inside a cell), which synthesises a
        temporary one-component Cell on the fly (cheap, no caching needed —
        a separate cell file just for one role with no via/track would be
        cumbersome). Returns (None, label) if a cell: reference doesn't exist
        (caller logs and skips — same behaviour as before this was factored
        out)."""
        if cell_ref is not None:
            cell = self.cfg.cells.get(cell_ref)
            if cell is None:
                logger.warning(_("{name}: cell {cell!r} not found in cells, skipping")
                               .format(name=label, cell=cell_ref))
                return None, cell_ref
            return cell, cell_ref
        cell = Cell(
            name=f"__role__{role_ref}",
            components=[TemplateComponentSlot(
                role=role_ref, offset_along_mm=0.0, offset_across_mm=0.0, angle_deg=0.0,
            )],
        )
        return cell, cell.name

    def _resolve_one_level(
        self,
        placement: ClonePlacement | CellPlacement,
        cell: Cell,
        cell_name: str,
        anchor_position: Vector2 | None,
        parent_rotation_deg: float,
        anchor_id: str,
        chain: tuple[str, ...] = (),
    ) -> tuple[list[PlacedComponentInfo], list[ViaCommand], list[TrackCommand]]:
        """
        Resolves ONE placement (top-level ClonePlacement or a nested
        CellPlacement — same shape of work either way, see CellPlacement's
        docstring for why it duck-types against the same role-resolution
        code) against its cell: that cell's own direct leaf content, PLUS
        (recursively) every nested CellPlacement inside cell.clone_placements
        — Phase 4, recursive Cell (2026-07-31).

        anchor_position is this placement's PARENT frame's own world-space
        origin (None only for a top-level, anchor-less, absolute-coordinate
        ClonePlacement — never None for a nested CellPlacement, which is
        always relative to its parent's origin). parent_rotation_deg is the
        parent's ALREADY-ACCUMULATED world rotation (0.0 at the top level).
        anchor_id is THIS placement's own registry identity — path-composed
        one level deeper for each nested recursive call (see the recursive
        call below), so nested content gets a unique, rename-stable key.

        chain — the ordered tuple of CELL NAMES already visited on the
        CURRENT branch of the recursion (each nested cell's name appended
        before recursing, never shared between sibling branches — a diamond
        where two branches reference the same leaf is NOT a cycle). Used to
        catch a cyclic clone_placements graph (A -> B -> A) with a clean
        ValidationError instead of Python's own RecursionError: the
        load-time check_no_cell_definition_cycles (config/loader.py) catches
        it in configs that go through load_config, but a cfg assembled in
        memory (GUI single-file edit flows, programmatic construction) can
        still reach here — this guard is the last line of defence, and after
        ExtractDock starts auto-generating clone_placements (2026-08-25) such
        cycles become possible without any hand-typing.
        """
        mirror = placement.mirror
        # Human-readable label for logs / via-track ownership. ClonePlacement
        # carries its save identity in `name` (falling back to the Cluster tag);
        # a nested CellPlacement has a required `name` and no `cluster`.
        placement_label = (clone_placement_effective_name(placement)
                           if isinstance(placement, ClonePlacement) else placement.name)
        if mirror and cell.clone_placements:
            # Composing an outer mirror with an already-resolved nested
            # subtree is a real reflection-composition problem (see
            # techdocs/handoff — deferred, not "no new theory" like rotation
            # composition is) — reject explicitly rather than silently
            # producing wrong geometry.
            raise ValidationError(format_fatal_error(
                _("mirror of a composite cell {cell!r} is not supported yet").format(cell=cell_name),
                [_("cell {cell!r} has its own nested clone_placements — mirroring a composite "
                   "cell as a whole isn't implemented yet, only leaf cells can be mirrored; "
                   "remove mirror: true on whatever placement resolves to this cell")
                 .format(cell=cell_name)]
            ))

        # clone.ignore_selection — per-item counterpart of --no-selection,
        # scoped to just this placement's own resolution (see
        # temporarily_ignore_selection's docstring). CellPlacement has no
        # such field at all (closed boundary, no selection mode either —
        # see below) — always False for it, a plain no-op here.
        with self.adapter.temporarily_ignore_selection(getattr(placement, "ignore_selection", False)):
            # Selection mode only exists for a top-level ClonePlacement (the
            # old cluster: branch — an exact Cluster-tag match — was migrated
            # to coordinate_placements on 2026-08-12, Group 0); a nested
            # CellPlacement is a reusable, closed-boundary recipe with no live
            # GUI interaction concept, always resolved by nets.
            if isinstance(placement, ClonePlacement) and clone_uses_selection_mode(
                    placement, adapter=self.adapter, cell=cell,
                    sheet_names=self.sheet_names):
                role_to_ref = resolve_roles_by_selection(self.adapter, cell, placement,
                                                          anchor_position=anchor_position,
                                                          sheet_names=self.sheet_names)
            else:
                role_to_ref = resolve_roles_by_nets(self.adapter, cell, placement,
                                                     anchor_position=anchor_position,
                                                     sheet_names=self.sheet_names)

        # Cell is assumed to be front; back = mirror (see apply_clone_geometry).
        # Resolve net_from_role-bearing via/track nets NOW (role_to_ref is
        # ready, and the live read belongs here, outside the geometry layer),
        # then hand the pre-resolved map to geometry so it stays free of any
        # live-board access.
        resolved_role_nets = self._resolve_role_nets(cell, role_to_ref)
        layout = apply_clone_geometry(placement, cell, role_to_ref,
                                      anchor_position=anchor_position,
                                      mirror=mirror,
                                      parent_rotation_deg=parent_rotation_deg,
                                      resolved_role_nets=resolved_role_nets)
        logger.info(_("  [{name}] cell {tpl!r} on {layer}{mirror_suffix}")
                    .format(name=placement_label, tpl=cell.name, layer=cell.layer,
                            mirror_suffix=_(" -> mirrored as a whole") if mirror else _(" -> as written")))

        components_result: list[PlacedComponentInfo] = []
        vias_result: list[ViaCommand] = []
        tracks_result: list[TrackCommand] = []

        for via_index, via in enumerate(layout.vias):
            vias_result.append(ViaCommand(
                position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                net_name=via.net, owner_ref=placement_label,
                registry_key=make_registry_key(anchor_id, cell_name, None, via_index),
            ))
            logger.debug(_("  [{name}] spoke‑level via: ({x:.3f}, {y:.3f}) mm, net={net}")
                         .format(name=placement_label, x=via.position.x/1e6,
                                 y=via.position.y/1e6, net=via.net))

        for track_index, track in enumerate(layout.tracks):
            track_layer = BoardLayer.BL_B_Cu if track.layer == 'B.Cu' else BoardLayer.BL_F_Cu
            tracks_result.append(TrackCommand(
                start=track.start, end=track.end, width_mm=track.width_mm,
                net_name=track.net, layer=track_layer, owner_ref=placement_label,
                registry_key=make_registry_key(anchor_id, cell_name, None, track_index),
            ))
            logger.debug(_("  [{name}] track: ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, "
                           "net={net}, layer={layer}")
                         .format(name=placement_label, sx=track.start.x/1e6, sy=track.start.y/1e6,
                                 ex=track.end.x/1e6, ey=track.end.y/1e6,
                                 net=track.net, layer=track.layer))

        for comp_layout in layout.components:
            # Slot layer: its own absolute or inherited from the cell;
            # mirror inverts ALL layers — the construction is flipped as a
            # physical object.
            slot_layer = comp_layout.slot_layer or cell.layer
            if mirror:
                slot_layer = 'F.Cu' if slot_layer == 'B.Cu' else 'B.Cu'
            comp_layer = BoardLayer.BL_B_Cu if slot_layer == 'B.Cu' else BoardLayer.BL_F_Cu
            components_result.append(PlacedComponentInfo(
                ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                layer=comp_layer, owner_ref=placement_label,
            ))
            logger.debug(
                _("  [{name}] {ref} (role {role}): position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°")
                .format(name=placement_label, ref=comp_layout.ref, role=comp_layout.role,
                        x=comp_layout.position.x/1e6, y=comp_layout.position.y/1e6,
                        angle=comp_layout.angle_deg)
            )
            for via_index, via in enumerate(comp_layout.vias):
                vias_result.append(ViaCommand(
                    position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                    net_name=via.net, owner_ref=comp_layout.ref,
                    registry_key=make_registry_key(anchor_id, cell_name, comp_layout.role, via_index),
                ))

        # Recurse into nested clone_placements, if any — composing this
        # level's own resolved world-space origin/rotation as the parent
        # frame for each of them, and a path-composed anchor_id
        # ("<this>/<nested.name>") so nested content is uniquely and
        # rename-stably keyed in the registry.
        world_rotation_deg = parent_rotation_deg + placement.rotation_deg
        for nested in cell.clone_placements:
            # Sheet inheritance (2026-08-26, handoff cell_placement_sheet_inherit) +
            # {sheet} placeholder injection (2026-08-26, handoff
            # cell_placement_net_sheet_template): the EFFECTIVE sheet (own explicit
            # value, or inherited from the enclosing placement) is both (a) what a
            # nested CellPlacement with no own sheet resolves to, AND (b) injected
            # into this nested placement's OWN params under "sheet", so any
            # {sheet} placeholder in its nets:/params: (net_resolution.resolve_net ->
            # resolve_placeholder) resolves against it automatically. setdefault, not
            # unconditional overwrite — an explicit user-authored params["sheet"]
            # (unlikely, but possible) wins. `nested` is a SHARED object between
            # recursion branches (see cf1041a) — never mutate it, always a local
            # dataclasses.replace copy.
            effective_sheet = nested.sheet if nested.sheet is not None else getattr(placement, "sheet", None)
            if effective_sheet is not None:
                merged_params = dict(nested.params)
                merged_params.setdefault("sheet", effective_sheet)
                nested = dataclasses.replace(nested, sheet=effective_sheet, params=merged_params)
            nested_cell, nested_cell_name = self._resolve_content(
                nested.cell, nested.role, f"{placement_label}/{nested.name}")
            if nested_cell is None:
                continue
            if nested_cell_name in chain:
                # Cycle guard (2026-08-25, handoff composite_cell_autodetect_
                # and_cycle_guard): a cell referencing itself, directly or
                # through a longer chain, would recurse forever — Python's own
                # RecursionError is an unhelpful crash. Report the actual path
                # (cell names in this branch, e.g. "A -> B -> A") so the user
                # can see exactly where the cycle is. A diamond (the same cell
                # referenced from two SIBLING branches) is deliberately NOT a
                # cycle — each branch carries its own `chain`, so the shared
                # leaf never sees itself twice on one path.
                chain_path = " -> ".join(chain + (nested_cell_name,))
                raise ValidationError(format_fatal_error(
                    _("circular clone_placements reference in {label}: {path}")
                    .format(label=placement_label, path=chain_path),
                    [_("cell {cell!r} already appears in the current branch's chain "
                       "({path}) — a cell may not reference itself, directly or "
                       "indirectly; check the clone_placements: of cell {head!r}")
                     .format(cell=nested_cell_name, path=chain_path, head=chain[0])]))
            nc, nv, nt = self._resolve_one_level(
                nested, nested_cell, nested_cell_name,
                anchor_position=layout.origin,
                parent_rotation_deg=world_rotation_deg,
                anchor_id=f"{anchor_id}/{nested.name}",
                chain=chain + (nested_cell_name,),
            )
            components_result.extend(nc)
            vias_result.extend(nv)
            tracks_result.extend(nt)

        return components_result, vias_result, tracks_result

    def compute_raw_positions(
        self,
        clone_placements: list[ClonePlacement],
    ) -> tuple[list[PlacedComponentInfo], list[ViaCommand], list[TrackCommand]]:
        components_result: list[PlacedComponentInfo] = []
        vias_result: list[ViaCommand] = []
        tracks_result: list[TrackCommand] = []

        for clone in clone_placements:
            if clone.retired:
                continue

            cell, cell_name = self._resolve_content(clone.cell, None,
                                                    clone_placement_effective_name(clone))
            if cell is None:
                continue

            # Resolve anchor BEFORE role resolution — needed for physical
            # proximity narrowing (resolve_roles_by_nets), and the same anchor
            # is later used in apply_clone_geometry (avoid double resolution).
            # clone.ignore_selection — per-item counterpart of --no-selection,
            # scoped to just this clone's own resolution (see
            # temporarily_ignore_selection's docstring).
            with self.adapter.temporarily_ignore_selection(clone.ignore_selection):
                anchor_position = self._resolve_anchor(clone)

            anchor_id = clone_anchor_id(clone)
            # chain starts at the top-level cell: a clone whose cell directly
            # references itself (cell: X -> clone_placements[].cell: X) is a
            # self-cycle and must be caught on the very first recursion.
            c, v, t = self._resolve_one_level(clone, cell, cell_name, anchor_position,
                                              parent_rotation_deg=0.0, anchor_id=anchor_id,
                                              chain=(cell_name,))
            components_result.extend(c)
            vias_result.extend(v)
            tracks_result.extend(t)

        return components_result, vias_result, tracks_result