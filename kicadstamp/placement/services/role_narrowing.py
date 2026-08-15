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
paths narrow roles INSIDE the cell by the PLACEMENT'S OWN Cluster — by
convention carried in `name` (the GUI's "Cluster:" field on the Source
tab, see gui/docks/placer.py) — NOT by `anchor_cluster`, which narrows
only the EXTERNAL anchor (resolve_footprint_by_role in
clone_role_resolver.py). The two were conflated into one field before
(Denis: "Мы печатаем два раза кластер размещаемого целла. Зачем?!"),
split per his own (Sheet, Cluster, Role) addressing convention, already
correctly separate for Rule/ManualSpoke (rule.anchor_cluster vs
spoke.cluster — see manual_position_calculator.py).

The "Sheet" step got the same split (2026-08-15, the second half of the
08-14 one): the by-nets/by-selection paths narrow roles INSIDE the cell
by the PLACEMENT'S OWN `sheet` field — NOT by `anchor_sheet`, which
narrows only the EXTERNAL anchor (resolve_footprint_by_role). Same
(Sheet, Cluster, Role) addressing convention, this time completing it
for the internal-role cascade. The shared narrow_candidates_by_sheet()
helper below serves BOTH this internal path and CoordinatePlacement's own
sheet: identity (see coordinate_position_calculator.py).
"""
import logging
import math

from kipy.geometry import Vector2

from ...config import ClonePlacement
from ...utils.units import MM
from ...cluster_matching import cluster_prefix_match
from ...constants import CLUSTER_FIELD_NAME
from ...sheet_names import resolve_sheet_path_names
from ...i18n import _

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
    """
    names = resolve_sheet_path_names(fp, sheet_names)
    return anchor_sheet in names


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


def _narrow_by_sheet_cluster_selection(
    candidates,
    adapter,
    selected_refs: set,
    anchor_sheet: str | None,
    anchor_cluster: str | None,
    sheet_names: dict[str, str],
    label: str,
    role_str: str,
) -> list:
    """3-level narrowing cascade shared by _narrow_ambiguous_candidates and
    resolve_footprint_by_role.

    Each step only narrows if it reduces the set — never chooses for the user:
      1. anchor_sheet — filter by _fp_on_sheet (structural, survives Cluster absence).
      2. anchor_cluster — filter by cluster_prefix_match.
      3. selected_refs — filter intersection with current board selection.

    Returns the (possibly narrowed) list — same list if no narrowing applied.
    """
    narrowed = list(candidates)

    if anchor_sheet and len(narrowed) > 1:
        by_sheet = narrow_candidates_by_sheet(narrowed, anchor_sheet, sheet_names)
        if len(by_sheet) < len(narrowed):
            logger.info(_("[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by anchor_sheet {sheet!r}")
                        .format(label=label, role_str=role_str, count=len(narrowed),
                                narrowed=len(by_sheet), sheet=anchor_sheet))
            narrowed = by_sheet

    if anchor_cluster and len(narrowed) > 1:
        by_cluster = [fp for fp in narrowed
                     if cluster_prefix_match(
                         adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '',
                         anchor_cluster)]
        if by_cluster and len(by_cluster) < len(narrowed):
            logger.info(_("[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by anchor_cluster {cluster!r}")
                        .format(label=label, role_str=role_str, count=len(narrowed),
                                narrowed=len(by_cluster), cluster=anchor_cluster))
            narrowed = by_cluster

    if len(narrowed) > 1 and selected_refs:
        by_selection = [fp for fp in narrowed if fp.reference_field.text.value in selected_refs]
        if by_selection and len(by_selection) < len(narrowed):
            logger.info(_("[{label}] role {role_str!r}: {count} candidates narrowed to {narrowed} by current selection")
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
    # sheet concept at all — always None for it, real values for ClonePlacement.
    # `name` is required (and non-empty) on BOTH ClonePlacement and
    # CellPlacement (see config/entries.py), so reading it here is safe.
    # Internal-role narrowing uses the PLACEMENT'S OWN Cluster — by convention
    # `name` already carries it (GUI's "Cluster:" field on Source tab writes
    # straight into name, see placer.py). NOT anchor_cluster: that field narrows
    # only the EXTERNAL anchor (see resolve_footprint_by_role below) — the two
    # were conflated into one field before 2026-08-14 (Denis: "Мы печатаем два
    # раза кластер размещаемого целла. Зачем?!"), split per his own (Sheet,
    # Cluster, Role) addressing convention, already correctly separate for
    # Rule/ManualSpoke (rule.anchor_cluster vs spoke.cluster — see
    # manual_position_calculator.py).
    placement_cluster = getattr(clone, "name", None)
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
        note = note.format(name=clone_name, ref=closest_fp.reference_field.text.value,
                           d=closest_dist, d2=second_dist)
        if second_dist >= 2 * max(closest_dist, 1e-6):
            logger.info(_("[{name}] role {role!r}: {count} candidates narrowed to 1 by physical proximity to anchor "
                          "({ref}, {d:.2f} mm, second closest — {d2:.2f} mm, sufficient gap)")
                        .format(name=clone_name, role=role, count=len(narrowed),
                                ref=closest_fp.reference_field.text.value,
                                d=closest_dist, d2=second_dist))
            narrowed = [closest_fp]
        else:
            logger.debug(_("[{name}] role {role!r}: cannot narrow by proximity — "
                           "{d:.2f} mm vs {d2:.2f} mm, insufficient gap")
                         .format(name=clone_name, role=role, d=closest_dist, d2=second_dist))

    return narrowed, note
