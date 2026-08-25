from collections.abc import Callable
# kicadstamp/placement/services/component_resolver.py

import logging


from ...domain.geometry import Vector2

from ...domain.board import Footprint

from ...config import Config
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError, format_fatal_error
from .component_pool import ComponentPool
from .clone_role_resolver import resolve_footprint_by_role
from ...i18n import _

logger = logging.getLogger(__name__)


def resolve_anchor_identity(anchor_ref: str | None, anchor_role: str | None,
                            anchor_point: str | None,
                            resolve_role_fp: Callable[[], Footprint]) -> str | None:
    """Shared ref-or-role-or-point-or-none identity dispatch, used by the
    three lightweight "identity only" resolvers (resolve_rule_anchor_ref /
    resolve_clone_anchor_ref / resolve_point_anchor_ref) that
    dependency_order.py calls to build the producer/consumer graph before
    any real planning/position resolution happens. This was the same six
    lines of if/elif copy-pasted three times (see
    handoff_2026_07_31_consolidated.md §7 item 10, "merge when Point
    appears" — Point landed in Phase 3, this is that merge).

    resolve_role_fp is a zero-arg callable, invoked ONLY when anchor_role is
    actually set (matching the original elif's laziness — no wasted/
    erroneous call when anchor_ref already won). It stays a caller-supplied
    callback rather than being folded in here because the underlying
    role-resolve call genuinely differs: Rule/ThermalViaArrayConfig/Point
    all resolve anchor_role via the plain resolve_footprint_by_role,
    ClonePlacement needs resolve_anchor_by_role instead ({placeholder}
    substitution in anchor_sheet from clone.params — Rule/Point have no
    params field and don't need this) — see resolve_footprint_by_ref's own
    docstring for why that decision boundary isn't collapsed either.

    ThermalViaArrayConfig has no identity-only resolver of its own: thermal
    vias are planned strictly after Phase 1 (rules/clone_placements/points)
    has fully committed (see ViaPlanner._resolve_thermal_anchor's
    docstring), so nothing else in the graph can depend on a thermal via's
    position — it never needs to be a graph node, this dispatch is unused
    for it by design, not an oversight.
    """
    if anchor_ref is not None:
        return anchor_ref
    if anchor_role is not None:
        fp = resolve_role_fp()
        return fp.ref
    if anchor_point is not None:
        # Namespaced token — see dependency_order.py's Item.produces for points.
        return f"point:{anchor_point}"
    return None


def resolve_anchor_pad_position(adapter: KiCadBoardAdapter, fp: Footprint,
                                anchor_pad: str, label: str) -> Vector2:
    """Resolves a specific pad's position on an already-resolved anchor
    footprint, or raises a fatal ValidationError. Shared by ClonePlacement
    (clone_position_calculator.py._resolve_anchor) and Point
    (point_resolver.py.resolve_point) — the two anchor types that support
    anchor_pad on the anchor itself. Rule has no anchor_pad field (each
    spoke carries its own pad: individually, resolved later per-spoke, not
    here); ThermalViaArrayConfig's pad: is a different concern entirely (it
    centres the thermal grid on a pad of the ALREADY-resolved anchor
    footprint, not part of anchor resolution — see
    ViaPlanner.plan_vias/plan_thermal_vias, which fatals with
    ComponentNotFoundError, a different exception class, if that lookup
    fails — deliberately not unified with this function).

    label is the same caller-supplied string already passed to
    resolve_footprint_by_ref/resolve_footprint_by_role for the "anchor not
    found" fatal — reused here for "{label}: {ref} has no pad {pad!r}", so
    every caller's existing label convention (a bare name for
    ClonePlacement, an already-formatted "point {name!r}" for Point) keeps
    producing the same lead-in text it did before this was unified.
    """
    pad = adapter.get_pad_by_number(fp, anchor_pad)
    if pad is None:
        raise ValidationError(format_fatal_error(
            _("{label}: {ref} has no pad {pad!r}").format(
                label=label, ref=fp.ref, pad=anchor_pad),
            [_("check anchor_pad — pad numbers are strings as in KiCad ('1', '17', 'A3')")]
        ))
    return pad.position


def resolve_footprint_by_ref(adapter: KiCadBoardAdapter, anchor_ref: str, label: str,
                             not_found_hint: str | None = None) -> Footprint:
    """Look up a footprint by exact ref, or raise a fatal ValidationError.

    Was written three times near-identically (Rule via ComponentResolver
    below, ClonePlacement, ThermalViaArrayConfig — see
    handoff_2026_07_31_consolidated.md §8 Phase 2) — this is the single
    shared "anchor_ref -> footprint" lookup; the ref-vs-role DECISION and the
    role branch itself stay with each caller, since ClonePlacement's role
    branch needs {placeholder} substitution in anchor_sheet
    (clone_role_resolver.resolve_anchor_by_role) that Rule/
    ThermalViaArrayConfig don't have and don't need — not shared logic, not
    worth forcing through one signature.

    not_found_hint — caller-specific actionable hint line; a generic one is
    used if omitted.
    """
    fp = adapter.get_footprint(anchor_ref)
    if fp is None:
        hint = not_found_hint or _("no such ref on the board (typo? component not yet in PCB?)")
        raise ValidationError(format_fatal_error(
            _("{label}: anchor {anchor!r} not found on board").format(label=label, anchor=anchor_ref),
            [hint]
        ))
    return fp


class ComponentResolver:
    """Common logic shared by ``ManualPositionCalculator`` (and, in a
    structurally similar way, ``ClonePositionCalculator``):

    * Resolve an anchor footprint by ref (``anchor_ref``) **or** by role
      (+ sheet + cluster).
    * Build :class:`ComponentPool`` instances per cluster for role-based
      footprint allocation.

    Removes the duplicated "ref vs role" branching that both calculators
    had inline.
    """

    def __init__(self, adapter: KiCadBoardAdapter, config: Config,
                 sheet_names: dict[str, str]):
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names

    def resolve_anchor_fp(self,
                          anchor_ref: str | None,
                          anchor_role: str | None,
                          anchor_sheet: str | None,
                          anchor_cluster: str | None,
                          label: str = "") -> Footprint:
        """Resolve a footprint by ref **or** by role/sheet/cluster.

        Returns the footprint instance. Raises a fatal :class:`ValidationError`
        if *anchor_ref* refers to a footprint that doesn't exist on the board.
        """
        if anchor_ref is not None:
            return resolve_footprint_by_ref(self.adapter, anchor_ref, label)
        return resolve_footprint_by_role(
            self.adapter, anchor_role, anchor_sheet, anchor_cluster,
            self.sheet_names, label=label,
        )

    @staticmethod
    def build_pools(adapter: KiCadBoardAdapter, net: str,
                    roles_needed: set[str],
                    clusters_needed: set[str | None]
                    ) -> dict[str | None, ComponentPool]:
        """Build a :class:`ComponentPool` for each cluster in
        *clusters_needed*.

        Returns ``{cluster_name: ComponentPool}`` — one pool per cluster,
        each covering all *roles_needed*.
        """
        return {
            cluster: ComponentPool(adapter, net,
                                   roles=sorted(roles_needed),
                                   cluster=cluster)
            for cluster in clusters_needed
        }
