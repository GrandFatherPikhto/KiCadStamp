#!.venv/bin/python
"""After Э1: is the leftover field-map cost in READING the map or in BUILDING it?

Input        — KiCad running with a board open (no config needed).
Expected     — median ms for a full-board sweep of get_field_value() in two
               states: maps already built (read cost) and maps dropped (build +
               read cost). Nothing is written.
Live KiCad   — Yes (reads the open board only).
Run          — python -m kicadstamp.diagnostics.probe_field_map_unit_cost

WHY this exists (Э2 of plan_2026_09_13_placement_field_scan_cost): Э1 replaced a
per-call scan of texts_and_fields with a map built once per footprint per cache
generation. Э3 (a role index) would remove CALLS, not map builds, so whether it
is worth doing depends entirely on which of the two the remaining milliseconds
sit in. This separates them instead of guessing.

Note on where a rebuild's cost actually goes: kipy's
Footprint.texts_and_fields rebuilds a Footprint(definition) wrapper whose
__init__ unwraps EVERY item of the footprint (board_types.py:1832-1841) and
additionally constructs Field wrappers for reference/value/datasheet/description.
That per-access cost is kipy's, not ours, and it is the reason a rebuild is not
free even though our own loop over the result is trivial.
"""
import argparse
import statistics
import sys
import time

from kicadstamp.adapter_factory import create_board_adapter

ROLE_FIELD = "Role"


def _sweep(adapter, footprints) -> list:
    return [adapter.get_field_value(fp, ROLE_FIELD) for fp in footprints]


def _median_ms(fn, iterations):
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iter", type=int, default=20)
    # 20 s ON PURPOSE (Э4, plan_2026_09_13_timeout_sweep) — deliberately NOT
    # DEFAULT_TIMEOUT_MS: the probe sweeps EVERY footprint of the board --iter
    # times over, so a per-call ceiling sized for the GUI would fake timeouts
    # into the medians this tool exists to report.
    ap.add_argument("--timeout-ms", type=int, default=20000)
    args = ap.parse_args(argv)

    # BARE: Role/Cluster does not enter this probe's answer at all (plan Т2а)
    adapter = create_board_adapter(timeout_ms=args.timeout_ms, use_store=False)
    adapter.refresh_board()
    footprints = adapter.get_footprints()
    n = len(footprints)
    if n == 0:
        print("no footprints on the open board — nothing to measure")
        return 1

    # State A: every map present. One priming sweep first, then measure reads.
    adapter._field_values_cache = None      # the same invalidation refresh_board() does
    _sweep(adapter, footprints)
    read_ms = _median_ms(lambda: _sweep(adapter, footprints), args.iter)

    # State B: no maps at all — the whole board has to be built again.
    def rebuild_and_sweep():
        adapter._field_values_cache = None
        _sweep(adapter, footprints)

    rebuild_ms = _median_ms(rebuild_and_sweep, args.iter)

    per_read_us = read_ms / n * 1000.0
    per_rebuild_us = (rebuild_ms - read_ms) / n * 1000.0

    print(f"board: {n} footprints, field read: {ROLE_FIELD!r}")
    print(f"read sweep   (maps warm)     {read_ms:>8.2f} ms   "
          f"{per_read_us:>7.2f} us per footprint")
    print(f"read sweep   (maps dropped)  {rebuild_ms:>8.2f} ms   "
          f"{per_rebuild_us:>7.2f} us per footprint built + read")
    print()
    print("a real placement run pays one full rebuild per refresh_board():")
    print(f"  20 generations on this board  ~{rebuild_ms * 20:>8.0f} ms")
    print(f"  one  generation               ~{rebuild_ms:>8.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
