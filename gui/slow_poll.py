# gui/slow_poll.py
"""The SLOW poll tick: connect / refresh / the per-tick board identity check.

Extracted from `gui/main_window.py` by the "giants only shrink" rule (Д-2 of the
Ч1 acceptance): the bodies moved VERBATIM, the window keeps one-line delegates
under the same names, so every caller and every test seam still reaches them
through `MainWindow`.

Two things the tick can submit, decided in `submit`:

* DISCONNECTED (or a manual Refresh): the plain connect/refresh pass —
  `run_poll`, which also collects the net-name lists the docks consume.
* CONNECTED, automatic: ONE cheap board-identity request (В of
  plan_2026_10_15_honest_live_read, `gui/board_switch_watch.py`), and a full
  refresh only when KiCad reports a different board.

Everything in `run_poll` / `collect_net_names` runs on the poll WORKER thread
(the window submits it through `PollWorkerHandle`); `finish_poll` is the UI half.
"""
import logging
from time import perf_counter

from kicadstamp.i18n import _

from .board_nets import board_filename, board_net_names, copper_net_names
from .board_switch_watch import identity_tick
from .connection import LATENCY_KIND_SLOW

logger = logging.getLogger(__name__)


def submit(window, manual: bool) -> None:
    """Hand THIS tick to the poll worker (or drop it — the busy check is the
    caller's, at the top of `MainWindow._poll`).

    A CONNECTED automatic tick asks KiCad which board is open instead of doing
    real work (В of plan_2026_10_15_honest_live_read): one `GetOpenDocuments`
    round trip, ~0.127 ms measured against ~30 ms for a refresh plus the board
    read it forces, and a full refresh only when the name differs. The plain
    pass stays for a manual Refresh and for connecting."""
    if window.connection.is_connected and not manual:
        window._poll_worker.submit(
            window.connection, identity_and_names, (window,),
            window._finish_poll, window._on_poll_failed)
        return
    # The MODULE function, not the window's delegate: the door's census marks a
    # worker fn by the name it sees here, and `run_poll` is what calls the one
    # board-reading collector by name.
    window._poll_worker.submit(
        window.connection, run_poll, (window, manual),
        window._finish_poll, window._on_poll_failed)


def identity_and_names(window) -> dict:
    """WORKER thread: В's cheap tick, with the net names where they belong.

    The identity check itself is `board_switch_watch.identity_tick` (no board
    access of its own); the net names are collected HERE, by a DIRECT call, and
    only when the board really was re-read and that re-read succeeded — the same
    "only on success" rule `run_poll` follows. The direct call also keeps the ONE
    `connection.board` read in this module inside `collect_net_names`, which the
    UI-thread door then sees as a worker fn (called by name from two workers)."""
    result = identity_tick(window.connection)
    if result.get("board_changed") and not result.get("error"):
        result.update(collect_net_names(window))
    return result


def run_poll(window, manual: bool) -> dict:
    """Worker thread: connection IPC only — never touches a widget.

    The call is timed here (perf_counter, on the worker) and its duration
    returned in the result — the "fat board read" measurement of Э2. It is
    reported on BOTH outcomes (the error is a returned string, not an
    exception), so the Э4б timeout check (_log_timeout_hint) runs for a
    FAILED tick as well, not only for a successful one.

    What a failed tick does NOT do is feed the rolling latency window —
    deliberately, and by construction. On any error,
    BoardConnection.refresh() calls disconnect() (its documented behaviour:
    the next tick retries connect() from scratch), disconnect() calls
    reset_latency(), and _record_latency records only while
    `window.connection.is_connected` — already False by the time the UI
    thread sees the result. So the SLOW window holds successful refreshes
    and successful connects only; a failed call is timed for the
    recommendation but never seeds a window the drop just cleared (see
    _record_latency's own docstring).

    A SUCCESSFUL tick also collects the net-name lists the docks consume
    (collect_net_names, Э1 of plan_2026_09_13_ui_thread_net_reads) — on
    this thread, while the tick still owns the shared socket. They travel
    in the same result dict as everything else: the dict is the ONE thing
    that crosses the thread boundary, so nothing extra has to be shared."""
    start = perf_counter()
    if window.connection.is_connected:
        error = window.connection.refresh()
    else:
        error = window.connection.connect()
    result = {"error": error, "duration_s": perf_counter() - start}
    if not error:
        # Only on success: refresh()/connect() drop the connection on
        # failure, so there would be no board left to ask.
        result.update(collect_net_names(window))
    return result


