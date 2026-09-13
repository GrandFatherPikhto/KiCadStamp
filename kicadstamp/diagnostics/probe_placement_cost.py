#!.venv/bin/python
"""Where does one placement run actually spend its time?

Input        — the .sexp config you place with; KiCad running, that board open.
Expected     — a cost breakdown of apply's Phase 1 on stdout, for BOTH refresh
               strategies (see below). Nothing is written.
Live KiCad   — Yes (the board is read; no move is executed).
Run          — python -m kicadstamp.diagnostics.probe_placement_cost <config.sexp>

Denis (2026-09-13): placement grew slow as the project grew. Two shapes in the
code can explain it, and this tool separates them instead of arguing about them:

  1. apply_pipeline.py called refresh_board() once PER ITEM. That drops the
     footprint cache, so the next get_footprints() is a full-board IPC round
     trip plus kipy deserialisation of every footprint.
  2. adapter.get_field_value() was a linear scan over texts_and_fields, and
     clone_role_resolver.py filters ALL footprints by role/cluster once per
     resolution — O(resolutions x footprints) scans, entirely CPU-side.

Shape 2 is gone (2026-09-13, the per-generation field map — see
probe_field_map_unit_cost.py for its own numbers). Shape 1 is what the second
block below measures: since 2026-09-13 Phase 1 re-reads only the footprints the
PREVIOUS item moved (adapter.reread_footprints_by_id) instead of the whole board.
Both strategies are reproduced side by side on the same board and config, so the
comparison is like for like:

  A) LEGACY   — refresh_board() before every item but the first (apply's loop
                until 2026-09-13; the numbers this probe used to print).
  B) CURRENT  — the targeted re-read apply_pipeline runs today. It calls the
                REAL ApplyPipeline._moved_footprint_uuids, so the probe measures
                the production decision, not a re-implementation of it.

READ-ONLY. Phase 1 is reproduced faithfully minus its one writing step:
refresh_board()/reread_footprints_by_id() and planner.plan_item() only read;
execute_moves() is never called, so nothing reaches the board or the KiCad
project directory.

Caveat, stated up front: with no move executed the board never changes, so each
round re-reads the same positions (and the targeted re-read returns objects with
the same values the cache already had). The COST STRUCTURE (round trips, kipy
deserialisation, linear scans) is nevertheless exactly the one a real apply
pays, and that is all this measures. Both blocks pay the same caveat, which is
why they are comparable.

Note on the numbers: "inside adapter methods" is NOT the IPC bill. Several
adapter methods (get_field_value, a warm get_footprints) never touch the
socket. The socket line below is measured separately, from the profiler's own
pynng frames, so the two are never confused.
"""
import argparse
import cProfile
import io
import pstats
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from kicadstamp.apply_pipeline import ApplyPipeline

UNIT_ITERATIONS_DEFAULT = 7


