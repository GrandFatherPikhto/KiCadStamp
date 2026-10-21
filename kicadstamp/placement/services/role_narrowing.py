# kicadstamp/placement/services/role_narrowing.py
"""
role_narrowing.py — the shared candidate-narrowing cascade for role‑to‑ref
resolution. Split out of clone_role_resolver.py during the T3.1 god-file
decomposition (behavior-preserving code move — see
handoff_2026_08_05_architecture_fixes_roadmap.md).

This is the ONE narrowing logic used by all three resolution paths in
clone_role_resolver.py (by-nets, by-selection, and anchor-by-role):
sheet -> Cluster -> current selection -> (optionally, in
_narrow_ambiguous_candidates) physical proximity to the anchor. Each step
only narrows if it reduces the set — it never chooses for the user.

The "Cluster" step is deliberately split (2026-08-14, plan
split_anchor_cluster_from_placement_cluster): the by-nets/by-selection
paths narrow roles INSIDE the cell by the PLACEMENT'S OWN Cluster — carried
in `cluster` (renamed 2026-08-24 from `name`; the GUI's "Cluster:" field on
the Source tab, see gui/docks/placer.py) — NOT by `anchor_cluster`, which
narrows only the EXTERNAL anchor (resolve_footprint_by_role in
clone_role_resolver.py). The two were conflated into one field before
(Denis: "We print the placed cell's cluster twice. Why?!"),
split per his own (Sheet, Cluster, Role) addressing convention (the removed
Rule/ManualSpoke pair kept rule.anchor_cluster separate from spoke.cluster).

The "Sheet" step got the same split (2026-08-15, the second half of the
08-14 one): the by-nets/by-selection paths narrow roles INSIDE the cell
by the PLACEMENT'S OWN `sheet` field — NOT by `anchor_sheet`, which
narrows only the EXTERNAL anchor (resolve_footprint_by_role). Same
(Sheet, Cluster, Role) addressing convention, this time completing it
for the internal-role cascade. The shared narrow_candidates_by_sheet()
helper below serves BOTH this internal path and CoordinatePlacement's own
sheet: identity (see coordinate_position_calculator.py).

2026-10-13 (plan plan_2026_10_13_cluster_confined_roles.md): an entity/record
that carries a `cluster` addresses its components BY that cluster, so a
candidate whose Cluster field is anything else is NEVER a valid answer — not
with one candidate and not with several. own_cluster_candidates() below is the
ONE implementation of that rule (a filter, deliberately not a chooser). The
old behaviour — the cascade narrowing by cluster only when 2+ candidates
survived, and the baked literal net_template of the cell falling back to a
component of a FOREIGN cluster — let a redraw of spoke_vdd_02_mcu drag C27/C29
out of the SPOKE_VDDA cluster (live incident 2026-10-13).
"""
import logging
import math

from ...domain.geometry import Vector2

from ...config import ClonePlacement
from ...utils.units import MM
from ...cluster_matching import cluster_prefix_match
from ...constants import CLUSTER_FIELD_NAME
from ...sheet_names import resolve_sheet_path_names, sheet_in_path
from ...i18n import _
from ...role_conflict_reasons import role_scope_problems
from .role_refusals import raise_role_outside_cluster

logger = logging.getLogger(__name__)


def _fp_on_sheet(fp, anchor_sheet: str, sheet_names: dict[str, str]) -> bool:
    """
    anchor_sheet appears as ONE OF THE SEGMENTS of the human‑readable path of fp
    (not necessarily the last one — the component may be deeper than the specified
    sheet). The path is built via sheet_names (see kicadstamp/sheet_names.py) —
    direct parsing of .kicad_sch, empirically confirmed on a real project
    (0 conflicts, 0 unresolved UUIDs on mishin‑coil). Works for ANY component —
    unlike the previous approach via local net names, does not require fp to have
    a local net at all.

    The "one of the segments" test itself lives in
    sheet_names.sheet_in_path — ONE rule, shared with the inter-node copper
    capture's node matching (internode_capture), so an anchor and its copper can
    never again disagree about which sheet a component is on.
    """
    return sheet_in_path(resolve_sheet_path_names(fp, sheet_names), anchor_sheet)


