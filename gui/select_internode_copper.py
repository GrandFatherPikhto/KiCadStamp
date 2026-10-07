# gui/select_internode_copper.py
"""Tools → Trees → Copper → "Select inter-node copper" (В1) and "Select recorded
inter-node copper" (В2) — the GUI half of Т5 of
plan_2026_10_05_tree_reread_modules (Denis, 2026-10-07).

Both are READ-ONLY selection actions on the CURRENT tree: they highlight on the
board, in ONE ``select_items`` call, copper the configuration already describes.
No config, no registry, no board write.

  * В1 "Select inter-node copper" highlights what a WHOLE-BOARD re-read would
    take: the SAME classifier both paths run (``internode_capture.internode_units``,
    the ONE place the classification lives — T5-1). The invariant "selected ==
    what a whole-board re-read takes" is guarded by a test.
  * В2 "Select recorded inter-node copper" highlights the live copper of every
    ``net_traces`` record the tree's ``kind "net_trace"`` nodes reference, each
    through the SAME ``copper_select.record_live_items`` the node's own "Select
    copper on board" uses — never a second path.

Qt-light by design (the ``gui/select_enclosed_copper.py`` shape): the workers are
plain data in / plain data out, the outcomes are LOG LINES (never a dialog —
deepseek.md §43), and the giants (``gui/docks/trees_dock.py``, ``gui/dock_hub.py``,
``gui/main_window.py``) keep one-line delegates only (deepseek.md §45).

Every live read/write runs on the WORKER (``gui.worker.start_long_op``, the
shared kipy socket's only in-flight owner): each worker builds its OWN adapter and
hands that socket back in a ``finally`` — the ``gui/docks/copper_select.py``
shape, on both paths. Nothing here reads the board from the UI thread.
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _

from .connection import worker_timeout_ms

logger = logging.getLogger(__name__)

__all__ = [
    "report_select_inter_node_copper",
    "report_select_recorded_inter_node_copper",
    "run_select_inter_node_copper_worker",
    "run_select_recorded_inter_node_copper_worker",
    "select_inter_node_copper",
    "select_recorded_inter_node_copper",
]


def _node_key_labels(tree, cfg) -> list[str]:
    """The tree's own node keys, "label/sheet" — the same wording the re-read
    report uses for "the tree's nodes expect: …". Only needed when nothing was
    taken, so it names WHAT the tree was waiting for."""
    from kicadstamp.internode_nodes import tree_node_keys

    return [f"{label}/{sheet}" if sheet else label
            for (_cluster, sheet), label in tree_node_keys(tree, cfg).items()]


# ── В1: "Select inter-node copper" ─────────────────────────────────────────

def run_select_inter_node_copper_worker(payload: dict) -> dict:
    """start_long_op worker entry point for "Select inter-node copper" (В1):
    read the WHOLE board, classify the CURRENT tree's inter-node copper with the
    SAME ``internode_units`` the re-read uses, and select it — plain data out.

    It builds its OWN adapter (``timeout_ms`` from the payload, ``config_path``
    the profile so the adapter sees OUR Role/Cluster overrides) and closes it in
    the ``finally``. The area is ALWAYS the whole board, whatever is selected on
    screen: "what a whole-board re-read would take" is the contract the В1/В2
    pairing rests on (plan Т5-1)."""
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.domain.board import Track, Via
    from kicadstamp.internode_capture import internode_units

    adapter = None
    try:
        adapter = create_board_adapter(timeout_ms=worker_timeout_ms(payload),
                                       config_path=payload.get("config_path"))
        adapter.refresh_board()
        footprints = list(adapter.get_footprints())
        items = list(adapter.get_tracks()) + list(adapter.get_vias())
        units, discarded, warnings = internode_units(
            adapter, payload["cfg"], payload["tree"],
            area_items=items, area_footprints=footprints,
            sheet_names=payload.get("sheet_names") or {})
        selected = [piece for unit in units for piece in (*unit.tracks, *unit.vias)]
        adapter.select_items(selected)
        return {
            "tree": payload["tree"].name,
            "pieces": len(units),
            "tracks": sum(1 for p in selected if isinstance(p, Track)),
            "vias": sum(1 for p in selected if isinstance(p, Via)),
            "discarded": {k: v for k, v in discarded.items() if v},
            "warnings": list(warnings),
            # Named only when nothing was taken, so the Log can say what the
            # tree's nodes wait for (never computed for a non-empty result).
            "node_keys": ([] if units
                          else _node_key_labels(payload["tree"], payload["cfg"])),
        }
    finally:
        if adapter is not None:
            adapter.close()


def report_select_inter_node_copper(result: dict) -> None:
    """UI thread (worker finished): the outcome as LOG LINES — never a dialog.
    A zero-piece result is a YELLOW line naming what the tree's nodes expect."""
    from kicadstamp.internode_capture import _DISCARD_ORDER

    tree = result.get("tree")
    pieces = int(result.get("pieces") or 0)
    if not pieces:
        logger.warning(_(
            "Select inter-node copper: no inter-node copper of tree {tree!r} is "
            "on the whole board — nothing was selected. The tree's nodes expect: "
            "{keys}").format(tree=tree,
                             keys=", ".join(result.get("node_keys") or []) or "-"))
        return
    logger.info(_(
        "Selected {pieces} inter-node piece(s) of tree {tree!r} — {tracks} "
        "track(s), {vias} via(s)").format(
            pieces=pieces, tree=tree, tracks=result.get("tracks", 0),
            vias=result.get("vias", 0)))
    discarded = result.get("discarded") or {}
    counts = ", ".join(
        "{verdict} {count}".format(verdict=verdict.value, count=discarded[verdict.value])
        for verdict in _DISCARD_ORDER if discarded.get(verdict.value))
    if counts:
        logger.info(_("  not taken:  {counts} — copper the classification left "
                      "alone").format(counts=counts))
    for warning in result.get("warnings") or []:
        logger.info("  " + warning)


