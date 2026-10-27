# kicadstamp/diagnostics/probe_registry_key_at_another_record_place_2026_10_27.py
"""probe_registry_key_at_another_record_place_2026_10_27.py — «уехал, но стоит
ровно на месте ДРУГОЙ записи того же экземпляра».

Ч0 of `plan_2026_10_27_registry_key_at_another_record_place` (worker: DeepSeek),
continuation of `plan_2026_10_26_select_cell_shifted_registry_pair` (accepted
`dda2a7f1`). There the pair «away + ANOTHER net» became a shifted number and the
key was freed. This probe measures the case left over — «away + THE SAME net» —
where the item is not where ITS record puts the copper but exactly where a
NEIGHBOURING record of the same instance puts it. On v103 the instance
`ldo_adj_n2v5` / `LDO_ADJ_N2V5` / `Power` is the live defect: key `|4` holds
record `|2`'s GND track and key `|5` holds record `|1`'s, so «Select cell» loses
`3ef337ff` and `31e9c78a`.

THE RULE BEING MEASURED (plan §3, Denis): the proof is not the NET, it is the
PLACE — an item standing exactly where ANOTHER record of the same instance puts
its copper is that record's copper, and the key holding it is a shifted number at
ANY net. The neighbour must ACCEPT the item's net when its own net is declared
(the same `_record_net` the net guard uses); a net we cannot resolve accuses
nobody, so such a pair is NOT released by this path.

TWO PLACE VOCABULARIES, one per door — measured separately, never merged:

  * COMMAND (select-cell and dry-run doors): another PLANNED COMMAND of the same
    instance, compared by the product's `registry_match.same_place`
    (`POSITION_TOLERANCE_MM`). «Same instance» = the first two parts of the
    registry key (`anchor|cell`), and keys without that head never witness;
  * FRAME (at-current-place and registry-only doors): another RECORD of the cell,
    compared by `absent_copper_prune.pair_agrees_with_record` in the instance's
    cell frame (`RIGID_TOLERANCE_MM`).

Every comparison is the PRODUCT's own predicate — this probe decides nothing and
re-implements nothing. It counts, names the witnesses and shows the raw distance,
so a reader can see whether a witness sits there by construction or by luck.

READ-ONLY for the board and for the profile: the profile is COPIED into a temp
dir (`probe_reread_noise_2026_10_26._copy_profile` absolutizes its relative paths,
so the copy reads the same live board). The «Redraw dry run» section runs the
product's `reconcile()` — which WRITES the registry — on a SCRATCH copy of the two
registry files, never on the profile's own.

USAGE
    .venv/bin/python -m kicadstamp.diagnostics.probe_registry_key_at_another_record_place_2026_10_27 \
        [--cell ldo_adj_n2v5 --cluster LDO_ADJ_N2V5 --sheet Power] \
        [--json-instance OUT.json] [--json-whole OUT.json] [--no-whole] \
        [--no-frame-walk] [--keep]
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.config.loader import load_config
from kicadstamp.config_working_set import set_active_graph_root
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.diagnostics.probe_reread_noise_2026_10_26 import _copy_profile
from kicadstamp.diagnostics.probe_shifted_registry_pair_2026_10_26 import _describe
from kicadstamp.registry_match import PAIR_AGREES, pair_verdict, same_place
from kicadstamp.utils.paths import registry_paths_for_config
from kicadstamp.utils.units import MM

__all__ = [
    "NEW_AWAY",
    "NEW_SHIFTED",
    "PAIR_GONE",
    "NEW_UNRESOLVED",
    "command_rows",
    "frame_door_instance",
    "instance_of",
]

#: Verdicts of the rule MEASURED here: the item stands at another record's place
#: (the key is a shifted number at any net) — or it does not, and today's verdict
#: stands. `NEW_UNRESOLVED` — a witness was found but the neighbour's net could
#: not be resolved, so this path would NOT release the key.
NEW_SHIFTED = "shifted by place (witness)"
NEW_AWAY = "away (no witness)"
NEW_UNRESOLVED = "witness, net unresolved"

#: Not a verdict of the product's rule — a planned command with no live copper.
PAIR_GONE = "ghost (no live item)"


# ── small pure helpers ────────────────────────────────────────────────────────
def _points(item) -> tuple:
    """World points (mm) of a live item or a planned command: a via is ONE point,
    a track TWO ends. Both sides carry the same units (nm — every product
    predicate divides by `MM`), so this divides too."""
    if hasattr(item, "position"):
        return ((item.position.x / MM, item.position.y / MM),)
    return ((item.start.x / MM, item.start.y / MM),
            (item.end.x / MM, item.end.y / MM))


def instance_of(key) -> Optional[str]:
    """``anchor|cell`` of a registry key — the INSTANCE identity (a key is
    `anchor|cell|role|index`). None when the key carries no such head, and such a
    key is never a witness for anybody."""
    parts = str(key or "").split("|")
    return "|".join(parts[:2]) if len(parts) == 4 else None


def _record_distance(points, live_item, frame) -> Optional[float]:
    """How far the live item stands from where the RECORD puts its copper, in mm —
    the number BEHIND `pair_agrees_with_record`'s yes/no. Reported so the report
    can say «the witness place is 0.000 mm away while its own record is 1.500 mm
    away», not merely «both answers are boolean»."""
    if points is None or frame is None or live_item is None:
        return None
    expect = tuple(frame.point_to_world_mm(along, across)
                   for along, across in points)
    return round(_max_distance(expect, _points(live_item)), 4)


def _max_distance(a: tuple, b: tuple) -> float:
    """max endpoint distance between two same-length point tuples; a track is
    UNORIENTED, so the better-fitting orientation is taken — the convention of
    `absent_copper_prune._max_point_distance`. Reported only: the product's own
    predicates decide, this number only shows HOW exact the witness is."""
    if len(a) != len(b):
        return float("inf")
    if len(a) == 1:
        return math.dist(a[0], b[0])
    straight = max(math.dist(a[0], b[0]), math.dist(a[1], b[1]))
    flipped = max(math.dist(a[0], b[1]), math.dist(a[1], b[0]))
    return min(straight, flipped)


# ── the COMMAND vocabulary ────────────────────────────────────────────────────
def _command_witnesses(kind, match, matches) -> list[dict]:
    """The OTHER planned commands of the same instance that put their copper where
    this pair's live item stands (the product's `same_place`). Same kind only — a
    via and a track share the key string but live in two registries."""
    if match.live is None:
        return []
    own_key = getattr(match.command, "registry_key", None)
    own_instance = instance_of(own_key)
    if own_instance is None:
        return []
    mine = _points(match.live)
    out: list[dict] = []
    for other_kind, other in matches:
        if other_kind != kind:
            continue
        other_key = getattr(other.command, "registry_key", None)
        if other_key is None or other_key == own_key:
            continue
        if instance_of(other_key) != own_instance:
            continue
        if not same_place(match.live, other.command):
            continue
        out.append({"key": other_key,
                    "net": getattr(other.command, "net_name", None),
                    "max_endpoint_mm": round(_max_distance(
                        mine, _points(other.command)), 4),
                    "place": _describe(other.command)})
    return out


def command_rows(matches) -> list[dict]:
    """One row per planned command of ONE call: the product's verdict, its own
    place answer, and the witnesses in the COMMAND vocabulary plus what the
    measured rule would say.

    ``matches`` — ``[(kind, PlannedCopperMatch), …]`` from
    `registry_match.match_planned_copper`, one entry per kind."""
    from kicadstamp.registry_match import same_net

    rows: list[dict] = []
    for kind, match in matches:
        key = getattr(match.command, "registry_key", None)
        row: dict[str, Any] = {
            "kind": kind,
            "key": key,
            "instance": instance_of(key),
            "tier": match.tier,
            "live": getattr(match.live, "uuid", None),
            "planned": _describe(match.command),
            "found": _describe(match.live),
        }
        if match.live is None:
            row.update(verdict=PAIR_GONE, own_place=None,
                       witnesses=[], new_verdict=PAIR_GONE)
            rows.append(row)
            continue
        row["verdict"] = pair_verdict(match)
        row["own_place"] = same_place(match.live, match.command)
        witnesses = _command_witnesses(kind, match, matches)
        row["witnesses"] = witnesses
        if row["own_place"]:
            row["new_verdict"] = row["verdict"]      # place decides first, unchanged
        elif not witnesses:
            row["new_verdict"] = row["verdict"]      # nothing new to say
        else:
            admitted = [w for w in witnesses
                        if same_net(match.live, match.command, w["net"])]
            row["new_verdict"] = NEW_SHIFTED if admitted else NEW_UNRESOLVED
        rows.append(row)
    return rows


# ── the FRAME vocabulary (at-current-place / registry-only doors) ─────────────
def frame_door_instance(adapter, cfg, config_path: str, cell_name: str,
                        cluster, sheet, *, sheet_names=None, own_refs=None,
                        clone=None, own_refs_map=None,
                        live_board=None) -> dict:
    """The instance's own registry pairs judged in the CELL FRAME — the vocabulary
    of `adopt_at_current_place._shifted_uuids` and of
    `absent_copper_prune.registry_record_copper_map`.

    Per own key: is the item at ITS record's place, at ANOTHER record's place (and
    which), and what would the measured rule say. ``clone`` + ``own_refs_map``
    (the planner's role resolution) let the neighbour's net be resolved through
    the net guard's own `_record_net`; without them the neighbour's LITERAL net is
    all that can be read and an unresolved one is reported as such."""
    from kicadstamp.absent_copper_prune import (cell_record_slots,
                                                own_instance_context,
                                                own_registry_entries,
                                                pair_agrees_with_record,
                                                record_of, record_points)
    from kicadstamp.cell_geometry_refresh import cell_frame_from_live
    from kicadstamp.cell_origin import cell_slot_dicts
    from kicadstamp.registry import load_registry_entries

    cell = (getattr(cfg, "cells", {}) or {}).get(cell_name)
    footprints, cell_identity, own_addresses, chosen_address, refs = \
        own_instance_context(adapter, cfg, cell_name, cluster, sheet,
                             sheet_names=sheet_names, own_refs=own_refs)
    via_entries, track_entries, _owner = load_registry_entries(str(config_path), cfg)
    entries = own_registry_entries(via_entries, track_entries, cell_identity,
                                   own_addresses, chosen_address, refs)
    live = {"via": {getattr(i, "uuid", None): i for i in adapter.get_vias()},
            "track": {getattr(i, "uuid", None): i for i in adapter.get_tracks()}}
    out: dict[str, Any] = {
        "cell": cell_name, "cluster": cluster, "sheet": sheet,
        "footprints": len(footprints), "entries": len(entries),
        "records": len(cell_record_slots(cell)),
        "slots": sorted(f"{k}|{r}|{i}" for k, r, i in cell_record_slots(cell)),
        "rows": [], "frame_residual_mm": None, "rigid": None,
    }
    frame = cell_frame_from_live(cell_slot_dicts(cell), footprints, adapter,
                                 origin_role=getattr(cell, "anchor_role", None))
    if frame is None:
        out["frame"] = "not built"
        return out
    out["frame_residual_mm"] = round(frame.residual_mm, 4)
    out["rigid"] = frame.residual_mm <= RIGID_TOLERANCE_MM
    if not out["rigid"]:
        return out                      # a non-rigid frame cannot tell neighbours apart

    slots = sorted(cell_record_slots(cell))
    for kind, role_part, index, uuid in entries:
        item = live[kind].get(uuid)
        row: dict[str, Any] = {"kind": kind, "role_part": role_part,
                               "index": index, "uuid": uuid,
                               "live": _describe(item) if item is not None else None}
        points = record_points(cell, kind, role_part, index)
        if item is None:
            row.update(own_place=None, witnesses=[], new_verdict=PAIR_GONE)
            out["rows"].append(row)
            continue
        if points is None:
            row.update(own_place=None, witnesses=[], new_verdict="ORPHAN key")
            out["rows"].append(row)
            continue
        row["own_place"] = pair_agrees_with_record(points, item, frame,
                                                  RIGID_TOLERANCE_MM)
        row["own_distance_mm"] = _record_distance(points, item, frame)
        # TODAY's verdict of the at-current-place door for this very pair: «away
        # AND another net» already releases the key (`_shifted_uuids`). Measuring
        # the NEW rule cannot be done without saying which keys it would ADD.
        own_rec = record_of(cell, kind, role_part, index)
        own_net = _net_verdict(item, own_rec, clone, own_refs_map, adapter,
                               f"{kind}|{role_part}|{index}")
        row["own_net"] = own_net
        row["today_shifted"] = bool(
            own_net["declared"] and own_net["record_net"] is not None
            and own_net["item_net"] != own_net["record_net"])
        witnesses: list[dict] = []
        for other_kind, other_role, other_index in slots:
            if (other_kind, other_role, other_index) == (kind, role_part, index):
                continue
            # A live VIA has one point, a live TRACK two: a neighbour of ANOTHER
            # KIND can never stand at this item's place (the product never pairs
            # across kinds either — one registry per kind). Asking would compare a
            # 1-tuple with a 2-tuple and mean nothing.
            if other_kind != kind:
                continue
            other_points = record_points(cell, other_kind, other_role, other_index)
            if not pair_agrees_with_record(other_points, item, frame,
                                           RIGID_TOLERANCE_MM):
                continue
            other_rec = record_of(cell, other_kind, other_role, other_index)
            witness = _net_verdict(item, other_rec, clone, own_refs_map, adapter,
                                   f"{other_kind}|{other_role}|{other_index}")
            witness["distance_mm"] = _record_distance(other_points, item, frame)
            witnesses.append(witness)
        row["witnesses"] = witnesses
        if row["own_place"]:
            row["new_verdict"] = PAIR_AGREES
        elif not witnesses:
            row["new_verdict"] = NEW_AWAY
        elif any(w["item_admitted"] for w in witnesses):
            row["new_verdict"] = NEW_SHIFTED
        else:
            row["new_verdict"] = NEW_UNRESOLVED
        out["rows"].append(row)
    return out


def _net_verdict(item, other_rec, clone, own_refs_map, adapter, other_slot) -> dict:
    """Does the NEIGHBOUR record accept this item's net? Resolved by the net
    guard's own `_record_net` when the planner's role resolution is at hand, and
    by the record's LITERAL net otherwise."""
    declared = False
    expected = None
    resolved_by = "literal"
    if other_rec is not None:
        if clone is not None and own_refs_map is not None:
            from kicadstamp.adopt_at_current_place import _record_net
            declared, expected = _record_net(other_rec, clone, own_refs_map, adapter)
            resolved_by = "net guard"
        else:
            expected = getattr(other_rec, "net", None)
            declared = expected is not None or getattr(other_rec, "net_from_role",
                                                      None) is not None
    live_net = getattr(item, "net_name", None)
    admitted = bool(declared and expected is not None and live_net == expected)
    return {"record": other_slot, "record_net": expected,
            "record_net_from_role": getattr(other_rec, "net_from_role", None),
            "resolved_by": resolved_by, "declared": declared,
            "item_net": live_net, "item_admitted": admitted}


# ── printing ──────────────────────────────────────────────────────────────────
def _print_rows(title: str, rows, *, all_rows: bool = True) -> None:
    print(f"\n== {title}: {len(rows)} pair(s) ==")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(f"   verdicts (today)          : {counts}")
    new_counts: dict[str, int] = {}
    for row in rows:
        new_counts[row["new_verdict"]] = new_counts.get(row["new_verdict"], 0) + 1
    print(f"   verdicts (measured rule)  : {new_counts}")
    shown = rows if all_rows else [r for r in rows if r["new_verdict"] == NEW_SHIFTED]
    for row in shown:
        print(f"   · {row['key']}  [{row['tier']}]  {row['verdict']}  -> {row['new_verdict']}")
        print(f"       planned: {row['planned']}")
        print(f"       live   : {row['found']}")
        for w in row["witnesses"]:
            print(f"       witness: {w['key']}  net={w['net']}  "
                  f"max endpoint distance {w['max_endpoint_mm']} mm")


def _print_frame(result: dict) -> None:
    print(f"\n== frame door: {result['cell']} / {result['cluster']} / "
          f"{result['sheet']} ==")
    print(f"   records {result['records']}, own registry keys {result['entries']}, "
          f"footprints {result['footprints']}, frame residual "
          f"{result['frame_residual_mm']} mm (rigid: {result['rigid']})")
    for row in result["rows"]:
        print(f"   · {row['kind']}|{row['role_part']}|{row['index']}  "
              f"uuid {row['uuid']}  own place {row['own_place']}  "
              f"-> {row['new_verdict']}")
        if row.get("live"):
            print(f"       live : {row['live']}")
        for w in row["witnesses"]:
            print(f"       witness record {w['record']}: net {w['record_net']!r} "
                  f"({w['resolved_by']}, declared {w['declared']}) vs item net "
                  f"{w['item_net']!r} -> admitted {w['item_admitted']}, "
                  f"distance {w['distance_mm']} mm "
                  f"(its own record is {row['own_distance_mm']} mm away)")


# ── the Redraw dry run on SCRATCH registries ──────────────────────────────────
def redraw_dry_run(adapter, config_path: str, cfg, items, planned_vias,
                   planned_tracks, *, sheet_names=None, known_anchor_ids=None,
                   scratch: Path) -> dict:
    """What a Redraw would DELETE and CREATE — the product's own `reconcile()`,
    run on a COPY of the two registry files (the profile's own files are never
    touched, the board is never touched).

    The at-current-place WRITE pass runs first, exactly as the real run orders it
    (`apply_pipeline` line ~788 then `reconcile`), so the dump reflects the
    product's behaviour and not a reading of it."""
    from kicadstamp.adopt_at_current_place import adopt_cell_copper_at_current_place
    from kicadstamp.registry import PlacementRegistry, TrackRegistry

    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    scratch.mkdir(parents=True, exist_ok=True)
    via_copy = scratch / Path(via_path).name
    trk_copy = scratch / Path(trk_path).name
    for src, dst in ((Path(via_path), via_copy), (Path(trk_path), trk_copy)):
        if src.is_file():
            shutil.copy2(src, dst)
    via_reg = PlacementRegistry(adapter, str(via_copy))
    trk_reg = TrackRegistry(adapter, str(trk_copy))

    out: dict[str, Any] = {"scratch": str(scratch)}
    report = adopt_cell_copper_at_current_place(
        adapter, cfg, items, via_reg, trk_reg, write=True,
        sheet_names=sheet_names)
    out["adoption"] = {"adopted": report.adopted, "shifted": report.shifted,
                       # The companion (Ч1 of the 27.10 plan): a released key whose
                       # record found no copper is DROPPED from the registry copy.
                       "dropped": getattr(report, "dropped", None),
                       "dropped_keys": [{"kind": kind, "key": key}
                                        for kind, key in
                                        (getattr(report, "dropped_keys", None) or ())],
                       "bound": [{"kind": kind, "key": key,
                                  "uuid": getattr(item, "uuid", None)}
                                 for kind, key, item in report.bound]}
    for kind, reg, cmds in (("via", via_reg, planned_vias),
                            ("track", trk_reg, planned_tracks)):
        if not cmds:
            out[f"{kind}_reconcile"] = {"to_delete": [], "to_create": []}
            continue
        # `known_anchor_ids` is the pipeline's OWN protection set: without it
        # reconcile PRUNES every entry this run did not plan, and the dump would
        # slander the product with deletions it never performs.
        to_create, to_delete = reg.reconcile(list(cmds),
                                             known_anchor_ids=known_anchor_ids)
        out[f"{kind}_reconcile"] = {
            "to_delete": list(to_delete),
            "to_create": [getattr(c, "registry_key", None) for c in to_create],
        }
    return out


# ── main ──────────────────────────────────────────────────────────────────────
def _matches(adapter, config_path, cfg, planned_vias, planned_tracks):
    """``[(kind, PlannedCopperMatch)]`` for both registries — one call per kind,
    exactly like `select_cell_copper_targets`."""
    from kicadstamp.registry import PlacementRegistry, TrackRegistry
    from kicadstamp.registry_match import match_planned_copper

    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    via_reg = PlacementRegistry(adapter, via_path)
    trk_reg = TrackRegistry(adapter, trk_path)
    out = []
    for kind, reg, cmds, live in (
            ("via", via_reg, planned_vias, list(adapter.get_vias() or ())),
            ("track", trk_reg, planned_tracks, list(adapter.get_tracks() or ()))):
        out.extend((kind, m) for m in
                   match_planned_copper(reg, list(cmds or ()), live_items=live))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile",
                        default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--cell", default="ldo_adj_n2v5")
    parser.add_argument("--cluster", default="LDO_ADJ_N2V5")
    parser.add_argument("--sheet", default="Power")
    parser.add_argument("--json-instance", default=None)
    parser.add_argument("--json-whole", default=None)
    parser.add_argument("--no-whole", action="store_true")
    parser.add_argument("--no-frame-walk", action="store_true",
                        help="skip the whole-profile frame-vocabulary walk")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    profile = Path(args.profile).resolve()
    if not profile.is_file():
        raise SystemExit(f"no such profile: {profile}")

    workdir = Path(tempfile.mkdtemp(prefix="registry-key-another-place-"))
    instance_result: dict[str, Any] = {"profile": str(profile),
                                       "cell": args.cell,
                                       "cluster": args.cluster,
                                       "sheet": args.sheet}
    whole_result: dict[str, Any] = {}
    pipeline = None
    whole = None
    try:
        copy_config = _copy_profile(profile, workdir)
        set_active_graph_root(copy_config)
        cfg, ctx = load_config(str(copy_config))
        sheet_names = dict(getattr(ctx, "sheet_names", None) or {})

        from kicadstamp.apply_pipeline import ApplyPipeline
        from gui.select_cell import (instance_recording_name,
                                     select_cell_copper_targets)

        record = instance_recording_name(cfg, args.cell, args.cluster, args.sheet)
        print(f"record placing : {record!r}")
        if record is None:
            raise SystemExit("no record places this cell in this profile")

        pipeline = ApplyPipeline(str(copy_config), only=[record], dry_run=True,
                                 timeout_ms=DEFAULT_TIMEOUT_MS)
        _run_pipeline(pipeline)
        planned_vias, planned_tracks = pipeline.plan_copper()
        adapter = pipeline.adapter
        print(f"live board     : {adapter.get_board_filename()}")
        print(f"planned        : {len(planned_vias)} via(s), "
              f"{len(planned_tracks)} track(s)")

        # (а) the COMMAND vocabulary for the instance
        matches = _matches(adapter, copy_config, cfg, planned_vias, planned_tracks)
        rows = command_rows(matches)
        _print_rows("instance — command vocabulary (another planned command)", rows)
        instance_result["command_rows"] = rows

        # (а) the FRAME vocabulary for the instance, with the planner's own role
        # resolution so the neighbour's net is resolved by the net guard itself.
        clone = _clone_for(pipeline, args.cell, args.cluster)
        role_refs_map = None
        if clone is not None:
            from kicadstamp.placement.services.clone_position_calculator import (
                ClonePositionCalculator)
            try:
                role_refs_map = ClonePositionCalculator(
                    adapter, cfg, sheet_names=sheet_names).role_refs_of(clone, None)
            except Exception as exc:            # a refused instance is a measurement too
                print(f"role resolution failed: {exc!r}")
        frame_result = frame_door_instance(
            adapter, cfg, copy_config, args.cell, args.cluster, args.sheet,
            sheet_names=sheet_names, clone=clone, own_refs_map=role_refs_map)
        _print_frame(frame_result)
        instance_result["frame_door"] = frame_result

        # the product's own door, for the line Денис sees
        plan = select_cell_copper_targets(
            adapter, cfg, copy_config, args.cell, args.cluster, args.sheet,
            sheet_names, planned_vias, planned_tracks, own_refs=None,
            at_current_place=getattr(getattr(pipeline, "at_current_place", None),
                                     "bound", None))
        print(f"\n== Select cell (the product's own door) ==\n   {plan.line}")
        instance_result["select_line"] = plan.line
        instance_result["selected"] = [getattr(i, "uuid", None) for i in plan.copper]
        instance_result["missing"] = plan.copper_missing

        # The pass Б2 answer (dry run): what «Adopt at current place» bound and how
        # many keys it called shifted — the second of the three doors, as it runs
        # today.
        adoption = getattr(pipeline, "at_current_place", None)
        instance_result["dry_run_adoption"] = {
            "adopted": getattr(adoption, "adopted", None),
            "shifted": getattr(adoption, "shifted", None),
            "dropped": getattr(adoption, "dropped", None),
            "net_refused": getattr(adoption, "net_refused", None),
            "ambiguous": getattr(adoption, "ambiguous", None),
            "bound": [{"kind": kind, "key": key,
                       "uuid": getattr(item, "uuid", None)}
                      for kind, key, item in (getattr(adoption, "bound", None) or ())],
        }
        print(f"\n== adopt at current place (dry run) ==\n"
              f"   bound {len(instance_result['dry_run_adoption']['bound'])}, "
              f"shifted {instance_result['dry_run_adoption']['shifted']}, "
              f"dropped {instance_result['dry_run_adoption']['dropped']}, "
              f"net_refused {instance_result['dry_run_adoption']['net_refused']}")

        # (а) what a REAL Redraw would do to this instance — on scratch registries
        instance_result["redraw_dry_run"] = redraw_dry_run(
            adapter, copy_config, cfg, getattr(pipeline, "items", ()),
            planned_vias, planned_tracks, sheet_names=sheet_names,
            known_anchor_ids=getattr(pipeline, "all_anchor_ids", None),
            scratch=workdir / "scratch-registry")

        if not args.no_whole:
            whole = ApplyPipeline(str(copy_config), dry_run=True,
                                  timeout_ms=DEFAULT_TIMEOUT_MS)
            try:
                _run_pipeline(whole)
                wvias, wtracks = whole.plan_copper()
                print(f"\n== whole profile: {len(wvias)} via(s), "
                      f"{len(wtracks)} track(s) planned ==")
                wrows = command_rows(_matches(whole.adapter, copy_config,
                                              whole.cfg, wvias, wtracks))
                same_instance = [r for r in wrows
                                 if r["witnesses"]]
                _print_rows("whole profile — command vocabulary", wrows,
                            all_rows=False)
                whole_result["dry_run_adoption"] = {
                    "adopted": getattr(getattr(whole, "at_current_place", None),
                                       "adopted", None),
                    "shifted": getattr(getattr(whole, "at_current_place", None),
                                       "shifted", None),
                }
                whole_result["command"] = {
                    "pairs": len(wrows),
                    "verdicts": _counts(wrows, "verdict"),
                    "measured": _counts(wrows, "new_verdict"),
                    "with_witness": len(same_instance),
                    "witnesses": same_instance,
                }
                if not args.no_frame_walk:
                    whole_result["frame"] = _whole_frame_walk(
                        whole, copy_config, sheet_names)
            finally:
                whole.close()
    finally:
        if pipeline is not None:
            pipeline.close()
        if args.json_instance:
            Path(args.json_instance).write_text(
                json.dumps(instance_result, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"\ninstance json  : {args.json_instance}")
        if args.json_whole:
            Path(args.json_whole).write_text(
                json.dumps(whole_result, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"whole json     : {args.json_whole}")
        if args.keep:
            print(f"temp copy kept : {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


def _run_pipeline(pipeline, attempts: int = 5, delay_s: float = 6.0) -> None:
    """Run the pipeline, waiting out «KiCad is busy» — the editor answers that
    while the operator is dragging something, and it is NOT a measurement."""
    from kipy.errors import ApiError

    for attempt in range(1, attempts + 1):
        try:
            pipeline.run()
            return
        except ApiError as exc:
            if "busy" not in str(exc).lower() or attempt == attempts:
                raise
            print(f"KiCad is busy ({attempt}/{attempts}) — waiting {delay_s} s")
            time.sleep(delay_s)


def _counts(rows, field: str) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        out[row[field]] = out.get(row[field], 0) + 1
    return out


def _clone_for(pipeline, cell_name: str, cluster: str):
    """The clone the pipeline resolved for this cell instance — resolved by the
    SAME `clone_registry_identity` the adoption pass uses, never by reading a
    field name the model may not carry."""
    from kicadstamp.placement.services.clone_position_calculator import (
        clone_registry_identity)

    cfg = pipeline.cfg
    for item in getattr(pipeline, "items", ()) or ():
        obj = getattr(item, "obj", None)
        if obj is None or getattr(item, "kind", None) != "clone":
            continue
        _anchor_id, resolved_cell, cell_key_part = clone_registry_identity(
            obj, cfg.cells)
        if resolved_cell != cell_name or cell_key_part is None:
            continue
        if cluster and getattr(obj, "cluster", None) != cluster:
            continue
        return obj
    return None


def _whole_frame_walk(pipeline, config_path, sheet_names) -> dict:
    """The FRAME vocabulary over EVERY instance of the profile — the door a refused
    tree lives on (no commands there at all). Walks the run's own clone instances
    with the planner's role resolution, so an instance is resolved exactly as the
    at-current-place pass resolves it."""
    from kicadstamp.placement.services.clone_position_calculator import (
        ClonePositionCalculator, clone_registry_identity)

    adapter = pipeline.adapter
    cfg = pipeline.cfg
    calculator = ClonePositionCalculator(adapter, cfg, sheet_names=sheet_names)
    totals: dict[str, int] = {}
    witnesses: list[dict] = []
    instances = 0
    unbuilt = non_rigid = 0
    # Of the pairs standing at a neighbour's place, how many the at-current-place
    # door ALREADY releases today (the «another net» rule) and how many would be
    # NEW to the measured rule. The second number is the size of the change.
    already = new = unresolved = 0
    for item in getattr(pipeline, "items", ()) or ():
        clone = getattr(item, "obj", None)
        if clone is None or getattr(item, "kind", None) != "clone":
            continue
        anchor_id, cell_name, cell_key_part = clone_registry_identity(
            clone, cfg.cells)
        if cell_name is None or cell_key_part is None:
            continue
        try:
            role_refs_map = calculator.role_refs_of(clone, None)
        except Exception:
            continue
        instances += 1
        result = frame_door_instance(
            adapter, cfg, config_path, cell_name,
            getattr(clone, "cluster", None), getattr(clone, "sheet", None),
            sheet_names=sheet_names, clone=clone, own_refs_map=role_refs_map)
        if result.get("frame_residual_mm") is None:
            unbuilt += 1
        elif not result.get("rigid"):
            non_rigid += 1
        for row in result["rows"]:
            totals[row["new_verdict"]] = totals.get(row["new_verdict"], 0) + 1
            if row["new_verdict"] == NEW_UNRESOLVED:
                unresolved += 1
            if row["new_verdict"] in (NEW_SHIFTED, NEW_UNRESOLVED):
                if row["new_verdict"] == NEW_SHIFTED:
                    if row.get("today_shifted"):
                        already += 1
                    else:
                        new += 1
                witnesses.append({"cell": cell_name,
                                  "cluster": getattr(clone, "cluster", None),
                                  "sheet": getattr(clone, "sheet", None),
                                  "record": f"{row['kind']}|{row['role_part']}|"
                                            f"{row['index']}",
                                  "uuid": row["uuid"],
                                  "verdict": row["new_verdict"],
                                  "today_shifted": row.get("today_shifted"),
                                  "own_distance_mm": row.get("own_distance_mm"),
                                  "witnesses": row["witnesses"]})
    print(f"\n== whole profile — frame vocabulary: {instances} instance(s) ==")
    print(f"   no frame: {unbuilt}, non-rigid: {non_rigid}")
    print(f"   records by measured verdict: {totals}")
    print(f"   at a neighbour's place: already released today {already}, "
          f"NEW {new}, net unresolved {unresolved}")
    for row in witnesses[:20]:
        print(f"   · {row['cell']} / {row['cluster']} / {row['sheet']}  "
              f"{row['record']}  {row['verdict']}")
    return {"instances": instances, "no_frame": unbuilt, "non_rigid": non_rigid,
            "records_by_verdict": totals, "already_released": already,
            "new_releases": new, "net_unresolved": unresolved,
            "witnesses": witnesses}


if __name__ == "__main__":
    sys.exit(main())
