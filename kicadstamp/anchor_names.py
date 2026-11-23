# kicadstamp/anchor_names.py
"""«Anchor id → the name the user knows the record by» — the ONE naming step of a
Log line (С2 of plan_2026_11_20_cell_read_takes_inter_node_copper, Denis 23.11).

WHY. The read's lines name copper by its registry ANCHOR id, and under the
format-3 gate that id carries a UUID: «subtracted from selection: 1 item(s) —
name:6038e889-…: 1» says nothing to the person reading the Log. The same line as
«spoke_rst_switcha: 1» says WHICH record lost its copper — and the record is the
thing the user edits.

ONE home, built with the SAME builders that WRITE those ids (``entity_anchor_id``
/ ``clone_anchor_id``, ``thermal_anchor_id``, ``net_trace_anchor_id``): no key is
parsed and no second list of prefixes is kept. ``point:`` needs no entry — its
anchor id already IS the point's name.

A name is missing when the record is not in the config any more (an ORPHAN key):
the anchor id is then returned unchanged, so the Log still points at the exact key
(``name:<uuid>``) instead of inventing a placeholder. A NESTED anchor
(``<outer>/<nested>``) is NOT resolved either: splitting that path is forbidden
(``clone_position_calculator.nested_anchor_id`` — a slash is legal inside a cluster
name), and its suffix is the nested record's own name already.

Qt-free, no board: it reads the LOADED config only.
"""
from __future__ import annotations

__all__ = ["name_for", "named_anchors"]


def named_anchors(cfg) -> dict[str, str]:
    """``{anchor_id -> record name}`` for every record the config still holds.

    The heavy builders are imported INSIDE the function: this module is called from
    a GUI worker, and ``placement`` must not be dragged in by an import at load
    time (the same rule ``anchor_ids.config_anchor_ids`` follows)."""
    from .config import net_trace_effective_name
    from .net_trace_planner import net_trace_anchor_id
    from .placement.services.clone_position_calculator import (
        clone_anchor_id,
        entity_anchor_id,
    )
    from .placement.services.via_planner import thermal_anchor_id

    names: dict[str, str] = {}
    for clone in getattr(cfg, "clone_placements", None) or ():
        name = getattr(clone, "name", None) or getattr(clone, "cluster", None)
        if name:
            names[clone_anchor_id(clone)] = str(name)
    for entity in getattr(cfg, "entities", None) or ():
        name = getattr(entity, "name", None) or getattr(entity, "cluster", None)
        if name:
            names[entity_anchor_id(entity)] = str(name)
    for array in getattr(cfg, "thermal_via_arrays", None) or ():
        name = getattr(array, "name", None)
        if name:
            names[thermal_anchor_id(array)] = str(name)
    for record in getattr(cfg, "net_traces", None) or ():
        names[net_trace_anchor_id(record)] = net_trace_effective_name(record)
    return names


def name_for(anchors: dict, anchor: str) -> str:
    """The name ``anchor`` is known by, or the anchor id itself.

    ``anchors`` is :func:`named_anchors`' map. No name means the record is gone (an
    orphan) or the anchor is a nested path — both are returned UNCHANGED on purpose:
    a wrong friendly name in the Log is worse than a long right one."""
    return (anchors or {}).get(anchor) or str(anchor)
