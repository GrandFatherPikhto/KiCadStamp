# gui/docks/redraw_drift.py
"""The ONE journal every Redraw door writes before it writes to the board: «this
moves N parts by up to X mm» (plan_2026_11_16_reread_forest_from_board, commit 2).

WHY. On 16.11.2026 a Redraw of one module moved four cells by 3.08 mm and tore a
hand-routed board apart. The Redraw never said what it was about to do. Commit 1 made
the move READABLE («Reread positions from board…»); this module makes the move VISIBLE.

NO WINDOW (К2-Ф9, Denis 17.11: «таких предупреждений нигде, кроме как в логе быть не
должно. Они жутко тормозят работу!»). A Redraw never asks and never stops because of a
drift. The measurement stays; its result is ONE `logger.warning` line per run, and the
four doors call it and carry on — no branching, no dialog, no `QMessageBox` here at all.

WHAT IS MEASURED, and from what. Both halves already exist:

  * where a part stands — `trees_base_pose.child_live_pose`, the one absolute reader
    the offset read and the anchor read share;
  * where the redraw will put it — `layout_tree_from_anchor`, the SAME layout the
    curated redraw plans from (no second planner).

TWO REASONS TO NAME A PART, not one:

  * it MOVES more than `DRIFT_MM` (0.1 mm), or
  * it TURNS more than `DRIFT_DEG` (1°) on the spot — a two-pad part rotated 180° moves
    0 mm and still swaps its pads, which is exactly how the routing to it breaks
    (К2-Ф8, the eleven FPGA capacitors).

WHICH PARTS. The node index is the WHOLE FOREST (`cfg.trees`, recursively), never the
door's own `tree.nodes`: the content of an embedded tree does not live there, while the
layout of the owner DOES place it — walking the owner's own list made the guard blind to
module content, i.e. exactly to the 16.11 case (К2-Ф7). And a `module` marker in the set
is EXPANDED to its tree's whole content by `expand_modules`, the one helper all four
doors go through: a run that redraws a marker redraws that content (D2).

A part the layout does not place (a mount, a copper record) is not counted: this journal
names what will MOVE, and a guess is not a number.

WHY IT RUNS ON THE UI THREAD. Exactly the reason `TreesDock._confirm_first_run_redraw`
does: the number must be taken from the board as it is BEFORE the worker starts moving
it, and a modal dialog from a worker thread is not a thing anyway — now there is no
dialog, only a Log line, which is why the cost is the reads alone. They happen under
`ui_thread_board_read` (the door's sign) and only when the shared socket is free
(`socket_busy` → the measurement is skipped, the run proceeds: the same silent skip the
first-run copper heads-up makes).

CLI is untouched: `ApplyPipeline --dry-run` is its own preview, and this module is a GUI
journal, not a second planning phase.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from kicadstamp.i18n import _
from kicadstamp.tree_position import layout_tree_from_anchor, relative_rotation_deg
from kicadstamp.trees import Tree
from kicadstamp.utils.units import MM

from ..connection import ui_thread_board_read
from ..worker import socket_busy
from .trees_base_pose import child_live_pose

logger = logging.getLogger(__name__)

#: A move bigger than this is worth naming: one tenth of a millimetre is the order
#: where a part stops being «the same place» for the routing around it.
DRIFT_MM = 0.1

#: A turn bigger than this is worth naming even at ZERO distance: a two-pad part
#: rotated 180° keeps its centre and swaps its pads (К2-Ф8).
DRIFT_DEG = 1.0

#: How many parts the line names before it says «and N more».
NAMES_SHOWN = 5


@dataclass(frozen=True)
class DriftRow:
    """One part this run will move or turn, and by how much."""

    ref: str
    delta_mm: float
    turn_deg: Optional[float] = None


def _forest(cfg) -> dict:
    return {t.name: t for t in (getattr(cfg, "trees", None) or [])}


def _index(forest: dict) -> dict:
    """{ref: node} over EVERY tree of the forest, module content included — the door's
    own `tree.nodes` does not hold the content of a tree that tree embeds."""
    index: dict = {}
    for tree in forest.values():
        stack = list(tree.nodes or [])
        while stack:
            node = stack.pop()
            index.setdefault(node.ref, node)
            stack.extend(node.children or [])
    return index


def expand_modules(refs, forest: dict) -> set:
    """`refs` plus the WHOLE content of every tree a `module` marker in it places,
    recursively — the run of a marker redraws that content (D2), so it must be measured.

    A ref is a marker when it names a tree of the forest: a module node's ref IS the
    tree name (plan 2026-09-02 P4). Content refs that are no tree names pass through."""
    out: set = set(refs)
    stack = [ref for ref in refs if ref in forest]
    seen: set = set()
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = forest.get(name)
        if tree is None:
            continue
        nodes = list(tree.nodes or [])
        while nodes:
            node = nodes.pop()
            out.add(node.ref)
            nodes.extend(node.children or [])
            if node.ref in forest:
                stack.append(node.ref)
    return out


def measure_drift(adapter, cfg, tree: Tree, refs, sheet_names, *,
                  snapshot=None) -> list[DriftRow]:
    """The parts in `refs` (markers expanded) this redraw would move or turn more than
    the thresholds — the biggest move first. Reads the board; call it under the sign."""
    forest = _forest(cfg)
    wanted = expand_modules(refs, forest)
    try:
        predicted = layout_tree_from_anchor(tree, forest, adapter=adapter, cfg=cfg,
                                            sheet_names=sheet_names, snapshot=snapshot)
    except Exception as exc:              # noqa: BLE001 — a journal never blocks a run
        logger.warning(_("Move check skipped: the tree could not be laid out ({error})")
                       .format(error=exc))
        return []
    index = _index(forest)
    rows: list[DriftRow] = []
    for ref, pose in predicted.items():
        if ref not in wanted:
            continue
        node = index.get(ref)
        if node is None:
            continue
        try:
            live_pos, live_deg, _mirror = child_live_pose(
                cfg, adapter, sheet_names, ref, node.kind,
                component_address=node.anchor if node.kind == "component" else None,
                snapshot=snapshot)
        except Exception:                 # noqa: BLE001 — an unreadable part is not a number
            continue
        delta = math.hypot(live_pos.x - pose[0].x, live_pos.y - pose[0].y) / MM
        turn = (abs(relative_rotation_deg(live_deg, pose[1]))
                if live_deg is not None else None)
        if delta > DRIFT_MM or (turn is not None and turn > DRIFT_DEG):
            rows.append(DriftRow(ref, delta, turn))
    rows.sort(key=lambda row: (-row.delta_mm, -(row.turn_deg or 0.0)))
    return rows


def copper_records_in(cfg, refs) -> int:
    """How many copper records this run re-creates: the `net_trace` records in its own
    set (their geometry lives in the record, so the registry replaces it)."""
    forest = _forest(cfg)
    wanted = expand_modules(refs, forest)
    index = _index(forest)
    return len([ref for ref in wanted
                if getattr(index.get(ref), "kind", None) == "net_trace"])


def _named(row: DriftRow) -> str:
    if row.turn_deg is not None and row.turn_deg > DRIFT_DEG:
        return _("{ref} {deg:.0f}°").format(ref=row.ref, deg=row.turn_deg)
    return _("{ref} {mm:.3f} mm").format(ref=row.ref, mm=row.delta_mm)


def _line(rows: list) -> str:
    named = ", ".join(_named(row) for row in rows[:NAMES_SHOWN])
    if len(rows) > NAMES_SHOWN:
        named += _(" and {n} more").format(n=len(rows) - NAMES_SHOWN)
    return _("Redraw moves {count} part(s) by up to {max_mm:.3f} mm: {names}").format(
        count=len(rows), max_mm=rows[0].delta_mm, names=named)


def log_redraw_drift(dock, tree: Optional[Tree], refs) -> None:
    """Write what this Redraw will move or turn — ONE Log line, then the run goes on.

    Called by all four GUI Redraw doors (a node, a module, «Redraw selected», «Redraw
    whole tree») before `start_long_op`; nothing is returned, so no door can branch on
    it. None of the early exits writes anything: no tree, no refs, no adapter, a busy
    socket or no drift at all means silence."""
    if tree is None or not refs:
        return
    connection = getattr(getattr(dock, "_main_window", None), "connection", None)
    if socket_busy(connection):
        return
    with ui_thread_board_read(
            reason="measure the move a Redraw is about to make: one layout plus one "
                   "live pose per part in the run"):
        adapter = dock._live_adapter()
    if adapter is None:
        return
    cfg = dock._cfg
    sheet_names = dict(getattr(getattr(dock, "_ctx", None), "sheet_names", None) or {})
    rows = measure_drift(adapter, cfg, tree, refs, sheet_names)
    if rows:
        logger.warning(_line(rows))
    copper = copper_records_in(cfg, refs)
    if copper:
        logger.warning(_(
            "{n} copper record(s) of this run will be deleted and re-created — hand "
            "routing on them is not kept").format(n=copper))


__all__ = ["DRIFT_DEG", "DRIFT_MM", "DriftRow", "copper_records_in",
           "expand_modules", "log_redraw_drift", "measure_drift"]
