# kicadstamp/placement/dependency_order.py
"""
dependency_order.py — determines the order in which rules/clone_placements
must be planned+executed within one apply run, so that an item anchored on a
component another item in the SAME run is about to move always sees that
component's real, post-move position — not a stale snapshot from before the
run started.

Found 2026-07-27: p5v_led_spoke was anchored on C9, a role slot inside
p5v_pi_filter's own cell — it landed wherever C9 last happened to sit
manually, not where p5v_pi_filter was about to move it to, because the whole
run used to plan from a single board snapshot taken before any moves happened.

Level-by-level (Kahn's algorithm — this is exactly the "build the chain first,
then move and fix" picture): level 0 = items with no anchor at all (absolute
coordinates), or anchored on something nobody in this run moves ("taken
as-is"); level 1 = items anchored on something level 0 produces; level 2 =
anchored on level 0/1 output; etc. cmd_apply plans+executes+commits one whole
level before moving to the next, so by the time a later level's anchor is
resolved, the board already reflects the earlier levels' real moves.

Known limitation: cell-role resolution (which refs get PRODUCED) can, as a
last resort, use physical proximity to the anchor to break ties between
otherwise-identical candidates (see
clone_role_resolver._narrow_ambiguous_candidates). Since this dependency pass
runs against the board BEFORE any moves happen, such a tie could in principle
resolve differently here than it will once execution actually reaches that
item post-move. Only matters for configs that are already relying on a
proximity tie-break to disambiguate; not a regression versus the old
single-snapshot behaviour.
"""
import logging
from dataclasses import dataclass


from ..config import (Config, Rule, ClonePlacement, Point,
                      clone_placement_effective_name)
from ..kicad.adapter import KiCadBoardAdapter
from ..exceptions import ValidationError, format_fatal_error
from .services.manual_position_calculator import resolve_rule_anchor_ref
from .services.clone_position_calculator import resolve_clone_anchor_ref
from .services.point_resolver import resolve_point_anchor_ref
from .services.clone_role_resolver import (resolve_roles_by_selection, resolve_roles_by_nets,
                                           clone_uses_selection_mode)
from .services.component_pool import ComponentPool
from ..i18n import _

logger = logging.getLogger(__name__)


@dataclass
class Item:
    kind: str  # 'rule' | 'clone' | 'point'
    obj: Rule | ClonePlacement | Point
    label: str
    # For 'point' items this is possibly a "point:<name>" token, not a bare
    # ref — see resolve_point_anchor_ref. producer_of/the Kahn loop below are
    # fully generic over the token string, no special-casing needed.
    anchor_ref: str | None
    produces: set[str]


def _resolve_rule_produces(adapter: KiCadBoardAdapter, cfg: Config, rule: Rule) -> set[str]:
    """
    Lightweight equivalent of ManualPositionCalculator.compute_raw_positions()
    that returns ONLY the set of refs this rule will produce — without resolving
    the anchor, looking up pads, computing spoke geometry, or creating
    ViaCommand/TrackCommand objects.

    ComponentPools ARE built and consumed here (in the same order as the real
    geometry pass in planner.py/plan_item), so the 'produces' set matches what
    the real placement will produce. This is the expensive part that cannot be
    avoided — the pool must be scanned to know which refs match the role+net
    criteria. What we skip is all the geometry computation (apply_spoke_geometry,
    pad lookups, via/track command creation).
    """
    produces: set[str] = set()

    # --- Collect all roles needed across non-retired spokes ---
    roles_needed: set[str] = set()
    for spoke in rule.spokes:
        if spoke.retired:
            continue
        cell = cfg.cells.get(spoke.cell)
        if cell is not None:
            roles_needed.update(slot.role for slot in cell.components)

    # --- Collect unique clusters used in non-retired spokes ---
    clusters_needed: set[str | None] = {spoke.cluster for spoke in rule.spokes if not spoke.retired}

    # --- Build ComponentPools per cluster (same as ManualPositionCalculator) ---
    pools_by_cluster: dict[str | None, ComponentPool] = {}
    for cluster in clusters_needed:
        pools_by_cluster[cluster] = ComponentPool(
            adapter,
            rule.net,
            roles=sorted(roles_needed),
            cluster=cluster,
        )

    # --- Consume pools in spoke order (same as ManualPositionCalculator) ---
    for spoke in rule.spokes:
        if spoke.retired:
            continue
        cell = cfg.cells.get(spoke.cell)
        if cell is None:
            continue

        pool = pools_by_cluster[spoke.cluster]
        role_to_ref = {slot.role: pool.pop(slot.role, spoke.pad) for slot in cell.components}
        produces.update(role_to_ref.values())

    return produces


