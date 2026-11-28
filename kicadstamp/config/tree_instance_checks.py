# kicadstamp/config/tree_instance_checks.py
"""Read-only QUESTIONS about a `tree_instances:` TEMPLATE — the raw-dict face of
the expansion, kept out of the giant `config/tree_instances.py` (Н8/Н4 of
plan_2026_11_07_tree_instances_dialog_and_subtree).

Moved here verbatim from `tree_instances._template_generated_clusters`
(composite-guard, 2026-09-08, plan tree_instances_cluster_composite_guard): the
question "which clusters would this template's placement nodes produce" is now
also asked by the Instances dialog BEFORE a row is written (Н4), and by the
cluster-axis converter step — one host, no second walk.
"""
from __future__ import annotations

import logging

from ..i18n import _
from ..sheet_names import ROOT_SHEET
from .sheet_instance_policy import follows_source

logger = logging.getLogger(__name__)

__all__ = ["derive_old_sheet", "template_entry_sheets",
           "template_generated_clusters", "template_needs_old_sheet"]


def template_needs_old_sheet(nodes: list) -> bool:
    """True when the template carries a node whose expansion READS the
    template's own sheet (`old_sheet`): a MOUNT node, whose anchor's sheet
    decides whether it looks inside the template or out at the board.

    A net_trace node never does any more: its record always carries a NAME under
    the format-3 gate and a named record's copper is (role, pad)-based, so
    nothing is rewritten by sheet — the legacy literal-net branch is GONE.

    Used by the entry gate: a declaration that brings its own `anchor` may use a
    template of ANY anchor mode, but such a template must still be able to yield
    a sheet somewhere — otherwise the gate says exactly what to add instead of
    leaving a per-node mystery fatal."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get('kind') == 'mount':
            return True
        if template_needs_old_sheet(node.get('children') or []):
            return True
    return False


def derive_old_sheet(template: dict, entities_by_name: dict,
                     instance_name: str | None = None) -> str | None:
    """The template's own sheet (S) — where its records live BEFORE the instance
    moves them — or None when the template never says.

    TWO sources, in this order, both read off the PLACEMENT RECORDS:

    Р1 (plan_2026_11_27_tree_instances_nested_sheet_paths, Н1): ONE top-level
    placement node whose Entity carries an explicit sheet, with every OTHER
    top-level node being copper (a `net_trace`/`copper` node places no record).
    The live `Ch_L` shape: one placement + six neighbouring net_trace records at
    the top. Requiring exactly one top-level node made that template unable to
    yield its own sheet at all; the copper beside the root places nothing, so it
    cannot make the answer ambiguous. Every OTHER placement record of the
    template must sit on that sheet or below it (segment boundaries, the ONE rule
    in `sheet_instance_policy.follows_source`) — otherwise the template spans
    sheets and this source is not used.

    Н3 (plan_2026_11_07_tree_instances_dialog_and_subtree): every placement
    record of the template sits on ONE non-empty sheet and none is sheetless —
    that sheet IS the answer. Kept as the fallback, because a template may spread
    its records over children of ONE sheet while its root placement is unnamed.

    Both INFO lines name the source, because a derived value must never look
    hand-written. `None` is honest: the caller's policy then keeps the historical
    flat behaviour (and a template with a MOUNT node is refused by the gate,
    exactly as before)."""
    nodes = template.get('nodes') or []
    sheet = _source_from_single_placement(nodes, entities_by_name, instance_name)
    if sheet is not None:
        return sheet
    sheets, without = template_entry_sheets(nodes, entities_by_name)
    if len(sheets) != 1 or without:
        return None
    sheet = next(iter(sheets))
    logger.info(_("tree_instance {name!r}: the template's own sheet {sheet!r} was "
                  "derived from the sheets of its placement records (no role "
                  "anchor and no single root to read it off)").format(
                      name=instance_name, sheet=sheet))
    return sheet


def _source_from_single_placement(nodes: list, entities_by_name: dict,
                                  instance_name: str | None) -> str | None:
    """Р1's source: ONE top-level placement among copper, read off its Entity.

    The narrow rule the plan asks for — never a guess: the sole top-level
    placement must carry an explicit sheet, every other top-level node must place
    no record (copper), and every placement record of the WHOLE template must sit
    on that sheet or below it. A sheetless record or a record on a sister sheet
    means the template spans sheets, so this source is not used and Н3 decides."""
    top = [n for n in nodes if isinstance(n, dict)]
    roots = [n for n in top if n.get('kind') in (None, 'placement')]
    if len(roots) != 1:
        return None
    if any(n.get('kind') not in ('net_trace', 'copper')
           for n in top if n is not roots[0]):
        return None
    ref = roots[0].get('ref')
    entity = entities_by_name.get(ref) if ref is not None else None
    source = entity.get('sheet') if isinstance(entity, dict) else None
    if not source or source == ROOT_SHEET:
        return None
    sheets, without = template_entry_sheets(nodes, entities_by_name)
    if without or not sheets:
        return None
    if not all(follows_source(address, source) for address in sheets):
        return None
    logger.info(_(
        "tree_instance {name!r}: the template's own sheet {sheet!r} was taken from "
        "its single top-level placement node {ref!r} (the other top-level nodes "
        "place no record)").format(name=instance_name, sheet=source, ref=ref))
    return source


def template_entry_sheets(nodes: list, entities_by_name: dict
                          ) -> tuple[set[str], list[str]]:
    """(the sheets of this template's PLACEMENT records, refs whose record has
    no sheet) — the third source of a template's own sheet (Н3, 07.11).

    The template's own sheet is normally read off a role anchor or off the
    single root Entity. A POINT-anchored template with several top-level nodes
    has neither — yet all its placement records may sit on ONE sheet, which
    makes the answer unambiguous (the live `switch_a`: `driver_sw_a` and eight
    mounts, every record on `SW A`). Only `kind` empty/`placement` nodes count:
    a mount/component/net_trace node names no Entity record of its own, and a
    copper container places nothing."""
    sheets: set[str] = set()
    without: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get('kind')
        if kind is None or kind == 'placement':
            ref = node.get('ref')
            entity = entities_by_name.get(ref) if ref is not None else None
            sheet = entity.get('sheet') if isinstance(entity, dict) else None
            if sheet:
                sheets.add(sheet)
            elif ref is not None:
                without.append(ref)
        children = node.get('children') or []
        if children:
            child_sheets, child_without = template_entry_sheets(
                children, entities_by_name)
            sheets |= child_sheets
            without.extend(child_without)
    return sheets, without


def template_generated_clusters(nodes: list, entities_by_name: dict) -> set[str]:
    """The set of distinct non-empty `cluster` values the given template
    placement nodes (recursively through children) would generate BEFORE any
    declaration-level cluster: override — used to detect whether a template is
    homogeneous (<=1 distinct value, override safe to apply everywhere, today's
    behaviour) or composite (>1 distinct value — e.g. ch0_dac_buf's DAC_BUF main
    entity + PIF_AVDD/PIF_CLKVDD/PIF_DVDD sub-blocks — where a blanket override
    would erase the very distinction role_narrowing.py's Cluster step depends
    on, confirmed live 2026-09-08, done_2026_09_08_role_narrowing_live_probe.md).

    net_trace nodes are skipped (their ref names a net, not an entity, and the
    override never applies to them anyway — see the module docstring's v1.2
    note); a placement node whose ref has no entities: entry is skipped too (it
    would be a load-time fatal elsewhere, here it must not crash the probe)."""
    clusters: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get('kind') == 'net_trace':
            continue
        ref = node.get('ref')
        entity = entities_by_name.get(ref) if ref is not None else None
        if entity is not None:
            c = entity.get('cluster')
            if c:
                clusters.add(c)
        children = node.get('children') or []
        if children:
            clusters |= template_generated_clusters(children, entities_by_name)
    return clusters