def narrow_candidates_by_sheet(candidates, sheet: str | None,
                               sheet_names: dict[str, str]) -> list:
    """Narrow `candidates` to those whose resolved sheet-instance path
    contains `sheet` — but ONLY if that actually reduces the set (never
    chooses for the user, same convention as every other step in
    _narrow_by_sheet_cluster_selection). `sheet` is optional in every
    caller — this is a no-op when it's None/empty or candidates already
    has 0-1 entries."""
    if not sheet or len(candidates) <= 1:
        return candidates
    narrowed = [fp for fp in candidates if _fp_on_sheet(fp, sheet, sheet_names)]
    if narrowed and len(narrowed) < len(candidates):
        return narrowed
    return candidates


def own_cluster_candidates(adapter, candidates, cluster) -> list:
    """The candidates that belong to the entity's OWN Cluster — the ONE place
    this rule is implemented (2026-10-13, plan
    plan_2026_10_13_cluster_confined_roles.md).

    An entity that carries a `cluster` addresses its components BY it
    ((Sheet, Cluster, Role) addressing), so a candidate carrying a DIFFERENT
    Cluster is never a valid answer — not when it is the only candidate left by
    net matching, not when several survive. A candidate with an EMPTY Cluster
    field is not a valid answer either: the component is not tagged as belonging
    to this cluster, and "untagged" must not pass for "mine"; both are NAMED in
    the refusal (see role_conflict_reasons.py), never silently taken.

    An empty/None `cluster` on the RECORD (refs-only or sheet-only addressing)
    keeps the previous behaviour: the filter is a no-op then.

    The comparison is the project's ONE cluster matcher, cluster_prefix_match
    (kicadstamp/cluster_matching.py) — segment prefix, case-insensitive — so a
    hierarchical tag ("Channel_1/sub") still belongs to "Channel_1". Exact
    equality would break that hierarchy.

    Callers: clone_role_resolver.resolve_roles_by_nets /
    resolve_roles_by_selection and the read-only cluster search of
    gui/docks/live_position.py. This function only FILTERS; the WORDING of a
    refusal lives in role_conflict_reasons.py (one text for both paths).

    Cells of the plan's table: 0 own candidates while the role exists elsewhere
    -> refuse, NAME the foreign/untagged component; 1 own candidate -> it (a
    baked net_template never picks one, its disagreement with the live net is a
    Log line only); 2+ own candidates -> refuse, naming either the copies of ONE
    refdes or the several refdes that need distinct roles in the schematic.
    """
    if not cluster:
        return list(candidates)
    return [fp for fp in candidates
            if cluster_prefix_match(
                adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '', cluster)]


def confine_candidates_to_cluster(adapter, candidates, role: str,
                                  cluster: str | None, label: str,
                                  *, hint: bool = False) -> list:
    """``candidates`` confined to a cluster the caller NAMED — at ANY count.

    Ч1 of plan_2026_10_21_role_lookup_steals_across_clusters (В1): a named
    cluster means the candidate MUST carry it, one candidate or ten. Before this
    the cascade narrowed by cluster only at 2+, so a single candidate was
    returned unchecked — the live fact: `R_LED`/`P5V_IN` resolved to R2 of
    `LDO_P3V3` after KiCad renamed that Cluster away.

    FILTER — `own_cluster_candidates` above; WORDS —
    `role_conflict_reasons.role_scope_problems`, the pair the entity path has
    used since 2026-10-13; `role_refusals.raise_role_outside_cluster` only adds
    the label prefix the other anchor refusals carry. The cascade keeps its own
    "narrow only if it reduces the set" rule — that is the internal-role path's.

    ``hint=True`` — the cluster is NOT an address of ``role``, only a suggestion.
    The ONE caller is ``net_trace_planner._item_net_name``, where the value is
    the ANCHOR's cluster handed to the role at the OTHER end of the copper (the
    FPGA side of a channel bridge). Nothing is filtered then and only the
    cascade's soft cluster step applies, exactly as before Ч1.

    An unnamed cluster (None/'') returns the candidates untouched, and an EMPTY
    list is not this rule's business: the caller raises "not found" first, so a
    board fact is never re-worded as a cluster complaint.
    """
    if not cluster or hint:
        return list(candidates)
    confined = own_cluster_candidates(adapter, candidates, cluster)
    if not confined:
        raise_role_outside_cluster(
            label, role, cluster,
            role_scope_problems(adapter, role, cluster, []))
    return confined


