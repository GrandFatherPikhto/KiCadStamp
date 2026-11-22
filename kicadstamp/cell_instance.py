# kicadstamp/cell_instance.py
"""The (Cluster, Sheet) instance of a cell on the live board — the ONE owner of
the "cell instance on the board" rule.

Moved here from ``gui/cell_edit_context.py`` on 2026-10-05 (plan
``plan_2026_10_05_explode_r1_core.md`` §1, part of the "Разнос" work): it is a
CORE rule and the core package ``kicadstamp/`` must not import from ``gui/``
(the ``gui -> kicadstamp`` boundary is strictly one-way). No second copy is
left behind; every caller imports the rule from here.

A Cell is an abstract, deliberately cluster-agnostic template (the
template-zoo design): the same slug-named cell may be created from any placed
Cluster instance and used on many boards/channels. The (Cluster, Sheet) pair
that created it is captured in GUI-local state as a HINT — this module turns
that hint into the actual board footprints, through the SAME
role_narrowing cascade the whole project uses for (Sheet, Cluster) addressing,
so there is no second terminology.

Qt-free: the only imports are the addressing trio (``cluster_prefix_match``,
``CLUSTER_FIELD_NAME``, ``narrow_candidates_by_sheet``). Callers are
``gui/select_cell.py``, ``gui/mixed_selection.py``, ``gui/docks/cell_editor.py``
and the "Select cell" / explode paths.
"""
from __future__ import annotations

from typing import Optional

from .cluster_matching import cluster_prefix_match
from .constants import CLUSTER_FIELD_NAME
from .placement.services.role_narrowing import narrow_candidates_by_sheet

__all__ = ["cell_instance_count", "resolve_context_footprints"]


def cell_instance_count(cfg, cell_name: str | None) -> int:
    """How many INSTANCES of ``cell_name`` the loaded config declares — the number
    of DISTINCT (cluster, sheet) addresses among the records that place it.

    Р6 of plan_2026_11_20_cell_read_takes_inter_node_copper (Denis, 20.11): the
    Log line «the cell now draws these pieces at all its N instance(s)» is a claim
    about the CELL — the transferred copper became part of its geometry, so every
    instance gets a copy — and this is the ONE count behind it. It comes from
    ``selection_narrowing.cell_record_addresses`` (the ONE walk of
    ``entities``/``clone_placements``, materialized ``tree_instances`` copies
    included), never from a second walk of the graph.

    An address with NO cluster is not an instance — the same rule ``cell_clusters``
    follows; 0 is then an honest answer for a cell nothing places yet."""
    from .selection_narrowing import cell_record_addresses

    return len({address for address in cell_record_addresses(cfg, cell_name).values()
                if address[0]})


def resolve_context_footprints(adapter, footprints, cluster: str, sheet,
                               sheet_names: Optional[dict]) -> list:
    """The live footprints of the (Cluster, Sheet) instance.

    The CLUSTER step is the hard gate: only footprints whose Cluster field
    cluster_prefix_matches `cluster`. An empty result means the context no
    longer exists on this board — the caller then leaves the UI/selection alone
    (a message, never a fatal). The SHEET step narrows that set through
    role_narrowing.narrow_candidates_by_sheet — the project's ONE (Sheet,
    Cluster) addressing cascade — and, exactly like that cascade everywhere,
    only when it genuinely reduces the set (a stale Sheet never hides the
    cluster's footprints; when the cluster sits on one instance only, Sheet is
    moot)."""
    if not cluster:
        return []
    try:
        members = [fp for fp in (footprints or [])
                   if cluster_prefix_match(
                       adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or "",
                       cluster)]
        if members and sheet:
            by_sheet = narrow_candidates_by_sheet(members, sheet, sheet_names or {})
            if by_sheet and len(by_sheet) < len(members):
                members = by_sheet
        return members
    except Exception:  # noqa: BLE001 — best-effort; a stale context is the norm
        return []
