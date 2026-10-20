# gui/board_switch_watch.py
"""В of plan_2026_10_15_honest_live_read: the AUTOMATIC poll tick notices that the
board in KiCad was SWITCHED — at the price of ONE cheap request.

Why the tick used to be a no-op, and why that is no longer the whole truth: a
full refresh (`connection.refresh()`) drops the adapter's caches and rebuilds the
whole-board snapshot, which is exactly what an idle tick must not do every few
seconds. But an idle tick that does NOTHING also means the connection keeps
describing the board that was open when it connected: the badge names the old
board, the snapshot answers about the old board (fact Б of the same plan — a
miss that then reads as "this cluster is not on the board"), and every live read
is answered from a stale identity until the user presses Refresh.

Denis's correction to Ч0 (20.10): `adapter.get_board_filename()` reads the
descriptor captured at connect / the last `refresh_board()`, so comparing names
WITHOUT a request can never see a switch. Hence this tick: ONE
`GetOpenDocuments(DOCTYPE_PCB)` round trip, and a full refresh ONLY when the name
it answers differs from the one the connection holds. The decision and the two
IPC calls live on `BoardConnection.refresh_if_board_changed` — this module is the
tick's SHAPE (what the result dict means), nothing else: it never touches
`connection.board` itself, which is why the UI-thread door has nothing to say
about it.

MEASURED 20.10.2026 (3CH-AWG-TIA-v103, 336 footprints, KiCad 10.0.7,
`kicadstamp.diagnostics.probe_identity_tick_cost`):

    read_open_board_name          median 0.127 ms   max 0.489 ms   (n=200)
    one full refresh + the board read it forces      ~30 ms        (n=5)

i.e. the tick is ~230x cheaper than what it replaces AND it drops no cache — the
refresh itself is a desk change, the 30 ms is the board being re-read afterwards
(332 footprints x 2 field scans).

Everything here runs on the POLL WORKER thread (`MainWindow._poll` hands it to
`PollWorkerHandle.submit`, which also holds the shared socket for the call): no
widget is touched, and the result dict is the only thing that crosses back.

An automatic tick NEVER turns a refusal into a lost connection: kipy answers this
request with an ApiError while the editor has no PCB frame or is switching
documents (measured in probe_document_switch), and
`refresh_if_board_changed` reports that as "nothing to say this tick", not as
"KiCad is gone". The tick you are reading is a courtesy check; the user's Refresh
button and the fast selection tick still own the error path.
"""
from time import perf_counter


def identity_tick(connection, collect_names=None) -> dict:
    """WORKER thread: the cheap per-tick board check.

    Returns a result in the shape `MainWindow._finish_poll` already reads:

    * `{"identity_only": True, "error": None, "duration_s": ...}` — the ordinary
      answer: the board is the same one (or the editor cannot say), so NOTHING
      else happens. `_finish_poll` records the cost and returns; no badge touch,
      no snapshot push, no net-name collection.
    * `{"error": <text|None>, "board_changed": True, ...}` plus every key a normal
      tick carries (`board_name`, `net_names`, `copper_net_names`) — the LIVE name
      differs from the one the connection knows, so the board WAS re-read here,
      once, and the UI half runs its usual success path: the badge names the new
      board and the docks get the fresh snapshot.

    `collect_names` is the caller's own net-name collector (MainWindow's
    `_collect_net_names`): it is IPC, it belongs on this thread, and the docks
    read the lists out of the very same result dict."""
    start = perf_counter()
    changed, error = connection.refresh_if_board_changed()
    if not changed:
        return {"identity_only": True, "error": None,
                "duration_s": perf_counter() - start}
    result = {"error": error, "board_changed": True,
              "duration_s": perf_counter() - start}
    if not error and collect_names is not None:
        # Same reason the plain tick collects them here (Э1 of
        # plan_2026_09_13_ui_thread_net_reads): this thread already owns the
        # socket, so the net lists cost nothing extra — and WITHOUT them the
        # docks' combos would go empty on a switch.
        result.update(collect_names())
    return result