def _narrow_by_sheet_cluster_selection(
    candidates,
    adapter,
    selected_refs: set,
    anchor_sheet: str | None,
    anchor_cluster: str | None,
    sheet_names: dict[str, str],
    label: str,
    role_str: str,
    *,
    quiet: bool = False,
) -> list:
    """3-level narrowing cascade shared by _narrow_ambiguous_candidates and
    resolve_footprint_by_role.

    Each step only narrows if it reduces the set — never chooses for the user:
      1. anchor_sheet — filter by _fp_on_sheet (structural, survives Cluster absence).
      2. anchor_cluster — filter by cluster_prefix_match.
      3. selected_refs — filter intersection with current board selection.

    ``quiet`` — an EXPLICIT request from a caller whose question is a dry, per
    record comparison (the mixed-selection read resolves the net of EVERY
    ``net_traces`` record, so these lines used to number in the thousands for ONE
    click — part 2 of plan_2026_10_08_narrowing_net_traces_cost): the steps then
    log at DEBUG instead of INFO. Never global state — the REDRAW passes nothing
    and writes exactly as before.

    Returns the (possibly narrowed) list — same list if no narrowing applied.
    """
    narrowed = list(candidates)
    log = logger.debug if quiet else logger.info

    if anchor_sheet and len(narrowed) > 1:
        by_sheet = narrow_candidates_by_sheet(narrowed, anchor_sheet, sheet_names)
        if len(by_sheet) < len(narrowed):
            log(_("[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by anchor_sheet {sheet!r}")
                .format(label=label, role_str=role_str, count=len(narrowed),
                        narrowed=len(by_sheet), sheet=anchor_sheet))
            narrowed = by_sheet

    if anchor_cluster and len(narrowed) > 1:
        by_cluster = [fp for fp in narrowed
                     if cluster_prefix_match(
                         adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '',
                         anchor_cluster)]
        if by_cluster and len(by_cluster) < len(narrowed):
            log(_("[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by anchor_cluster {cluster!r}")
                .format(label=label, role_str=role_str, count=len(narrowed),
                        narrowed=len(by_cluster), cluster=anchor_cluster))
            narrowed = by_cluster

    if len(narrowed) > 1 and selected_refs:
        by_selection = [fp for fp in narrowed if fp.ref in selected_refs]
        if by_selection and len(by_selection) < len(narrowed):
            log(_("[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by current selection")
                .format(label=label, role_str=role_str, count=len(narrowed),
                        narrowed=len(by_selection)))
            narrowed = by_selection

    return narrowed


