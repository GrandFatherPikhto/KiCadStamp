# kicadstamp/explode_cli.py
"""CLI for the "Разнос" tool (Р1, plan
``plan_2026_10_05_explode_r1_core.md`` §4).

    kicadstamp explode plan    --config C --cell X [--cluster K --sheet S] [--margin MM] [--gap MM]
    kicadstamp explode run     ... [--tick UUID]... [--untick UUID]...
    kicadstamp explode restore --config C
    kicadstamp explode status  --config C

`plan` PRINTS and writes nothing (the plan is read-only by construction).
`run` journals first, then shifts in one transaction and verifies 0 nm. Refusals
carry a clear text and a non-zero exit (they are raised as ``PlacerError`` — the
CLI's one owner of exit codes); nothing is written on a refusal.
"""
from __future__ import annotations

import logging

from .exceptions import PlacerError
from .i18n import _

logger = logging.getLogger(__name__)


def cmd_explode(args) -> list:
    command = getattr(args, "explode_command", None)
    if command == "restore":
        return _restore(args)
    if command == "status":
        return _status(args)
    return _plan_or_run(args, run=(command == "run"))


# ── helpers ─────────────────────────────────────────────────────────────────

def _adapter(config_path):
    from .adapter_factory import create_board_adapter
    adapter = create_board_adapter(config_path=config_path)
    adapter.refresh_board()
    return adapter


def _label(cluster, sheet) -> str:
    return f"{cluster}/{sheet}" if sheet else str(cluster)


def _resolve_instance(cfg, cell_name, cluster, sheet) -> tuple:
    """The instance to explode. An explicit --cluster is taken as-is; without it
    the "Select cell" rule on the CONFIG records: exactly one record places the
    cell -> it; none or several -> a refusal with the list."""
    if cluster:
        return str(cluster), (sheet or None)
    pairs, seen = [], set()
    for attr in ("entities", "clone_placements"):
        for rec in getattr(cfg, attr, ()) or ():
            if getattr(rec, "cell", None) != cell_name:
                continue
            c = getattr(rec, "cluster", None)
            if not c:
                continue
            key = (str(c), getattr(rec, "sheet", None))
            if key not in seen:
                seen.add(key)
                pairs.append(key)
    if not pairs:
        raise PlacerError(_(
            "no config record places {cell} — pass --cluster (and --sheet?)")
            .format(cell=cell_name))
    if len(pairs) > 1:
        raise PlacerError(_(
            "{cell} is placed by several records: {names} — pass --cluster/"
            "--sheet to choose one").format(
                cell=cell_name, names=", ".join(_label(*k) for k in pairs)))
    return pairs[0]


def _format_plan(plan) -> list:
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


def _plan_or_run(args, run: bool) -> list:
    from .config.loader import load_config
    from .explode import ExplodeError, plan_explode
    from .explode_journal import explode as run_explode

    cfg, ctx = load_config(args.config)
    cluster, sheet = _resolve_instance(
        cfg, args.cell, getattr(args, "cluster", None), getattr(args, "sheet", None))
    sheet_names = dict(getattr(ctx, "sheet_names", {}) or {})
    overrides = {}
    for uuid in getattr(args, "tick", None) or []:
        overrides[uuid] = True
    for uuid in getattr(args, "untick", None) or []:
        overrides[uuid] = False

    adapter = _adapter(args.config)
    try:
        try:
            plan = plan_explode(
                adapter, cfg, args.config, args.cell, cluster, sheet,
                sheet_names, margin_mm=getattr(args, "margin", 5.0),
                gap_mm=getattr(args, "gap", 5.0), tick_overrides=overrides)
        except ExplodeError as e:
            raise PlacerError(str(e)) from None
        lines = _format_plan(plan)
        if run:
            try:
                lines += run_explode(adapter, plan)
            except ExplodeError as e:
                raise PlacerError(str(e)) from None
        return lines
    finally:
        adapter.close()


def _restore(args) -> list:
    from .explode import ExplodeError
    from .explode_journal import journal_path, restore

    adapter = _adapter(args.config)
    try:
        path = journal_path(adapter)
        if not path.is_file():
            raise PlacerError(_(
                "no explode journal for this board — nothing to restore"))
        try:
            return restore(adapter, path)
        except ExplodeError as e:
            raise PlacerError(str(e)) from None
    finally:
        adapter.close()


def _status(args) -> list:
    from .explode_journal import journal_status

    adapter = _adapter(args.config)
    try:
        journal = journal_status(adapter)
        if journal is None:
            return [_("no explode journal for this board")]
        return [_(
            "clusters exploded {when}: cell {cell} on {cluster}/{sheet}, "
            "{n} item(s); journal kept — run 'explode restore' to undo").format(
                when=journal.get("time", "?"), cell=journal.get("cell", "?"),
                cluster=journal.get("cluster", "?"),
                sheet=journal.get("sheet") or "-",
                n=len(journal.get("items", {})) or 0)]
    finally:
        adapter.close()
