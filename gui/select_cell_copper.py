# gui/select_cell_copper.py
"""«Select cell components» / «Select cell» — the GUI half
(plan ``plan_2026_10_05_select_cell_split.md``; Denis 2026-10-05).

Two menu items and two CellDock buttons share this module:

* **«Select cell components»** — the instance's board components ONLY (the
  identified-refs branch as before, else ``resolve_context_footprints``);
* **«Select cell»** — the components PLUS the RECORDED copper of the instance
  (``gui/select_cell.select_cell_copper_targets``: registry then geometry over
  the commands the REDRAW PLANNER produced for this one record).

Qt-light by design (deepseek.md §45 — the giants keep one-line wiring): this
module holds the workers (plain data in, plain data out). Every board touch runs
on the WORKER (``gui.worker.start_long_op``): the copper path drives a whole
``ApplyPipeline(..., dry_run=True)`` and uses ITS adapter (``pipeline.adapter``)
for the live reads and ``select_items``, closing it in a ``finally``
(``pipeline.close()``); the components path builds its OWN adapter and closes it
in a ``finally``. Nothing here reads the board from the UI thread, and the
UI-thread board handle is never carried in the payload — it is not needed.
"""
from __future__ import annotations

import logging

from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

__all__ = [
    "identified_footprints",
    "select_identified_refs",
    "run_select_cell_worker",
]


def identified_footprints(adapter, refs) -> tuple:
    """(footprints, stale_reasons) for the identified refs of ONE instance.

    The refs are an INTERFACE CACHE (gui_state.json), so they are checked on the
    worker against the board as it is NOW: a ref that is gone, or whose Role is
    no longer the one the map claims, makes the identification STALE. Pure data
    in / data out (no widget)."""
    stale: list = []
    footprints: list = []
    for role in sorted(refs):
        ref = refs[role]
        fp = adapter.get_footprint(ref)
        if fp is None:
            stale.append(_("{ref} is no longer on the board").format(ref=ref))
            continue
        live_role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if live_role != role:
            stale.append(_("{ref} now has Role {role!r}").format(
                ref=ref, role=live_role))
            continue
        footprints.append(fp)
    return footprints, stale


def select_identified_refs(adapter, refs: dict) -> dict:
    """Select EXACTLY the identified refs of ONE instance on the live board.

    A stale map selects NOTHING and the reasons travel back — never a fallback
    to the whole cluster (on the spoke FPGA_PWR_BANK that would hand the user 50
    components he did not ask for)."""
    footprints, stale = identified_footprints(adapter, refs)
    if stale:
        return {"stale": stale}
    if footprints:
        adapter.select_items(footprints)
    return {"selected": len(footprints), "identified": True}


def run_select_cell_worker(payload: dict) -> dict:
    """start_long_op worker — dispatches on ``payload["with_copper"]``.

    ``with_copper`` False -> «Select cell components» (components only);
    True -> «Select cell» (components + recorded copper). Plain data out; the
    caller writes the Log line."""
    if payload.get("with_copper"):
        return _run_with_copper(payload)
    return _run_components(payload)


# ── «Select cell components» ────────────────────────────────────────────────

def _run_components(payload: dict) -> dict:
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.cell_instance import resolve_context_footprints
    from kicadstamp.config import load_config

    adapter = None
    try:
        adapter = create_board_adapter(timeout_ms=payload["timeout_ms"],
                                       config_path=payload.get("config_path"))
        adapter.refresh_board()
        refs = payload.get("refs") or {}
        if refs:
            return select_identified_refs(adapter, refs)
        cluster = payload.get("cluster")
        if not cluster:
            return {"selected": 0, "cluster": None, "sheet": payload.get("sheet")}
        cfg, ctx = load_config(payload["root_path"])
        footprints = resolve_context_footprints(
            adapter, adapter.get_footprints(), cluster, payload.get("sheet"),
            dict(getattr(ctx, "sheet_names", None) or {}))
        if footprints:
            adapter.select_items(list(footprints))
        return {"selected": len(footprints), "cluster": cluster,
                "sheet": payload.get("sheet")}
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}
    finally:
        if adapter is not None:
            adapter.close()


