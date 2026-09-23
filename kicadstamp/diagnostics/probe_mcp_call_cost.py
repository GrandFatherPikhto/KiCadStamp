# kicadstamp/diagnostics/probe_mcp_call_cost.py
"""probe_mcp_call_cost.py — what ONE MCP tool call costs the board, counted at the kipy boundary.

Input        — nothing; needs a live KiCad with a board open. READ ONLY: nothing is
               written, nothing is selected, no raw-write tool is reached, no profile is
               read, no adapter of its own is built (it drives the REAL shipping seam).
Expected     — one row per tool per seam state: adapter calls (the column that used to
               lie, printed for contrast), board re-mints, real footprint reads, ms/call.
Live KiCad   — YES, read only.
Run          — .venv/bin/python -m kicadstamp.diagnostics.probe_mcp_call_cost [--calls 5]

WHY the kipy boundary and not the adapter (the Ш1 lesson, quoted in the seam's own
docstring): the adapter CACHES, so `KiCadBoardAdapter.get_footprints` answers a warm call
without touching the board at all. A counter there shows a busy picture while the board is
read once, or a quiet picture while it is read three hundred times. The columns are:

  * adapter — kicadstamp `KiCadBoardAdapter.get_footprints` CALLS, cache hits included.
              This is the counter that lied once before; it is printed so the contrast is
              visible rather than argued;
  * remint  — `kipy.KiCad.get_open_documents`: the round trip behind a board refresh, i.e.
              how many times the board HANDLE was re-minted (that is what the seam does on
              every call now);
  * fpread  — `kipy.board.Board.get_footprints`: a full read of the footprint list. This is
              the expensive one (measured class 166/186 ms on the 325-footprint board).

The probe runs the REAL MCP path in this process — `ConnectionManager.execute` into
`mcp_server.handlers` — so what is measured is what ships. `--calls N` sets how many calls
per tool are counted; one warm-up call per row is made OUTSIDE the counters, because the
first call in a process also connects and would otherwise be counted as steady state.

The `off` rows are the OTHER half of the price: `_seam_off()` monkeypatches
`mcp_server.connection.refresh_board_before_live_read` to a no-op, which reproduces the old
behaviour (`plan_2026_09_24_mcp_stale_board` §1) — the same board, the same tools, no
rebuild. Reading the two rows together is the point: the defect was not expensive, it was
FREE, which is why nothing noticed it.
"""
import argparse
import contextlib
import logging
import time

from kicadstamp.constants import DEFAULT_TIMEOUT_MS


class _KipyBoundaryCounter:
    """Counts the round trips of interest where they really happen.

    Three patches, installed for the duration of a `with` block and always removed: the
    kipy client's document query (behind `get_board`), kipy's own footprint read, and the
    project adapter's `get_footprints` (whose calls include cache hits — the misleading
    column, kept deliberately).
    """

    def __init__(self):
        self.adapter_calls = 0
        self.remints = 0
        self.footprint_reads = 0
        self._originals = []

    def __enter__(self):
        import kipy
        from kipy.board import Board

        from kicadstamp.kicad.adapter import KiCadBoardAdapter

        original_docs = kipy.KiCad.get_open_documents
        original_board_read = Board.get_footprints
        original_adapter_read = KiCadBoardAdapter.get_footprints
        counter = self

        def counting_docs(kicad_self, doc_type):
            counter.remints += 1
            return original_docs(kicad_self, doc_type)

        def counting_board_read(board_self, *args, **kwargs):
            counter.footprint_reads += 1
            return original_board_read(board_self, *args, **kwargs)

        def counting_adapter_read(adapter_self, *args, **kwargs):
            counter.adapter_calls += 1
            return original_adapter_read(adapter_self, *args, **kwargs)

        kipy.KiCad.get_open_documents = counting_docs
        Board.get_footprints = counting_board_read
        KiCadBoardAdapter.get_footprints = counting_adapter_read
        self._originals = [(kipy.KiCad, "get_open_documents", original_docs),
                           (Board, "get_footprints", original_board_read),
                           (KiCadBoardAdapter, "get_footprints", original_adapter_read)]
        return self

    def __exit__(self, *exc_info):
        for owner, name, original in self._originals:
            setattr(owner, name, original)
        self._originals = []
        return False


