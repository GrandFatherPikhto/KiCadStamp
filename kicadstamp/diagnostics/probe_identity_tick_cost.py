# kicadstamp/diagnostics/probe_identity_tick_cost.py
"""probe_identity_tick_cost.py — what does the per-tick BOARD-IDENTITY request cost?

Input        — nothing but a live KiCad with a board open. READ ONLY: only
               `GetOpenDocuments` and the refresh's own `GetBoard` are sent; nothing is
               written, selected or saved.
Expected     — the price of `read_open_board_name` (kicadstamp/kicad/kipy_board_identity.py,
               ONE `GetOpenDocuments(DOCTYPE_PCB)` round trip) in ms per tick, plus one
               `refresh_board()` for SCALE — the number item В of
               plan_2026_10_15_honest_live_read exists to keep off the automatic tick.
Live KiCad   — YES, read only.
Run          — .venv/bin/python -m kicadstamp.diagnostics.probe_identity_tick_cost
               [--count N]   (default N = 200)

WHY (В of plan_2026_10_15_honest_live_read; Denis's correction to Ч0, 20.10): the stored
`Board.name` cannot see a board switched inside KiCad — only a REQUEST can. The automatic
poll tick used to be a deliberate no-op because a full refresh is expensive, so В is
allowed to remove the early return ONLY for a request whose price is known. This probe is
that measurement; the number it prints is the one quoted in `gui/board_switch_watch.py`.

`adapter._kicad` is reached on purpose: this file lives in kicadstamp/diagnostics/, the
documented exception to the door's rule about private handles, and the seam under test is
exactly the raw kipy call.
"""
import argparse
import statistics
import sys
from time import perf_counter

DEFAULT_TIMEOUT_MS = 5000


def _measure(label, fn, count):
    """(last value, median ms, min ms, max ms) over `count` timed calls."""
    fn()                                    # warm-up, never timed
    samples = []
    value = None
    for _ in range(count):
        start = perf_counter()
        value = fn()
        samples.append((perf_counter() - start) * 1000.0)
    samples.sort()
    print(f"{label}: n={count}  min={samples[0]:.3f} ms  "
          f"median={statistics.median(samples):.3f} ms  "
          f"mean={statistics.fmean(samples):.3f} ms  max={samples[-1]:.3f} ms")
    return value, statistics.median(samples)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    args = parser.parse_args(argv)

    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.kicad.kipy_board_identity import read_open_board_name

    adapter = create_board_adapter(timeout_ms=args.timeout_ms, use_store=False)
    try:
        # The connect load, as every adapter creation does it: before this call the
        # adapter has no board object and `get_board_filename()` answers None.
        adapter.refresh_board()
        name, tick_ms = _measure("read_open_board_name (the identity tick)",
                                 lambda: read_open_board_name(adapter._kicad),
                                 args.count)
        print(f"  the editor has open: {name!r}; the adapter knows "
              f"{adapter.get_board_filename()!r}")

        _board, refresh_ms = _measure("adapter.refresh_board (a full refresh, for scale)",
                                      adapter.refresh_board,
                                      max(5, args.count // 20))
        print(f"  footprints on it: {len(adapter.get_footprints())}")
        if tick_ms > 0:
            print(f"\nidentity tick : full refresh = 1 : {refresh_ms / tick_ms:.1f}")
        else:
            print("\nidentity tick measured below the timer's resolution")
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