# ── «Select cell» (components + recorded copper) ────────────────────────────

def _run_with_copper(payload: dict) -> dict:
    from kicadstamp.config import load_config

    from .select_cell import instance_recording_name

    root = payload["root_path"]
    cfg, ctx = load_config(root)
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    record = instance_recording_name(
        cfg, payload["cell_name"], payload.get("cluster"), payload.get("sheet"))
    if record is None:
        return _run_with_copper_registry_only(payload, cfg, sheet_names)

    pipeline = None
    try:
        from kicadstamp.apply_pipeline import ApplyPipeline

        from .select_cell import (cell_has_recorded_copper,
                                  no_planned_copper_targets,
                                  select_cell_copper_targets)

        pipeline = ApplyPipeline(root, only=[record], dry_run=True,
                                 timeout_ms=payload["timeout_ms"])
        pipeline.run()
        vias, tracks = pipeline.plan_copper()
        refs = payload.get("refs") or {}
        if refs:
            footprints, stale = identified_footprints(pipeline.adapter, refs)
            if stale:
                return {"stale": stale}
        if (not vias and not tracks
                and cell_has_recorded_copper(
                    (getattr(cfg, "cells", {}) or {}).get(payload["cell_name"]))):
            # СЦ-4-2: the record carries copper, but the planner produced no
            # command for it (a refused tree, an unrealized record) — registry
            # only, with a line that says so instead of a lying "0".
            plan = no_planned_copper_targets(
                pipeline.adapter, cfg, root, payload["cell_name"],
                payload.get("cluster"), payload.get("sheet"), sheet_names,
                record, own_refs=refs or None)
        else:
            plan = select_cell_copper_targets(
                pipeline.adapter, cfg, root, payload["cell_name"],
                payload.get("cluster"), payload.get("sheet"), sheet_names,
                vias, tracks, own_refs=refs or None)
        items = list(plan.footprints) + list(plan.copper)
        if items:
            pipeline.adapter.select_items(items)
        return {"components": len(plan.footprints), "copper": len(plan.copper),
                "by_registry": plan.copper_by_registry,
                "by_geometry": plan.copper_by_geometry,
                "missing": plan.copper_missing, "planned": plan.copper_planned,
                "cluster": payload.get("cluster"), "sheet": payload.get("sheet"),
                "line": plan.line}
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}
    finally:
        if pipeline is not None:
            pipeline.close()


def _run_with_copper_registry_only(payload: dict, cfg, sheet_names) -> dict:
    """No record places the cell (e.g. only a chain spoke): registry-only.

    Geometry needs the redraw planner's commands for ONE record; a chain is not
    planned per instance here (that is «Разнос» Р4-2) — so the copper is the
    registry tier only, and the Log line says so."""
    from kicadstamp.adapter_factory import create_board_adapter

    from .select_cell import instance_placed_by_chain, select_cell_targets

    adapter = None
    try:
        adapter = create_board_adapter(timeout_ms=payload["timeout_ms"],
                                       config_path=payload.get("config_path"))
        adapter.refresh_board()
        refs = payload.get("refs") or {}
        plan = select_cell_targets(
            adapter, cfg, payload["root_path"], payload["cell_name"],
            payload.get("cluster"), payload.get("sheet"), sheet_names,
            own_refs=refs or None)
        items = list(plan.footprints) + list(plan.copper)
        if items:
            adapter.select_items(items)
        chain = instance_placed_by_chain(cfg, payload["cell_name"],
                                         payload.get("cluster"))
        line = plan.line
        if chain:
            line += " " + _("the cell is placed by a chain — geometry is not "
                            "searched (registry only)")
        return {"components": len(plan.footprints), "copper": len(plan.copper),
                "by_registry": plan.copper_by_registry, "by_geometry": 0,
                "missing": plan.missing_registry, "chain_only": chain,
                "cluster": payload.get("cluster"), "sheet": payload.get("sheet"),
                "line": line}
    except Exception as e:  # noqa: BLE001 — reported as a Log line, no modal
        return {"error": str(e)}
    finally:
        if adapter is not None:
            adapter.close()
