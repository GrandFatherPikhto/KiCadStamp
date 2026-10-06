# kicadstamp/diagnostics/claude_probe_explode_2026_10_05.py
"""LIVE probe for design_2026_10_05_explode_cluster.md (Р0/З2), Claude, 2026-10-05.

Denis's protocol (05.10): "1. the script moves every cluster of the selection
(as read) far aside; 2. I say 'moved' and select DAC_BUF; 3. you re-read it and
select what came out; I say right or wrong."

    explode <config> --cell dac_buf [--dx MM]   WRITES the board (one commit)
    read    <config> --cell dac_buf             read-only: plan + select result
    restore <journal.json>                      WRITES the board (one commit)

explode — the selection's foreign cluster instances (every (Cluster, sheet)
group except the cell's chosen one) move rigidly by +dx along X: their selected
footprints and the selected copper the registry records for a NON-own,
NON-net_traces record. Inter-cluster (net_traces) and unregistered copper STAY.
The journal (absolute original positions, by UUID) is written BEFORE the move,
to ~/.local/state/kicadstamp/explode/. Then the board is re-read and the probe
reports: moved items on their target (nm), UUIDs unchanged, and every NON-moved
track/via whose position changed (did KiCad drag copper after a footprint?).

read — the cell read exactly as CellDock's "Update from selection" builds it
(narrow_mixed_selection + build_refresh_plan + apply_live_copper_rule), with
the explode-mode difference: selected copper the registry gives to a
net_traces record is TRANSFERRED (read), not subtracted. Nothing is written to
the config; the board selection is set to what the read would take.

restore — every journal item back to its ABSOLUTE original position; then a
re-read checks 0 nm and names the items moved by hand / gone.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.cell_geometry_refresh import build_refresh_plan
from kicadstamp.config.loader import load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via, unwrap
from kicadstamp.registry import load_registry_entries, record_key_part
from kicadstamp.cell_instance import resolve_context_footprints
from kicadstamp.selection_narrowing import (
    FootprintInfo, apply_live_copper_rule, cell_clusters, cell_record_addresses,
    choose_instance, group_selection, is_own_key)
from kicadstamp.sheet_names import resolve_sheet_path_names

from gui.mixed_selection import narrow_mixed_selection

from kipy.geometry import Vector2 as KV

STATE = Path(os.path.expanduser("~/.local/state/kicadstamp/explode"))
NM = 1_000_000


def _adapter(config_path):
    a = create_board_adapter(config_path=config_path)
    a.refresh_board()
    return a


def _snap(item):
    if isinstance(item, Footprint):
        return {"kind": "fp", "ref": item.ref, "x": item.position.x, "y": item.position.y,
                "angle": item.angle_deg}
    if isinstance(item, Via):
        return {"kind": "via", "x": item.position.x, "y": item.position.y}
    return {"kind": "track", "sx": item.start.x, "sy": item.start.y,
            "ex": item.end.x, "ey": item.end.y}


def _all_copper(adapter):
    return {i.uuid: i for i in list(adapter.get_tracks()) + list(adapter.get_vias())}


def _instance(adapter, cfg, ctx, cell, footprints):
    sheet_names = dict(getattr(ctx, "sheet_names", {}) or {})
    clusters = cell_clusters(cfg, cell)
    infos = [FootprintInfo(item=fp, role=adapter.get_field_value(fp, ROLE_FIELD_NAME),
                           cluster=adapter.get_field_value(fp, CLUSTER_FIELD_NAME),
                           sheet=tuple(resolve_sheet_path_names(fp, sheet_names) or ()))
             for fp in footprints]
    groups = group_selection(infos, getattr(cfg, "entities", ()) or ())
    roles = {c.get("role") for c in _cell_entry(cfg, cell)["components"]}
    choice = choose_instance(groups, roles, clusters)
    return groups, choice, sheet_names


def _cell_entry(cfg, cell, config_path=None):
    """The cell's raw dict exactly as CellDock loads it (load_entry)."""
    from gui.docks.rename import collect_section_entries
    return collect_section_entries(Path(config_path or _CONFIG[0]), "cells")[cell]


