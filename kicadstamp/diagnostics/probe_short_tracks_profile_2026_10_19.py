# kicadstamp/diagnostics/probe_short_tracks_profile_2026_10_19.py
"""READ-ONLY recon for plan_2026_10_15_micro_track_redraw_duplicates (Ч0-4/Ч0-5).

Answers, from the profile's config file alone (no board, nothing written):

  * every ``net_traces`` track shorter than ``--micro-um`` (default 50 µm): which
    record, index, local along/across geometry, width, layer, net reference;
  * whether that segment is a BRIDGE of the record's own copper: remove it from
    the record's track graph and see whether the number of connected components
    grows. That is exactly the question Denis's read-threshold decision needs —
    "is this stub the only thing joining two pieces of copper?";
  * the same count for the WHOLE profile (``--all-tracks``) so the "how many are
    there" number is measured, not quoted.

Length is computed in the record's LOCAL frame (start_along/start_across ->
end_along/end_across): a rotation preserves length, so the local length IS the
board length — no anchor has to be resolved for this question.

Usage:

    python -m kicadstamp.diagnostics.probe_short_tracks_profile_2026_10_19
    python -m ... --config <path> --micro-um 10 --all-tracks

Exit code is 0 — a measuring probe, not a gate.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(
    "profiles/Heating-Table-Controller-STM32/Heating-Table-Controller-STM32.sexp")

_RECORD_START_RE = re.compile(r"^\s{4}\(net_trace\s*$")
_NAME_RE = re.compile(r'^\s{6}\(name "([^"]*)"\)')
_UUID_RE = re.compile(r'^\s{6}\(uuid "([^"]*)"\)')
_NET_RE = re.compile(r'^\s{6}\(net "([^"]*)"\)')
_TRACK_START_RE = re.compile(r"^\s{8}\(track\s*$")
_TRACK_END_RE = re.compile(r"^\s{8}\)\s*$")
_FIELD_RE = re.compile(r"^\s{10}\((\w+)\s+(.+?)\)\s*$")

#: Two ends closer than this (mm) are treated as the SAME node of the graph.
#: 1 µm: the writer keeps 4 decimals in mm, so a genuinely shared point is
#: exact; anything larger would merge distinct corners of a trace.
NODE_EPS_MM = 0.001


@dataclass
class Seg:
    along_a: float
    across_a: float
    along_b: float
    across_b: float
    width_mm: float
    layer: str
    net_ref: str

    @property
    def length_um(self) -> float:
        return math.hypot(self.along_b - self.along_a,
                          self.across_b - self.across_a) * 1000.0

    def ends(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return ((self.along_a, self.across_a), (self.along_b, self.across_b))


@dataclass
class Record:
    name: str = ""
    uuid: str = ""
    net: str = ""
    segments: list[Seg] = field(default_factory=list)


def _parse_segment(lines: list[str], start: int) -> tuple[Seg | None, int]:
    """Parse one `(track …)` block; returns (segment, index after the block)."""
    values: dict[str, float | str] = {
        "start_along_mm": 0.0, "start_across_mm": 0.0,
        "end_along_mm": 0.0, "end_across_mm": 0.0,
        "width_mm": 0.65, "layer": "", "net_from_role": "", "net_from_role_pad": "",
    }
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if _TRACK_END_RE.match(line):
            i += 1
            break
        m = _FIELD_RE.match(line)
        if m:
            key, raw = m.group(1), m.group(2).strip()
            if key in values:
                if key in ("layer", "net_from_role", "net_from_role_pad"):
                    values[key] = raw.strip('"')
                else:
                    try:
                        values[key] = float(raw)
                    except ValueError:
                        pass
        i += 1
    if not values["layer"]:
        return None, i
    seg = Seg(
        along_a=float(values["start_along_mm"]),       # type: ignore[arg-type]
        across_a=float(values["start_across_mm"]),     # type: ignore[arg-type]
        along_b=float(values["end_along_mm"]),         # type: ignore[arg-type]
        across_b=float(values["end_across_mm"]),       # type: ignore[arg-type]
        width_mm=float(values["width_mm"]),            # type: ignore[arg-type]
        layer=str(values["layer"]),
        net_ref=f"{values['net_from_role']}/{values['net_from_role_pad']}",
    )
    return seg, i


def parse_records(path: Path) -> list[Record]:
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[Record] = []
    current: Record | None = None
    in_tracks = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _RECORD_START_RE.match(line):
            current = Record()
            in_tracks = False
            records.append(current)
            i += 1
            continue
        if current is None:
            i += 1
            continue
        if (m := _NAME_RE.match(line)) is not None:
            current.name = m.group(1)
        elif (m := _UUID_RE.match(line)) is not None:
            current.uuid = m.group(1)
        elif (m := _NET_RE.match(line)) is not None:
            current.net = m.group(1)
        elif _TRACK_START_RE.match(line):
            in_tracks = True
            seg, i = _parse_segment(lines, i)
            if seg is not None:
                current.segments.append(seg)
            continue
        elif _TRACK_END_RE.match(line):
            in_tracks = False
        i += 1
    return records


def _components(segments: list[Seg]) -> int:
    """Number of connected components of the segments' end-point graph."""
    nodes: list[tuple[float, float]] = []
    edges: list[tuple[int, int]] = []
    for seg in segments:
        ids = []
        for point in seg.ends():
            for idx, node in enumerate(nodes):
                if (abs(node[0] - point[0]) <= NODE_EPS_MM
                        and abs(node[1] - point[1]) <= NODE_EPS_MM):
                    ids.append(idx)
                    break
            else:
                nodes.append(point)
                ids.append(len(nodes) - 1)
        edges.append((ids[0], ids[1]))
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = {find(i) for i in range(len(nodes))}
    return len(roots)


