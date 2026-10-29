# kicadstamp/diagnostics/probe_thermal_duplicate_pad_number_2026_10_27.py
"""probe_thermal_duplicate_pad_number_2026_10_27.py — which pad a thermal via array
lands on when the pad NUMBER repeats.

Ч0 of `plan_2026_10_27_thermal_via_duplicate_pad_number` (Denis, 27.10). The live
case on v103: `thermal_via_arrays` has `p3v3_thermal_via_pad` with
`anchor_ref "U2"`, `pad "2"` — and U2 is `Package_TO_SOT_SMD:TO-252-3_TabPin2`,
where «2» names BOTH the little pin and the tab.
`ViaPlanner._plan_thermal_vias` asks `adapter.get_pad_by_number`, which returns the
FIRST pad with that number.

TWO MODES, one measurement:

  * ``live`` — through the board adapter (exactly what the planner sees). Needs the
    PCB editor's API to answer;
  * ``file`` — the same facts read from the ``.kicad_pcb`` FILE (the project's own
    ``cloner.sexp`` helpers), so the measurement does not depend on KiCad being
    open. Pad ``number``/``size``/``layers``/order come from the file verbatim.

Both print:

  1. every footprint whose pad NUMBERS repeat (ref, number, how many, sizes,
     layers, whether the pad carries copper at all) — the population of the rule;
  2. for every LIVE `thermal_via_array`: its anchor, the pads its number names, and
     the grid two ways — as the planner builds it TODAY (the FIRST pad) and as the
     rule proposes (the LARGEST pad by copper area) — with how many points fall
     inside each pad;
  3. (live only) the vias already on the board for that net, matched to both grids.

READ-ONLY: the board is only read (API or file), the profile is COPIED
(``_copy_profile`` absolutizes its relative paths), nothing is written.

USAGE
    .venv/bin/python -m kicadstamp.diagnostics.probe_thermal_duplicate_pad_number_2026_10_27 \
        [--mode auto|live|file] [--profile profiles/3ch-awg-tia-v103/config.sexp] \
        [--board <path.kicad_pcb>] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from kicadstamp.domain.board import Pad
from kicadstamp.domain.geometry import Vector2
from kicadstamp.diagnostics.probe_reread_noise_2026_10_26 import _copy_profile
from kicadstamp.geometry.pad_area import pad_area_of
from kicadstamp.geometry.thermal_grid import compute_thermal_via_grid
from kicadstamp.utils.layers import layer_from_str
from kicadstamp.utils.units import MM

__all__ = ["duplicate_rows", "pad_row", "pick_largest", "read_board_file"]


def _mm(value) -> float:
    return round(value / MM, 4)


# ── the rule's candidate pick (the proposal under measurement) ───────────────

def has_copper_layers(pad) -> bool:
    """False for a PASTE-only pad (`copper_layers == ()`): it carries no copper at
    all. `None` = «the padstack reported no layers» = through (every copper layer)."""
    layers = getattr(pad, "copper_layers", None)
    return not (layers is not None and not layers)


def pad_row(pad) -> dict:
    layers = getattr(pad, "copper_layers", None)
    if layers is None:
        layer_text = "unknown (= all copper)"
    elif not layers:
        layer_text = "() = no copper"
    else:
        layer_text = ", ".join(getattr(layer, "name", str(layer)) for layer in layers)
    size = getattr(pad, "size", None)
    return {
        "number": str(getattr(pad, "number", "")),
        "w_mm": None if size is None else _mm(size.x),
        "h_mm": None if size is None else _mm(size.y),
        "area_mm2": (None if size is None
                     else round(size.x / MM * size.y / MM, 4)),
        "layers": layer_text,
        "has_copper": bool(has_copper_layers(pad)),
        "net": getattr(pad, "net_name", None),
    }


def pick_largest(pads) -> tuple:
    """The rule's pick: the LARGEST pad by copper area; first one on a tie.

    A copper-less pad is not a candidate (a paste-only opening carries no copper at
    all), and a pad without a size cannot be measured."""
    best = None
    best_area = -1.0
    for pad in pads:
        if not has_copper_layers(pad):
            continue
        size = getattr(pad, "size", None)
        if size is None:
            continue
        area = float(size.x) * float(size.y)
        if area > best_area:
            best, best_area = pad, area
    return best, best_area


def _grid(pad, rows: int, cols: int, margin: float) -> list:
    if pad is None:
        return []
    try:
        return compute_thermal_via_grid(pad, rows=rows, cols=cols,
                                        margin_mm=margin, stagger=False)
    except Exception as exc:  # noqa: BLE001 — a grid that cannot be built is a fact
        print(f"    grid failed for pad {getattr(pad, 'number', '?')}: {exc}")
        return []


def _inside(points, pad) -> int:
    if pad is None:
        return 0
    area = pad_area_of(pad)
    if area is None:
        return 0
    return sum(1 for p in points if area.contains(p))


def _report_footprint(title: str, pads, rows: int, cols: int, margin: float) -> dict:
    """One anchor: its pads, and the two grids over them."""
    first = next(iter(pads), None)
    largest, _area = pick_largest(pads)
    entry = {"pads": [pad_row(p) for p in pads]}
    print(f"{title}: pads — {len(pads)}")
    for row in entry["pads"]:
        print(f"      {row['number']!r}: {row['w_mm']}×{row['h_mm']} mm "
              f"({row['area_mm2']} mm²) [{row['layers']}] "
              f"copper={row['has_copper']} net={row['net']}")
    now = _grid(first, rows, cols, margin)
    rule = _grid(largest, rows, cols, margin)
    entry.update({
        "points_now": len(now), "points_rule": len(rule),
        "points_now_inside_first": _inside(now, first),
        "points_now_inside_largest": _inside(now, largest),
        "points_rule_inside_largest": _inside(rule, largest),
        "same_pad": (first is largest),
    })
    print(f"    TODAY  (FIRST pad):   {len(now)} point(s), "
          f"{entry['points_now_inside_first']} inside the first pad, "
          f"{entry['points_now_inside_largest']} inside the largest")
    print(f"    RULE   (LARGEST pad): {len(rule)} point(s), "
          f"{entry['points_rule_inside_largest']} inside the largest")
    if entry["same_pad"]:
        print("    the number is unique for this reference — no change")
    return entry


def _group_by_number(pads) -> dict:
    out: dict = {}
    for pad in pads:
        out.setdefault(str(getattr(pad, "number", "")), []).append(pad)
    return out


def duplicate_rows(pads_by_ref: dict) -> list[dict]:
    """One row per footprint whose pad NUMBER repeats — the population of the rule."""
    rows: list[dict] = []
    for ref, pads in sorted(pads_by_ref.items()):
        for number, group in sorted(_group_by_number(pads).items()):
            if len(group) < 2:
                continue
            rows.append({"ref": ref, "number": number, "count": len(group),
                         "pads": [pad_row(p) for p in group]})
    return rows


# ── file mode: the same facts, straight from the .kicad_pcb ─────────────────

def _synthetic_pad(number, w_mm, h_mm, x_mm, y_mm, angle_deg, layer_names,
                   net=None) -> Pad:
    copper = tuple(layer_from_str(name) for name in layer_names
                   if name.endswith(".Cu"))
    layers = copper if layer_names else None
    return Pad(number=number, net_name=net,
               position=Vector2.from_xy_mm(x_mm, y_mm),
               size=Vector2.from_xy_mm(w_mm, h_mm), angle_rad=angle_deg,
               shape="rect", copper_layers=layers)


def read_board_file(path: Path) -> tuple[dict, dict, list]:
    """(pads_by_ref, footprints_by_ref, vias) from the board FILE.

    Only what the rule reads: the pad NUMBER (the first atom of a ``(pad …)``
    block), its ``(size w h)`` in mm, its ``(layers …)`` and its ``(net …)``.
    Order is the file's. Pad ``(at x y)`` is FOOTPRINT-LOCAL, so the centre is
    turned into board coordinates with the footprint's own ``(at … angle)`` — the
    same Y-down rotation the product uses (`geometry.spoke_layout`), which is what
    makes the grid comparable with the vias in the file."""
    from kicadstamp.cloner.sexp import child, children, load_file, sval
    from kicadstamp.geometry.spoke_layout import rotate_local_offset

    root = load_file(str(path))
    by_ref: dict = {}
    files: dict = {}
    for footprint in children(root, "footprint"):
        ref = None
        for prop in children(footprint, "property"):
            if len(prop) > 2 and str(sval(prop[1])) == "Reference":
                ref = str(sval(prop[2]))
                break
        if ref is None:
            continue
        fp_at = child(footprint, "at")
        fx = fy = fang = 0.0
        if fp_at is not None and len(fp_at) >= 3:
            fx, fy = float(sval(fp_at[1])), float(sval(fp_at[2]))
            if len(fp_at) >= 4 and fp_at[3] is not None:
                fang = float(sval(fp_at[3]))
        pads = []
        for block in children(footprint, "pad"):
            number = str(sval(block[1])) if len(block) > 1 else ""
            size = child(block, "size")
            w = h = 0.0
            if size is not None and len(size) >= 3:
                w, h = float(sval(size[1])), float(sval(size[2]))
            layers = child(block, "layers")
            names = [str(sval(x)) for x in layers[1:]] if layers else []
            at = child(block, "at")
            px = py = pang = 0.0
            if at is not None and len(at) >= 3:
                px, py = float(sval(at[1])), float(sval(at[2]))
                if len(at) >= 4 and at[3] is not None:
                    pang = float(sval(at[3]))
            turned = rotate_local_offset(px, py, fang) if fang else None
            x = fx + (turned.x if turned is not None else px)
            y = fy + (turned.y if turned is not None else py)
            net_name = _net_name(child(block, "net"))
            pads.append(_synthetic_pad(number, w, h, x, y, pang, names, net_name))
        by_ref[ref] = pads
        files[ref] = footprint

    vias = []
    for block in children(root, "via"):
        at = child(block, "at")
        if at is None or len(at) < 3:
            continue
        vias.append({"x_mm": float(sval(at[1])), "y_mm": float(sval(at[2])),
                     "net": _net_name(child(block, "net"))})
    return by_ref, files, vias


def _net_name(node) -> Optional[str]:
    """A ``(net …)`` block in EITHER form the files use: ``(net "GND")`` (a pad /
    via inside a footprint) or ``(net 12 "GND")`` (a board-level entry)."""
    from kicadstamp.cloner.sexp import sval

    if node is None:
        return None
    values = [sval(x) for x in node[1:]]
    for value in values:
        if isinstance(value, str):
            return value
    return None


def _retry(op: str, fn, *, attempts: int = 3, backoff_s: float = 3.0):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — «KiCad is busy» is a retry
            last = exc
            print(f"  {op}: attempt {attempt}/{attempts} failed — {exc}",
                  file=sys.stderr)
            time.sleep(backoff_s)
    raise RuntimeError(f"{op} failed after {attempts} attempts: {last}")


def _arrays(cfg) -> list:
    return [tva for tva in (getattr(cfg, "thermal_via_arrays", None) or ())
            if not getattr(tva, "retired", False)]


def _array_entry(tva) -> dict:
    return {"name": getattr(tva, "name", None),
            "anchor_ref": getattr(tva, "anchor_ref", None),
            "anchor_role": getattr(tva, "anchor_role", None),
            "pad": str(getattr(tva, "pad", "")),
            "rows": getattr(tva, "rows", 4), "cols": getattr(tva, "cols", 4),
            "margin_mm": getattr(tva, "margin_mm", 0.5),
            "net": getattr(tva, "net", None)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("auto", "live", "file"), default="auto")
    parser.add_argument("--profile", default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--board", default=None,
                        help="path to the .kicad_pcb (default: derived from root_sheet)")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    from kicadstamp.config.loader import load_config
    from kicadstamp.config_working_set import set_active_graph_root

    out: dict[str, Any] = {"profile": args.profile, "mode": args.mode}
    with tempfile.TemporaryDirectory(prefix="thermal_dup_") as tmp:
        copy_config = _copy_profile(Path(args.profile).resolve(), Path(tmp))
        set_active_graph_root(copy_config)
        cfg, ctx = load_config(str(copy_config))
        arrays = _arrays(cfg)
        out["arrays"] = [_array_entry(tva) for tva in arrays]

        board_path = (Path(args.board) if args.board
                      else _board_beside(copy_config))
        pads_by_ref: Optional[dict] = None
        file_vias: list = []
        live_adapter = None
        if args.mode in ("auto", "live"):
            try:
                live_adapter = _connect_board(str(copy_config), str(args.profile))
                fps = _retry("get_footprints", live_adapter.get_footprints)
                pads_by_ref = {getattr(fp, "ref", "?"):
                               live_adapter.get_footprint_pads(fp) for fp in fps}
                # WHICH board the editor has open — the live mode reads the OPEN
                # document, not the profile's file, and they may differ (measured
                # 28.10: the editor had a board whose U2 «2» is 2.0×1.5 + 2.0×3.8,
                # while the profile's file has 2.2×1.2 + 6.4×5.8).
                try:
                    board_name = live_adapter.get_board_filename()
                except Exception:  # noqa: BLE001 — a nice-to-have, not a fact
                    board_name = None
                out["board_filename"] = board_name
                out["source"] = f"live board (adapter), open document {board_name!r}"
            except Exception as exc:  # noqa: BLE001 — the file is the fallback
                print(f"live mode unavailable: {exc}", file=sys.stderr)
                if args.mode == "live":
                    return 2
        if pads_by_ref is None:
            if board_path is None or not Path(board_path).exists():
                print(f"no board file found (looked at {board_path}) — give --board")
                return 2
            pads_by_ref, _raw, file_vias = read_board_file(Path(board_path))
            out["source"] = f"board file {board_path}"
        print(f"source: {out['source']}")

        # ── 1. the population ──────────────────────────────────────────────
        rows = duplicate_rows(pads_by_ref)
        out["duplicate_number_footprints"] = rows
        print(f"\nfootprints with REPEATED pad numbers: {len(rows)}")
        for row in rows:
            sizes = ", ".join(f"{p['number']!r}:{p['w_mm']}×{p['h_mm']}mm"
                              for p in row["pads"])
            print(f"  {row['ref']}: number {row['number']!r} × {row['count']}"
                  f" — {sizes}")

        # ── 2. every live thermal array ────────────────────────────────────
        out["thermal_arrays"] = []
        print(f"\nlive thermal_via_arrays: {len(arrays)}")
        for tva in arrays:
            entry = _array_entry(tva)
            ref = entry["anchor_ref"]
            print(f"\n  {entry['name']} → ref {ref!r}, pad {entry['pad']!r}")
            if ref is None:
                print("    (anchor is a ROLE / point — this probe resolves refs only)")
                entry["resolved"] = False
                out["thermal_arrays"].append(entry)
                continue
            pads = pads_by_ref.get(ref)
            if pads is None:
                print(f"    footprint {ref!r} is not on the board")
                entry["resolved"] = False
                out["thermal_arrays"].append(entry)
                continue
            named = [p for p in pads if str(getattr(p, "number", "")) == entry["pad"]]
            entry["resolved"] = True
            entry.update(_report_footprint(f"    {ref}", named, entry["rows"],
                                           entry["cols"], entry["margin_mm"]))
            if live_adapter is not None and named:
                entry["board_vias"] = _board_vias(live_adapter, tva, named)
            elif file_vias and named:
                entry["board_vias"] = _board_vias_file(file_vias, tva, named)
            out["thermal_arrays"].append(entry)

        duplicate_refs = {row["ref"] for row in rows}
        out["arrays_on_duplicate_numbers"] = sorted(
            e["anchor_ref"] for e in out["thermal_arrays"]
            if e.get("resolved") and e.get("anchor_ref") in duplicate_refs)
        print(f"\narrays whose anchor has a REPEATED pad number: "
              f"{out['arrays_on_duplicate_numbers']}")

        if live_adapter is not None:
            try:
                live_adapter.close()
            except Exception:  # noqa: BLE001 — a probe must not fail on close
                pass

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        print(f"json: {args.json}")
    return 0


def _board_beside(config_path: Path) -> Optional[Path]:
    """The .kicad_pcb named by the profile's ``(root_sheet …)``."""
    text = ""
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'\(root_sheet\s+"([^"]+)"', text)
    if not match:
        return None
    root = Path(match.group(1))
    if not root.is_absolute():
        root = (config_path.parent / root).resolve()
    return root.with_suffix(".kicad_pcb")


