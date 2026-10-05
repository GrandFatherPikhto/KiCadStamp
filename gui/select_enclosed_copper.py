# gui/select_enclosed_copper.py
"""«Select enclosed copper» — the GUI half (plan
``plan_2026_10_05_select_enclosed_copper.md``; Denis 2026-10-05).

The context-menu item highlights on the board the chosen cell instance WHOLE —
its components AND the copper enclosed by them (``kicadstamp/enclosed_copper.py``,
the core rule) — in ONE ``select_items`` call, ready for "Re-read by selection".

The instance is chosen by the SAME resolver "Select cell" / "Explode…" use
(``gui/select_cell.resolve_action_instance``) — there is no third selection rule.

Qt-light by design: this module holds the worker (plain data in, plain data out),
the result handler (a LOG LINE — never a dialog, deepseek.md §43) and the door
orchestrator. The giants (``gui/dock_hub.py``, ``gui/docks/config_tree.py``) keep
one-line delegates only (deepseek.md §45).

Everything that touches the live board runs on the WORKER (``gui.worker``
``start_long_op``, the shared kipy socket's only in-flight owner): the worker
builds its OWN adapter (a new adapter IS a new pynng REQ socket) and hands that
socket back in a ``finally`` — the ``gui/docks/copper_select.py`` shape, on both
paths. Nothing here reads the board from the UI thread.
"""
from __future__ import annotations

import logging

from kicadstamp.i18n import _

from .connection import worker_timeout_ms

logger = logging.getLogger(__name__)

__all__ = [
    "run_select_enclosed_copper_worker",
    "report_select_enclosed_copper",
    "select_enclosed_copper",
]


def run_select_enclosed_copper_worker(payload: dict) -> dict:
    """start_long_op worker: read the board, classify, and select — plain data.

    Builds its OWN adapter (``timeout_ms`` from the payload, ``config_path`` the
    profile so the adapter sees OUR Role/Cluster overrides) and closes it in the
    ``finally``. An instance with no live component is an honest ``{"empty": True}``
    (the caller writes the red Log line), never an exception."""
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.cell_instance import resolve_context_footprints
    from kicadstamp.config import load_config
    from kicadstamp.enclosed_copper import enclosed_copper

    adapter = None
    try:
        adapter = create_board_adapter(timeout_ms=worker_timeout_ms(payload),
                                       config_path=payload.get("config_path"))
        adapter.refresh_board()
        cfg, ctx = load_config(payload["root_path"])
        instance = resolve_context_footprints(
            adapter, adapter.get_footprints(), payload["cluster"],
            payload.get("sheet"), dict(getattr(ctx, "sheet_names", None) or {}))
        if not instance:
            return {"empty": True, "cell": payload["cell_name"],
                    "cluster": payload["cluster"], "sheet": payload.get("sheet")}
        result = enclosed_copper(adapter, instance)
        adapter.select_items(list(result.items))
        return {
            "cell": payload["cell_name"],
            "cluster": payload["cluster"], "sheet": payload.get("sheet"),
            "components": len(instance),
            "pieces": result.pieces,
            "foreign": result.not_taken_foreign,
            "dangling": result.not_taken_dangling,
            "pruned": result.pruned,
        }
    except Exception as e:  # noqa: BLE001 — reported as a red Log line, no modal
        return {"error": str(e)}
    finally:
        if adapter is not None:
            adapter.close()


def _where(result: dict) -> str:
    sheet = result.get("sheet")
    return _("{cluster} on {sheet}").format(
        cluster=result.get("cluster"),
        sheet=sheet if sheet is not None else _("(no sheet)"))


def report_select_enclosed_copper(result: dict) -> None:
    """UI thread (worker finished): the outcome as a LOG LINE — never a dialog.

    Success is one green line with the honest counters; an empty instance / a
    context that no longer resolves is a RED line (logger.error), and the board
    is left exactly as it was (``select_items`` was never called)."""
    if result.get("error"):
        logger.error(_("Select enclosed copper failed: {error}").format(
            error=result["error"]))
        return
    where = _where(result)
    if result.get("empty"):
        logger.error(_(
            "No component of cell {cell!r} is on the board for {where} — "
            "nothing was selected.").format(cell=result.get("cell"), where=where))
        return
    logger.info(_(
        "Selected {cell} on {where}: {components} component(s), {pieces} copper "
        "piece(s); not taken: {foreign} reaching a foreign pad, {dangling} "
        "dangling (one pad); trimmed {pruned} hanging element(s)").format(
            cell=result.get("cell"), where=where,
            components=result.get("components", 0),
            pieces=result.get("pieces", 0),
            foreign=result.get("foreign", 0),
            dangling=result.get("dangling", 0),
            pruned=result.get("pruned", 0)))


def _on_error(message: str) -> None:
    logger.error(_("Select enclosed copper failed: {error}").format(error=message))


def select_enclosed_copper(hub, name, file_path=None, cluster=None, sheet=None) -> None:
    """The ONE door of «Select enclosed copper», for the Config-tree menu item.

    Resolves the instance EXACTLY like "Select cell" / "Explode…"
    (``resolve_action_instance``: explicit address / remembered / single record /
    a shared submenu / "no-record"), then runs the worker through
    ``start_long_op`` with ``allowed_while_exploded=True`` — the selection writes
    nothing to the board, so the "clusters are exploded" gate is opened for this
    op alone, exactly like "Select cell"."""
    if not name:
        return
    root = hub.root_metadata_dock.root_path
    if root is None:
        logger.error(_("Set the project root first."))
        return
    connection = hub.main_window.connection
    if not getattr(connection, "is_connected", False):
        logger.error(_("Connect to KiCad first."))
        return
    if cluster is None:
        from .explode_wiring import load_cfg
        from .select_cell import pick_instance, resolve_action_instance
        choice = resolve_action_instance(load_cfg(root), root, name, None, None, None)
        if choice.kind in ("explicit", "remembered", "single"):
            cluster, sheet = choice.cluster, choice.sheet
        elif choice.kind == "choose":
            pick_instance(
                hub.main_window, choice.candidates,
                lambda c, s: select_enclosed_copper(hub, name, file_path, c, s))
            return
        else:                                   # "none" / "no-record"
            if choice.message:
                logger.error(choice.message)
            return
    if not cluster:
        logger.error(_(
            "No remembered cluster for cell {name!r} — extract it from a board "
            "cluster, or remember one via the cell-anchor page’s “Fill from "
            "selection”.").format(name=name))
        return
    from .worker import start_long_op
    payload = {
        "root_path": str(root),
        "config_path": str(root),
        "timeout_ms": worker_timeout_ms(connection),
        "cell_name": name,
        "cluster": cluster,
        "sheet": sheet,
    }
    start_long_op(connection, (), run_select_enclosed_copper_worker,
                  report_select_enclosed_copper, _on_error, payload,
                  busy_text=_("selecting enclosed copper"),
                  allowed_while_exploded=True)