# ── В2: "Select recorded inter-node copper" ────────────────────────────────

def run_select_recorded_inter_node_copper_worker(payload: dict) -> dict:
    """start_long_op worker entry point for "Select recorded inter-node copper"
    (В2): read the board, find the live copper of EVERY ``net_traces`` record the
    tree's ``kind "net_trace"`` nodes reference (through the SAME
    ``record_live_items`` the node action uses) and select it in ONE call.

    Own adapter, closed in the ``finally``; the registries are opened READ-ONLY
    (``peek_registry_paths``, the same explicit-vs-default decision apply uses)."""
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.config import net_trace_effective_name
    from kicadstamp.domain.board import Track, Via
    from kicadstamp.internode_capture import tree_net_trace_identities

    from .docks.copper_select import _readonly_registries, record_live_items

    adapter = None
    try:
        adapter = create_board_adapter(timeout_ms=worker_timeout_ms(payload),
                                       config_path=payload.get("config_path"))
        adapter.refresh_board()
        via_registry, track_registry = _readonly_registries(
            adapter, payload["config_path"])
        cfg = payload["cfg"]
        identities = set(tree_net_trace_identities(payload["tree"]))
        records = [nt for nt in cfg.net_traces
                   if net_trace_effective_name(nt) in identities]
        sheet_names = payload.get("sheet_names") or {}
        selected: list = []
        seen: set[str] = set()
        selected_records = 0
        missing: list[str] = []
        for nt in records:
            live = record_live_items(adapter, nt, via_registry=via_registry,
                                     track_registry=track_registry,
                                     sheet_names=sheet_names)
            found = live.found
            if not found:
                missing.append(live.identity)
                continue
            selected_records += 1
            for item in found:
                if item.uuid not in seen:
                    seen.add(item.uuid)
                    selected.append(item)
        adapter.select_items(selected)
        return {
            "tree": payload["tree"].name,
            "records": len(records),
            "selected_records": selected_records,
            "tracks": sum(1 for p in selected if isinstance(p, Track)),
            "vias": sum(1 for p in selected if isinstance(p, Via)),
            "missing": missing,
        }
    finally:
        if adapter is not None:
            adapter.close()