def _connect_board(config_path: str, profile: str):
    """A read-only adapter on the live board (raises when KiCad cannot answer)."""
    from kicadstamp.adapter_factory import create_board_adapter
    from kicadstamp.constants import DEFAULT_TIMEOUT_MS

    adapter = create_board_adapter(timeout_ms=DEFAULT_TIMEOUT_MS,
                                   config_path=config_path)
    _retry("refresh_board", adapter.refresh_board)
    return adapter


def _vias_inside(vias, net, pad) -> int:
    """How many vias of ``net`` lie INSIDE ``pad``'s own copper area.

    «Inside the pad» — not «on the ideal grid»: `_plan_thermal_vias` passes every
    point through `find_free_point`, so a planned via may be shifted off the ideal
    grid and still sit in the pad. The pad is the question."""
    if pad is None:
        return 0
    area = pad_area_of(pad)
    if area is None:
        return 0
    count = 0
    for via in vias:
        if net and str(via.get("net") or "") != str(net):
            continue
        point = Vector2.from_xy_mm(via["x_mm"], via["y_mm"])
        if area.contains(point):
            count += 1
    return count


def _candidate_net(pad, tva):
    """The net to look for: the PAD's own (`net_name`), else the array's."""
    return getattr(pad, "net_name", None) or getattr(tva, "net", None)


