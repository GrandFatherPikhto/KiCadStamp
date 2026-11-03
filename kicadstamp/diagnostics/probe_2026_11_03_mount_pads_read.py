"""probe_2026_11_03_mount_pads_read.py — Т1 of plan_2026_11_03_add_mounts_table.

Denis, 03.11: «Add mounts…» is to add SEVERAL mounts of one part at once, from a
table of that part's pads. Before the table is designed, three things must come
from the LIVE board rather than from a formula on paper:

  1. how many ROWS the table gets after Д3 (copper pads only, a repeated pad
     number — TabPin — is ONE row), and how many distinct NETS that is (what the
     «Net contains…» filter works on);
  2. what ONE «Read pads» costs, split into its parts — the identity resolve
     (`ComponentResolver.resolve_anchor_fp`, which the redraw itself uses) and
     `adapter.get_footprint_pads` — because the door sign of the real command
     must name the real price;
  3. which pads ALREADY carry a mount in this profile's trees (Д6 «Уже есть»),
     so the dry picture is known before the dialog exists.

HOW IT MEASURES. Every number is wall time around ONE call, repeated `--repeat`
times; min/median/max are printed because a single IPC answer is noisy. The
resolve is measured TWICE — without the connection's snapshot (the full-board
sweep `_role_candidates` falls back to) and with it (what the GUI will hand
over) — because the difference is exactly what the door sign has to describe.
`adapter.get_footprints()` and `adapter.get_selected_items()` are timed on their
own so the resolve's cost can be READ, not guessed. `get_footprint_pads` is timed
on a cold and on a warm call: its docstring says it never goes to IPC at all
(pads already sit in `footprint.definition.items`), and a probe that repeats the
claim must show it.

WHAT IT READS. The profile (a COPY — `load_config` upgrades an old on-disk format
and rewrites the file, so the copy is not a courtesy; the run prints the file's
size/mtime before and after and says whether they changed) and the LIVE board,
read-only: no write to the board, no write to the profile. The board opened in
KiCad is PRINTED (get_board_filename/get_board_project) — a probe that trusted
"the right board is open" would report numbers about some other board.

WHAT IT CANNOT ANSWER. Nothing here says whether a mount ANCHOR resolves
uniquely: the profile has no mount nodes on this part yet. The resolve below asks
the SAME question the anchor will ask (role + sheet + cluster), so an ambiguity
would show up as a raising resolve, not as a number.

Usage:
    python -m kicadstamp.diagnostics.probe_2026_11_03_mount_pads_read \\
        --config profiles/HiPiMS-Controller-v099-copy/HiPiMS-Controller-v099.sexp \\
        --ref IC1 [--repeat 3] [--show 24] [--json PATH]

Defaults: --config profiles/HiPiMS-Controller-v099/HiPiMS-Controller-v099.sexp,
--ref IC1, --repeat 3, --show 24.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.adapter_factory import create_board_adapter          # noqa: E402
from kicadstamp.config import load_config                           # noqa: E402
from kicadstamp.constants import (CLUSTER_FIELD_NAME, DEFAULT_TIMEOUT_MS,
                                  ROLE_FIELD_NAME)                   # noqa: E402
from kicadstamp.domain.board import pad_has_copper                   # noqa: E402
from kicadstamp.explore import Board                                # noqa: E402
from kicadstamp.placement.services.component_resolver import (       # noqa: E402
    ComponentResolver)
from kicadstamp.sheet_names import resolve_sheet_path_names, sheet_of_names  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = (_ROOT / "profiles" / "HiPiMS-Controller-v099"
                   / "HiPiMS-Controller-v099.sexp")
_DEFAULT_REF = "IC1"

__all__ = ["pad_stats", "read_pads_once", "ref_address"]


# ── timing ─────────────────────────────────────────────────────────────────

def _ms(fn, *args, **kwargs):
    """(result, milliseconds) of ONE call — the only timing this probe trusts."""
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - started) * 1000.0


def _summary(label: str, samples: list[float]) -> str:
    """One line: min/median/max of the samples, in ms — noise stated, not hidden."""
    if not samples:
        return f"{label:38s} — (no samples)"
    return (f"{label:38s} min {min(samples):7.1f} ms   "
            f"median {statistics.median(samples):7.1f} ms   "
            f"max {max(samples):7.1f} ms   (n={len(samples)})")


# ── the address, exactly as Д2 takes it ────────────────────────────────────

def ref_address(adapter, sheet_names, ref: str) -> dict:
    """Ref -> (role, cluster, sheet) — the SAME two owners Д2 will call: the
    adapter for the FIELD values, `sheet_names.sheet_of_names` for the sheet.

    No classification of its own: a footprint with no Role is REPORTED as such
    (the dialog will refuse it in words), never guessed around."""
    fp = adapter.get_footprint(ref)
    if fp is None:
        return {"ref": ref, "found": False}
    role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
    cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
    names = resolve_sheet_path_names(fp, sheet_names or {})
    return {"ref": ref, "found": True, "role": role, "cluster": cluster,
            "sheet": sheet_of_names(names), "sheet_chain": list(names),
            "angle_deg": getattr(fp, "angle_deg", None)}


# ── what the table would show (Д3), and the pads it hides ─────────────────

def pad_stats(pads: list) -> dict:
    """Counts AND the honest disagreements of the Д3 rule.

    Д3 keeps ONE row per pad number. That rule can hide a real difference: two
    pads of the same number (TabPin, a thermal tab, a split pad) may sit on
    DIFFERENT nets, and one row can only show one. The counter for that exists
    here so the dialog's own rule is chosen on a measured fact, not on a guess."""
    copper = [p for p in pads if pad_has_copper(p)]
    paste_only = [p for p in pads if not pad_has_copper(p)]
    unknown_layers = [p for p in pads if getattr(p, "copper_layers", None) is None]
    by_number: dict[str, list] = {}
    for pad in copper:
        by_number.setdefault(str(pad.number), []).append(pad)
    nets_by_number = {number: {str(p.net_name) for p in group if p.net_name}
                      for number, group in by_number.items()}
    conflicting = {number: sorted(nets)
                   for number, nets in nets_by_number.items() if len(nets) > 1}
    all_nets = sorted({str(p.net_name) for p in copper if p.net_name})
    return {
        "pads_total": len(pads),
        "pads_copper": len(copper),
        "pads_paste_only": len(paste_only),
        "pads_layers_unknown": len(unknown_layers),
        "rows_by_number": len(by_number),
        "numbers_repeated": {number: len(group) for number, group in by_number.items()
                             if len(group) > 1},
        "numbers_with_conflicting_nets": conflicting,
        "pads_without_net": sum(1 for p in copper if not p.net_name),
        "nets": all_nets,
        "nets_total": len(all_nets),
        "rows": [{"pad": number,
                  "net": (group[0].net_name or ""),
                  "pads_with_this_number": len(group)}
                 for number, group in by_number.items()],
    }


