# gui/read_extra_copper.py
"""Tools → Trees → Copper → «Read extra copper from selection»
(plan_2026_11_10_extra_copper_by_selection, п.3) — the GUI half of the third
copper owner.

ONE door, two menus: the Tools → Trees → Copper item and the tree-root context
menu item both land HERE with the tree the human clicked; Tools passes None and
the ACTIVE tab is used (`_guarded_tree`). Nothing else differs between them.

READ-ONLY on the board, a WRITE to the working set: the pieces of the selection
that the classifier leaves alone (dead ends, п.4) become `net_traces:` records
with `category: extra`, marked in the tree exactly as a re-read's fresh copper is
(`TreesDock._copper_reread_children` + `_stage_net_traces`), and `File > Save`
persists them. No dialog, no board write, nothing ever removed.

The SELECTION is read on the WORKER (`adapter.get_selected_items`), never on the
UI thread — the same rule `kept` by `ui_thread_board_read` and the re-read: every
board call belongs to the worker that owns the kipy REQ socket for the run.

Qt-light by design (the `gui/select_internode_copper.py` shape): the worker is
plain data in / plain data out, the outcome is LOG LINES, and the giants
(`gui/docks/trees_dock.py`, `gui/dock_hub.py`) keep one-line delegates only.
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _

from .connection import worker_timeout_ms

logger = logging.getLogger(__name__)

__all__ = [
    "finish_read_extra_copper",
    "read_extra_copper",
    "run_read_extra_copper_worker",
]


def run_read_extra_copper_worker(payload: dict) -> dict:
    """start_long_op worker entry point for «Read extra copper from selection»:
    read the selection and the board's pads, plan the extra records, and hand back
    both the plan and the config they were applied to.

    It builds its OWN adapter (a pynng REQ socket is single-owner) and closes it in
    a `finally` — the shape every board door in this project uses."""
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.extra_copper import plan_extra_copper
    from kicadstamp.internode_capture import apply_reread_plan
    from kicadstamp.reread_area import read_reread_area

    adapter = create_board_adapter(timeout_ms=worker_timeout_ms(payload),
                                   config_path=payload.get("config_path"))
    try:
        adapter.refresh_board()
        # п.3: the SELECTION is a board call, so it is read HERE — the AREA itself
        # (selection copper, whole-board pads) comes from its one owner.
        area = read_reread_area(adapter, adapter.get_selected_items() or [])
        plan = plan_extra_copper(
            adapter, payload["cfg"], payload["tree"],
            config_path=payload["config_path"],
            area_items=area.items, area_footprints=area.footprints,
            sheet_names=payload.get("sheet_names") or {},
            narrowed=area.narrowed)
        return {
            "plan": plan,
            "cfg": apply_reread_plan(payload["cfg"], plan),
            "tree": payload["tree"],
            "added": [c.identity for c in plan.added],
            "narrowed_to_selection": area.narrowed,
        }
    finally:
        adapter.close()


def finish_read_extra_copper(dock, result: dict) -> None:
    """UI thread (worker finished): adopt the fresh records, add the tree nodes for
    the NEW ones, stage both sections and report — in the Log, never a dialog.

    The stale mark is deliberately NOT touched: this door looks only at the records
    it writes (п.6), so it has nothing to say about a record whose copper is gone —
    that is the inter-node re-read's answer to give."""
    from kicadstamp.extra_copper import extra_report_lines
    from kicadstamp.trees import TreeNode

    tree = result["tree"]
    plan = result["plan"]
    if dock._cfg is not None:
        dock._cfg = result["cfg"]
    if result["added"]:
        target = dock._copper_reread_children(tree)
        for identity in result["added"]:
            target.append(TreeNode(ref=identity, kind="net_trace", xy=None,
                                   polar=None, rotation=0.0, name=None,
                                   group=None, children=[]))
    dock._stage_net_traces(plan.records)
    for line in extra_report_lines(tree.name, plan):
        logger.info(line)
    dock._show_status(_(
        "Extra copper: {added} added, {updated} updated, {unchanged} unchanged "
        "({area}).").format(
            added=len(plan.added), updated=len(plan.updated),
            unchanged=len(plan.unchanged),
            area=(_("selection") if result.get("narrowed_to_selection")
                  else _("whole board"))))
    dock._mark_dirty()
    dock._rebuild_tabs()


def read_extra_copper(dock, trigger=None, tree=None) -> None:
    """The ONE door of «Read extra copper from selection» (п.3).

    `tree` is the tree whose ROOT was right-clicked, or None for the active tab.
    A read-only template instance, a missing project and a missing board are
    refused before any worker starts, exactly like the re-read."""
    from .docks._common import ERROR_STYLE as _ERROR_STYLE, show_message
    from .select_internode_copper import _guarded_tree
    from .worker import start_long_op

    tree = _guarded_tree(dock, tree)
    if tree is None:
        return
    payload = {
        "cfg": dock._cfg,
        "tree": tree,
        "config_path": str(dock._root_path),
        "sheet_names": dict(getattr(dock._ctx, "sheet_names", None) or {}),
        "timeout_ms": worker_timeout_ms(dock._main_window.connection),
    }

    def _done(result):
        dock._active_op = None
        finish_read_extra_copper(dock, result)

    def _failed(message):
        dock._active_op = None
        show_message(_("Read extra copper failed: {error}").format(error=message),
                     _ERROR_STYLE, logger)

    widgets = [trigger] if trigger is not None else []
    dock._active_op = start_long_op(
        dock._main_window.connection, widgets, run_read_extra_copper_worker,
        _done, _failed, payload, busy_text=_("reading extra copper"))
