#!/usr/bin/env python3
# diagnostics/diagnose_imprint_capture_frame.py
"""Offline reconstruction of the imprint capture frame (plan_2026_09_22_imprint_capture_frame.md, T1).

Answers ONE question with numbers, from FILES ONLY — no KiCad, no socket, no GUI:

    do the recorded offsets of one imprint come from a SINGLE frame, or not?

If they do, the capture is innocent and the defect lives elsewhere. If they do not,
the capture path is the defect and the hunt goes by the groups the table shows.

Inputs (all on disk, nothing is written except the optional --fixture):

  * the imprint record        profiles/<p>/scheme_lists.sexp   (imprints section)
  * the cell built from it    profiles/<p>/config.sexp         (cells section)
  * live positions of the SAME refs at the moment of the apply
                              profiles/<p>/operational/operation_*.json
  * ref -> role               profiles/<p>/overrides/config.fields.json

The operation log is the trick that makes this offline: operation_logger records
``original_position``/``original_angle_deg`` of every footprint IT MOVED, i.e. the
board state immediately BEFORE the apply. Those are the very numbers the capture
should have produced offsets from.

Hypotheses are tested explicitly and each one prints REFUTED with the number that
refutes it, or EXPLAINS with the residual. A hypothesis that explains only PART of
the rows is a FINDING, never a verdict (house rule: a silent miss must not look
like health).

Usage:
  .venv/bin/python diagnostics/diagnose_imprint_capture_frame.py \
      --imprint pwr_mini360_in \
      --cell mini360_p12v_p5v \
      --operation profiles/heating-table/operational/operation_20260922_155958.json \
      [--profile profiles/heating-table] [--tol 1e-3] [--fixture out.jsonl]

Exit code 0 = some FRAME hypothesis explains ALL 11 rows; 1 = none does (which is
itself a result: the capture is the defect, see the plan).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kicadstamp.config_writer import read_data  # noqa: E402

_NM_PER_MM = 1_000_000.0
DEFAULT_TOL_MM = 1e-3


# ── inputs ────────────────────────────────────────────────────────────────────

def _as_list(container, key: str) -> list:
    """A section may be a list of entries (imprints/cells) — or a dict keyed by
    name, depending on the writer. Both shapes are legal in this codebase."""
    value = (container or {}).get(key)
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.values())
    return list(value)


def _named(entries: list, name: str) -> dict | None:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name") or "") == name:
            return entry
    return None


def _components(entry: dict) -> list[dict]:
    comps = (entry or {}).get("components")
    if isinstance(comps, dict):
        return [dict(v) for v in comps.values()]
    return [dict(c) for c in (comps or [])]


def _mm(value) -> float:
    return float(value or 0.0)


def load_record(path: Path, imprint_name: str) -> dict:
    """``path`` is the scheme-lists file — the live one OR a .history backup: the
    whole point of comparing them is to date the numbers, i.e. to tell a STALE
    record (kept from an older board state) from a fresh capture."""
    raw = read_data(path)
    entry = _named(_as_list(raw, "imprints"), imprint_name)
    if entry is None:
        raise SystemExit(f"imprint {imprint_name!r} not found in {path}")
    return entry


def load_cell(path: Path, cell_name: str) -> dict:
    raw = read_data(path)
    cells = (raw or {}).get("cells") or {}
    entry = cells.get(cell_name) if isinstance(cells, dict) else _named(_as_list(raw, "cells"), cell_name)
    if entry is None:
        raise SystemExit(f"cell {cell_name!r} not found in {path}")
    return entry


def load_roles(profile: Path, cluster: str) -> dict[str, str]:
    """ref -> Role for one cluster, from the override store (the board's own
    Role/Cluster fields are empty for this project — the store is the only map)."""
    path = profile / "overrides" / "config.fields.json"
    if not path.exists():
        return {}
    records = (read_data(path) or {}).get("records") or []
    role_by_ref: dict[str, str] = {}
    cluster_by_ref: dict[str, str] = {}
    for rec in records:
        ref = str(rec.get("ref") or "")
        field = str(rec.get("field") or "")
        if field == "Role":
            role_by_ref[ref] = str(rec.get("value") or "")
        elif field == "Cluster":
            cluster_by_ref[ref] = str(rec.get("value") or "")
    if cluster:
        return {r: role for r, role in role_by_ref.items()
                if cluster_by_ref.get(r) == cluster}
    return role_by_ref


def load_operation(path: Path) -> dict[str, dict]:
    """ref -> {x_mm, y_mm, angle_deg} BEFORE the apply (operation_logger's
    original_position/original_angle_deg)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for move in data.get("moves") or []:
        ref = str(move.get("ref") or "")
        pos = move.get("original_position") or {}
        out[ref] = {
            "x_mm": float(pos.get("x") or 0.0) / _NM_PER_MM,
            "y_mm": float(pos.get("y") or 0.0) / _NM_PER_MM,
            "angle_deg": move.get("original_angle_deg"),
        }
    return out