_CONFIG = [None]


def _move(item, dx):
    k = unwrap(item)
    if isinstance(item, Footprint):
        item.position = type(item.position).from_xy(item.position.x + dx, item.position.y)
    elif isinstance(item, Via):
        k.position = KV.from_xy(item.position.x + dx, item.position.y)
    else:
        k.start = KV.from_xy(item.start.x + dx, item.start.y)
        k.end = KV.from_xy(item.end.x + dx, item.end.y)


def _set(item, rec):
    k = unwrap(item)
    if isinstance(item, Footprint):
        item.position = type(item.position).from_xy(rec["x"], rec["y"])
        item.angle_deg = rec["angle"]
    elif isinstance(item, Via):
        k.position = KV.from_xy(rec["x"], rec["y"])
    else:
        k.start = KV.from_xy(rec["sx"], rec["sy"])
        k.end = KV.from_xy(rec["ex"], rec["ey"])


def _commit(adapter, items, label):
    c = adapter.begin_commit()
    try:
        adapter.update_items(items)
    except Exception:
        adapter.drop_commit(c)
        raise
    adapter.push_commit(c, label)


def _diff(a, b):
    keys = [k for k in a if k not in ("kind", "ref", "moved")]
    return max(abs(a[k] - b[k]) for k in keys)


def cmd_explode(config_path, cell, dx_mm):
    cfg, ctx = load_config(config_path)
    adapter = _adapter(config_path)
    try:
        sel = adapter.get_selected_items()
        fps = [i for i in sel if isinstance(i, Footprint)]
        copper = [i for i in sel if isinstance(i, (Track, Via))]
        groups, choice, sheet_names = _instance(adapter, cfg, ctx, cell, fps)
        if choice.chosen_key is None:
            raise SystemExit(f"no single instance of {cell!r} in the selection: "
                             f"{[k for k, _m in choice.candidate_groups]}")
        cluster, sheet = choice.chosen_key
        inst = resolve_context_footprints(adapter, adapter.get_footprints(), cluster, sheet,
                                          sheet_names)
        inst_refs = frozenset(f.ref for f in inst)
        foreign_fps = [m.item for key, members in groups.items() if key != choice.chosen_key
                       for m in members]
        _v, _t, owner = load_registry_entries(config_path, cfg)
        cell_obj = cfg.cells[cell]
        identity = record_key_part(cell, getattr(cell_obj, "uuid", None))
        own_addr = cell_record_addresses(cfg, cell)
        move_cu, stay = [], {"net_traces": 0, "own": 0, "unregistered": 0}
        for it in copper:
            key = owner.get(it.uuid)
            if key is None:
                stay["unregistered"] += 1
            elif key.startswith("net:"):
                stay["net_traces"] += 1
            elif is_own_key(key, identity, own_addr, (cluster, sheet), inst_refs):
                stay["own"] += 1
            else:
                move_cu.append(it)
        moving = foreign_fps + move_cu
        if not moving:
            raise SystemExit("nothing foreign in the selection — nothing to move")
        xs = [f.position.x for f in fps]
        dx = int((dx_mm if dx_mm is not None else (max(xs) - min(xs)) / NM + 30.0) * NM)
        before_all = {u: _snap(i) for u, i in _all_copper(adapter).items()}
        journal = {"board": str(getattr(getattr(adapter, "_board", None), "name", "")),
                   "config": str(Path(config_path).resolve()), "cell": cell,
                   "instance": [cluster, sheet], "dx_nm": dx, "time": time.strftime("%F %T"),
                   "items": {i.uuid: {**_snap(i), "moved": None} for i in moving}}
        for i in moving:
            s = _snap(i)
            journal["items"][i.uuid]["moved"] = {k: (v + dx if k in ("x", "sx", "ex") else v)
                                                 for k, v in s.items() if k not in ("kind", "ref")}
        STATE.mkdir(parents=True, exist_ok=True)
        jpath = STATE / f"probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
        tmp = jpath.with_suffix(".tmp")
        tmp.write_text(json.dumps(journal, indent=1), encoding="utf-8")
        os.replace(tmp, jpath)
        print(f"journal: {jpath}")
        print(f"instance: {cluster} on {sheet} ({len(inst)} footprints on the board)")
        print(f"moving +{dx / NM:.1f} mm X: {len(foreign_fps)} footprints "
              f"({', '.join(sorted({str(k[0]) + '/' + str(k[1]) for k in groups if k != choice.chosen_key}))}), "
              f"{len(move_cu)} copper items")
        print(f"staying in the selection: {stay}")
        for i in moving:
            _move(i, dx)
        _commit(adapter, moving, "KiCadStamp probe: explode")
        adapter.refresh_board()
        _check(adapter, journal, before_all, "moved")
    finally:
        adapter.close()


