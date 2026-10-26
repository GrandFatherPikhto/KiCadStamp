# kicadstamp/diagnostics/probe_shifted_registry_pair_2026_10_26.py
"""probe_shifted_registry_pair_2026_10_26.py — registry pairs whose live item contradicts the plan.

Ч0 of plan_2026_10_26_select_cell_shifted_registry_pair (worker: DeepSeek). Live
defect on v103: «Select cell» for the instance `mcu_vdda_spoke_9` does not select
the track `2f9a0799…` (+3V3_VDDA) and the re-read drops it. The established cause
(Claude, 26.10) is a SHIFTED registry number: the entry under key `…|2` still
holds the uuid of ANOTHER record's copper — a GND track — and that pair

  * occupies the key (`match_planned_copper` tier 1 takes the entry's uuid
    whenever the uuid is live on the board), and
  * claims the live item (`owned` — every registry uuid blocks tier 2 / the
    at-current-place pass), so the record that really owns that GND track cannot
    take it by geometry, and `2f9a0799` has nobody to take it at all.

This probe MEASURES that, and nothing else:

  1. the instance — the SAME calls the worker makes
     (`ApplyPipeline(only=[record], dry_run=True)` → `plan_copper()` →
     `select_cell_copper_targets`), then the pairs behind the counters: every
     planned command, its tier, its live item, and whether that item contradicts
     the command by KIND / NET / LAYER / WIDTH-DIAMETER (position left out — that
     is the point of the rule: a pair may be away from the place and still be
     the record's copper);
  2. the instance's own registry entries — key → uuid → live? → does the live
     item's net/width/layer agree with the RECORD the key names;
  3. the WHOLE profile — one dry run, then the same pair analysis over every
     planned command, so the number is not a claim about one instance only.

READ-ONLY: nothing is written to the board; the profile is COPIED into a temp dir
and every relative path inside the copy is made absolute (`load_config` itself
writes — format/registry lifts). The write path of the product is never touched.

The identity comparison here MIRRORS `kicadstamp.track_identity.track_matches` /
`kicadstamp.registry.via_matches` field for field (net string, `layer_to_str`,
width / drill+diameter at 1e-6) and deliberately DROPS their position clause — it
is the measurement of the rule, not a second implementation of it (Ч1 puts the
one shared predicate next to `accept_planned_match`).

USAGE
    .venv/bin/python -m kicadstamp.diagnostics.probe_shifted_registry_pair_2026_10_26 \
        [--cell mcu_pwr_spoke --cluster MCU_PWR_VDDA_9 --sheet MCU] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.config.loader import load_config
from kicadstamp.config_working_set import set_active_graph_root
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.diagnostics.probe_reread_noise_2026_10_26 import _copy_profile
from kicadstamp.utils.layers import layer_to_str
from kicadstamp.utils.paths import registry_paths_for_config

#: The number that closes the epsilon on width / drill / diameter — the very one
#: `track_matches` and `via_matches` use.
EPS = 1e-6


# ── the identity half of a pair (kind/net/layer/width — never the place) ────
def live_identity(live_item) -> dict:
    """(kind, net, layer, size) of a live board item, as the board reports it."""
    if hasattr(live_item, "drill_mm"):
        return {"kind": "via", "net": live_item.net_name, "layer": None,
                "size": (round(live_item.drill_mm, 6),
                         round(live_item.diameter_mm, 6))}
    return {"kind": "track", "net": live_item.net_name,
            "layer": layer_to_str(live_item.layer),
            "size": round(live_item.width_mm, 6)}


def command_identity(cmd) -> dict:
    """(kind, net, layer, size) of a planned ViaCommand / TrackCommand."""
    if hasattr(cmd, "drill_mm"):
        return {"kind": "via", "net": cmd.net_name, "layer": None,
                "size": (round(cmd.drill_mm, 6), round(cmd.diameter_mm, 6))}
    return {"kind": "track", "net": cmd.net_name,
            "layer": layer_to_str(cmd.layer), "size": round(cmd.width_mm, 6)}


def contradicts(live_item, cmd) -> tuple[bool, list[str]]:
    """(contradicts?, the fields that differ) — position is NOT among them."""
    live, planned = live_identity(live_item), command_identity(cmd)
    fields: list[str] = []
    if live["kind"] != planned["kind"]:
        fields.append("kind")
    if live["net"] != planned["net"]:
        fields.append(f"net {live['net']!r} != {planned['net']!r}")
    if live["layer"] != planned["layer"]:
        fields.append(f"layer {live['layer']!r} != {planned['layer']!r}")
    if live["size"] != planned["size"] and not _sizes_close(live["size"],
                                                           planned["size"]):
        fields.append(f"size {live['size']} != {planned['size']}")
    return bool(fields), fields


def _sizes_close(a, b) -> bool:
    if isinstance(a, tuple) != isinstance(b, tuple):
        return False
    if isinstance(a, tuple):
        return all(abs(x - y) < EPS for x, y in zip(a, b))
    return abs(a - b) < EPS


def position_agrees(live_item, cmd) -> bool:
    """Does the live item sit where the command PLANS it (the only thing
    `_live_matches` adds on top of the identity fields)?

    A pair can therefore be at the place and still contradict by width — and then
    `accept_planned_match` calls it "away from the planned place", which is not
    what is wrong with it."""
    from kicadstamp.constants import POSITION_TOLERANCE_MM

    if hasattr(cmd, "drill_mm"):
        if not hasattr(live_item, "position"):
            return False
        return (abs(live_item.position.x - cmd.position.x) / 1e6
                <= POSITION_TOLERANCE_MM
                and abs(live_item.position.y - cmd.position.y) / 1e6
                <= POSITION_TOLERANCE_MM)
    if not hasattr(live_item, "start"):
        return False
    pairs = (
        ((live_item.start.x, live_item.start.y), (cmd.start.x, cmd.start.y)),
        ((live_item.end.x, live_item.end.y), (cmd.end.x, cmd.end.y)))
    swapped = (
        ((live_item.start.x, live_item.start.y), (cmd.end.x, cmd.end.y)),
        ((live_item.end.x, live_item.end.y), (cmd.start.x, cmd.start.y)))

    def same(ends) -> bool:
        return all(abs(a[0] - b[0]) / 1e6 <= POSITION_TOLERANCE_MM
                   and abs(a[1] - b[1]) / 1e6 <= POSITION_TOLERANCE_MM
                   for a, b in ends)

    return same(pairs) or same(swapped)


def _describe(item) -> str:
    if item is None:
        return "—"
    if hasattr(item, "drill_mm"):
        pos = item.position
        return (f"via ({pos.x / 1e6:g},{pos.y / 1e6:g}) net={item.net_name} "
                f"d{item.drill_mm:g}/{item.diameter_mm:g}")
    return (f"track ({item.start.x / 1e6:g},{item.start.y / 1e6:g})->"
            f"({item.end.x / 1e6:g},{item.end.y / 1e6:g}) net={item.net_name} "
            f"w{item.width_mm:g} {layer_to_str(item.layer)}")


# ── the pair tables ────────────────────────────────────────────────────────
def pairs_for(adapter, cfg, config_path, planned_vias, planned_tracks) -> list[dict]:
    """Every planned command with its tier, its live item and the verdict.

    Runs the product's own `match_planned_copper` over BOTH registries (one call
    per kind, exactly like `select_cell_copper_targets`) and then asks the two
    questions the defect turns on: does the item contradict the command by
    identity, and does it sit where the command plans it."""
    from kicadstamp.registry import PlacementRegistry, TrackRegistry
    from kicadstamp.registry_match import (
        accept_planned_match,
        match_planned_copper,
    )

    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)

    rows: list[dict] = []
    for reg, cmds, live_items in (
            (via_reg, planned_vias, list(adapter.get_vias())),
            (trk_reg, planned_tracks, list(adapter.get_tracks()))):
        for match in match_planned_copper(reg, list(cmds or ()),
                                          live_items=live_items):
            row: dict[str, Any] = {
                "key": getattr(match.command, "registry_key", None),
                "tier": match.tier,
                "live": getattr(match.live, "uuid", None),
                "planned": _describe(match.command),
                "found": _describe(match.live),
                "in_place": accept_planned_match(reg, match),
            }
            if match.live is not None:
                bad, fields = contradicts(match.live, match.command)
                row["contradicts"] = bad
                row["fields"] = fields
                row["position_agrees"] = position_agrees(match.live, match.command)
            else:
                row["contradicts"] = None
                row["fields"] = []
                row["position_agrees"] = None
            rows.append(row)
    return rows


def _print_pairs(title: str, rows: list[dict], *, show_all: bool = False) -> None:
    print(f"\n== {title}: {len(rows)} planned command(s) ==")
    bad = [r for r in rows if r["contradicts"]]
    print(f"   found by tier     : registry "
          f"{sum(1 for r in rows if r['tier'] == 'registry')}, geometry "
          f"{sum(1 for r in rows if r['tier'] == 'geometry')}, "
          f"missing {sum(1 for r in rows if r['live'] is None)}")
    print(f"   away from place   : {sum(1 for r in rows if r['live'] and not r['in_place'])}")
    print(f"   CONTRADICT by kind/net/layer/size: {len(bad)}")
    for row in (rows if show_all else bad):
        flag = "CONTRADICTS" if row["contradicts"] else "ok"
        print(f"   · {row['key']}  [{row['tier']}]  {flag}"
              f"{'' if not row['fields'] else ' — ' + '; '.join(row['fields'])}")
        print(f"       planned: {row['planned']}")
        print(f"       live   : {row['found']}"
              f"{'' if row['live'] is None or row['in_place'] else '   (away from the planned place)'}")


def instance_entries(adapter, cfg, config_path, cell_name, cluster, sheet,
                     sheet_names) -> list[dict]:
    """The instance's OWN registry entries: key → uuid → live? → agrees with the
    RECORD the key names (net/width/layer, position left out)."""
    from kicadstamp.absent_copper_prune import (
        own_instance_context,
        own_registry_entries,
        record_of,
    )
    from kicadstamp.registry import load_registry_entries

    _footprints, cell_identity, own_addresses, chosen_address, refs = \
        own_instance_context(adapter, cfg, cell_name, cluster, sheet,
                             sheet_names=sheet_names, own_refs=None)
    via_entries, track_entries, _owner = load_registry_entries(str(config_path), cfg)
    entries = own_registry_entries(via_entries, track_entries, cell_identity,
                                   own_addresses, chosen_address, refs)
    live = {"via": {getattr(i, "uuid", None): i for i in adapter.get_vias()},
            "track": {getattr(i, "uuid", None): i for i in adapter.get_tracks()}}
    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    rows: list[dict] = []
    for kind, role_part, index, uuid in entries:
        item = live[kind].get(uuid)
        rec = record_of(cell, kind, role_part, index)
        row: dict[str, Any] = {"kind": kind, "role_part": role_part,
                               "index": index, "uuid": uuid,
                               "live": item is not None}
        if item is None:
            row["verdict"] = "gone from the board"
        elif rec is None:
            row["verdict"] = "ORPHAN key (no such record in the cell)"
        else:
            fields = _record_vs_live(rec, item, kind)
            row["verdict"] = "agrees" if not fields else "; ".join(fields)
            row["record"] = _describe_record(rec, kind)
            row["found"] = _describe(item)
        rows.append(row)
    return rows


def _record_size(rec, kind: str):
    """The record's own size, or None when it carries none to compare."""
    if kind == "via":
        if rec.drill_mm is None or rec.diameter_mm is None:
            return None
        return (round(rec.drill_mm, 6), round(rec.diameter_mm, 6))
    return None if rec.width_mm is None else round(rec.width_mm, 6)


