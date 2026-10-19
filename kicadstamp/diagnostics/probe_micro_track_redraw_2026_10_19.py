# kicadstamp/diagnostics/probe_micro_track_redraw_2026_10_19.py
"""READ-ONLY recon for plan_2026_10_15_micro_track_redraw_duplicates (Ч0-1).

What it answers, from the profile's own ``actions.log`` alone (no live board,
nothing written anywhere):

  * per pipeline run — how many tracks/vias the adapter SAW live before the
    positional pre-check, how many commands the planner produced, how many were
    actually created and how many the pre-check skipped;
  * how many of the created tracks were MICRO segments (shorter than
    ``--micro-um``, default 50 µm) and where they lie;
  * which registry keys hit the two reconcile branches — "registry has an entry
    (uuid …), but no such item is on the board" (stale uuid) vs
    "position/parameters changed, deleting old item" (geometry drift).

The point of tie 1 is the plan's Ч0-1: Claude's table claims every 15.10 run
followed a BOARD WIPE, i.e. the 29-of-31 re-creation is "the board was empty",
not "the redraw failed to recognise copper". That claim is only true if the
runs show a near-empty live list; a run with a FULL live list and a non-zero
created count would be the original defect coming back into scope. This probe
prints exactly the numbers that decision needs.

Usage (any interpreter in the repo, no KiCad, no kipy):

    python -m kicadstamp.diagnostics.probe_micro_track_redraw_2026_10_19
    python -m ... --since 2026-10-15 --micro-um 10 --log <path>

Exit code is 0 — this is a measuring probe, not a gate.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LOG = Path("profiles/Heating-Table-Controller-STM32/logs/actions.log")

# Every line of actions.log starts with this stamp (see setup_logging).
_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - ")
_RETRIEVED_TRACKS_RE = re.compile(r"Retrieved (\d+) tracks")
_RETRIEVED_VIAS_RE = re.compile(r"Retrieved (\d+) vias")
_PLANNED_TRACKS_RE = re.compile(
    r"Planned tracks: (\d+), actually to create \(registry filtered already correctly "
    r"placed\): (\d+)")
_SKIPPED_TRACKS_RE = re.compile(r"Skipped (\d+) tracks already present on the board")
_PLANNED_VIAS_RE = re.compile(
    r"Planned vias: (\d+), actually to create \(registry filtered already correctly "
    r"placed\): (\d+)")
_SKIPPED_VIAS_RE = re.compile(r"Skipped (\d+) vias already present on the board")
_CREATING_TRACK_RE = re.compile(
    r"Creating track \((-?[\d.]+), (-?[\d.]+)\) -> \((-?[\d.]+), (-?[\d.]+)\) mm, net=(\S+)")
_STALE_UUID_RE = re.compile(r"(\S+): registry has an entry \(uuid [0-9a-f-]+\), but no such item")
_MOVED_RE = re.compile(r"\s*(\S+): position/parameters changed, deleting old item")

#: A run is "flushed" when its own summary line arrives; lines are attributed to
#: the run being accumulated, so the numbers always belong together.
_RUN_END_RE = re.compile(r"Planned tracks:")


@dataclass
class MicroTrack:
    """One created track shorter than the micro threshold."""

    when: str
    start_mm: tuple[float, float]
    end_mm: tuple[float, float]
    length_um: float
    net: str


@dataclass
class Run:
    """One apply/redraw run reconstructed from the log."""

    when: str = ""
    live_tracks: int | None = None
    live_vias: int | None = None
    planned_tracks: int | None = None
    to_create_tracks: int | None = None
    skipped_tracks: int | None = None
    planned_vias: int | None = None
    to_create_vias: int | None = None
    skipped_vias: int | None = None
    created_tracks: int = 0
    micro_tracks: list[MicroTrack] = field(default_factory=list)
    stale_uuids: int = 0
    moved_keys: int = 0

    def is_complete(self) -> bool:
        return self.planned_tracks is not None


def _length_um(sx: float, sy: float, ex: float, ey: float) -> float:
    return math.hypot(ex - sx, ey - sy) * 1000.0


def parse_log(path: Path, *, since: str | None, micro_um: float) -> list[Run]:
    """Reconstruct runs in file order. Read-only: the file is opened 'r'."""
    runs: list[Run] = []
    current = Run()
    last_stamp = ""

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _LINE_RE.match(line)
            if not m:
                continue
            stamp = m.group(1)
            if since and stamp[:10] < since:
                last_stamp = stamp
                continue

            if _RUN_END_RE.search(line):
                # The summary line closes the run it belongs to; start a new one.
                if current.is_complete() or current.live_tracks is not None:
                    runs.append(current)
                current = Run()
                current.when = stamp

            if current.when == "":
                current.when = stamp
            last_stamp = stamp

            if (r := _RETRIEVED_TRACKS_RE.search(line)) is not None:
                current.live_tracks = int(r.group(1))
            elif (r := _RETRIEVED_VIAS_RE.search(line)) is not None:
                current.live_vias = int(r.group(1))
            elif (r := _PLANNED_TRACKS_RE.search(line)) is not None:
                current.planned_tracks = int(r.group(1))
                current.to_create_tracks = int(r.group(2))
            elif (r := _SKIPPED_TRACKS_RE.search(line)) is not None:
                current.skipped_tracks = int(r.group(1))
            elif (r := _PLANNED_VIAS_RE.search(line)) is not None:
                current.planned_vias = int(r.group(1))
                current.to_create_vias = int(r.group(2))
            elif (r := _SKIPPED_VIAS_RE.search(line)) is not None:
                current.skipped_vias = int(r.group(1))
            elif (r := _CREATING_TRACK_RE.search(line)) is not None:
                sx, sy, ex, ey = (float(r.group(i)) for i in range(1, 5))
                current.created_tracks += 1
                length_um = _length_um(sx, sy, ex, ey)
                if length_um < micro_um:
                    current.micro_tracks.append(MicroTrack(
                        when=stamp, start_mm=(sx, sy), end_mm=(ex, ey),
                        length_um=length_um, net=r.group(5)))
            elif _STALE_UUID_RE.search(line):
                current.stale_uuids += 1
            elif _MOVED_RE.search(line):
                current.moved_keys += 1

    if current.live_tracks is not None or current.is_complete():
        runs.append(current)
    if last_stamp:
        print(f"# log read up to {last_stamp}", file=sys.stderr)
    return runs


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def print_runs(runs: list[Run], *, micro_um: float) -> None:
    print(f"Runs reconstructed: {len(runs)} (micro threshold {micro_um} µm)\n")
    print("when                live_trk live_via  plan  create  skip | created  micro | stale moved")
    print("-" * 100)
    for run in runs:
        print(f"{run.when:19} {_fmt(run.live_tracks):>8} {_fmt(run.live_vias):>8} "
              f"{_fmt(run.planned_tracks):>5} {_fmt(run.to_create_tracks):>7} "
              f"{_fmt(run.skipped_tracks):>5} | {run.created_tracks:>7} "
              f"{len(run.micro_tracks):>6} | {run.stale_uuids:>5} {run.moved_keys:>5}")

    with_micro = [r for r in runs if r.micro_tracks]
    print(f"\nRuns that created at least one micro track: {len(with_micro)}")
    seen: dict[tuple, float] = {}
    for run in with_micro:
        for mt in run.micro_tracks:
            key = (round(mt.start_mm[0], 3), round(mt.start_mm[1], 3),
                   round(mt.end_mm[0], 3), round(mt.end_mm[1], 3))
            seen.setdefault(key, mt.length_um)
    print(f"Distinct micro geometries: {len(seen)}")
    for key, length in sorted(seen.items()):
        print(f"  ({key[0]:.3f}, {key[1]:.3f}) -> ({key[2]:.3f}, {key[3]:.3f})  "
              f"len={length:.1f} µm")

    # Ч0-1 decision input #1: a micro track created while the board ALREADY held
    # a real amount of copper (live_tracks large) is the "defect without a board
    # wipe" case — it goes back into scope. 100 is a deliberately low bar: the
    # wiped runs of 15.10 start at 7 and climb, so anything at/above 100 was a
    # board that had its copper.
    _RICH = 100
    rich_micro = [r for r in with_micro
                  if r.live_tracks is not None and r.live_tracks >= _RICH]
    poor_micro = [r for r in with_micro
                  if r.live_tracks is not None and r.live_tracks < _RICH]
    print(f"\nMicro runs on a board holding >={_RICH} tracks: {len(rich_micro)}")
    for run in rich_micro:
        print(f"  {run.when}: live_tracks={run.live_tracks} planned={run.planned_tracks} "
              f"to_create={run.to_create_tracks} skip={_fmt(run.skipped_tracks)} "
              f"created={run.created_tracks} micro={len(run.micro_tracks)}")
    if not rich_micro:
        print("  none — every micro creation happened on a nearly empty board "
              "(Claude's Ч0-1 claim holds)")
    print(f"Micro runs on a board holding <{_RICH} tracks: {len(poor_micro)}")

    # Ч0-1 decision input #2: board wipes are visible as a collapse of the live
    # track count between consecutive runs.
    print("\nLive-track collapses (wipe candidates):")
    prev = None
    for run in runs:
        if run.live_tracks is None:
            continue
        if prev is not None and prev.live_tracks is not None and prev.live_tracks >= 4 * max(
                run.live_tracks, 1):
            print(f"  {prev.when} live={prev.live_tracks} -> {run.when} "
                  f"live={run.live_tracks}")
        prev = run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--since", default=None,
                    help="only stamps from this date on (YYYY-MM-DD)")
    ap.add_argument("--micro-um", type=float, default=50.0,
                    help="tracks shorter than this are reported as micro (µm)")
    args = ap.parse_args(argv)

    if not args.log.exists():
        print(f"log not found: {args.log}", file=sys.stderr)
        return 2
    runs = parse_log(args.log, since=args.since, micro_um=args.micro_um)
    print_runs(runs, micro_um=args.micro_um)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