def _check(adapter, journal, before_all, which):
    by_uuid = {f.uuid: f for f in adapter.get_footprints()}
    by_uuid.update(_all_copper(adapter))
    worst, gone = 0, []
    for u, rec in journal["items"].items():
        live = by_uuid.get(u)
        if live is None:
            gone.append(u)
            continue
        target = rec["moved"] if which == "moved" else rec
        worst = max(worst, _diff(target, _snap(live)))
    print(f"{which}: worst deviation {worst} nm over {len(journal['items'])} items; "
          f"missing uuids: {len(gone)}")
    if before_all is not None:
        dragged = []
        for u, s in before_all.items():
            if u in journal["items"]:
                continue
            live = by_uuid.get(u)
            if live is None:
                dragged.append((u, "GONE"))
            elif _diff(s, _snap(live)) > 0:
                dragged.append((u, f"{_diff(s, _snap(live)) / NM:.3f} mm"))
        print(f"non-moved copper that changed position: {len(dragged)}")
        for u, d in dragged[:15]:
            print(f"   {u} {d}")


def cmd_read(config_path, cell):
    cfg, ctx = load_config(config_path)
    adapter = _adapter(config_path)
    try:
        sel = adapter.get_selected_items()
        fps = [i for i in sel if isinstance(i, Footprint)]
        vias = [i for i in sel if isinstance(i, Via)]
        tracks = [i for i in sel if isinstance(i, Track)]
        sheet_names = dict(getattr(ctx, "sheet_names", {}) or {})
        entry = _cell_entry(cfg, cell)
        roles = {c.get("role") for c in entry["components"]}
        prelude = narrow_mixed_selection(
            config_path=config_path, adapter=adapter, footprints=fps, vias=vias,
            tracks=tracks, cfg=cfg, sheet_names=sheet_names, cell_name=cell,
            cell_roles=roles)
        if prelude is None or prelude.refusal:
            raise SystemExit(f"prelude: {prelude.refusal if prelude else 'None (cluster unknown)'}")
        for text, _lvl in prelude.log_lines:
            print("LOG:", text)
        kept = {i.uuid for i in prelude.kept_copper}
        _v, _t, owner = load_registry_entries(config_path, cfg)
        transfer, subtracted = [], []
        for it in vias + tracks:
            if it.uuid in kept:
                continue
            key = owner.get(it.uuid, "")
            (transfer if key.startswith("net:") or not key else subtracted).append((it, key))
        print(f"selected copper: {len(vias) + len(tracks)}; read as usual: {len(kept)}; "
              f"TRANSFER from net_traces (explode mode): {len(transfer)}; "
              f"subtracted (other cells/chains/thermal): {len(subtracted)}")
        by_rec = {}
        for it, key in transfer:
            by_rec[key.split("|", 1)[0] or "(net_traces, unregistered)"] = \
                by_rec.get(key.split("|", 1)[0] or "(net_traces, unregistered)", 0) + 1
        for rec, n in sorted(by_rec.items()):
            print(f"   transfer {rec}: {n}")
        plan_v = [i for i in vias if i.uuid in kept] + [i for i, _k in transfer if isinstance(i, Via)]
        plan_t = [i for i in tracks if i.uuid in kept] + [i for i, _k in transfer if isinstance(i, Track)]
        plan = build_refresh_plan(
            entry["components"], entry.get("vias") or [], entry.get("tracks") or [],
            prelude.footprints, plan_v, plan_t, adapter,
            origin_role=entry.get("anchor_role"), add_new_copper=True,
            remove_missing=False, keep_unpaired=True, reconcile_components=True,
            config=cfg, chosen_cluster=prelude.copper_ctx.chosen_address[0],
            chosen_sheet=prelude.copper_ctx.chosen_address[1],
            cell_layer=entry.get("layer") or "F.Cu",
            nested_placements=entry.get("clone_placements") or [],
            cells=dict(cfg.cells), sheet_names=sheet_names)
        lines = apply_live_copper_rule(
            plan, prelude.copper_ctx, entry.get("vias") or [],
            entry.get("tracks") or [])

        def changed(pairs):
            return sum(1 for rec, geo in pairs
                       if any(abs(float(rec.get(k, 0) or 0) - float(v)) > 1e-6
                              for k, v in geo.items() if isinstance(v, (int, float))))
        print(f"components: {len(plan.component_updates)} paired ({changed(plan.component_updates)} moved), "
              f"+{len(plan.new_component_records)} {[r['role'] for r in plan.new_component_records]}, "
              f"-{len(plan.removed_component_records)} {[r.get('role') for r in plan.removed_component_records]}")
        print(f"vias: {len(plan.via_updates)} paired ({changed(plan.via_updates)} moved), "
              f"+{len(plan.new_via_records)}, -{len(plan.removed_via_records)}")
        print(f"tracks: {len(plan.track_updates)} paired ({changed(plan.track_updates)} moved), "
              f"+{len(plan.new_track_records)}, -{len(plan.removed_track_records)}")
        for w in list(plan.warnings) + list(lines):
            print("WARN:", w)
        adapter.select_items(list(prelude.instance_footprints) + plan_v + plan_t)
        print(f"board selection set: {len(prelude.instance_footprints)} footprints + "
              f"{len(plan_v)} vias + {len(plan_t)} tracks")
    finally:
        adapter.close()