def _layer_text(layer) -> str:
    """`layer_to_str`, but a record that INHERITS the layer (`None`) says so
    instead of raising: a cell record may carry no layer of its own."""
    return "inherit" if layer is None else layer_to_str(layer)


def _record_vs_live(rec, item, kind: str) -> list[str]:
    """Contradictions between a STORED cell record and its live item.

    A field the record does not carry (`layer`/`width_mm` omitted — the record
    inherits them) is not compared: an absent field is not a contradiction."""
    fields: list[str] = []
    live = live_identity(item)
    if kind == "track" and rec.layer is not None \
            and live["layer"] != layer_to_str(rec.layer):
        fields.append(f"layer {live['layer']!r} != {layer_to_str(rec.layer)!r}")
    size = _record_size(rec, kind)
    if size is not None and not _sizes_close(live["size"], size):
        fields.append(f"size {live['size']} != {size}")
    # The record's net may be literal or a (role, pad) reference; only a literal
    # one can be compared WITHOUT the run's resolver, and a silently unresolved
    # reference must not look like a contradiction here.
    literal = getattr(rec, "net", None)
    if literal is not None and live["net"] != literal:
        fields.append(f"net {live['net']!r} != record {literal!r}")
    return fields


def _describe_record(rec, kind: str) -> str:
    if kind == "via":
        return (f"via ({rec.offset_along_mm:g},{rec.offset_across_mm:g}) "
                f"d{rec.drill_mm:g}/{rec.diameter_mm:g} net={getattr(rec, 'net', None)!r}")
    return (f"track ({rec.start_along_mm:g},{rec.start_across_mm:g})->"
            f"({rec.end_along_mm:g},{rec.end_across_mm:g}) w{rec.width_mm} "
            f"{_layer_text(rec.layer)} net={getattr(rec, 'net', None)!r}")