@contextlib.contextmanager
def _seam_off():
    """Switch the freshness seam off — the OLD behaviour, on the same board.

    The seam is looked up as a module global by `ConnectionManager.execute`, so replacing
    the global here is exactly the no-op the base commit performed by simply not calling
    anything."""
    from mcp_server import connection as mcp_connection

    original = mcp_connection.refresh_board_before_live_read
    mcp_connection.refresh_board_before_live_read = lambda adapter: False
    try:
        yield
    finally:
        mcp_connection.refresh_board_before_live_read = original


def _measure(manager, call, calls: int) -> dict:
    """Per-call cost of *call*, steady state, plus the COLD first call.

    The warm-up call is made outside the counters (it connects and fills caches),
    but it is TIMED separately and reported: the first read of a board in a
    process is a different class of cost from the steady state, and the seam's
    docstring quotes a 166/186 ms class measured once on a 325-footprint board —
    the two numbers have to be told apart, not averaged.
    """
    cold_started = time.perf_counter()
    manager.execute(call)  # warm-up: connects if needed, fills caches, not counted
    cold_ms = (time.perf_counter() - cold_started) * 1000.0
    counter = _KipyBoundaryCounter()
    with counter:
        started = time.perf_counter()
        for _ in range(calls):
            manager.execute(call)
        elapsed = time.perf_counter() - started
    return {
        "adapter": counter.adapter_calls / calls,
        "remint": counter.remints / calls,
        "fpread": counter.footprint_reads / calls,
        "ms": elapsed * 1000.0 / calls,
        "cold_ms": cold_ms,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calls", type=int, default=5,
                        help="measured calls per tool and per seam state (default 5)")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                        help="IPC timeout handed to the adapter factory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)

    from mcp_server import handlers
    from mcp_server.connection import ConnectionManager

    manager = ConnectionManager(timeout_ms=args.timeout_ms)
    try:
        identity = manager.execute(handlers.get_board_identity)
        print(f"board      : {identity['board_name']!r} (KiCad {identity['kicad_version']})")
        inventory = manager.execute(handlers.list_footprints)
        if not inventory:
            print("!! the open board reports no footprints — nothing to measure")
            return 2
        ref = inventory[0]["ref"]
        print(f"footprints : {len(inventory)} (per-footprint row uses ref {ref!r})")
        print(f"calls      : {args.calls} measured per row, one warm-up call before the "
              "counters")

        tools = [
            ("kicadstamp_get_footprint",
             lambda adapter: handlers.get_footprint(adapter, ref=ref)),
            ("kicadstamp_list_footprints", handlers.list_footprints),
            ("kicadstamp_get_items_by_uuid",
             lambda adapter: handlers.get_items_by_uuid(adapter, uuids=["no-such-uuid"])),
            ("kicadstamp_list_tracks", handlers.list_tracks),
            ("kicadstamp_list_vias", handlers.list_vias),
            ("kicadstamp_list_nets", handlers.list_nets),
        ]

        header = (f"\n{'tool':32} {'seam':>5} {'adapter':>8} {'remint':>7} "
                  f"{'fpread':>7} {'ms/call':>8} {'cold ms':>8}")
        print(header)
        print("-" * len(header))
        for name, call in tools:
            for seam, context in (("on", contextlib.nullcontext()), ("off", _seam_off())):
                with context:
                    row = _measure(manager, call, args.calls)
                print(f"{name:32} {seam:>5} {row['adapter']:>8.2f} {row['remint']:>7.2f} "
                      f"{row['fpread']:>7.2f} {row['ms']:>8.1f} {row['cold_ms']:>8.1f}")
        print("\nper-call figures are averages over the measured calls (the seam column says "
              "whether\nrefresh_board_before_live_read ran); cold ms is the FIRST call of the "
              "row, outside\nthe averages — the class the seam docstring quotes separately.")
        return 0
    finally:
        manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