def _narrow_ambiguous_candidates(candidates, clone: ClonePlacement, adapter, selected_refs: set,
                                 anchor_position: Vector2 | None, clone_name: str, role: str,
                                 sheet_names: dict[str, str]):
    """
    Common narrowing cascade for ambiguous candidates: the placement's OWN
    sheet (`sheet`, not `anchor_sheet` — see module docstring for the
    2026-08-15 split) -> the placement's OWN Cluster (`name`, not
    `anchor_cluster` — see module docstring for the 2026-08-14 split) ->
    current selection -> physical proximity to anchor. Used both in
    resolve_roles_by_nets (after net‑based matching) and in
    resolve_roles_by_selection (when a role is not found in the selection but
    is ambiguous on the whole board) — one narrowing logic, not two copies.
    Returns (narrowed list, note for error message — empty string if narrowed
    to one).

    The sheet step was added for anchor_sheet on 2026-07-28 (found via
    dac_pi_filter on a global net like +3V3 shared by every PI‑filter on the
    board — Cluster alone can't help when the schematic reuses ONE physical
    section per channel via a hierarchical sheet, since custom fields like
    Cluster are shared across every instance of a reused sheet). Split
    2026-08-15: internal-role narrowing now uses the placement's OWN `sheet`
    (the same (Sheet, Cluster, Role) convention as Cluster via name), while
    `anchor_sheet` narrows only the EXTERNAL anchor search
    (resolve_footprint_by_role) — the second half of the 2026-08-14
    anchor_cluster split. The sheet step goes FIRST, before Cluster: sheet
    membership is the more structural signal here (survives even when
    Cluster isn't set at all on these particular components).
    """
    # getattr, not direct attribute access: this function also serves
    # CellPlacement (nested, closed-boundary references inside a composite
    # Cell — see config/models.py), which has no anchor_sheet/anchor_cluster/
    # anchor_pad/by_selection/ignore_selection at all — always None for it,
    # real values for ClonePlacement. Since 2026-08-26 CellPlacement DOES have
    # its own-identity sheet/cluster (own identity for internal role
    # narrowing, NOT an external anchor — see config/models.py), so those two
    # read real values for nested placements too.
    # `name` is required (and non-empty) on BOTH ClonePlacement and
    # CellPlacement (see config/entries.py), so reading it here is safe.
    # Internal-role narrowing uses the PLACEMENT'S OWN Cluster — the `cluster`
    # field (renamed 2026-08-24 from `name`; the GUI's "Cluster:" field on the
    # Source tab writes straight into cluster, see placer.py). NOT anchor_cluster:
    # that field narrows only the EXTERNAL anchor (see resolve_footprint_by_role
    # below) — the two were conflated into one field before 2026-08-14 (Denis:
    # "We print the placed cell's cluster twice. Why?!"), split per
    # his own (Sheet, Cluster, Role) addressing convention (the removed
    # Rule/ManualSpoke pair kept rule.anchor_cluster separate from spoke.cluster).
    placement_cluster = getattr(clone, "cluster", None)
    # Internal-role narrowing ALSO uses the placement's OWN sheet — the
    # 2026-08-15 second half of the 08-14 anchor_cluster split, the (Sheet,
    # Cluster, Role) convention completed for the internal-role cascade. NOT
    # anchor_sheet: that field narrows only the EXTERNAL anchor
    # (resolve_footprint_by_role), the same conflation anchor_sheet had before
    # (the code even referenced the same dac_pi_filter bug that motivated
    # anchor_sheet's own external-anchor narrowing). Deliberately NOT passed
    # through resolve_placeholder — this is an "own identity" field, not an
    # "external, templated" field (anchor_sheet keeps that treatment in
    # resolve_anchor_by_role / resolve_footprint_by_role).
    placement_sheet = getattr(clone, "sheet", None)

    narrowed = _narrow_by_sheet_cluster_selection(
        candidates, adapter, selected_refs,
        placement_sheet, placement_cluster,
        sheet_names, clone_name, role,
    )

    note = ""
    if len(narrowed) > 1 and anchor_position is not None:
        with_dist = sorted(
            ((math.hypot((fp.position.x - anchor_position.x) / MM,
                         (fp.position.y - anchor_position.y) / MM), fp)
             for fp in narrowed),
            key=lambda t: t[0]
        )
        closest_dist, closest_fp = with_dist[0]
        second_dist = with_dist[1][0]
        note = _(" (closest to anchor {name!r}: {ref} at {d:.2f} mm, second — {d2:.2f} mm)")
        note = note.format(name=clone_name, ref=closest_fp.ref,
                           d=closest_dist, d2=second_dist)
        if second_dist >= 2 * max(closest_dist, 1e-6):
            logger.info(_("[{name}] role {role!r}: {count} candidates narrowed to 1 by physical proximity to anchor "
                          "({ref}, {d:.2f} mm, second closest — {d2:.2f} mm, sufficient gap)")
                        .format(name=clone_name, role=role, count=len(narrowed),
                                ref=closest_fp.ref,
                                d=closest_dist, d2=second_dist))
            narrowed = [closest_fp]
        else:
            logger.debug(_("[{name}] role {role!r}: cannot narrow by proximity — "
                           "{d:.2f} mm vs {d2:.2f} mm, insufficient gap")
                         .format(name=clone_name, role=role, d=closest_dist, d2=second_dist))

    return narrowed, note
