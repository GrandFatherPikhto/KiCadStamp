# kicadstamp/placement/services/manual_position_calculator.py

import logging
from typing import TYPE_CHECKING

from ...domain.geometry import BoardLayer, Vector2, Angle

from ...config import Config, Chain, Cell, chain_effective_name
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError, format_fatal_error
from ...geometry.spoke_layout import apply_spoke_geometry
from ...net_resolution import resolve_net_from_role
from ...registry import make_registry_key
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from .clone_role_resolver import resolve_footprint_by_role
from .component_resolver import ComponentResolver, resolve_anchor_identity
from .component_pool import ComponentPool
from ...i18n import _

if TYPE_CHECKING:
    from ...tree_position import PositionOverride

logger = logging.getLogger(__name__)

_ORIGIN = Vector2.from_xy(0, 0)


def resolve_chain_anchor_ref(adapter: KiCadBoardAdapter, cfg: Config, chain: Chain,
                             sheet_names=None) -> str | None:
    """
    Resolves chain's anchor to a concrete ref — see
    component_resolver.resolve_anchor_identity for the shared dispatch (used
    by dependency_order.py to build the producer/consumer graph before any
    planning happens).
    """
    _sn = sheet_names or {}
    return resolve_anchor_identity(
        chain.anchor_ref, chain.anchor_role, chain.anchor_point,
        lambda: resolve_footprint_by_role(
            adapter, chain.anchor_role, chain.anchor_sheet, chain.anchor_cluster,
            _sn, label=_("chain (net {net!r})").format(net=chain.net),
        ),
    )


def chain_roles_needed(cfg: Config, chain: Chain) -> set[str]:
    """The set of component Role fields across all non-retired spokes' cells —
    the SAME roles ComponentPool is built with. Shared with dependency_order.py
    (2026-08-25, P1-3 sibling dedup), so the produced-refs set computed there
    and the real geometry pass below can never drift."""
    roles_needed: set[str] = set()
    for spoke in chain.spokes:
        if spoke.retired:
            continue
        cell = cfg.cells.get(spoke.cell)
        if cell is not None:
            roles_needed.update(slot.role for slot in cell.components)
    return roles_needed


def chain_clusters_needed(chain: Chain) -> set[str | None]:
    """Unique clusters across non-retired spokes (``None`` included) — shared
    with dependency_order.py (2026-08-25)."""
    return {spoke.cluster for spoke in chain.spokes if not spoke.retired}


def consume_role_to_ref(pool: ComponentPool, cell, spoke_pad: str) -> dict[str, str]:
    """Pop one component per role slot of ``cell`` from ``pool`` — the SAME
    consumption expression dependency_order.py uses to compute produced refs
    (2026-08-25). Kept here so the two passes agree exactly."""
    return {slot.role: pool.pop(slot.role, spoke_pad) for slot in cell.components}


def chain_anchor_ids(chain: Chain) -> set[str]:
    """
    Registry identity/identities of a chain — one 'pad:{pad}' per non-retired
    spoke (see compute_raw_positions below: anchor_id = f"pad:{spoke.pad}",
    passed to make_registry_key). Unlike ClonePlacement (one clone_anchor_id
    per placement), a Chain is a GROUP of per-pad spokes, each with its own
    registry identity — so this returns a set, not a single id.

    Used for known_anchor_ids (kicadstamp_cli.py's cmd_apply, see
    clone_anchor_id/thermal_anchor_id for the same idea). Without this, a
    chain excluded from a run (retired: true, --only, --cluster) has its
    via/track registry entries pruned unconditionally —
    registry.reconcile()'s known_anchor_ids protection only recognises the
    'anchor:'/'role:'/'name:'/'thermal:' prefixes (ClonePlacement/
    thermal_via_array), never 'pad:', so chain-based geometry was never
    actually protected by --only/--cluster at all (found 2026-07-29: "hiding
    part of fpga.yaml's chains deletes its routing", true even without
    touching retired at all — just being excluded by --only was enough).
    """
    return {f"pad:{spoke.pad}" for spoke in chain.spokes if not spoke.retired}


# Backward-compat aliases for the 2026-09-01 Rule -> Chain rename.
resolve_rule_anchor_ref = resolve_chain_anchor_ref
rule_roles_needed = chain_roles_needed
rule_clusters_needed = chain_clusters_needed
rule_anchor_ids = chain_anchor_ids