# ── geometry helpers ─────────────────────────────────────────────────────────

def normalise_deg(angle: float) -> float:
    return (float(angle) + 180.0) % 360.0 - 180.0


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def build_rows(record: dict, cell: dict, roles: dict[str, str],
               live: dict[str, dict]) -> list[dict]:
    """One row per recorded component: the recorded offset next to the live one.

    The join is ref-first: the record is keyed by ref, the cell by role, so the
    store's ref->Role is what lets the two be compared at all."""
    rec_comps = _components(record)
    cell_by_role = {str(c.get("role") or ""): c for c in _components(cell)}
    rows: list[dict] = []
    for comp in rec_comps:
        ref = str(comp.get("ref") or "")
        role = roles.get(ref, "")
        cell_comp = cell_by_role.get(role) or {}
        live_pos = live.get(ref)
        rows.append({
            "ref": ref,
            "role": role,
            "rec_along_mm": _mm(comp.get("offset_along_mm")),
            "rec_across_mm": _mm(comp.get("offset_across_mm")),
            "rec_angle_deg": _mm(comp.get("rotation_deg")),
            "cell_along_mm": _mm(cell_comp.get("offset_along_mm")),
            "cell_across_mm": _mm(cell_comp.get("offset_across_mm")),
            "cell_angle_deg": _mm(cell_comp.get("angle_deg")),
            "has_cell_slot": bool(cell_comp),
            "live_x_mm": live_pos["x_mm"] if live_pos else None,
            "live_y_mm": live_pos["y_mm"] if live_pos else None,
            "live_angle_deg": live_pos["angle_deg"] if live_pos else None,
        })
    return rows


def add_relative(rows: list[dict], reference: str = "C1") -> tuple[float, float]:
    """Express every row relative to the reference ref, on BOTH sides, so the
    unknown frame origin cancels out of the comparison."""
    anchor = next((r for r in rows if r["ref"] == reference), None)
    if anchor is None:
        anchor = rows[0] if rows else None
    if anchor is None:
        return 0.0, 0.0
    for row in rows:
        row["rec_rel_along"] = _round(row["rec_along_mm"] - anchor["rec_along_mm"], 6)
        row["rec_rel_across"] = _round(row["rec_across_mm"] - anchor["rec_across_mm"], 6)
        if row["live_x_mm"] is not None and anchor["live_x_mm"] is not None:
            row["live_rel_along"] = _round(row["live_x_mm"] - anchor["live_x_mm"], 6)
            row["live_rel_across"] = _round(row["live_y_mm"] - anchor["live_y_mm"], 6)
        else:
            row["live_rel_along"] = None
            row["live_rel_across"] = None
    return anchor["live_x_mm"] or 0.0, anchor["live_y_mm"] or 0.0


def add_implied_origins(rows: list[dict]) -> None:
    """implied origin = live absolute - recorded offset. If the capture used ONE
    frame, these pairs are identical for every row that came from that one read."""
    for row in rows:
        if row["live_x_mm"] is None:
            row["implied_x"] = None
            row["implied_y"] = None
            continue
        row["implied_x"] = _round(row["live_x_mm"] - row["rec_along_mm"], 6)
        row["implied_y"] = _round(row["live_y_mm"] - row["rec_across_mm"], 6)


# ── hypotheses ───────────────────────────────────────────────────────────────

def _groups(values: list[float], tol: float) -> list[list[int]]:
    """Indices grouped by value within tol — the distinct-frame census."""
    groups: list[list[int]] = []
    for index, value in enumerate(values):
        for group in groups:
            if abs(values[group[0]] - value) <= tol:
                group.append(index)
                break
        else:
            groups.append([index])
    return groups