class CallCounter:
    """Counts and times every adapter call by shadowing the bound methods on
    ONE instance (instance attributes win over class attributes). Local to this
    probe — production code is untouched, unlike a module-level monkey patch."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.n = defaultdict(int)
        self.ms = defaultdict(float)
        # Adapter methods call each other (get_footprint -> get_footprints), so
        # naive summing would count the same seconds twice and report more time
        # inside the adapter than the wall clock. Depth 0 = an outermost call;
        # only those are added to top_ms. Per-method rows still record every
        # call, nested ones included.
        self.top_ms = 0.0
        self.depth = 0
        self.enabled = False
        self._originals = {}
        for name in dir(type(adapter)):
            if name.startswith("_"):
                continue
            attr = getattr(adapter, name, None)
            if not callable(attr):
                continue
            self._originals[name] = attr
            setattr(adapter, name, self._wrap(name, attr))

    def _wrap(self, name, fn):
        def wrapper(*args, **kwargs):
            if not self.enabled:
                return fn(*args, **kwargs)
            t0 = time.perf_counter()
            self.depth += 1
            try:
                return fn(*args, **kwargs)
            finally:
                self.depth -= 1
                elapsed = (time.perf_counter() - t0) * 1000.0
                self.n[name] += 1
                self.ms[name] += elapsed
                if self.depth == 0:
                    self.top_ms += elapsed
        return wrapper


def _median_ms(fn, iterations, before=None):
    samples = []
    for _ in range(iterations):
        if before is not None:
            before()
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def _socket_seconds(profiler) -> float:
    """Time actually spent waiting on the IPC socket, taken from the profiler's
    own pynng frames rather than from any adapter-level guess. tottime, not
    cumtime: the wait itself, with no Python above it."""
    total = 0.0
    for (_file, _line, func), entry in pstats.Stats(profiler).stats.items():
        if "nng_recvmsg" in func or "nng_sendmsg" in func:
            total += entry[2]
    return total


def _kipy_call_count(profiler, func_name: str) -> int:
    """How many times a named kipy function ran during the profile — the honest
    count of round trips, straight from the profiler instead of inferred from
    the adapter's own call log (board.get_footprints -> kipy board.get_items is
    the full read; board.get_items_by_id is a targeted one)."""
    total = 0
    for (file, _line, func), entry in pstats.Stats(profiler).stats.items():
        if func == func_name and "kipy" in file:
            total += entry[0]
    return total


def _profile_rows(profiler, rows):
    buf = io.StringIO()
    pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(rows)
    return [line for line in buf.getvalue().splitlines() if line.strip()]


def _reproduce_phase1(pipeline, adapter, items, targeted: bool, profile_rows: int):
    """One pass over Phase 1's planning loop, with either refresh strategy.

    targeted=False replays the LEGACY loop (a refresh_board() before every item
    but the first); targeted=True replays the CURRENT one (a targeted re-read of
    the previous item's footprints, via the very helper apply_pipeline uses).

    No move is executed in either pass, so the board is identical at the start
    of both and the two blocks compare the refresh strategy and nothing else.
    """
    counter = CallCounter(adapter)
    adapter.refresh_board()
    pipeline.planner.begin_planning()

    profiler = cProfile.Profile()
    counter.enabled = True
    wall0 = time.perf_counter()
    profiler.enable()
    total_moves = 0
    pending_reread: list[str] = []
    full_refresh_needed = False
    try:
        for idx, item in enumerate(items):
            if targeted:
                if full_refresh_needed:
                    adapter.refresh_board()
                elif pending_reread:
                    adapter.reread_footprints_by_id(pending_reread)
                pending_reread, full_refresh_needed = [], False
            elif idx > 0:
                adapter.refresh_board()
            moves = pipeline.planner.plan_item(item)
            total_moves += len(moves)
            if targeted:
                pending_reread, full_refresh_needed = \
                    pipeline._moved_footprint_uuids(moves)
    finally:
        profiler.disable()
        counter.enabled = False
    wall = time.perf_counter() - wall0
    return {
        "wall": wall,
        "moves": total_moves,
        "socket": _socket_seconds(profiler),
        "in_adapter": counter.top_ms / 1000.0,
        "calls": counter,
        "full_reads": _kipy_call_count(profiler, "get_items"),
        "targeted_reads": _kipy_call_count(profiler, "get_items_by_id"),
        "profile": _profile_rows(profiler, profile_rows),
    }


def _print_phase1(title, result, n_items):
    wall = result["wall"]
    socket = result["socket"]
    print(title)
    print(f"  items planned              {n_items:>9}")
    print(f"  moves that would be made   {result['moves']:>9}")
    print(f"  full board reads (kipy board.get_items)        {result['full_reads']:>6}")
    print(f"  targeted reads (kipy board.get_items_by_id)    {result['targeted_reads']:>6}")
    print(f"  wall clock                 {wall:>9.2f} s")
    print(f"  waiting on the socket      {socket:>9.2f} s   {socket / wall * 100:>5.1f}%"
          "   <- the only part IPC owns")
    print(f"  everything else            {wall - socket:>9.2f} s   "
          f"{(wall - socket) / wall * 100:>5.1f}%   <- our Python + kipy decoding")
    print(f"    of which inside adapter  {result['in_adapter']:>9.2f} s   "
          "(adapter methods, socket time included)")
    print()
    print("  adapter calls during that run:")
    print(f"    {'method':<26}{'n':>7}{'total ms':>12}{'per call':>11}{'per item':>10}")
    print("    " + "-" * 64)
    counter = result["calls"]
    for name in sorted(counter.n, key=lambda k: -counter.ms[k]):
        n = counter.n[name]
        ms = counter.ms[name]
        print(f"    {name:<26}{n:>7}{ms:>12.1f}{ms / n:>11.2f}{n / n_items:>10.1f}")
    print("    rows overlap: a nested call is counted in its caller's row too;")
    print("    the adapter total above sums only outermost calls.")
    print()
    print(f"  top {len(result['profile'])} by cumulative time:")
    for line in result["profile"]:
        print("    " + line)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="path to the .sexp config to plan")
    ap.add_argument("--iter", type=int, default=UNIT_ITERATIONS_DEFAULT,
                    help="iterations for the unit-cost medians (default %(default)s)")
    ap.add_argument("--timeout-ms", type=int, default=20000)
    ap.add_argument("--profile-rows", type=int, default=18)
    args = ap.parse_args(argv)

    if not Path(args.config).exists():
        print(f"config not found: {args.config}")
        return 2

    print("Read-only probe: no move is executed, nothing is written.\n")

    # --- Bring the pipeline up to the point Phase 1 starts from -------------
    pipeline = ApplyPipeline(args.config, timeout_ms=args.timeout_ms,
                             no_selection=True)
    t0 = time.perf_counter()
    pipeline._load_config()
    pipeline._filter_config()
    t_config = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    pipeline._connect_adapter()
    t_connect = (time.perf_counter() - t0) * 1000.0

    adapter = pipeline.adapter

    t0 = time.perf_counter()
    pipeline._validate()
    t_validate = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    pipeline._resolve_order()
    t_order = (time.perf_counter() - t0) * 1000.0
    pipeline._create_planner()

    items = list(pipeline.items or [])
    footprints = adapter.get_footprints()
    n_fp, n_items = len(footprints), len(items)

    print(f"board:  {n_fp} footprints")
    print(f"config: {n_items} items in execution order")
    print()
    print("startup (once per apply):")
    print(f"  load + filter config       {t_config:>9.1f} ms")
    print(f"  connect + first refresh    {t_connect:>9.1f} ms")
    print(f"  validation                 {t_validate:>9.1f} ms")
    print(f"  resolve execution order    {t_order:>9.1f} ms")
    print()

    if n_items == 0:
        print("no items to place — nothing to measure")
        return 1

    # --- Unit costs ---------------------------------------------------------
    probe_ref = footprints[len(footprints) // 2].ref
    probe_uuid = footprints[0].uuid
    refresh_med = _median_ms(adapter.refresh_board, args.iter)
    cold_med = _median_ms(adapter.get_footprints, args.iter, before=adapter.refresh_board)
    warm_med = _median_ms(adapter.get_footprints, args.iter)
    lookup_med = _median_ms(lambda: adapter.get_footprint(probe_ref), args.iter)
    # The targeted re-read needs a WARM cache (with none it is a deliberate
    # no-op), so give it one first — otherwise this row would measure nothing.
    adapter.refresh_board()
    adapter.get_footprints()
    reread_med = _median_ms(lambda: adapter.reread_footprints_by_id([probe_uuid]), args.iter)

    print(f"unit costs (median of {args.iter}):")
    print(f"  refresh_board()            {refresh_med:>9.2f} ms")
    print(f"  get_footprints()  cold     {cold_med:>9.2f} ms   (cache dropped by refresh)")
    print(f"  get_footprints()  warm     {warm_med:>9.2f} ms   (cached, list copy only)")
    print(f"  get_footprint(ref)         {lookup_med:>9.2f} ms   (linear scan, ref={probe_ref!r})")
    print(f"  reread_footprints_by_id(1) {reread_med:>9.2f} ms   "
          "(ONE named footprint, cache warm)")
    print()

    # --- Phase 1, both refresh strategies, same board and config ------------
    legacy = _reproduce_phase1(pipeline, adapter, items, targeted=False,
                               profile_rows=args.profile_rows)
    current = _reproduce_phase1(pipeline, adapter, items, targeted=True,
                                profile_rows=args.profile_rows)

    print("phase 1 reproduced TWICE on the same board and config "
          "(planning only, moves NOT executed):\n")
    _print_phase1("A) LEGACY loop — refresh_board() before every item "
                  "(apply's loop until 2026-09-13):", legacy, n_items)
    print()
    _print_phase1("B) CURRENT loop — targeted re-read of the previous item's "
                  "footprints (apply_pipeline today):", current, n_items)
    print()
    wall_pct = (current["wall"] - legacy["wall"]) / legacy["wall"] * 100
    print("A -> B:")
    print(f"  wall clock                 {legacy['wall']:>9.2f} s -> "
          f"{current['wall']:>6.2f} s   {wall_pct:+.1f}%")
    print(f"  full board reads           {legacy['full_reads']:>9} -> "
          f"{current['full_reads']:>6}")
    print(f"  targeted reads             {legacy['targeted_reads']:>9} -> "
          f"{current['targeted_reads']:>6}")
    print(f"  waiting on the socket      {legacy['socket']:>9.2f} s -> "
          f"{current['socket']:>6.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
