"""Probe: which of the four role/cluster caches must be forgotten TOGETHER —
§5.3а of plan_2026_09_24_reload_store_snapshot.md.

Run it by hand from the repository root (it is a measurement, not a test and not a
mutation):

    .venv/bin/python -m kicadstamp.diagnostics.probe_forget_pairs

WHY IT EXISTS. §3.1 of the plan says the new method forgets FOUR caches, and the
reason is not "two are not enough" — dropping only the effective pair is
behaviourally sufficient. The reason is that the four are TWO PAIRS, and the
WRONG pair (the physical one, alone) does not degrade: it raises. `_board_role()`
calls `_role()` first, and with `_role_cache` still warm that returns at once,
leaving the physical lookup to index a key nobody will refill — a KeyError in the
middle of a snapshot. The decision "forget all four" was bought with this probe,
so the probe is part of the record, not a curiosity.

WHAT IT MEASURES, and on what. The production composition — a real
kicadstamp.explore.Board over a real FieldOverrideAdapter over the two-layer
stand-in of tests/override_store_board_fixtures.py — with a WARM snapshot (built
once before the write) and a record saved by another holder. Then, instead of the
shipped method, it clears the chosen combination by hand and asks the snapshot for
the role. The ASSUMPTION this reading rests on, named because a number without it
means nothing (deepseek.md §39): the adapter's per-footprint field-map cache is
warm and `refresh_board()` was not called between the snapshot build and the
reprojection — which is exactly the state the GUI is in when a write event fires.

Three rows, as the plan's table asks: effective pair only, physical pair only, all
four. The first is expected GREEN and that green is NOT a reason to shrink the
method — it is green by a coincidence of `_role()`, and the second row is what
happens to whoever optimises on that coincidence.
"""
import sys
import tempfile
import traceback
from pathlib import Path

from tests.override_store_board_fixtures import SYMBOL_UUID, wired_board

VARIANTS = [
    ("effective pair only  (_role_cache, _cluster_cache)",
     ("_role_cache", "_cluster_cache")),
    ("physical pair only   (_board_role_cache, _board_cluster_cache)",
     ("_board_role_cache", "_board_cluster_cache")),
    ("all four (as shipped)",
     ("_role_cache", "_cluster_cache",
      "_board_role_cache", "_board_cluster_cache")),
]


def _one_run(caches) -> str:
    """One variant, one fresh stack: rebind the store, clear `caches`, then try
    the rebuild the shipped method performs.

    The rebinding is not optional dressing: without it the layer still serves the
    store it was bound to at project open (a different OBJECT from the holder's),
    and every row would print the PREVIOUS value while looking like it had
    measured the new one."""
    with tempfile.TemporaryDirectory() as tmp:
        wiring = wired_board(Path(tmp))
        wiring.connection._rebuild_snapshot()          # warm, as the poll leaves it
        wiring.write_role("R_WRITTEN")
        wiring.rebind_store()                          # what reload_store does
        for cache in caches:
            getattr(wiring.board, cache).clear()
        try:
            wiring.connection._rebuild_snapshot()
        except Exception as error:                     # noqa: BLE001 — the point
            return (f"{type(error).__name__}: {error}\n" +
                    "".join(traceback.format_exc(limit=4).splitlines(keepends=True)[-4:]))
        return f"role in the snapshot = {wiring.role_in_snapshot()!r}"


def main() -> int:
    print(f"symbol uuid under test: {SYMBOL_UUID}")
    print("assumption: the adapter's field-map cache is WARM and refresh_board() "
          "was not called between the snapshot build and the reprojection\n")
    for label, caches in VARIANTS:
        print(f"--- {label}")
        for line in _one_run(caches).splitlines():
            print(f"    {line}")
        print()
    print("reading: the effective pair alone is GREEN by a coincidence of _role(); "
          "the physical pair alone RAISES, which is why the method forgets all "
          "four (plan §3.1, and the docstring of Board.forget_role_cluster_values)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