def collect_net_names(window) -> dict:
    """Worker thread, successful tick only: the net-name lists
    DockHub.push_snapshot hands to the four net-consuming docks.

    Where they come from (Э1): thermal_via/tools want every named net and
    used to make three identical adapter.get_all_nets() calls; net_trace
    wants the nets that carry copper (tracks + vias) and used to make two.
    Both lists are collected once, here, through gui/board_nets.py.

    Why here and not in finish_poll: this is IPC, and finish_poll runs on
    the UI thread. Doing it there is what froze the window for ~0.1 s on
    every manual Refresh (measured live 2026-09-13), with a 5 s IPC timeout
    waiting at the far end of each call — the freeze the 2026-08-08 hang
    rule exists to prevent. This tick already owns the shared kipy REQ
    socket, so the reads cost the same and block nobody.

    An absent board is not an error: the docks treat an empty list exactly
    as they treated a None board before — the combo simply goes empty."""
    board = window.connection.board
    adapter = getattr(board, "adapter", None) if board is not None else None
    if adapter is None:
        return {"net_names": [], "copper_net_names": []}
    return {"net_names": board_net_names(adapter),
            "copper_net_names": copper_net_names(adapter),
            # WHICH board the badge names (zero-cost read, gui/board_nets.py)
            "board_name": board_filename(adapter)}


def finish_poll(window, result: dict) -> None:
    """UI thread: reflect the worker's result into widgets.

    An `identity_only` result (В) ends here: the cheap per-tick board check found
    nothing to say — the board is the same one. Its cost is its OWN number (it is
    not a refresh and must not feed that window): one DEBUG line, no widget."""
    if result.get("identity_only"):
        logger.debug("board-identity tick: %.1f ms",
                     1000.0 * (result.get("duration_s") or 0.0))
        return
    window._record_latency(LATENCY_KIND_SLOW, result.get("duration_s"))
    error = result["error"]
    if error:
        window._log_timeout_hint(result.get("duration_s"))
        # Red on the FIRST tick that lost KiCad, slow or fast (no hook — Д3).
        window.connection_badge.show_poll_result(error=error)
        window._dock_hub.clear_components()
    else:
        # connect()/refresh() already rebuilt BoardConnection.snapshot —
        # this is the ONE place that snapshot is consumed, so the docks
        # never call board.select() themselves (PlacerDock's
        # refresh_known_roles used to build a second full snapshot here;
        # the fast selection-watch tick used to build one every 400ms).
        snapshot = window.connection.snapshot
        # Green only after a SUCCESSFUL slow poll (Д5): the board's name and
        # this count, with T.2's qualifier (K.2 #4) in the badge's tooltip —
        # the count is the snapshot's size as of its LAST rebuild, not "now".
        window.connection_badge.show_poll_result(
            board_name=result.get("board_name"), count=len(snapshot))
        # The BOARD handle is deliberately not handed out any more — the
        # four net-consuming docks get the names the worker collected
        # (Э2). Passing the board is what let them read KiCad here, on the
        # UI thread, and what hid those reads from the door census.
        window._dock_hub.push_snapshot(
            snapshot,
            result.get("net_names") or [],
            result.get("copper_net_names") or [])
        window._dock_hub.push_fieldstool_snapshot(snapshot)
        # Overlay housekeeping (E.2.4, plan_2026_09_11_overlay_markers_
        # owner): on connect and on every manual refresh, make the overlay
        # key map agree with the live layer (drop dead keys, Log orphans).
        # Runs on a worker thread and never raises; a no-op without a board.
        window._dock_hub.reconcile_overlay(window.connection)

    # Phase 5.1 — the embedded fieldstool shares this connection and no
    # longer runs its own connect/refresh poll, so mirror the status we
    # just computed into its label instead of letting it go stale.
    window._dock_hub.push_fieldstool_connection_status(error)

    window.action_button.setText(
        _("Refresh") if window.connection.is_connected else _("Reconnect"))