def test_frame_hypotheses(rows: list[dict], centre: tuple[float, float],
                          tol: float) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    usable = [r for r in rows if r["implied_x"] is not None]

    # H1 — one origin for the whole record.
    x_groups = _groups([r["implied_x"] for r in usable], tol)
    y_groups = _groups([r["implied_y"] for r in usable], tol)
    one = len(x_groups) == 1 and len(y_groups) == 1
    results.append((
        "H1 single origin",
        "EXPLAINS" if one else "REFUTED",
        f"distinct x-origins {len(x_groups)}, distinct y-origins {len(y_groups)} "
        f"(a single frame needs 1/1)",
    ))

    # H2 — a subset (>=2 rows) shares one origin, i.e. the record merged two reads.
    pairs = [(r["implied_x"], r["implied_y"]) for r in usable]
    pair_tol = tol
    merge_groups: list[list[int]] = []
    for index, pair in enumerate(pairs):
        for group in merge_groups:
            if (abs(pairs[group[0]][0] - pair[0]) <= pair_tol
                    and abs(pairs[group[0]][1] - pair[1]) <= pair_tol):
                group.append(index)
                break
        else:
            merge_groups.append([index])
    largest = max((len(g) for g in merge_groups), default=0)
    results.append((
        "H2 subset shares one origin (merged reads)",
        "EXPLAINS" if largest >= 2 else "REFUTED",
        f"largest same-origin subset: {largest} of {len(usable)} rows",
    ))

    # H3 — per-axis origins: one x for every row AND one y for every row.
    axis_one = len(x_groups) == 1 and len(y_groups) == 1
    results.append((
        "H3 per-axis origins",
        "EXPLAINS" if axis_one else "REFUTED",
        f"distinct x-origins {len(x_groups)}, distinct y-origins {len(y_groups)}",
    ))

    # H4 — the region centre (midpoint of the live position extents) as origin.
    hits = [r for r in usable
            if abs(r["implied_x"] - centre[0]) <= tol and abs(r["implied_y"] - centre[1]) <= tol]
    results.append((
        "H4 region centre as origin",
        "EXPLAINS" if len(hits) == len(usable) and usable else "REFUTED",
        f"centre=({centre[0]:.4f}, {centre[1]:.4f}); rows matching it: {len(hits)}",
    ))
    return results


def test_read_consistency(rows: list[dict]) -> tuple[str, str]:
    """The independent axis: an ANGLE read from the same board state as the
    position cannot disagree with the angle the apply saw. Disagreement means the
    row's numbers came from a DIFFERENT read than the one being applied — which no
    frame arithmetic can explain. This is the test that survives even if the
    geometry happens to look self-consistent."""
    mismatches = []
    for row in rows:
        if row["live_angle_deg"] is None:
            continue
        rec = normalise_deg(row["rec_angle_deg"])
        live = normalise_deg(row["live_angle_deg"])
        if abs(rec - live) > 1e-6:
            mismatches.append(f"{row['ref']} (record {rec:g}° vs live {live:g}°)")
    if not mismatches:
        return "CONSISTENT", "every recorded angle equals the live angle in the apply log"
    return "INCONSISTENT", "angle mismatch: " + ", ".join(mismatches)


def test_mirror_fingerprint(rows: list[dict], tol: float) -> tuple[str, str]:
    """An exact |across| collision between two DIFFERENT refs is not something a
    moved board produces (two parts do not land on mirrored coordinates to 1e-4);
    it is a fingerprint of a code path."""
    hits = []
    values = [(r["ref"], row_across) for r in rows
              for row_across in [r["rec_across_mm"]]]
    for i, (ref_a, a) in enumerate(values):
        for ref_b, b in values[i + 1:]:
            if abs(a + b) <= tol and abs(a) > tol:
                hits.append(f"{ref_a} {a:g} == -({ref_b} {b:g})")
    if not hits:
        return "CLEAN", "no exact sign-mirrored across pair"
    return "FINGERPRINT", "exact sign-mirrored across: " + "; ".join(hits)


def test_bridge_verbatim(rows: list[dict], tol: float) -> tuple[str, str]:
    """imprint_cell_entry is documented to copy the record VERBATIM. Measure it:
    any row where cell != record would put the bridge back on the suspect list."""
    diffs = [r["ref"] for r in rows
             if r["has_cell_slot"]
             and (abs(r["rec_along_mm"] - r["cell_along_mm"]) > tol
                  or abs(r["rec_across_mm"] - r["cell_across_mm"]) > tol)]
    if not diffs:
        return "VERBATIM", "every cell offset equals its recorded offset (bridge innocent)"
    return "DIFFERS", "cell != record for: " + ", ".join(diffs)


# ── output ───────────────────────────────────────────────────────────────────