def report_select_recorded_inter_node_copper(result: dict) -> None:
    """UI thread: the outcome as LOG LINES — never a dialog. Records whose copper
    is not on the board are named (the "recorded but not on the board" answer)."""
    tree = result.get("tree")
    logger.info(_(
        "Selected recorded copper of {k} record(s) of tree {tree!r} — {tracks} "
        "track(s), {vias} via(s)").format(
            k=result.get("selected_records", 0), tree=tree,
            tracks=result.get("tracks", 0), vias=result.get("vias", 0)))
    missing = result.get("missing") or []
    if missing:
        logger.info(_(
            "  {count} record(s) of tree {tree!r} have no copper on the board: "
            "{names}").format(count=len(missing), tree=tree,
                              names=", ".join(missing)))


# ── the doors (a giant keeps one-line delegates — deepseek.md §45) ─────────

def _guarded_tree(dock):
    """The CURRENT tree for a Tools → Trees → Copper → Select… action, or None
    with the reason already reported (no tree / a read-only instance / no project
    / no board) — the guard both select actions share.

    The dock is duck-typed (``_current_tree``, ``_warn_read_only_instance``,
    ``_cfg``, ``_root_path``, ``_ctx``, ``_main_window``): the wiring lives HERE
    so the >800-line giant keeps no more than the two one-line delegates below."""
    tree = dock._current_tree()
    if tree is None or dock._warn_read_only_instance(tree):
        return None
    if dock._cfg is None or dock._root_path is None:
        logger.error(_("No project loaded — open a root config first."))
        return None
    if dock._main_window.connection is None:
        logger.error(_("No live board connection — connect KiCad first."))
        return None
    return tree


def _run_select_copper(dock, trigger, worker, report, busy_text, tree) -> None:
    """The ONE wiring both select doors share: gather the worker's payload, run
    it through ``start_long_op`` (the menu QAction is the guard widget) and log
    the outcome. A selection writes nothing, so the "clusters are exploded" gate
    is opened for these ops alone, exactly like "Select cell"."""
    from .worker import start_long_op

    payload = {
        "cfg": dock._cfg, "tree": tree,
        "config_path": str(dock._root_path),
        "sheet_names": dict(getattr(dock._ctx, "sheet_names", None) or {}),
        "timeout_ms": worker_timeout_ms(dock._main_window.connection),
    }

    def _done(result):
        dock._active_op = None
        report(result)

    def _failed(message):
        dock._active_op = None
        logger.error(message)

    widgets = [trigger] if trigger is not None else []
    dock._active_op = start_long_op(
        dock._main_window.connection, widgets, worker, _done, _failed,
        payload, busy_text=busy_text, allowed_while_exploded=True)


def select_inter_node_copper(dock, trigger=None) -> None:
    """The ONE door of «Select inter-node copper» (В1) for the Tools → Trees →
    Copper menu item. Refuses a read-only template instance and a missing project
    / board before any worker starts, exactly like the re-read."""
    tree = _guarded_tree(dock)
    if tree is None:
        return
    _run_select_copper(dock, trigger, run_select_inter_node_copper_worker,
                       report_select_inter_node_copper,
                       _("selecting inter-node copper"), tree)


def select_recorded_inter_node_copper(dock, trigger=None) -> None:
    """The ONE door of «Select recorded inter-node copper» (В2), same guards and
    the same worker shape."""
    tree = _guarded_tree(dock)
    if tree is None:
        return
    _run_select_copper(dock, trigger,
                       run_select_recorded_inter_node_copper_worker,
                       report_select_recorded_inter_node_copper,
                       _("selecting recorded copper"), tree)