def read_pads_once(adapter, cfg, sheet_names, address: dict, *, snapshot=None):
    """ONE «Read pads» as the command will do it: resolve the footprint the
    redraw would resolve, then take ITS pads. Returns (fp, pads, ms_resolve,
    ms_pads) — the two halves timed apart, which is the point of the probe."""
    resolver = ComponentResolver(adapter, cfg, sheet_names, snapshot=snapshot)
    fp, ms_resolve = _ms(resolver.resolve_anchor_fp,
                         None, address["role"], address["sheet"],
                         address["cluster"], label=address["role"])
    pads, ms_pads = _ms(adapter.get_footprint_pads, fp)
    return fp, pads, ms_resolve, ms_pads


# ── mounts already in the profile's trees (Д6 «Уже есть», dry) ────────────

def mount_inventory(trees) -> dict:
    """Every kind "mount" node of every tree: how many, and which (ref, role,
    pad) pairs — the pads Д6 would already call «уже есть» if a new node were
    added with a zero offset."""
    out: list[dict] = []

    def walk(nodes, tree_name: str) -> None:
        for node in nodes:
            if getattr(node, "kind", None) == "mount":
                anchor = getattr(node, "anchor", None)
                out.append({
                    "tree": tree_name, "ref": node.ref,
                    "role": getattr(anchor, "role", None),
                    "cluster": getattr(anchor, "anchor_cluster", None),
                    "sheet": getattr(anchor, "anchor_sheet", None),
                    "pad": getattr(anchor, "anchor_pad", None),
                    "xy": node.xy, "polar": node.polar,
                    "rotation": node.rotation,
                })
            walk(getattr(node, "children", []), tree_name)

    for tree in trees or []:
        walk(tree.nodes, tree.name)
    return {"count": len(out), "nodes": out}