def _report_record(record: Record, micro_um: float) -> None:
    shorts = [seg for seg in record.segments if seg.length_um < micro_um]
    if not shorts:
        return
    base = _components(record.segments)
    label = record.name or record.net or "?"
    print(f"\n{label}  (uuid {record.uuid or '?'}, net {record.net!r})")
    print(f"  tracks total: {len(record.segments)}; shorter than {micro_um} µm: "
          f"{len(shorts)}; connected components: {base}")
    for idx, seg in enumerate(record.segments):
        if seg.length_um >= micro_um:
            continue
        without = record.segments[:idx] + record.segments[idx + 1:]
        after = _components(without)
        bridge = "BRIDGE (removing splits copper)" if after > base else "spur / inside copper"
        print(f"  [{idx:>2}] {seg.length_um:>6.1f} µm  "
              f"({seg.along_a:.4f},{seg.across_a:.4f}) -> "
              f"({seg.along_b:.4f},{seg.across_b:.4f})  w={seg.width_mm} "
              f"{seg.layer}  {seg.net_ref}  components {base}->{after}: {bridge}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--micro-um", type=float, default=50.0)
    ap.add_argument("--all-tracks", action="store_true",
                    help="also count short tracks over the whole profile")
    args = ap.parse_args(argv)

    if not args.config.exists():
        print(f"config not found: {args.config}", file=sys.stderr)
        return 2
    records = parse_records(args.config)
    total_tracks = sum(len(r.segments) for r in records)
    shorts = [(r, seg) for r in records for seg in r.segments
              if seg.length_um < args.micro_um]
    print(f"config: {args.config}")
    print(f"net_traces records: {len(records)}; tracks total: {total_tracks}; "
          f"shorter than {args.micro_um} µm: {len(shorts)}")
    if args.all_tracks:
        bands = [(0, 10), (10, 20), (20, 50), (50, 100)]
        for lo, hi in bands:
            n = sum(1 for r in records for seg in r.segments
                    if lo <= seg.length_um < hi)
            print(f"  length {lo:>3}..{hi:<3} µm: {n}")

    for record in records:
        _report_record(record, args.micro_um)

    bridges = 0
    for record in records:
        base = _components(record.segments)
        for idx, seg in enumerate(record.segments):
            if seg.length_um >= args.micro_um:
                continue
            if _components(record.segments[:idx] + record.segments[idx + 1:]) > base:
                bridges += 1
    print(f"\nSummary: {len(shorts)} short track(s), {bridges} of them are bridges "
          "of their record's own copper.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
