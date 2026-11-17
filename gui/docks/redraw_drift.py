# gui/docks/redraw_drift.py
"""The ONE guard every Redraw door asks before it writes: «this moves N parts by up
to X mm — go on?» (plan_2026_11_16_reread_forest_from_board, commit 2).

WHY. On 16.11.2026 a Redraw of one module moved four cells by 3.08 mm and tore a
hand-routed board apart. The Redraw never asks — it plans and writes. The cure that
made the plan readable fixed WHERE it moves things; this guard makes the MOVE itself
a decision the user sees first.

WHAT IS MEASURED, and from what. The number a Redraw door needs is «how far will this
part travel», and the project already has both halves of it:

  * where the part stands — `trees_base_pose.child_live_pose`, the one absolute reader
    the offset read and the anchor read share;
  * where the redraw will put it — `layout_tree_from_anchor`, the SAME layout the
    curated redraw plans from (no second planner, no second composition of the tree).

A part missing from the layout (a mount, a copper record, a node the layout cannot
place) is NOT counted: this guard is about parts that will be MOVED, and a guess is
not a number.

WHY IT RUNS ON THE UI THREAD, synchronously. Exactly the reason
`TreesDock._confirm_first_run_redraw` does: its callers branch on the answer BEFORE
`start_long_op` starts the worker, so the question cannot live inside the operation —
a modal dialog from a worker thread is not a thing. The reads happen under
`ui_thread_board_read` (the door's sign) and only when the shared socket is free
(`socket_busy` — a busy socket means the answer is "proceed", the same silent yes the
first-run heads-up gives).

WHAT THE DIALOG SAYS: how many parts move, the largest move and the first few refs,
and how many COPPER records will be re-created — a curated redraw re-creates the
copper of every `net_trace` node in its own set (`kicadstamp/registry.py`:
«position/parameters changed, deleting old item»), so that number is counted from the
set itself, never guessed.

CANCEL IS ZERO TRANSACTIONS: nothing has been started yet, so «Cancel» is one Log line
and the board is untouched — the whole point of asking BEFORE the worker.

CLI is deliberately untouched: `ApplyPipeline --dry-run` is its own preview, and this
module is a GUI doorbell, not a second planning phase.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import QMessageBox

from kicadstamp.i18n import _
from kicadstamp.tree_position import layout_tree_from_anchor
from kicadstamp.trees import Tree
from kicadstamp.utils.units import MM

from ..connection import ui_thread_board_read
from ..worker import socket_busy
from .trees_base_pose import child_live_pose

logger = logging.getLogger(__name__)

#: A move bigger than this is worth asking about. One tenth of a millimetre is the
#: order where a part stops being «the same place» for the routing around it; below
#: it a redraw is re-placing, above it the user is being moved on.
DRIFT_MM = 0.1

#: How many refs the dialog names before it says «and N more».
NAMES_SHOWN = 5


@dataclass(frozen=True)
class DriftRow:
    """One part this run will move, and by how much (mm, straight-line)."""

    ref: str
    delta_mm: float


def measure_drift(adapter, cfg, tree: Tree, refs: set, sheet_names, *,
                  snapshot=None) -> list[DriftRow]:
    """[(ref, mm)] of the parts in `refs` the redraw would move more than `DRIFT_MM`,
    biggest first. Reads the board; call it under the door's sign."""
    forest = {t.name: t for t in (getattr(cfg, "trees", None) or [])}
    try:
        predicted = layout_tree_from_anchor(tree, forest, adapter=adapter, cfg=cfg,
                                            sheet_names=sheet_names, snapshot=snapshot)
    except Exception as exc:              # noqa: BLE001 — the guard never blocks a run
        logger.warning(_("Move check skipped: the tree could not be laid out ({error})")
                       .format(error=exc))
        return []
    rows: list[DriftRow] = []
    for node in _walk(tree.nodes):
        if node.ref not in refs:
            continue
        pose = predicted.get(node.ref)
        if pose is None:
            continue                      # not placed by the layout: nothing to compare
        try:
            live_pos, _deg, _mirror = child_live_pose(
                cfg, adapter, sheet_names, node.ref, node.kind,
                component_address=node.anchor if node.kind == "component" else None,
                snapshot=snapshot)
        except Exception:                 # noqa: BLE001 — an unreadable part is not a number
            continue
        delta = math.hypot(live_pos.x - pose[0].x, live_pos.y - pose[0].y) / MM
        if delta > DRIFT_MM:
            rows.append(DriftRow(node.ref, delta))
    rows.sort(key=lambda row: -row.delta_mm)
    return rows


