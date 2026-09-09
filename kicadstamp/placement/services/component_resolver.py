from collections.abc import Callable
# kicadstamp/placement/services/component_resolver.py

import logging


from ...domain.geometry import Vector2

from ...domain.board import Footprint

from ...config import Cell, Config
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError, format_fatal_error
from ...geometry.clone_geometry import resolve_pad_anchor_offset
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


def resolve_pad_mount(adapter: KiCadBoardAdapter, cell: Cell,
                      role_to_ref: dict[str, str], where: str) -> tuple[float, float] | None:
    """The cell's mount A resolved from a LIVE instance of its anchor_role's
    pad, or None when this cell has no pad anchor (every offline case —
    anchor_xy, role-only, no anchor — is left to cell_mount_offset).

    anchor_xy WINS: an explicitly stored mount is the real data (see Cell's
    docstring) and must not be silently overridden by a live re-derivation —
    GUARD 1 (plan_2026_09_09_cell_anchor_v2 §A.5, phase A). The caller passes
    the result to apply_clone_geometry/apply_spoke_geometry as resolved_mount;
    None keeps those functions' historical cell_mount_offset path intact.

    Every unresolvable piece of a DECLARED pad anchor is a FATAL
    ValidationError — never a silent fallback to (0,0), which would shift the
    whole cell content: the anchor role not resolved on the board (GUARD 3),
    its ref missing live, the role not among this cell's own components
    (GUARD 4 — next(..., None) + explicit fatal, never a bare next() whose
    StopIteration would escape), or the anchor pad missing on the footprint.
    The pure frame math is resolve_pad_anchor_offset
    (geometry/clone_geometry.py): the pad's LIVE position is read HERE (via
    the adapter) and handed in, so geometry stays free of live-board access.

    where — caller-supplied human label (clone/chain context) for the fatal
    messages.
    """
    if cell.anchor_xy is not None:
        return None                       # GUARD 1 — anchor_xy wins
    if not (cell.anchor_role and cell.anchor_pad):
        return None
    role = cell.anchor_role
    ref = role_to_ref.get(role)
    if ref is None:                       # GUARD 3
        raise ValidationError(format_fatal_error(
            _("cell {cell!r}: anchor_role {role!r} is not resolved on the board ({where})")
            .format(cell=cell.name, role=role, where=where),
            [_("a pad anchor cannot fall back to (0,0) — that would silently shift "
               "the whole cell content; place the anchor component on the board and "
               "check that its role resolves by net/selection, then apply again")]))
    fp = adapter.get_footprint(ref)
    if fp is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r}: anchor_role {role!r} resolved to {ref!r}, but that "
               "ref is not on the live board ({where})").format(
                cell=cell.name, role=role, ref=ref, where=where),
            [_("the board changed since the roles were resolved — place the anchor "
               "component on the board, then apply again")]))
    slot = next((c for c in cell.components if c.role == role), None)
    if slot is None:                      # GUARD 4 — not a bare next()
        raise ValidationError(format_fatal_error(
            _("cell {name!r}: anchor_role {role!r} is not a component of this cell")
            .format(name=cell.name, role=role),
            [_("anchor_role must name one of this cell's own components; the "
               "mount point cannot be derived from a role that does not "
               "exist")]))
    pad = adapter.get_pad_by_number(fp, cell.anchor_pad)
    if pad is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r}: anchor footprint {ref!r} has no pad {pad!r} ({where})")
            .format(cell=cell.name, ref=ref, pad=cell.anchor_pad, where=where),
            [_("check anchor_pad — pad numbers are strings as in KiCad ('1', '17', 'A3')")]))
    return resolve_pad_anchor_offset(fp, slot, pad.position, cell.layer)


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