def _resolve_clone_produces(adapter: KiCadBoardAdapter, cfg: Config, clone: ClonePlacement,
                            sheet_names=None) -> set[str]:
    """
    Lightweight equivalent of ClonePositionCalculator.compute_raw_positions()
    that returns ONLY the set of refs this clone will produce — without
    resolving the anchor position, applying clone geometry, or creating
    ViaCommand/TrackCommand objects.

    Role resolvers are called directly (resolve_roles_by_selection or
    resolve_roles_by_nets — the former cluster: mode is gone, see below) —
    they are standalone functions. The anchor_position parameter (used only
    for last-resort physical proximity tie-breaking) is omitted here; the
    known limitation documented in this module's docstring covers that case.
    """
    # Look up the cell — cell: is MANDATORY on ClonePlacement since 2026-08-12
    # (Group 0 consolidation: the role:/cluster: single-component modes migrated
    # 1:1 to coordinate_placements' anchor-relative mode, so there is no more
    # synthetic-cell branch here, same as ClonePositionCalculator._resolve_content).
    cell = cfg.cells.get(clone.cell)
    if cell is None:
        logger.warning(
            _("{name}: cell {cell!r} not found in cells, skipping")
            .format(name=clone_placement_effective_name(clone), cell=clone.cell)
        )
        return set()

    # clone.ignore_selection must apply here too — same as in the real pass
    with adapter.temporarily_ignore_selection(clone.ignore_selection):
        if clone_uses_selection_mode(clone):
            role_to_ref = resolve_roles_by_selection(
                adapter, cell, clone,
                anchor_position=None,
                sheet_names=sheet_names or {},
            )
        else:
            role_to_ref = resolve_roles_by_nets(
                adapter, cell, clone,
                anchor_position=None,
                sheet_names=sheet_names or {},
            )

    return set(role_to_ref.values())


def _build_items(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> list[Item]:
    """Read-only: resolves every non-retired rule/clone_placement's anchor ref and
    produced refs against the board as it is RIGHT NOW. No board mutation —
    lightweight ref-resolution only, no geometry computation (see
    _resolve_rule_produces / _resolve_clone_produces for what is skipped)."""
    _sn = sheet_names or {}
    items: list[Item] = []

    for point in cfg.points.values():
        anchor_ref = resolve_point_anchor_ref(adapter, point, sheet_names=_sn)
        items.append(Item(
            kind='point', obj=point, label=_("point {name!r}").format(name=point.name),
            # Namespaced token — see resolve_point_anchor_ref/Item.anchor_ref.
            anchor_ref=anchor_ref, produces={f"point:{point.name}"},
        ))

    for rule in cfg.rules:
        anchor_ref = resolve_rule_anchor_ref(adapter, cfg, rule, sheet_names=_sn)
        produces = _resolve_rule_produces(adapter, cfg, rule)
        items.append(Item(
            kind='rule', obj=rule, label=_("rule (net {net!r})").format(net=rule.net),
            anchor_ref=anchor_ref, produces=produces,
        ))

    for clone in cfg.clone_placements:
        if clone.retired:
            continue
        # clone.ignore_selection must apply here too — this pass resolves
        # the same anchor/roles as compute_raw_positions does again later
        # for real planning, and both must see the selection the same way
        # (see temporarily_ignore_selection's docstring in kicad/adapter.py).
        with adapter.temporarily_ignore_selection(clone.ignore_selection):
            anchor_ref = resolve_clone_anchor_ref(adapter, cfg, clone, sheet_names=_sn)
            produces = _resolve_clone_produces(adapter, cfg, clone, sheet_names=_sn)
        items.append(Item(
            kind='clone', obj=clone,
            label=_("clone_placement {name!r}").format(name=clone_placement_effective_name(clone)),
            anchor_ref=anchor_ref, produces=produces,
        ))

    return items


def resolve_execution_order(adapter: KiCadBoardAdapter, cfg: Config, sheet_names=None) -> list[Item]:
    """
    Read-only: resolves cfg.rules + cfg.clone_placements (already filtered by
    drop_disabled_rules/apply_only_filter/apply_cluster_filter) into
    level-ordered execution order. Raises ValidationError on a dependency
    cycle — a config where two or more items anchor on each other's output has
    no valid order and must be fixed in the YAML.
    """
    items = _build_items(adapter, cfg, sheet_names=sheet_names)

    producer_of = {}  # ref -> Item that produces it
    for item in items:
        for ref in item.produces:
            producer_of[ref] = item

    remaining = list(items)
    ordered: list[Item] = []
    placed_ids = set()

    while remaining:
        level = [
            it for it in remaining
            if it.anchor_ref is None
            or producer_of.get(it.anchor_ref) is None
            # An item anchored on its OWN pad (e.g. a cell whose origin
            # component is also one of its own role slots — seen live with
            # p5v_led_spoke: anchored on R1.pad2, and R1/R_LED is also a role
            # in its own cell) is not a real cross-item dependency — there
            # is no other item to sequence against, so treat it as satisfied.
            or producer_of[it.anchor_ref] is it
            or id(producer_of[it.anchor_ref]) in placed_ids
        ]
        if not level:
            raise ValidationError(format_fatal_error(
                _("dependency cycle among rules/clone_placements"),
                [_("{count} item(s) form a cycle through their anchors: {items}")
                 .format(count=len(remaining), items=", ".join(it.label for it in remaining)),
                 _("break the cycle: at least one of these must anchor on something "
                   "outside this set (a fixed, pre-existing component, or an absolute "
                   "coordinate)")]
            ))
        ordered.extend(level)
        level_ids = {id(it) for it in level}
        placed_ids.update(level_ids)
        remaining = [it for it in remaining if id(it) not in level_ids]

    return ordered
