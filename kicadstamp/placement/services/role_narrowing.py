# kicadstamp/placement/services/role_narrowing.py
"""
role_narrowing.py — the shared candidate-narrowing cascade for role‑to‑ref
resolution. Split out of clone_role_resolver.py during the T3.1 god-file
decomposition (behavior-preserving code move — see
handoff_2026_08_05_architecture_fixes_roadmap.md).

This is the ONE narrowing logic used by all three resolution paths in
clone_role_resolver.py (by-nets, by-selection, and anchor-by-role):
anchor_sheet -> anchor_cluster -> current selection -> (optionally, in
_narrow_ambiguous_candidates) physical proximity to the anchor. Each step
only narrows if it reduces the set — it never chooses for the user.
"""
import logging
import math

from kipy.geometry import Vector2

from ...config import ClonePlacement
from ...net_resolution import resolve_placeholder
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
        by_sheet = [fp for fp in narrowed if _fp_on_sheet(fp, anchor_sheet, sheet_names)]
        if by_sheet and len(by_sheet) < len(narrowed):
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
    Common narrowing cascade for ambiguous candidates: anchor_sheet -> Cluster
    -> current selection -> physical proximity to anchor. Used both in
    resolve_roles_by_nets (after net‑based matching) and in
    resolve_roles_by_selection (when a role is not found in the selection but
    is ambiguous on the whole board) — one narrowing logic, not two copies.
    Returns (narrowed list, note for error message — empty string if narrowed
    to one).

    anchor_sheet narrowing (added 2026-07-28, found via dac_pi_filter on a
    global net like +3V3 shared by every PI‑filter on the board — Cluster
    alone can't help when the schematic reuses ONE physical section per
    channel via a hierarchical sheet, since custom fields like Cluster are
    shared across every instance of a reused sheet, the same limitation
    anchor_sheet was already built to work around for anchor resolution
    itself — see resolve_footprint_by_role below, whose cascade order
    (sheet, then cluster) this mirrors) goes FIRST, before Cluster: sheet
    membership is the more structural signal here (survives even when
    Cluster isn't set at all on these particular components).
    """
    # getattr, not direct attribute access: this function also serves
    # CellPlacement (nested, closed-boundary references inside a composite
    # Cell — see config/models.py), which has no anchor_sheet/anchor_cluster
    # concept at all — always None for it, real values for ClonePlacement.
    anchor_sheet = getattr(clone, "anchor_sheet", None)
    anchor_cluster = getattr(clone, "anchor_cluster", None)
    resolved_anchor_sheet = None
    if anchor_sheet:
        resolved_anchor_sheet = resolve_placeholder(anchor_sheet, clone.params, what="anchor_sheet")

    narrowed = _narrow_by_sheet_cluster_selection(
        candidates, adapter, selected_refs,
        resolved_anchor_sheet, anchor_cluster,
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
