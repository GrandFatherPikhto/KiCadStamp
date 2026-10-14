# gui/board_nets.py
"""What the poll WORKER reads off the adapter for the refresh path: the two
net-name lists the docks are handed, and the board's filename for the status
bar's connection badge. Collected on the poll WORKER, never on the UI thread.

Why this module exists (plan_2026_09_13_ui_thread_net_reads Э1): four docks
(thermal_via, chain, tools, net_trace) used to be handed the live BOARD by
DockHub.push_snapshot and call its adapter themselves — on the UI thread,
inside MainWindow._finish_poll. Measured live 2026-09-13: 5 adapter calls,
0.1 s sitting on the UI thread per manual Refresh, with a 5 s IPC timeout
waiting at the far end of each one — the exact freeze plan_2026_09_08's S.1
rule (and the 2026-08-08 hang) exists to prevent. The names are now collected
once, on the worker thread that already owns the kipy REQ socket, and
distributed as plain lists (MainWindow._run_poll -> _collect_net_names ->
DockHub.push_snapshot -> each dock's refresh_known_nets).

These are pure adapter READS: no widgets, no Qt, and no Board object — the
same genre as gui/board_layers.py. Keeping them out of the docks is the point:
a dock that can reach the adapter is a dock that can read the board on the UI
thread again (the mutation Э4.4 of the plan guards).
"""
import logging

logger = logging.getLogger(__name__)


def board_net_names(adapter) -> list:
    """Every NAMED net of the live board (``adapter.get_all_nets()``), sorted
    and de-duplicated — the list the "which net is this?" combos want
    (ThermalViaDock, ToolsDock).

    Deliberately NOT the list NetTraceDock's picker gets: this one includes
    every net the board knows about, pads and all — see copper_net_names()."""
    return sorted({net.name for net in adapter.get_all_nets() if net.name})


def copper_net_names(adapter) -> list:
    """The nets that actually carry copper — the union of the net names of the
    board's TRACKS and VIAS, sorted and de-duplicated.

    This is the list NetTraceDock's Net picker needs, and it must stay this
    one: the dock captures copper, so a net that exists only on pads would be a
    choice that cannot be captured. ``adapter.get_all_nets()`` includes exactly
    those pad-only nets; swapping it in looks like a harmless simplification
    and is a bug (the dock's own docstring says so, and Э4.6 of the plan is the
    guard). Same collection pattern as kicadstamp/cloner/extract.py's origin
    choices, minus the selection restriction."""
    nets = {track.net_name for track in adapter.get_tracks() if track.net_name}
    nets |= {via.net_name for via in adapter.get_vias() if via.net_name}
    return sorted(nets)


def board_filename(adapter) -> str | None:
    """The live board's filename as KiCad reports it ('3CH-AWG-TIA-v103.
    kicad_pcb'), or None when there is no adapter, no board, or nothing to say.

    The status bar's connection badge shows WHICH board the last reading was
    about (2026-10-14, plan_2026_10_14_connection_badge), so this is collected on
    the same worker pass as the two lists above — same reasoning, same thread,
    same socket. AND THE PRICE IS ZERO: `get_board_filename` returns the STORED
    DocumentSpecifier's filename (kipy Board.name), never a fresh request —
    measured by kicadstamp/diagnostics/probe_document_switch.py:24-26.

    The ONE difference from the two readers above, and it is deliberate: this one
    NEVER RAISES. Those two are part of the poll's own contract, so their failure
    must surface; a name is a nicety, and an adapter that cannot answer (a test
    stand-in with no such method, or a plain adapter before its first
    refresh_board, where the getter answers None) must not fail the poll it rides
    on. The badge then shows the count without a name (C7 of that plan) and the
    Log keeps the reason."""
    name_of_board = getattr(adapter, "get_board_filename", None)
    if adapter is None or name_of_board is None:
        return None
    try:
        return name_of_board()
    except Exception:  # noqa: BLE001 — see the docstring: a nicety, not a read
        logger.debug("Board filename read failed", exc_info=True)
        return None
