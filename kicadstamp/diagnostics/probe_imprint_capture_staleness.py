#!/usr/bin/env python3
# kicadstamp/diagnostics/probe_imprint_capture_staleness.py
"""Two-column measurement of the age of the read that feeds an imprint capture.

Plan: `techdocs/handoff/deepseek/plan/plan_2026_09_22_imprint_capture_frame.md` §7.8.1 (H5).

The question, in one line: **does `capture_imprint` inherit a STALE adapter cache?**

`capture_imprint` starts with `all_footprints = adapter.get_footprints()`, and on this seam
that is the CACHED read: `KiCadBoardAdapter.get_footprints` returns `_footprints_cache` and
only `refresh_board()` drops it. No worker on the record path calls `refresh_board()`
(`gui/dock_hub.py:1682` `_run_record_capture`, `:1790` `_run_resource_capture`,
`gui/docks/imprint.py:1797` `_run_reread_apply`), while the dialog's PREVIEW comes from
`connection.snapshot` — two different reads in one action.

How it is measured, without writing anything to the board:

  1. own adapter, `refresh_board()` -> generation A: read positions, capture a record (A);
  2. print an instruction, then WAIT `--pause-seconds` — a human moves ONE component of the
     cluster in KiCad (or moves it and puts it back) during that window;
  3. read positions and capture a record WITHOUT a refresh  <- the warm-cache column (B);
  4. `refresh_board()`, read positions and capture again     <- the fresh column (C).

Verdict, and it has THREE outcomes on purpose:

  * C == A (nothing moved)      -> INCONCLUSIVE. The two columns coincide BY CONSTRUCTION,
                                   so this run proves nothing either way — re-run and move a
                                   component. A check that passes on a healthy sample proves
                                   nothing (plan §7.10);
  * B == A and C != A           -> H5 ALIVE: the capture returned the PRE-move positions while
                                   the board had already moved;
  * B == C (both moved)         -> H5 DEAD on this path: the cache followed the board.

READ-ONLY: own KiCad socket, closed in `finally`; nothing is written to the board, and the
only file written is the optional `--out` JSONL with the two columns.

Measured on the layer where the cache lives (lesson Кk): the probe counts cache MISSES at
`adapter.get_footprints` — a call is a KiCad read exactly when `_footprints_cache` was empty
BEFORE it — next to adapter call counts and refreshes. So "no refresh" shows up as a number
instead of an assumption; counting adapter CALLS alone would lie (a warm cache makes every
call cost nothing).

Usage:
  .venv/bin/python -m kicadstamp.diagnostics.probe_imprint_capture_staleness \
      --config /abs/path/profiles/heating-table/config.sexp \
      --cluster MINI360_P12V_P5V --pause-seconds 45 [--out diag.jsonl]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.adapter_factory import create_board_adapter          # noqa: E402
from kicadstamp.config_writer import read_data                       # noqa: E402
from kicadstamp.imprint_capture import capture_imprint               # noqa: E402

_NM_PER_MM = 1_000_000.0
TOL_MM = 1e-3


def refs_for_cluster(config_path: Path, cluster: str) -> list[str]:
    """The cluster's refs from the override store (the board's own Role/Cluster
    fields are empty in this profile — the store is the only map)."""
    store = config_path.parent / "overrides" / "config.fields.json"
    if not store.exists():
        return []
    records = (read_data(store) or {}).get("records") or []
    role_by_ref: dict[str, str] = {}
    cluster_by_ref: dict[str, str] = {}
    for rec in records:
        ref, field = str(rec.get("ref") or ""), str(rec.get("field") or "")
        if field == "Role":
            role_by_ref[ref] = str(rec.get("value") or "")
        elif field == "Cluster":
            cluster_by_ref[ref] = str(rec.get("value") or "")
    return sorted(ref for ref, value in cluster_by_ref.items() if value == cluster)


def positions_mm(adapter, refs: list[str]) -> dict[str, tuple[float, float]]:
    by_ref = {fp.ref: fp for fp in adapter.get_footprints()}
    out: dict[str, tuple[float, float]] = {}
    for ref in refs:
        fp = by_ref.get(ref)
        if fp is not None:
            out[ref] = (fp.position.x / _NM_PER_MM, fp.position.y / _NM_PER_MM)
    return out


def capture_offsets_mm(adapter, name: str, refs: list[str]) -> dict[str, tuple[float, float]]:
    """The real path: capture_imprint reads adapter.get_footprints() itself."""
    record = capture_imprint(name, list(refs), adapter=adapter)
    return {c.ref: (c.offset_along_mm, c.offset_across_mm) for c in record.components}


def install_read_meter(adapter) -> dict:
    """Meter on the adapter, where the cache lives.

    A call to ``adapter.get_footprints()`` is a KiCad read EXACTLY WHEN
    ``_footprints_cache`` was empty before it — `KiCadBoardAdapter.get_footprints`
    fills the cache only then. That is why adapter CALL counts are not used as the
    cost (lesson Кk): from a warm cache every call is free, and a meter on the
    calls would report the price of nothing.

    ``FieldOverrideAdapter.__getattr__`` delegates to the inner adapter, so the
    cache attribute is reached through either shape."""
    meter = {"adapter_calls": 0, "kicad_reads": 0, "refreshes": 0}
    real_get = adapter.get_footprints
    real_refresh = adapter.refresh_board

    def get():
        meter["adapter_calls"] += 1
        if getattr(adapter, "_footprints_cache", None) is None:
            meter["kicad_reads"] += 1
        return real_get()

    def refresh():
        meter["refreshes"] += 1
        return real_refresh()

    adapter.get_footprints = get
    adapter.refresh_board = refresh
    return meter


def max_delta_mm(a: dict[str, tuple[float, float]],
                 b: dict[str, tuple[float, float]]) -> tuple[float, int]:
    """Largest |a - b| over the refs present in both, and how many refs moved."""
    worst, moved = 0.0, 0
    for ref, (ax, ay) in a.items():
        if ref not in b:
            continue
        bx, by = b[ref]
        delta = max(abs(ax - bx), abs(ay - by))
        if delta > TOL_MM:
            moved += 1
        worst = max(worst, delta)
    return worst, moved


def print_columns(refs, columns) -> None:
    head = f"{'ref':<6}{'A: after refresh':>24}{'B: warm cache':>24}{'C: after refresh':>24}"
    print(head)
    for ref in refs:
        cells = []
        for column in columns:
            value = column.get(ref)
            cells.append("—" if value is None else f"{value[0]:.4f}, {value[1]:.4f}")
        print(f"{ref:<6}{cells[0]:>24}{cells[1]:>24}{cells[2]:>24}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="profile config.sexp on disk")
    parser.add_argument("--cluster", default="MINI360_P12V_P5V")
    parser.add_argument("--imprint-name", default="staleness-probe",
                        help="name for the throwaway captures (never written to a file)")
    parser.add_argument("--refs", help="comma-separated refs, overriding --cluster lookup")
    parser.add_argument("--pause-seconds", type=float, default=45.0)
    parser.add_argument("--out", help="write the three columns as JSONL")
    args = parser.parse_args(argv)

    config = Path(args.config)
    refs = ([r.strip() for r in args.refs.split(",") if r.strip()] if args.refs
            else refs_for_cluster(config, args.cluster))
    if not refs:
        print(f"no refs: cluster {args.cluster!r} not found in the store next to {config}")
        return 1
    print(f"profile : {config}")
    print(f"cluster : {args.cluster}")
    print(f"refs    : {', '.join(refs)}\n")

    adapter = create_board_adapter(config_path=str(config))
    try:
        meter = install_read_meter(adapter)
        adapter.refresh_board()

        # Column A — a freshly built generation.
        pos_a = positions_mm(adapter, refs)
        off_a = capture_offsets_mm(adapter, args.imprint_name, refs)
        reads_a = meter["kicad_reads"]

        print(f"MOVE ONE component of the cluster ~5 mm in KiCad now (or move it and put it "
              f"back). Waiting {args.pause_seconds:.0f} s — nothing is written by this probe.\n")
        if args.pause_seconds > 0:
            time.sleep(args.pause_seconds)

        # Column B — the warm cache: no refresh, exactly as the record path does it today.
        pos_b = positions_mm(adapter, refs)
        off_b = capture_offsets_mm(adapter, args.imprint_name, refs)
        reads_b = meter["kicad_reads"]

        # Column C — after an explicit refresh.
        adapter.refresh_board()
        pos_c = positions_mm(adapter, refs)
        off_c = capture_offsets_mm(adapter, args.imprint_name, refs)
        reads_c = meter["kicad_reads"]

        print_columns(refs, (pos_a, pos_b, pos_c))
        print(f"\nKiCad reads (cache miss at adapter.get_footprints): "
              f"A={reads_a} B={reads_b} C={reads_c}, total {meter['kicad_reads']} | "
              f"adapter calls {meter['adapter_calls']} | refreshes {meter['refreshes']}")

        moved, moved_refs = max_delta_mm(pos_a, pos_c)
        print(f"board moved between A and C: max |Δ| = {moved:.4f} mm on {moved_refs} ref(s)")
        stale, stale_refs = max_delta_mm(off_b, off_c)
        follows, follows_refs = max_delta_mm(off_b, off_a)
        print(f"warm column vs fresh column : max |Δ| = {stale:.4f} mm on {stale_refs} ref(s)")
        print(f"warm column vs column A     : max |Δ| = {follows:.4f} mm on {follows_refs} ref(s)")

        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                for ref in refs:
                    handle.write(json.dumps({
                        "ref": ref,
                        "A_live_pos_mm": pos_a.get(ref),
                        "B_warm_pos_mm": pos_b.get(ref),
                        "C_live_pos_mm": pos_c.get(ref),
                        "A_off_mm": off_a.get(ref),
                        "B_off_mm": off_b.get(ref),
                        "C_off_mm": off_c.get(ref),
                    }, ensure_ascii=False) + "\n")
            print(f"columns written: {args.out}")

        print()
        if moved <= TOL_MM:
            print("VERDICT: INCONCLUSIVE — nothing moved between A and C, so the two columns "
                  "coincide BY CONSTRUCTION. Re-run and move one component of the cluster "
                  "during the pause window.")
            return 1
        if stale > TOL_MM and follows <= TOL_MM:
            print("VERDICT: H5 ALIVE — a capture taken from the WARM CACHE returned the "
                  "PRE-move positions while the board had already moved. The record inherits "
                  "a stale read; the fix is a board refresh before the capture.")
            return 0
        if follows > TOL_MM and stale <= TOL_MM:
            print("VERDICT: H5 DEAD on this path — the warm-cache column followed the board "
                  "(no refresh needed here). Report it; do not fix the probable.")
            return 0
        print("VERDICT: AMBIGUOUS — the columns disagree in a way the three-outcome table "
              "does not cover; keep the numbers and report, do not fix the probable.")
        return 1
    finally:
        adapter.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