def _print_entries(rows: list[dict]) -> None:
    print(f"\n== the instance's own registry entries: {len(rows)} ==")
    for row in rows:
        print(f"   · {row['kind']}|{row['role_part']}|{row['index']}  "
              f"uuid {row['uuid']}  →  {row['verdict']}")
        if row.get("record"):
            print(f"       record: {row['record']}")
            print(f"       live  : {row['found']}")


# ── main ───────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile",
                        default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--cell", default="mcu_pwr_spoke")
    parser.add_argument("--cluster", default="MCU_PWR_VDDA_9")
    parser.add_argument("--sheet", default="MCU")
    parser.add_argument("--expect-uuid", default="2f9a0799-8f21-4b30-a834-9326ab444fdf")
    parser.add_argument("--no-whole", action="store_true",
                        help="skip the whole-profile dry run")
    parser.add_argument("--json", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    profile = Path(args.profile).resolve()
    if not profile.is_file():
        raise SystemExit(f"no such profile: {profile}")

    workdir = Path(tempfile.mkdtemp(prefix="shifted-pair-"))
    result: dict[str, Any] = {"profile": str(profile), "cell": args.cell,
                              "cluster": args.cluster, "sheet": args.sheet}
    pipeline = None
    try:
        copy_config = _copy_profile(profile, workdir)
        set_active_graph_root(copy_config)
        cfg, ctx = load_config(str(copy_config))
        sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
        print(f"profile copy   : {copy_config}")

        from kicadstamp.apply_pipeline import ApplyPipeline
        from gui.select_cell import instance_recording_name, select_cell_copper_targets

        record = instance_recording_name(cfg, args.cell, args.cluster, args.sheet)
        print(f"record placing : {record!r}")
        if record is None:
            raise SystemExit("no record places this cell — the registry-only door "
                             "would be measured instead, see the plan's §1")

        pipeline = ApplyPipeline(str(copy_config), only=[record], dry_run=True,
                                 timeout_ms=DEFAULT_TIMEOUT_MS)
        pipeline.run()
        vias, tracks = pipeline.plan_copper()
        adapter = pipeline.adapter
        print(f"live board     : {adapter.get_board_filename()}")
        print(f"planned        : {len(vias)} via(s), {len(tracks)} track(s)")

        rows = pairs_for(adapter, cfg, copy_config, vias, tracks)
        _print_pairs("instance — every planned command", rows, show_all=True)
        result["instance_pairs"] = rows

        entries = instance_entries(adapter, cfg, copy_config, args.cell,
                                   args.cluster, args.sheet, sheet_names)
        _print_entries(entries)
        result["instance_entries"] = entries

        plan = select_cell_copper_targets(
            adapter, cfg, copy_config, args.cell, args.cluster, args.sheet,
            sheet_names, vias, tracks, own_refs=None,
            at_current_place=getattr(getattr(pipeline, "at_current_place", None),
                                     "bound", None))
        selected = [getattr(item, "uuid", None) for item in plan.copper]
        print(f"\n== Select cell (the product's own door) ==")
        print(f"   {plan.line}")
        print(f"   selected uuid(s): {selected}")
        print(f"   expected {args.expect_uuid}: "
              f"{'SELECTED' if args.expect_uuid in selected else 'MISSING'}")
        result["select_line"] = plan.line
        result["selected"] = selected
        result["expected_selected"] = args.expect_uuid in selected
        result["missing"] = plan.copper_missing

        if not args.no_whole:
            whole = ApplyPipeline(str(copy_config), dry_run=True,
                                  timeout_ms=DEFAULT_TIMEOUT_MS)
            try:
                whole.run()
                wvias, wtracks = whole.plan_copper()
                print(f"\n== whole profile: {len(wvias)} via(s), "
                      f"{len(wtracks)} track(s) planned ==")
                bad = [r for r in pairs_for(whole.adapter, whole.cfg, copy_config,
                                            wvias, wtracks)
                       if r["contradicts"]]
                print(f"   registry pair(s) contradicting their command by "
                      f"kind/net/layer/size: {len(bad)}")
                for row in bad[:20]:
                    print(f"   · {row['key']}: {'; '.join(row['fields'])}")
                    print(f"       planned: {row['planned']}")
                    print(f"       live   : {row['found']}")
                result["whole_contradicting"] = len(bad)
                result["whole_examples"] = bad
                by_field: dict[str, int] = {}
                for row in bad:
                    for field in row["fields"]:
                        by_field[field.split()[0]] = by_field.get(
                            field.split()[0], 0) + 1
                result["whole_by_field"] = by_field
                # A pair that IS where the plan puts it, but with another width,
                # is the same shifted-number defect in its sharpest form:
                # `accept_planned_match` calls it "away from the planned place",
                # which is not what it is.
                result["whole_position_ok"] = sum(
                    1 for row in bad if row["position_agrees"])
                print(f"   by field          : {by_field}")
                print(f"   sitting exactly where the plan puts them (only the "
                      f"width/net differ): {result['whole_position_ok']}")
            finally:
                whole.close()
    finally:
        if pipeline is not None:
            pipeline.close()
        if args.json:
            Path(args.json).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\njson           : {args.json}")
        if args.keep:
            print(f"temp copy kept : {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