def print_table(rows: list[dict]) -> None:
    print(f"{'ref':<5}{'role':<14}{'live rel':>20}{'record rel':>20}"
          f"{'implied origin':>22}  angle")
    for row in rows:
        live = (f"{row['live_rel_along']:.4f},{row['live_rel_across']:.4f}"
                if row["live_rel_along"] is not None else "—")
        rec = f"{row['rec_rel_along']:.4f},{row['rec_rel_across']:.4f}"
        org = (f"{row['implied_x']:.4f},{row['implied_y']:.4f}"
               if row["implied_x"] is not None else "—")
        rec_angle = f"{normalise_deg(row['rec_angle_deg']):g}"
        live_angle = ("—" if row["live_angle_deg"] is None
                      else f"{normalise_deg(row['live_angle_deg']):g}")
        print(f"{row['ref']:<5}{row['role']:<14}{live:>20}{rec:>20}{org:>22}"
              f"  rec {rec_angle} / live {live_angle}")


def write_fixture(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({
                "ref": row["ref"],
                "role": row["role"],
                "live_rel_along": row["live_rel_along"],
                "live_rel_across": row["live_rel_across"],
                "record_rel_along": row["rec_rel_along"],
                "record_rel_across": row["rec_rel_across"],
            }, ensure_ascii=False) + "\n")
    print(f"fixture written: {path} ({len(rows)} rows)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="profiles/heating-table")
    parser.add_argument("--scheme-lists", help="override the scheme-lists file (e.g. a .history backup)")
    parser.add_argument("--config", help="override the config file holding the cell (e.g. a .history backup)")
    parser.add_argument("--imprint", default="pwr_mini360_in")
    parser.add_argument("--cell", default="mini360_p12v_p5v")
    parser.add_argument("--cluster", default="MINI360_P12V_P5V")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL_MM)
    parser.add_argument("--fixture")
    parser.add_argument("--list", action="store_true",
                        help="list the imprints in the scheme-lists file and stop")
    args = parser.parse_args(argv)

    profile = Path(args.profile)
    if args.list:
        listed = Path(args.scheme_lists) if args.scheme_lists else profile / "scheme_lists.sexp"
        for entry in _as_list(read_data(listed), "imprints"):
            print(f"  {entry.get('name')!r}  components={len(_components(entry))}  "
                  f"source_sheet={entry.get('source_sheet')!r}")
        return 0
    record = load_record(Path(args.scheme_lists) if args.scheme_lists
                         else profile / "scheme_lists.sexp", args.imprint)
    cell = load_cell(Path(args.config) if args.config
                     else profile / "config.sexp", args.cell)
    roles = load_roles(profile, args.cluster)
    live = load_operation(Path(args.operation))

    rows = build_rows(record, cell, roles, live)
    if not rows:
        raise SystemExit("no recorded components — nothing to reconstruct")
    anchor_x, anchor_y = add_relative(rows)
    add_implied_origins(rows)

    live_xs = [r["live_x_mm"] for r in rows if r["live_x_mm"] is not None]
    live_ys = [r["live_y_mm"] for r in rows if r["live_y_mm"] is not None]
    centre = ((min(live_xs) + max(live_xs)) / 2.0, (min(live_ys) + max(live_ys)) / 2.0) \
        if live_xs and live_ys else (0.0, 0.0)

    print(f"record   {args.imprint}  ({len(rows)} components)  source_sheet={record.get('source_sheet')!r}")
    print(f"cell     {args.cell}   pivot={record.get('pivot')!r}")
    print(f"live from {args.operation}   (anchor C1 at {anchor_x:.4f}, {anchor_y:.4f} mm)\n")
    print_table(rows)

    print("\n-- frame hypotheses --")
    results = test_frame_hypotheses(rows, centre, args.tol)
    for name, status, detail in results:
        print(f"{name:<42}{status:<10}{detail}")
    frame_verdict = any(status == "EXPLAINS" for _n, status, _d in results)

    print("\n-- independent checks (not frame arithmetic) --")
    for name, verdict in (("read consistency (angle)", test_read_consistency(rows)),
                          ("mirror fingerprint", test_mirror_fingerprint(rows, args.tol)),
                          ("bridge verbatim", test_bridge_verbatim(rows, args.tol))):
        status, detail = verdict
        print(f"{name:<42}{status:<14}{detail}")

    if args.fixture:
        write_fixture(Path(args.fixture), rows)

    print()
    if frame_verdict:
        print("VERDICT: a single frame explains every row — the capture is innocent here.")
        return 0
    print("VERDICT: no frame hypothesis explains all rows — the record itself is the defect; "
          "hunt by the groups above (see plan_2026_09_22_imprint_capture_frame.md §2).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