def cmd_restore(jpath):
    journal = json.loads(Path(jpath).read_text(encoding="utf-8"))
    adapter = _adapter(journal["config"])
    try:
        by_uuid = {f.uuid: f for f in adapter.get_footprints()}
        by_uuid.update(_all_copper(adapter))
        items, hand_moved = [], []
        for u, rec in journal["items"].items():
            live = by_uuid.get(u)
            if live is None:
                continue
            if _diff(rec["moved"], _snap(live)) > 0:
                hand_moved.append(rec.get("ref") or u)
            _set(live, rec)
            items.append(live)
        _commit(adapter, items, "KiCadStamp probe: restore")
        adapter.refresh_board()
        if hand_moved:
            print(f"moved by hand after explode (restored anyway): {hand_moved[:20]}")
        _check(adapter, journal, None, "restored")
    finally:
        adapter.close()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("explode"); e.add_argument("config"); e.add_argument("--cell", required=True)
    e.add_argument("--dx", type=float, default=None)
    r = sub.add_parser("read"); r.add_argument("config"); r.add_argument("--cell", required=True)
    s = sub.add_parser("restore"); s.add_argument("journal")
    a = p.parse_args()
    _CONFIG[0] = getattr(a, "config", None)
    if a.cmd == "explode":
        cmd_explode(a.config, a.cell, a.dx)
    elif a.cmd == "read":
        cmd_read(a.config, a.cell)
    else:
        cmd_restore(a.journal)


if __name__ == "__main__":
    sys.exit(main())
