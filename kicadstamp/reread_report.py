# kicadstamp/reread_report.py
"""The inter-node copper re-read's report as LOG LINES (design §6 — a list, never
a dialog; plan §0 п.3/п.5 of plan_2026_11_10_extra_copper_by_selection).

Moved out of `internode_capture.py`, which may only shrink: the report is the one
thing there that is pure wording, and §0 gave it two more lines (the AREA and the
«places module(s)» note) plus the «not in the selection» list. `internode_capture.
reread_report_lines` stays as a one-line delegate, so every caller and every test
keeps its import.

Full names, never abbreviated: they are how a bridge is found in the tree and in
the flat list. The found/updated/unchanged/not-found lines are the historical ones,
byte for byte (logs and tests read them as substrings); everything Э4 of
plan_2026_09_15_internode_copper_sheets_and_nets added — the discarded counters and
the «no component matched any node» line — is appended AFTER them: silence about
thrown-away copper was half of the live complaint, so the report also says what it
did NOT take and why.
"""
from __future__ import annotations

from .i18n import _
from .internode_copper import CopperVerdict

__all__ = ["DISCARD_ORDER", "report_lines"]

# The order the report lists copper the classification did NOT take in: the
# design's own table order (Р1), minus INTERNODE — that is what a re-read takes.
# MODULE rides between FOREIGN and CLUSTER: it is copper INSIDE a module, which
# that module's own tree re-reads, so the parent names it and leaves it (Т4-1).
DISCARD_ORDER = (CopperVerdict.FOREIGN, CopperVerdict.MODULE,
                 CopperVerdict.CLUSTER, CopperVerdict.UNMOORED,
                 CopperVerdict.STUB)


def report_lines(tree_name: str, plan) -> list[str]:
    """The plan's LOG LINES, in order: header, AREA, modules, added, updated,
    not found, not in the selection, unchanged, not taken, what the nodes expect,
    then one line per warning."""
    lines = [_("Tree {name!r}: inter-node copper re-read.").format(name=tree_name)]
    if plan.narrowed:
        lines.append(_("  area: selection — {tracks} track(s), {vias} via(s)").format(
            tracks=plan.area_tracks, vias=plan.area_vias))
    else:
        lines.append(_("  area: whole board"))
    if plan.module_refs:
        lines.append(_(
            "  Tree {name!r} places module(s) {trees} — their inter-node copper "
            "is re-read on their own tabs").format(
                name=tree_name, trees=", ".join(plan.module_refs)))
    if plan.added:
        lines.append(_("  added:      {names}").format(
            names=", ".join(c.identity for c in plan.added)))
    if plan.updated:
        described = "; ".join(
            _("{name} (was {old_tracks} tracks / {old_vias} via, now {new_tracks} / {new_vias})")
            .format(name=c.identity, old_tracks=counts[0], old_vias=counts[1],
                    new_tracks=counts[2], new_vias=counts[3])
            for c, counts in plan.updated)
        lines.append(_("  updated:    {names}").format(names=described))
    for ident in plan.missing:
        lines.append(_(
            "  not found:  {name} — the copper is no longer on the board, the "
            "record is kept").format(name=ident))
    if plan.not_in_selection:
        lines.append(_("  not in the selection: {count} record(s) — not checked")
                     .format(count=len(plan.not_in_selection)))
    lines.append(_("  unchanged:  {count}").format(count=len(plan.unchanged)))
    if plan.discarded:
        counts = ", ".join(
            "{verdict} {count}".format(verdict=verdict.value,
                                       count=plan.discarded[verdict.value])
            for verdict in DISCARD_ORDER if plan.discarded.get(verdict.value))
        lines.append(_("  not taken:  {counts} — copper the classification left "
                       "alone").format(counts=counts))
    if plan.unmatched_components and not plan.matched_components:
        lines.append(_(
            "  no component of the area matched any node of tree {tree!r} by "
            "(Cluster, sheet) — the tree's nodes expect: {keys}").format(
                tree=tree_name, keys=", ".join(plan.node_keys) or "-"))
    for warning in plan.warnings:
        lines.append("  " + warning)
    return lines
