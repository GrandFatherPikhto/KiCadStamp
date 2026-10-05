# kicadstamp/explode_format.py
"""Text lines for the "Разнос" plan and journal status (2026-10-05, plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` Р2-1).

ONE formatter for the CLI (``kicadstamp explode plan`` / ``status``) and the
GUI "Разнос" page: a second copy of these label formats is forbidden (rule Д8 —
one rule, one place). Plain text, Qt-free; the GUI turns its plan into rows from
the ``ExplodePlan`` itself and uses these lines for the "Показать журнал"
summary and the warnings.
"""
from __future__ import annotations

from .i18n import _

__all__ = ["format_plan", "format_status"]


def format_plan(plan) -> list:
    """The plan as CLI-ready text lines: cell/instance and area, one "leaves …"
    line per moving instance, the inter-cluster table with its ticks, and the
    plan's warnings. The SAME lines the tab shows."""
    area = plan.area
    lines = [_("explode: {cell} on {where}; area {w:.1f} x {h:.1f} mm").format(
        cell=plan.cell_name, where=plan.label,
        w=area.size.x / 1_000_000.0, h=area.size.y / 1_000_000.0)]
    for inst in plan.instances:
        lines.append(_("  leaves {where}: {fps} component(s), {cu} copper "
                       "item(s); by {dx:.1f}, {dy:.1f} mm").format(
            where=inst.label, fps=len(inst.footprints), cu=len(inst.copper),
            dx=inst.vector[0] / 1_000_000.0, dy=inst.vector[1] / 1_000_000.0))
    if plan.table:
        lines.append(_("  inter-cluster copper:"))
        for piece in plan.table:
            tick = "x" if piece.ticked else " "
            lines.append("    [{tick}] {kind:5} {net:<10} {layer:<6} "
                         "{length:5.1f}mm  touches: {touches}  {uuid}".format(
                             tick=tick, kind=piece.kind, net=piece.net,
                             layer=piece.layer, length=piece.length_mm,
                             touches=piece.touches, uuid=piece.uuid))
    for warning in plan.warnings:
        lines.append("  ! " + warning)
    return lines


def format_status(journal) -> list:
    """The journal status as one CLI-ready line ("clusters exploded …"), or the
    "no journal" line when ``journal`` is None."""
    if journal is None:
        return [_("no explode journal for this board")]
    return [_(
        "clusters exploded {when}: cell {cell} on {cluster}/{sheet}, "
        "{n} item(s); journal kept — run 'explode restore' to undo").format(
            when=journal.get("time", "?"), cell=journal.get("cell", "?"),
            cluster=journal.get("cluster", "?"),
            sheet=journal.get("sheet") or "-",
            n=len(journal.get("items", {})) or 0)]
