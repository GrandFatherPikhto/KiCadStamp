#!.venv/bin/python
"""Summarises the JSONL written by board_call_timing.py.

    python diagnostics/report_board_timing.py [log.jsonl ...]

With no arguments, reads every diagnostics/board_timing_*.jsonl.

Answers the two questions the async-proxy design is waiting on:
  1. how slow are the fat bulk calls, so DEFAULT_TIMEOUT_MS can be chosen
     from data instead of guessed;
  2. how much wall time a session spends inside the adapter at all, which
     bounds what moving local computation off the queue could ever buy.
"""
import glob
import json
import os
import sys
from collections import defaultdict

# The bulk round trips identified in adapter.py — the ones that would get the
# long timeout tier.
FAT = {
    "get_footprints", "get_tracks", "get_vias", "get_all_nets",
    "get_items_by_id", "update_items", "create_items", "remove_by_ids",
    "get_selected_items", "select_items",
}


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[k]


def main(paths):
    if not paths:
        from kicadstamp.diagnostics.board_call_timing import default_log_dir
        paths = sorted(glob.glob(os.path.join(default_log_dir(),
                                              "board_timing_*.jsonl")))
    if not paths:
        print("no board_timing_*.jsonl found — run `python -m kicadstamp.diagnostics.run_gui_with_timing` first")
        return 1

    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn last line after a crash
                if r.get("method") != "__install__":
                    rows.append(r)

    if not rows:
        print("log(s) present but empty")
        return 1

    by_method = defaultdict(list)
    for r in rows:
        by_method[r["method"]].append(r)

    span = max(r["ts"] for r in rows) - min(r["ts"] for r in rows)
    # Only outermost calls are disjoint in time — see board_call_timing._depth.
    top = [r for r in rows if r.get("depth", 0) == 0]
    total_ms = sum(r["ms"] for r in top)

    print(f"files: {len(paths)}   calls: {len(rows)}   session span: {span:.0f}s")
    if span > 0:
        print(f"time inside the adapter: {total_ms / 1000:.1f}s "
              f"({100 * total_ms / 1000 / span:.1f}% of the session) "
              f"— outermost calls only, {len(rows) - len(top)} nested rows excluded")
        print(f"everything else ({span - total_ms / 1000:.1f}s) is local "
              f"computation and idle time — the part a queue need not hold")
    print()
    print(f"{'method':<26}{'n':>6}{'med':>9}{'p90':>9}{'max':>10}{'total':>10}  {'items':>7}")
    print("-" * 82)
    for name, rs in sorted(by_method.items(), key=lambda kv: -sum(r["ms"] for r in kv[1])):
        ms = [r["ms"] for r in rs]
        counts = [r["out_n"] or r["in_n"] for r in rs if (r["out_n"] or r["in_n"])]
        peak = max(counts) if counts else 0
        mark = "*" if name in FAT else " "
        print(f"{mark}{name:<25}{len(rs):>6}{pct(ms, 50):>9.1f}{pct(ms, 90):>9.1f}"
              f"{max(ms):>10.1f}{sum(ms) / 1000:>9.1f}s  {peak:>7}")
    print("-" * 82)
    print("* = bulk call (long timeout tier).  times in ms unless marked s.")

    threads = defaultdict(float)
    for r in rows:
        threads[r["thread"]] += r["ms"]
    print("\nadapter time by thread (which of these is the UI thread matters):")
    for t, ms in sorted(threads.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<30}{ms / 1000:>8.1f}s")

    # Э4 (plan_2026_09_13_diagnostics_switch): the calls made ON the UI thread,
    # by method and by CALL SITE. The per-thread split above cannot name them,
    # and with the recorder embedded in the GUI a thread NAME is not a criterion
    # at all (in the MCP server and in the CLI everything legitimately runs on
    # MainThread) — which is why the recorder marks these rows explicitly (`ui`,
    # written only when the GUI's injected predicate said "this is the UI
    # thread") instead of matching on "MainThread".
    ui_top = [r for r in top if r.get("ui")]
    marked = any(r.get("ui") for r in rows)
    print("\nUI-thread calls (outermost rows only, as everywhere above — the "
          "nested ones double-count):")
    if ui_top:
        by_site = defaultdict(list)
        for r in ui_top:
            by_site[(r["method"], r.get("site", "<unknown>"))].append(r)
        print(f"  {'method':<26}{'n':>6}{'total':>10}{'max':>10}  site")
        print("  " + "-" * 74)
        for (method, site), rs in sorted(by_site.items(),
                                         key=lambda kv: -sum(r["ms"] for r in kv[1])):
            ms = [r["ms"] for r in rs]
            print(f"  {method:<26}{len(rs):>6}{sum(ms) / 1000:>9.1f}s"
                  f"{max(ms):>9.1f}ms  {site}")
        print(f"  total: {len(ui_top)} call(s), "
              f"{sum(r['ms'] for r in ui_top) / 1000:.1f}s on the UI thread")
    elif not marked:
        print("  none — no row carries a call site, so either no board call "
              "happened on the UI thread, or the recording ran WITHOUT the GUI's "
              "UI-thread predicate (the external launcher installs none; the "
              "Settings > Diagnostics switch does)")
    else:
        print("  none — no board call was made from the UI thread in this log")

    errs = [r for r in rows if not r["ok"]]
    if errs:
        print(f"\nfailed calls: {len(errs)}")
        seen = defaultdict(int)
        for r in errs:
            seen[(r["method"], r.get("err", "")[:60])] += 1
        for (m, e), n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}x {m}: {e}")

    slow = sorted(rows, key=lambda r: -r["ms"])[:10]
    print("\nten slowest single calls:")
    for r in slow:
        print(f"  {r['ms']:>9.1f}ms  {r['method']:<24} items={r['out_n'] or r['in_n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