def _board_vias_file(vias, tva, named) -> dict:
    """Which candidate pad the vias on the board (FILE) actually sit in."""
    first = next(iter(named), None)
    largest, _area = pick_largest(named)
    net_first = _candidate_net(first, tva)
    net_largest = _candidate_net(largest, tva)
    in_first = _vias_inside(vias, net_first, first)
    in_largest = _vias_inside(vias, net_largest, largest)
    print(f"    on the board FILE: {in_first} via(s) inside the FIRST pad "
          f"(net {net_first!r}), {in_largest} inside the LARGEST "
          f"(net {net_largest!r})")
    return {"net_first": net_first, "net_largest": net_largest,
            "inside_first": in_first, "inside_largest": in_largest}


def _board_vias(adapter, tva, named) -> dict:
    """The same question through the live adapter."""
    first = next(iter(named), None)
    largest, _area = pick_largest(named)
    net_first = _candidate_net(first, tva)
    net_largest = _candidate_net(largest, tva)
    vias = [{"x_mm": via.position.x / MM, "y_mm": via.position.y / MM,
             "net": getattr(via, "net_name", None)} for via in adapter.get_vias()]
    in_first = _vias_inside(vias, net_first, first)
    in_largest = _vias_inside(vias, net_largest, largest)
    print(f"    on the board: {in_first} via(s) inside the FIRST pad "
          f"(net {net_first!r}), {in_largest} inside the LARGEST "
          f"(net {net_largest!r})")
    return {"net_first": net_first, "net_largest": net_largest,
            "inside_first": in_first, "inside_largest": in_largest}


if __name__ == "__main__":
    sys.exit(main())