def _walk(nodes) -> list:
    found = []
    stack = list(nodes or [])
    while stack:
        node = stack.pop()
        found.append(node)
        stack.extend(node.children or [])
    return found


def copper_records_in(tree: Tree, refs: set) -> int:
    """How many copper records this run re-creates: the `net_trace` nodes of its own
    set (their record's geometry lives in the record, so the registry replaces it)."""
    return len([node for node in _walk(tree.nodes)
                if node.ref in refs and node.kind == "net_trace"])


def _ask(rows: list, copper: int) -> bool:
    """True = go on. One dialog, two buttons, the numbers first."""
    shown = ", ".join(row.ref for row in rows[:NAMES_SHOWN])
    if len(rows) > NAMES_SHOWN:
        shown += _(" and {n} more").format(n=len(rows) - NAMES_SHOWN)
    text = _(
        "This Redraw moves {count} part(s) by up to {max_mm:.3f} mm — more than "
        "{threshold} mm. {names}.").format(
            count=len(rows), max_mm=rows[0].delta_mm, threshold=DRIFT_MM, names=shown)
    if copper:
        text += "\n\n" + _(
            "{n} copper record(s) of this run will be deleted and re-created — hand "
            "routing on them is not kept.").format(n=copper)
    text += "\n\n" + _("Redraw anyway?")
    answer = QMessageBox.question(
        None, _("Redraw moves parts"), text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel)
    return answer == QMessageBox.StandardButton.Yes


def confirm_redraw_drift(dock, tree: Optional[Tree], refs, *,
                         trigger=None) -> bool:
    """THE ONE guard. True = the caller may start its worker; False = the user said no.

    Called by all four GUI Redraw doors (a node, «Redraw selected», «Redraw whole
    tree», a module) BEFORE `start_long_op`, so a refusal costs zero transactions."""
    if tree is None or not refs:
        return True
    connection = getattr(getattr(dock, "_main_window", None), "connection", None)
    if socket_busy(connection):
        # The ~400 ms selection tick owns the shared socket: the check cannot read,
        # and the run proceeds exactly as it did before this guard existed.
        return True
    with ui_thread_board_read(
            reason="measure the move a Redraw is about to make: one layout plus one "
                   "live pose per part in the run"):
        adapter = dock._live_adapter()
    if adapter is None:
        return True
    sheet_names = dict(getattr(getattr(dock, "_ctx", None), "sheet_names", None) or {})
    rows = measure_drift(adapter, dock._cfg, tree, set(refs), sheet_names)
    if not rows:
        return True
    if _ask(rows, copper_records_in(tree, set(refs))):
        logger.info(_("Redraw confirmed: {count} part(s) move by up to {max_mm:.3f} mm")
                    .format(count=len(rows), max_mm=rows[0].delta_mm))
        return True
    logger.info(_("Redraw cancelled: {count} part(s) would have moved by up to "
                  "{max_mm:.3f} mm — nothing was written").format(
                      count=len(rows), max_mm=rows[0].delta_mm))
    return False


__all__ = ["DRIFT_MM", "DriftRow", "confirm_redraw_drift", "copper_records_in",
           "measure_drift"]
