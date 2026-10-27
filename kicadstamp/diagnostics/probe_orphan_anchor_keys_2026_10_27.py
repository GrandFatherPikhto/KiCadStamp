# kicadstamp/diagnostics/probe_orphan_anchor_keys_2026_10_27.py
"""probe_orphan_anchor_keys_2026_10_27.py — registry keys whose RECORD is gone from the config.

Ч0 of `plan_2026_10_27_orphan_anchor_keys_own_nothing` (worker: DeepSeek). The live
defect (v103, 15:12): reading the instance `fpga_oscill_pif` subtracted 18 selected
items as «another record's copper», and the registry key responsible —
`name:e8b2c497-…` — is the anchor of an ENTITY (`fpga_oscill_fpga`) the config no
longer holds: Denis rebuilt the FPGA cell between 15:04 and 15:12. Under that anchor
the two registry files carry 21 tracks + 4 vias, and `reconcile`'s prune would delete
every one of them on the first real Redraw of ANY record.

This probe MEASURES, and nothing else:

  1. the CONFIG's anchor ids, through the very builder `reconcile` is protected by
     (`apply_pipeline._compute_all_anchor_ids` — one name for both questions, never
     a second copy), split by prefix;
  2. the REGISTRY keys: which of their `anchor_id`s the config no longer produces
     (an ORPHAN KEY), how many live items each holds, whether a live key holds the
     same uuid as well (the «two keys on one item» danger), and which prefixes they
     carry — a `point:`/`pad:`/`thermal:`/`net:` anchor is a different record kind
     and is named, not judged;
  3. how many of those live items lie inside the bounding box of one CLUSTER's live
     footprints — the closest honest stand-in for «the 18 items Denis had selected»
     when no selection is taken;
  4. whether the live anchor of `e8b2c497` is still in the config and in the
     registries (the two facts the defect rests on).

READ-ONLY: the board is only read, the profile is COPIED (`_copy_profile`
absolutizes its relative paths), nothing is written anywhere.

USAGE
    .venv/bin/python -m kicadstamp.diagnostics.probe_orphan_anchor_keys_2026_10_27 \
        [--profile profiles/3ch-awg-tia-v103/config.sexp] \
        [--cluster FPGA_OSCILL_PIF] [--anchor e8b2c497] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from kicadstamp.config.loader import load_config
from kicadstamp.config_working_set import set_active_graph_root
from kicadstamp.constants import CLUSTER_FIELD_NAME, DEFAULT_TIMEOUT_MS
from kicadstamp.diagnostics.probe_reread_noise_2026_10_26 import _copy_profile
from kicadstamp.registry import PROTECTED_ANCHOR_PREFIXES, load_registry_entries
from kicadstamp.utils.units import MM

__all__ = ["anchor_prefix", "orphan_rows", "regions_of"]

#: Prefixes that name a record KIND rather than a cell record. A key under one of
#: them can never be «another cell's copper», so the Ч1 predicate must ask about the
#: cell-record prefixes only — this list is the reason the probe prints them apart.
CELL_RECORD_PREFIXES = ("name:", "anchor:", "role:")


def anchor_prefix(anchor_id: str) -> str:
    """The `prefix:` part of a registry anchor, or '(none)'."""
    head, sep, _rest = str(anchor_id or "").partition(":")
    return f"{head}:" if sep else "(none)"


def regions_of(anchor_id: str) -> str:
    """What KIND of record an anchor names — «cell record» (name:/anchor:/role:) or
    one of the physical/other prefixes, so the report never lumps them together."""
    prefix = anchor_prefix(anchor_id)
    return "cell record" if prefix in CELL_RECORD_PREFIXES else prefix


def orphan_rows(via_entries, track_entries, anchors) -> list[dict]:
    """One row per registry KEY: its anchor, the prefix/region it belongs to, whether
    the config still produces that anchor, and the uuid it stores."""
    rows: list[dict] = []
    for kind, entries in (("via", via_entries or {}), ("track", track_entries or {})):
        for key, entry in (entries or {}).items():
            anchor = str(key).split("|")[0]
            rows.append({"kind": kind, "key": key, "anchor": anchor,
                         "prefix": anchor_prefix(anchor),
                         "region": regions_of(anchor),
                         "protected": anchor.startswith(PROTECTED_ANCHOR_PREFIXES),
                         "orphan": anchor not in anchors,
                         "uuid": getattr(entry, "uuid", None)})
    return rows


def _bbox(points) -> Optional[tuple]:
    """(x0, y0, x1, y1) in mm of the given (x_mm, y_mm) points, or None."""
    pts = list(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _inside(point, box) -> bool:
    if box is None or point is None:
        return False
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _item_point(item):
    """(x_mm, y_mm) of a live via's centre / a live track's midpoint."""
    if hasattr(item, "position"):
        return (item.position.x / MM, item.position.y / MM)
    return (((item.start.x + item.end.x) / 2) / MM,
            ((item.start.y + item.end.y) / 2) / MM)


def _describe(item) -> str:
    if item is None:
        return "—"
    if hasattr(item, "position"):
        return (f"via ({item.position.x / MM:g},{item.position.y / MM:g}) "
                f"net={item.net_name}")
    return (f"track ({item.start.x / MM:g},{item.start.y / MM:g})->"
            f"({item.end.x / MM:g},{item.end.y / MM:g}) net={item.net_name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile",
                        default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--cluster", default="FPGA_OSCILL_PIF")
    parser.add_argument("--anchor", default="e8b2c497",
                        help="a fragment of the anchor the defect names")
    parser.add_argument("--json", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    profile = Path(args.profile).resolve()
    if not profile.is_file():
        raise SystemExit(f"no such profile: {profile}")

    workdir = Path(tempfile.mkdtemp(prefix="orphan-anchor-"))
    result: dict[str, Any] = {"profile": str(profile), "cluster": args.cluster,
                              "anchor_fragment": args.anchor}
    adapter = None
    try:
        copy_config = _copy_profile(profile, workdir)
        set_active_graph_root(copy_config)
        cfg, _ctx = load_config(str(copy_config))

        from kicadstamp.adapter_factory import create_board_adapter
        from kicadstamp.apply_pipeline import _compute_all_anchor_ids

        # ── 1. the config's anchors, through the builder reconcile is protected by ─
        anchors = _compute_all_anchor_ids(cfg)
        by_prefix: dict[str, int] = {}
        for anchor in anchors:
            by_prefix[anchor_prefix(anchor)] = by_prefix.get(anchor_prefix(anchor),
                                                            0) + 1
        print(f"config         : {copy_config}")
        print(f"profile config : records — clone_placements {len(cfg.clone_placements)},"
              f" entities {len(cfg.entities)}, net_traces {len(cfg.net_traces)},"
              f" thermal_via_arrays {len(cfg.thermal_via_arrays)}")
        print(f"config anchors : {len(anchors)}  by prefix {by_prefix}")
        result["config_anchors"] = {"total": len(anchors), "by_prefix": by_prefix}

        # ── 2. the registry keys ───────────────────────────────────────────────────
        via_entries, track_entries, owner = load_registry_entries(str(copy_config),
                                                                 cfg)
        rows = orphan_rows(via_entries, track_entries, anchors)
        live = {"via": {}, "track": {}}
        adapter = create_board_adapter(DEFAULT_TIMEOUT_MS, config_path=str(copy_config))
        try:
            # The adapter is born without a board handle: the pipeline's own first
            # step is `refresh_board()`, and a probe about the BOARD must do the
            # same or read a None handle (measured: AttributeError on _board).
            adapter.refresh_board()
            for kind, getter in (("via", "get_vias"), ("track", "get_tracks")):
                fn = getattr(adapter, getter, None)
                for item in (list(fn() or ()) if fn else []):
                    live[kind][getattr(item, "uuid", None)] = item
        except Exception as exc:                      # a busy editor is not a silent 0
            print(f"!! live board read failed: {exc!r} — the counts below are NOT "
                  f"the board's")
            result["live_error"] = repr(exc)
        live_by_uuid = {uuid: (kind, item) for kind, items in live.items()
                        for uuid, item in items.items()}
        uuid_keys: dict[str, set] = {}
        for row in rows:
            if row["uuid"]:
                uuid_keys.setdefault(row["uuid"], set()).add(row["key"])

        orphan_keys = [r for r in rows if r["orphan"]]
        orphan_anchors: dict[str, dict] = {}
        for row in orphan_keys:
            slot = orphan_anchors.setdefault(row["anchor"], {
                "anchor": row["anchor"], "prefix": row["prefix"],
                "region": row["region"], "protected": row["protected"],
                "keys": 0, "kinds": set(), "uuid_live": 0, "uuid_shared": 0,
                "items": []})
            slot["keys"] += 1
            slot["kinds"].add(row["kind"])
            uuid = row["uuid"]
            if uuid in live_by_uuid:
                slot["uuid_live"] += 1
                if len(uuid_keys.get(uuid, ())) > 1:
                    slot["uuid_shared"] += 1
                slot["items"].append({"uuid": uuid, "kind": row["kind"],
                                      "key": row["key"],
                                      "shared_with": sorted(
                                          k for k in uuid_keys.get(uuid, ())
                                          if k != row["key"]),
                                      "live": _describe(live_by_uuid[uuid][1])})

        print(f"\n== registry keys: {len(rows)} (via "
              f"{len(via_entries or {})} + track {len(track_entries or {})}) ==")
        print(f"   keys whose anchor the config still produces : "
              f"{len(rows) - len(orphan_keys)}")
        print(f"   keys whose anchor it does NOT (ORPHAN)      : {len(orphan_keys)}"
              f" over {len(orphan_anchors)} anchor(s)")
        region_counts: dict[str, int] = {}
        for row in orphan_keys:
            region_counts[row["region"]] = region_counts.get(row["region"], 0) + 1
        print(f"   orphan keys by region                       : {region_counts}")

        # ── 3. how much live copper the orphans hold ───────────────────────────────
        live_orphan = [r for r in rows
                       if r["orphan"] and r["uuid"] in live_by_uuid]
        shared = [r for r in live_orphan if len(uuid_keys.get(r["uuid"], ())) > 1]
        print(f"   their keys holding a LIVE item              : {len(live_orphan)}"
              f"  (uuid also held by a live key: {len(shared)})")
        result["registry"] = {
            "keys": len(rows), "orphan_keys": len(orphan_keys),
            "orphan_anchors": len(orphan_anchors),
            "orphan_keys_by_region": region_counts,
            "orphan_keys_with_live_item": len(live_orphan),
            "orphan_uuids_also_held_by_a_live_key": len(shared),
        }

        # ── 4. the box of the CLUSTER's live footprints ────────────────────────────
        board_fps = list(adapter.get_footprints() or ()) if adapter else []
        cluster_fps = [fp for fp in board_fps
                       if adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
                       == args.cluster]
        box = _bbox(((fp.position.x / MM, fp.position.y / MM)
                     for fp in cluster_fps))
        inside = [r for r in live_orphan
                  if _inside(_item_point(live_by_uuid[r["uuid"]][1]), box)]
        print(f"\n== cluster {args.cluster}: {len(cluster_fps)} live footprint(s), "
              f"bbox {box} ==")
        print(f"   orphan live items inside that box           : {len(inside)}")
        result["cluster"] = {"footprints": len(cluster_fps), "bbox": box,
                             "orphan_live_items_inside": len(inside)}

        # ── 5. the anchor the defect names ─────────────────────────────────────────
        named = {a: s for a, s in orphan_anchors.items() if args.anchor in a}
        in_config = sorted(a for a in anchors if args.anchor in a)
        print(f"\n== the anchor fragment {args.anchor!r} ==")
        print(f"   in the config's anchors                     : "
              f"{in_config or 'NO'}")
        for anchor, slot in named.items():
            print(f"   orphan anchor {anchor}")
            print(f"      region {slot['region']}, protected prefix "
                  f"{slot['protected']}, keys {slot['keys']} "
                  f"({', '.join(sorted(slot['kinds']))}), live uuids "
                  f"{slot['uuid_live']}, of them shared {slot['uuid_shared']}")
            for item in slot["items"][:30]:
                print(f"      · {item['kind']} {item['uuid'][:8]} {item['live']}"
                      f"{'' if not item['shared_with'] else '   SHARED with ' + ', '.join(item['shared_with'])}")
        result["named"] = {"in_config": in_config,
                           "orphans": {a: {**s, "kinds": sorted(s["kinds"])}
                                       for a, s in named.items()}}
        result["all_orphan_anchors"] = {
            a: {**s, "kinds": sorted(s["kinds"])}
            for a, s in sorted(orphan_anchors.items())}
    finally:
        if adapter is not None:
            close = getattr(adapter, "close", None)
            if close:
                close()
        if args.json:
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False,
                                                  indent=2, default=list),
                                       encoding="utf-8")
            print(f"\njson           : {args.json}")
        if args.keep:
            print(f"temp copy kept : {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
