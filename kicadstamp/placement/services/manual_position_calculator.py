# kicadstamp/placement/services/manual_position_calculator.py

import logging

from ...domain.geometry import BoardLayer

from ...config import Config, Rule
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError, format_fatal_error
from ...geometry.spoke_layout import apply_spoke_geometry
from ...registry import make_registry_key
from ..commands import PlacedComponentInfo, ViaCommand, TrackCommand
from .clone_role_resolver import resolve_footprint_by_role
from .component_resolver import ComponentResolver, resolve_anchor_identity
from ...i18n import _

logger = logging.getLogger(__name__)


def resolve_rule_anchor_ref(adapter: KiCadBoardAdapter, cfg: Config, rule: Rule,
                            sheet_names=None) -> str | None:
    """
    Resolves rule's anchor to a concrete ref — see
    component_resolver.resolve_anchor_identity for the shared dispatch (used
    by dependency_order.py to build the producer/consumer graph before any
    planning happens).
    """
    _sn = sheet_names or {}
    return resolve_anchor_identity(
        rule.anchor_ref, rule.anchor_role, rule.anchor_point,
        lambda: resolve_footprint_by_role(
            adapter, rule.anchor_role, rule.anchor_sheet, rule.anchor_cluster,
            _sn, label=_("rule (net {net!r})").format(net=rule.net),
        ),
    )


def rule_anchor_ids(rule: Rule) -> set[str]:
    """
    Registry identity/identities of a rule — one 'pad:{pad}' per non-retired
    spoke (see compute_raw_positions below: anchor_id = f"pad:{spoke.pad}",
    passed to make_registry_key). Unlike ClonePlacement (one clone_anchor_id
    per placement), a Rule is a GROUP of per-pad spokes, each with its own
    registry identity — so this returns a set, not a single id.

    Used for known_anchor_ids (kicadstamp_cli.py's cmd_apply, see
    clone_anchor_id/thermal_anchor_id for the same idea). Without this, a
    rule excluded from a run (retired: true, --only, --cluster) has its
    via/track registry entries pruned unconditionally —
    registry.reconcile()'s known_anchor_ids protection only recognises the
    'anchor:'/'role:'/'name:'/'thermal:' prefixes (ClonePlacement/
    thermal_via_array), never 'pad:', so rule-based geometry was never
    actually protected by --only/--cluster at all (found 2026-07-29: "hiding
    part of fpga.yaml's rules deletes its routing", true even without
    touching retired at all — just being excluded by --only was enough).
    """
    return {f"pad:{spoke.pad}" for spoke in rule.spokes if not spoke.retired}


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

    def compute_raw_positions(
        self,
        rules: list[Rule],
    ) -> tuple[list[PlacedComponentInfo], list[ViaCommand], list[TrackCommand]]:
        components_result: list[PlacedComponentInfo] = []
        vias_result: list[ViaCommand] = []
        tracks_result: list[TrackCommand] = []

        for rule in rules:
            # --- Resolve anchor (anchor_ref / anchor_role / anchor_point) ---
            if rule.anchor_point is not None:
                # Guaranteed already resolved — dependency_order.py orders
                # this rule's Item after the point's. footprint is
                # guaranteed not None — config/loader.py's
                # _point_is_footprint_eligible already rejected any point
                # (or chain) with a shift/xy at load time; this is a
                # defensive check, not the primary guard.
                resolved = self.resolved_points[rule.anchor_point]
                if resolved.footprint is None:
                    raise ValidationError(format_fatal_error(
                        _("rule (net {net!r}): anchor_point {point!r} has no footprint")
                        .format(net=rule.net, point=rule.anchor_point),
                        [_("this should have been caught at load time (config/loader.py) — "
                           "please report")]
                    ))
                target_fp = resolved.footprint
            else:
                target_fp = self._resolver.resolve_anchor_fp(
                    rule.anchor_ref, rule.anchor_role,
                    rule.anchor_sheet, rule.anchor_cluster,
                    label=_("rule (net {net!r})").format(net=rule.net),
                )
            anchor_ref_resolved = target_fp.ref

            # --- Collect all roles needed for this rule ---
            roles_needed = set()
            for spoke in rule.spokes:
                if spoke.retired:
                    continue
                cell = self.cfg.cells.get(spoke.cell)
                if cell is not None:
                    roles_needed.update(slot.role for slot in cell.components)

            # Important: do not skip the rule entirely if roles_needed is empty —
            # this only means "no component‑bearing slots in any spoke cell",
            # not "the rule has no spokes at all". Spokes can carry spoke‑level
            # vias without any sub‑components (e.g. cap_pair_standard without
            # components in old configs) — they don't need a pool at all, but we
            # still need to create their geometry/vias. An empty roles_needed just
            # gives empty pools below — cheap, no special branch needed.

            # --- Collect clusters used in spokes (including None) ---
            clusters_needed = {spoke.cluster for spoke in rule.spokes if not spoke.retired}

            # --- Build pools for each cluster ---
            pools_by_cluster = ComponentResolver.build_pools(
                self.adapter, rule.net, roles_needed, clusters_needed,
            )

            # --- Process each spoke ---
            for spoke in rule.spokes:
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

                # Select pool by spoke cluster — by construction pools_by_cluster
                # already contains the key spoke.cluster (see clusters_needed above)
                # for any non-retired spoke; if it ever stops being true, let it fail
                # loudly (KeyError) rather than silently substituting a freshly
                # created pool that bypasses shared consumption accounting.
                pool = pools_by_cluster[spoke.cluster]

                # Consume pool by roles
                role_to_ref = {slot.role: pool.pop(slot.role, spoke.pad) for slot in cell.components}

                layout = apply_spoke_geometry(pad.position, spoke, cell, rule.net, role_to_ref)
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