def reproject_pad_for_override(pad_pos: Vector2, fp, override: "PositionOverride") -> Vector2:
    """Re-project a pad's LIVE absolute position into the override anchor
    frame (tree rigid-group redraw, plan_2026_08_29_tree_live_rigid_redraw.md):
    the rule's anchor footprint is treated as sitting at override.position with
    override.rotation_deg instead of its live fp.position/angle_deg — the SAME
    "REPLACES the anchor entirely" semantics ClonePositionCalculator applies to
    a ClonePlacement. pad_pos = fp.position + R(fp.angle_deg)@local, so
    local = R(-fp.angle_deg)@(pad_pos - fp.position); under the override the
    pad lands at override.position + R(override.rotation_deg)@local.
    Non-persistent: fp/record are never mutated, only the returned geometry is
    re-projected (registry identity, built from the real fields, is untouched)."""
    local = (pad_pos - fp.position).rotate(Angle.from_degrees(-fp.angle_deg), _ORIGIN)
    return override.position + local.rotate(Angle.from_degrees(override.rotation_deg), _ORIGIN)


class ManualPositionCalculator:
    """
    Manual positioning of components and vias via spoke cells.
    Supports clusters: for each unique cluster in the rule, a separate
    ComponentPool is built, and spokes take components from their own cluster.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config, sheet_names=None,
                 resolved_points=None):
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names or {}
        # name -> ResolvedPoint, for anchor_point: — see planner.py's
        # PlacementPlanner.resolved_points (owns/shares this dict).
        self.resolved_points = resolved_points if resolved_points is not None else {}
        self._resolver = ComponentResolver(adapter, config, self.sheet_names)

    def _resolve_role_nets(self, cell: Cell, role_to_ref: dict[str, str]) -> dict:
        """Resolve every net_from_role-bearing via/track net against the live
        board, BEFORE geometry — the "geometry does not touch the live board"
        boundary (apply_spoke_geometry docstring) is preserved by doing the
        live read here, outside the geometry layer. Mirrors
        ClonePositionCalculator._resolve_role_nets — Bug 3 spoke-path fix
        (2026-09-05).

        Why this exists: the spoke path previously ignored net_from_role
        entirely and planned every via/track as `net or chain.net` — so a
        GND-assigned via of a bypass role (net_from_role=ROLE, net_from_role_pad='2')
        was planned as the chain RAIL. The registry stored the rail net, the
        live copper was GND (KiCad assigns it by connectivity), adopt/pre-check
        honestly refused (net mismatch) and a second GND copy was created.
        Resolving the role's real pad net live makes the PLAN match the live
        board, so adopt/reconcile converge and the duplicate disappears.

        Returns {(role, pad): net} for each distinct net_from_role in the cell
        (cell-level vias/tracks and every component slot's vias). Each
        resolve_net_from_role is fatal if the role/pad cannot be resolved on
        THIS instance — apply stops, it does not guess. No rule_nets override
        is passed (mirror clone exactly): explicit-pad cells (fpga_pwr_bank)
        are unaffected by rule_nets, and a reused cell resolves identically on
        the chain and clone paths.
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

    def compute_raw_positions(
        self,
        chains: list[Chain],
        position_overrides: dict[str, "PositionOverride"] | None = None,
        isolate_spokes: dict[str, set[str]] | None = None,
    ) -> tuple[list[PlacedComponentInfo], list[ViaCommand], list[TrackCommand]]:
        """Compute component moves + spoke/component vias/tracks for chains.

        ``isolate_spokes`` — GUI "Redraw spoke" isolation (2026-09-05): maps a
        chain effective name to the set of spoke PAD numbers that must actually
        be placed this run. The other (sibling) spokes of such a chain are NOT
        placed, but they STILL consume the shared per-net ComponentPool in
        full-chain order (see the per-spoke loop) — so an isolated spoke is
        assigned exactly the components a FULL chain redraw would give it and
        can never re-claim a neighbour's component. None/empty = every
        non-retired spoke is placed (normal full redraw / apply)."""
        components_result: list[PlacedComponentInfo] = []
        vias_result: list[ViaCommand] = []
        tracks_result: list[TrackCommand] = []

        for chain in chains:
            # --- PositionOverride (tree rigid-group redraw): REPLACES the
            # anchor entirely — the rule's anchor footprint is treated as
            # sitting at override.position/rotation_deg instead of resolving
            # its own anchor_ref/anchor_role/anchor_point (same semantics as
            # ClonePositionCalculator, see clone_position_calculator.py:470+).
            # The identity (target_fp, the registry's 'pad:' keys) is still
            # resolved from the real fields; only the spoke geometry is
            # re-projected (reproject_pad_for_override). Non-persistent: the
            # shared record is never mutated. Bug #5 (2026-08-30): previously
            # position_overrides was never forwarded here, so tree-redrawn
            # rule-nodes silently ignored the override and resolved through
            # their own anchor_role.
            override = (position_overrides or {}).get(chain_effective_name(chain))
            # Single-spoke redraw isolation ("Redraw spoke", 2026-09-05):
            # active_pads = the pads of THIS chain that should actually emit
            # geometry this run (None = every spoke). When isolation is active
            # the sibling spokes are NOT placed, but they still consume the
            # shared pool below in full-chain order. (Before this fix the GUI
            # marked the siblings skip=True and drop_inactive_items removed
            # them from the chain first — so the isolated spoke popped the
            # FIRST natural-order component, i.e. the one owned by its skipped
            # neighbour: "спица ворует компоненты у соседней спицы".)
            active_pads = (isolate_spokes or {}).get(chain_effective_name(chain))
            # --- Resolve anchor (anchor_ref / anchor_role / anchor_point) ---
            if chain.anchor_point is not None:
                # Guaranteed already resolved — dependency_order.py orders
                # this rule's Item after the point's. footprint is
                # guaranteed not None — config/loader.py's
                # _point_is_footprint_eligible already rejected any point
                # (or chain) with a shift/xy at load time; this is a
                # defensive check, not the primary guard.
                resolved = self.resolved_points[chain.anchor_point]
                if resolved.footprint is None:
                    raise ValidationError(format_fatal_error(
                        _("chain (net {net!r}): anchor_point {point!r} has no footprint")
                        .format(net=chain.net, point=chain.anchor_point),
                        [_("this should have been caught at load time (config/loader.py) — "
                           "please report")]
                    ))
                target_fp = resolved.footprint
            else:
                target_fp = self._resolver.resolve_anchor_fp(
                    chain.anchor_ref, chain.anchor_role,
                    chain.anchor_sheet, chain.anchor_cluster,
                    label=_("chain (net {net!r})").format(net=chain.net),
                )
            anchor_ref_resolved = target_fp.ref

            # --- Collect all roles needed for this chain ---
            roles_needed = chain_roles_needed(self.cfg, chain)

            # Important: do not skip the chain entirely if roles_needed is empty —
            # this only means "no component‑bearing slots in any spoke cell",
            # not "the chain has no spokes at all". Spokes can carry spoke‑level
            # vias without any sub‑components (e.g. cap_pair_standard without
            # components in old configs) — they don't need a pool at all, but we
            # still need to create their geometry/vias. An empty roles_needed just
            # gives empty pools below — cheap, no special branch needed.

            # --- Collect clusters used in spokes (including None) ---
            clusters_needed = chain_clusters_needed(chain)

            # --- Build pools for each cluster ---
            pools_by_cluster = ComponentResolver.build_pools(
                self.adapter, chain.net, roles_needed, clusters_needed,
            )

            # --- Process each spoke ---
            for spoke in chain.spokes:
                if spoke.retired:
                    continue

                cell = self.cfg.cells.get(spoke.cell)
                if cell is None:
                    logger.warning(
                        _("Spoke on pad {pad}: cell {cell!r} not found in cells, spoke skipped")
                        .format(pad=spoke.pad, cell=spoke.cell)
                    )
                    continue

                pad = self.adapter.get_pad_by_number(target_fp, spoke.pad)
                if pad is None:
                    logger.warning(
                        _("{anchor} has no pad {pad}, spoke skipped")
                        .format(anchor=anchor_ref_resolved, pad=spoke.pad)
                    )
                    continue

                # Tree rigid-redraw override (bug #5): when present, the spoke's
                # anchor pad is re-projected onto the override anchor frame
                # (override.position/rotation_deg) instead of the footprint's
                # live pad position — see reproject_pad_for_override. The shared
                # record is never mutated; only this run's geometry is affected.
                pad_position = pad.position
                if override is not None:
                    pad_position = reproject_pad_for_override(pad.position, target_fp, override)

                # Select pool by spoke cluster — by construction pools_by_cluster
                # already contains the key spoke.cluster (see clusters_needed above)
                # for any non-retired spoke; if it ever stops being true, let it fail
                # loudly (KeyError) rather than silently substituting a freshly
                # created pool that bypasses shared consumption accounting.
                pool = pools_by_cluster[spoke.cluster]

                # Consume pool by roles — for EVERY non-retired spoke, in chain
                # order. On an isolated single-spoke redraw an inactive sibling
                # still reserves its own components here, so the active spoke
                # keeps the full-chain assignment (never a neighbour's).
                role_to_ref = consume_role_to_ref(pool, cell, spoke.pad)

                # Isolation: sibling spokes only reserve their pool slots above;
                # they emit no geometry/vias/tracks this run.
                if active_pads is not None and spoke.pad not in active_pads:
                    continue

                # Resolve net_from_role-bearing via/track nets NOW — role_to_ref
                # is ready and the live read belongs here, outside the geometry
                # layer (Bug 3 spoke-path fix, 2026-09-05; see _resolve_role_nets).
                resolved_role_nets = self._resolve_role_nets(cell, role_to_ref)
                layout = apply_spoke_geometry(pad_position, spoke, cell, chain.net,
                                              role_to_ref,
                                              resolved_role_nets=resolved_role_nets)
                anchor_id = f"pad:{spoke.pad}"

                # Spoke‑level vias
                for via_index, via in enumerate(layout.vias):
                    vias_result.append(ViaCommand(
                        position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                        net_name=via.net, owner_ref=anchor_ref_resolved,
                        registry_key=make_registry_key(anchor_id, spoke.cell, None, via_index),
                    ))
                    logger.debug(
                        _("  spoke‑level via (pad {pad}): ({x:.3f}, {y:.3f}) mm, net={net}")
                        .format(pad=spoke.pad, x=via.position.x/1e6, y=via.position.y/1e6, net=via.net)
                    )

                # Spoke‑level tracks (net=None in cell inherits rule.net —
                # see spoke_layout._resolve_track). Only spoke‑level: TemplateComponentSlot
                # carries vias, not tracks.
                for track_index, track in enumerate(layout.tracks):
                    track_layer = BoardLayer.BL_B_Cu if track.layer == 'B.Cu' else BoardLayer.BL_F_Cu
                    tracks_result.append(TrackCommand(
                        start=track.start, end=track.end, width_mm=track.width_mm,
                        net_name=track.net, layer=track_layer, owner_ref=anchor_ref_resolved,
                        registry_key=make_registry_key(anchor_id, spoke.cell, None, track_index),
                    ))
                    logger.debug(
                        _("  spoke‑level track (pad {pad}): ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}, layer={layer}")
                        .format(pad=spoke.pad, sx=track.start.x/1e6, sy=track.start.y/1e6,
                                ex=track.end.x/1e6, ey=track.end.y/1e6, net=track.net, layer=track.layer)
                    )

                # Component‑level slots. Slot layer: its own absolute or
                # inherited from the cell — same convention as
                # ClonePlacement (clone_position_calculator.py), minus
                # mirror (ManualSpoke does not support it, see
                # spoke_layout._resolve_track's docstring). Previously
                # never set here at all — components silently inherited
                # PlacementPlanner's single global target_layer regardless
                # of what the cell itself declared (found live:
                # fpga_cap_pair_spoke.yaml's layer: B.Cu was
                # honoured for its tracks but not its components).
                for comp_layout in layout.components:
                    slot_layer = comp_layout.slot_layer or cell.layer
                    comp_layer = BoardLayer.BL_B_Cu if slot_layer == 'B.Cu' else BoardLayer.BL_F_Cu
                    components_result.append(PlacedComponentInfo(
                        ref=comp_layout.ref, dest=comp_layout.position, angle_deg=comp_layout.angle_deg,
                        layer=comp_layer,
                    ))
                    logger.debug(
                        _("  {ref} (role {role}, pad {pad}): position ({x:.3f}, {y:.3f}) mm, angle {angle:.1f}°")
                        .format(ref=comp_layout.ref, role=comp_layout.role, pad=spoke.pad,
                                x=comp_layout.position.x/1e6, y=comp_layout.position.y/1e6,
                                angle=comp_layout.angle_deg)
                    )
                    for via_index, via in enumerate(comp_layout.vias):
                        vias_result.append(ViaCommand(
                            position=via.position, drill_mm=via.drill_mm, diameter_mm=via.diameter_mm,
                            net_name=via.net, owner_ref=comp_layout.ref,
                            registry_key=make_registry_key(anchor_id, spoke.cell, comp_layout.role, via_index),
                        ))
                        logger.debug(
                            _("    via {ref}: ({x:.3f}, {y:.3f}) mm, net={net}")
                            .format(ref=comp_layout.ref, x=via.position.x/1e6, y=via.position.y/1e6, net=via.net)
                        )

        return components_result, vias_result, tracks_result