# ── the run ───────────────────────────────────────────────────────────────

def _identity(adapter) -> dict:
    """WHICH board answered — read, never assumed (rule 31)."""
    try:
        filename = adapter.get_board_filename()
    except Exception as exc:                                   # noqa: BLE001
        filename = f"<{type(exc).__name__}: {exc}>"
    try:
        project = adapter.get_board_project()
    except Exception as exc:                                   # noqa: BLE001
        project = f"<{type(exc).__name__}: {exc}>"
    try:
        version = adapter.get_version()
    except Exception as exc:                                   # noqa: BLE001
        version = f"<{type(exc).__name__}: {exc}>"
    return {"board_filename": filename, "board_project": project,
            "kicad_version": version}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG),
                        help="profile config (.sexp) — a COPY is expected (rule 28)")
    parser.add_argument("--ref", default=_DEFAULT_REF,
                        help="the footprint the table is read for (default IC1)")
    parser.add_argument("--repeat", type=int, default=3,
                        help="how many times every timed call is repeated")
    parser.add_argument("--show", type=int, default=24,
                        help="how many table rows to print")
    parser.add_argument("--json", metavar="PATH", help="also write the numbers there")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"no such config: {config_path}")
        return 2
    report: dict = {
        "probe": "probe_2026_11_03_mount_pads_read.py",
        "when": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": {"system": platform.system(), "release": platform.release(),
                    "machine": platform.machine(), "node": platform.node(),
                    "python": platform.python_version()},
        "config": str(config_path),
    }

    before = (config_path.stat().st_size, config_path.stat().st_mtime)
    print(f"machine        : {report['machine']['system']} "
          f"{report['machine']['release']} {report['machine']['machine']} "
          f"({report['machine']['node']})")
    print(f"python         : {report['machine']['python']}")
    print(f"config         : {config_path}")
    print(f"config bytes   : {before[0]}, mtime "
          f"{_dt.datetime.fromtimestamp(before[1]).isoformat(timespec='seconds')}")

    cfg, ctx = load_config(str(config_path))
    after = (config_path.stat().st_size, config_path.stat().st_mtime)
    report["config_touched_by_load"] = before != after
    print(f"load_config wrote the file: {'YES' if before != after else 'no'}"
          f"{'' if before == after else '  ← the copy was necessary (rule 28)'}")
    sheet_names = dict(ctx.sheet_names or {})
    print(f"root_sheet     : {ctx.root_sheet}")
    print(f"sheet_names    : {len(sheet_names)} name(s); "
          f"trees: {len(getattr(cfg, 'trees', []) or [])}")
    report["sheet_names"] = len(sheet_names)
    report["root_sheet"] = ctx.root_sheet

    mounts = mount_inventory(getattr(cfg, "trees", []) or [])
    report["mounts_in_config"] = mounts
    print(f"mount nodes in the profile's trees: {mounts['count']}")

    adapter = create_board_adapter(DEFAULT_TIMEOUT_MS, config_path=str(config_path))
    report["adapter"] = type(adapter).__name__
    print(f"adapter        : {type(adapter).__name__}"
          f"{'  (override store layered)' if type(adapter).__name__ != 'KiCadBoardAdapter' else '  (bare)'}")

    _, ms_refresh = _ms(adapter.refresh_board)
    print(_summary("refresh_board()", [ms_refresh]))
    identity = _identity(adapter)
    report["board"] = identity
    print(f"live board     : {identity['board_filename']!r}")
    print(f"board project  : {identity['board_project']!r}")
    print(f"KiCad version  : {identity['kicad_version']!r}")

    # ── the address, as Д2 takes it ───────────────────────────────────────
    address, ms_address = _ms(ref_address, adapter, sheet_names, args.ref)
    report["address"] = address
    report["ms_address"] = ms_address
    if not address.get("found"):
        print(f"\n{args.ref}: not on the live board — is the right board open?")
        if args.json:
            Path(args.json).write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
        return 1
    print(f"\naddress of {args.ref}: role={address['role']!r} "
          f"cluster={address['cluster']!r} sheet={address['sheet']!r} "
          f"(chain {address['sheet_chain']})   [{ms_address:.1f} ms]")
    if not address["role"]:
        print("  → no Role field on this footprint: Д2 refuses in words; nothing "
              "below can resolve by role")

    # ── the two halves of the resolve's cost, timed alone ─────────────────
    sweeps, selections, footprint_reads = [], [], []
    for _ in range(max(1, args.repeat)):
        fps, ms_sweep = _ms(adapter.get_footprints)
        sweeps.append(ms_sweep)
        _, ms_sel = _ms(adapter.get_selected_items)
        selections.append(ms_sel)
        _, ms_fp = _ms(adapter.get_footprint, args.ref)
        footprint_reads.append(ms_fp)
    report["footprints_on_board"] = len(fps)
    report["ms_get_footprints"] = sweeps
    report["ms_get_selected_items"] = selections
    report["ms_get_footprint"] = footprint_reads
    print(f"\nfootprints on the board: {len(fps)}")
    print(_summary("adapter.get_footprints()", sweeps))
    print(_summary("adapter.get_selected_items()", selections))
    print(_summary(f"adapter.get_footprint({args.ref!r})", footprint_reads))

    # ── the snapshot the GUI would hand over ──────────────────────────────
    board, ms_board = _ms(Board, adapter, sheet_names)
    _, ms_refresh_board = _ms(board.refresh)
    snapshot, ms_select = _ms(board.select)
    report["snapshot"] = {"size": len(snapshot), "ms_board_refresh": ms_refresh_board,
                          "ms_select": ms_select, "ms_construct": ms_board}
    print(_summary("snapshot: Board.refresh()", [ms_refresh_board]))
    print(_summary("snapshot: Board.select()", [ms_select]))
    print(f"snapshot size  : {len(snapshot)} Selected row(s)")

    # ── «Read pads», with and without the snapshot ────────────────────────
    plans = {"sweep (no snapshot)": None, "snapshot": snapshot}
    report["resolves"] = {}
    for label, snap in plans.items():
        resolves, pad_reads = [], []
        fp = pads = None
        for _ in range(max(1, args.repeat)):
            fp, pads, ms_resolve, ms_pads = read_pads_once(
                adapter, cfg, sheet_names, address, snapshot=snap)
            resolves.append(ms_resolve)
            pad_reads.append(ms_pads)
        report["resolves"][label] = {"ms_resolve": resolves, "ms_pads": pad_reads,
                                     "resolved_ref": getattr(fp, "ref", None)}
        print(f"\n[{label}]  resolved to {getattr(fp, 'ref', None)!r}")
        print(_summary("  resolve_anchor_fp()", resolves))
        print(_summary("  get_footprint_pads()", pad_reads))

    # the SAME question the redraw asks: is the object the pads come from the one
    # `get_footprint(ref)` hands out, or a fresh object every time?
    print(f"  pads were read off an object identical to get_footprint({args.ref!r}): "
          f"{fp is adapter.get_footprint(args.ref)}")

    # ── what the table would show ─────────────────────────────────────────
    stats = pad_stats(pads)
    report["pads"] = stats
    print(f"\npads of {getattr(fp, 'ref', args.ref)}: "
          f"{stats['pads_total']} total, {stats['pads_copper']} copper, "
          f"{stats['pads_paste_only']} paste-only (not a pad), "
          f"{stats['pads_layers_unknown']} with unknown layers")
    print(f"rows after Д3  : {stats['rows_by_number']} (unique pad numbers)")
    print(f"nets           : {stats['nets_total']} distinct; "
          f"{stats['pads_without_net']} copper pad(s) with no net")
    if stats["numbers_repeated"]:
        print(f"repeated numbers (ONE row each): {stats['numbers_repeated']}")
    if stats["numbers_with_conflicting_nets"]:
        print("NUMBERS ON DIFFERENT NETS — one row can show only one of them:")
        for number, nets in stats["numbers_with_conflicting_nets"].items():
            print(f"    pad {number}: {nets}")
    print(f"\nfirst {min(args.show, len(stats['rows']))} of "
          f"{len(stats['rows'])} table row(s)  [☐ | Pad | Net | Ref | «Уже есть»]:")
    for row in stats["rows"][:args.show]:
        count = row["pads_with_this_number"]
        note = "" if count == 1 else f"({count} pads)"
        print(f"    [ ]  {row['pad']:>5s}  {row['net'] or '-':<34s} -   {note}")
    if len(stats["rows"]) > args.show:
        print(f"    … {len(stats['rows']) - args.show} more")
    numbers = [row["pad"] for row in stats["rows"]]
    odd = [number for number in numbers if not number.isdigit()]
    print(f"\npad numbers: {len(numbers)} — {'all numeric' if not odd else f'NOT numeric: {odd}'}")
    print(f"    {', '.join(numbers)}")
    print(f"\ndistinct nets ({stats['nets_total']}):")
    for net in stats["nets"]:
        print(f"    {net}")

    # ── a COLD adapter: what the WORKER actually pays ─────────────────────
    #
    # The GUI hands the worker the CONNECTION, not a warm adapter (door rule:
    # `start_long_op` + `connection.board` read on the worker thread), so every
    # «Read pads» starts on a fresh adapter whose FIRST IPC call is the whole
    # board read. Measured here on purpose: a number taken from the warm
    # adapter above would understate the command's real price by that read.
    #
    # MEASURED WHILE WRITING THIS: a fresh adapter has `_board is None` until
    # `refresh_board()` loads the document, and `get_selected_items()` —
    # the FIRST thing the resolve calls — dies on it with
    # `AttributeError: 'NoneType' object has no attribute 'document'`. So the
    # worker owes that load before any read, and its price is part of «Read
    # pads», not a one-off on connect.
    cold_totals, cold_resolves, cold_pads, cold_loads = [], [], [], []
    fp_cold = pads_cold = None
    for _ in range(max(1, args.repeat)):
        fresh = create_board_adapter(DEFAULT_TIMEOUT_MS, config_path=str(config_path))
        started = time.perf_counter()
        _, ms_load = _ms(fresh.refresh_board)
        fp_cold, pads_cold, ms_resolve, ms_pads = read_pads_once(
            fresh, cfg, sheet_names, address)
        cold_totals.append((time.perf_counter() - started) * 1000.0)
        cold_loads.append(ms_load)
        cold_resolves.append(ms_resolve)
        cold_pads.append(ms_pads)
    report["cold"] = {"ms_read_pads_total": cold_totals, "ms_load_board": cold_loads,
                      "ms_resolve": cold_resolves, "ms_pads": cold_pads}
    print(f"\n[COLD adapter — a fresh one per call, like the worker's]  "
          f"resolved to {getattr(fp_cold, 'ref', None)!r}")
    print(_summary("  WHOLE «Read pads»", cold_totals))
    print(_summary("  of it: refresh_board() (the load)", cold_loads))
    print(_summary("  of it: resolve_anchor_fp()", cold_resolves))
    print(_summary("  of it: get_footprint_pads()", cold_pads))
    cold_stats = pad_stats(pads_cold)
    report["cold"]["rows_agree_with_warm"] = (
        cold_stats["rows_by_number"] == stats["rows_by_number"]
        and [r["pad"] for r in cold_stats["rows"]] == [r["pad"] for r in stats["rows"]])
    print(f"  rows identical to the warm run: {report['cold']['rows_agree_with_warm']}")

    # ── Д6 dry: pads that already carry a mount in THIS profile ───────────
    occupied = [m for m in mounts["nodes"]
                if m["role"] == address["role"]
                and (m["cluster"] or None) == (address["cluster"] or None)
                and (m["pad"] or None) is not None]
    report["mounts_on_this_role"] = occupied
    print(f"\nmounts already on role {address['role']!r}: {len(occupied)}")
    for node in occupied:
        print(f"    {node['tree']}/{node['ref']}: pad {node['pad']!r}, "
              f"xy {node['xy']}, polar {node['polar']}, rotation {node['rotation']}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nnumbers written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
