#!.venv/bin/python
"""Summarises the JSONL written by board_read_probe.py.

    python -m kicadstamp.diagnostics.report_board_reads [reads.jsonl ...]

With no arguments, reads every diagnostics/board_reads_*.jsonl.

Answers the ONE question the enforcement task waits on: who reads the live board,
and does any of it happen on the UI thread (MainThread)? Counts are printed by
thread and by call site, both descending, and the UI-thread reads are repeated in
their own block so they cannot be missed.
"""
import glob
import json
import os
import sys
from collections import Counter


def main(paths):
    if not paths:
        from kicadstamp.diagnostics.board_read_probe import default_log_dir
        paths = sorted(glob.glob(os.path.join(default_log_dir(),
                                              "board_reads_*.jsonl")))
    if not paths:
        print("no board_reads_*.jsonl found — run "
              "`python -m kicadstamp.diagnostics.run_gui_with_read_probe` first")
        return 1

    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a torn last line after a crash

    if not rows:
        print("log(s) present but empty")
        return 1

    print(f"files: {len(paths)}   board reads: {len(rows)}")

    threads = Counter(r.get("thread", "?") for r in rows)
    print("\nreads by thread:")
    for name, n in threads.most_common():
        mark = "   <-- UI THREAD" if name == "MainThread" else ""
        print(f"  {name:<28}{n:>8}{mark}")

    print("\nreads by call site (descending):")
    for site, n in Counter(r.get("site", "?") for r in rows).most_common():
        print(f"  {n:>8}  {site}")

    ui = [r for r in rows if r.get("thread") == "MainThread"]
    if ui:
        print(f"\nUI-THREAD reads: {len(ui)} ({100 * len(ui) / len(rows):.1f}% of "
              f"all) — these are the ones the enforcement task must judge:")
        for site, n in Counter(r.get("site", "?") for r in ui).most_common():
            print(f"  {n:>8}  {site}")
    else:
        print("\nUI-THREAD reads: none — no read happened on MainThread.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
