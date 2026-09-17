#!/usr/bin/env python3
"""Stage 4 probe — what happens to the copper when a Scheme List becomes a Cell?

2026-09-17, chat plan "Scheme List -> cell" (stage 4 of the spoke work): a
refdes-based Scheme List record, once its components carry Roles, is turned into
an ordinary cloneable Cell and its Entity switches from `scheme_list` to `cell`.
The risk is duplicated copper on the next redraw. What decides it, from the code:

  * Scheme List copper takes NO part in the registry
    (scheme_list_apply.execute_scheme_list_plans: positional idempotency only),
    its keys look like "scheme_list:{entity}:via:{i}" and are never stored;
  * the cell path keys its copper "{anchor_id}|{cell}|{role|__spoke__}|{i}" and
    registers it; before reconcile, adopt_matching_unowned CLAIMS a live item
    that exactly matches a planned-but-unregistered command (via_matches /
    track_matches) and is not owned by another key — so the switch is duplicate-
    free exactly when the cell reproduces the recorded copper EXACTLY.

This probe measures the preconditions on the live board, per scheme_list Entity:

  1. registry: keys that mention the entity or start with "scheme_list:" (expected
     none), and the registry entries count;
  2. the Scheme List plan (plan_all_scheme_lists, only=[entity]) — components,
     vias, tracks — or the error;
  3. conversion precondition: the target refs' Roles — missing, and Roles that
     occur more than once (a record with repeated Roles cannot become ONE cell;
     it is a spoke bank or several cells);
  4. adoption precondition: how many planned vias/tracks already match a live
     item exactly, and how many of those live items are ALREADY owned by a
     registry entry (adopt_matching_unowned would refuse them -> a duplicate).

What it does NOT measure: whether a Cell extracted from the same components
reproduces the recorded copper bit-exactly — that needs the conversion itself.

READ-ONLY: own KiCad socket, closed in `finally`; registry files are read, never
written.

Usage:
    python -m kicadstamp.diagnostics.probe_scheme_list_to_cell [config] [--entity NAME]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config                                 # noqa: E402
from kicadstamp.constants import ROLE_FIELD_NAME                          # noqa: E402
from kicadstamp.diagnostics.probe_spoke_cell_identification import first_line  # noqa: E402
from kicadstamp.exceptions import ValidationError                         # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter                    # noqa: E402
from kicadstamp.registry import track_matches, via_matches                # noqa: E402
from kicadstamp.scheme_list_apply import plan_all_scheme_lists            # noqa: E402
from kicadstamp.utils.paths import (                                      # noqa: E402
    registry_path_for_config,
    track_registry_path_for_config,
)

DEFAULT_PROFILE = (Path(__file__).resolve().parents[2] / "profiles"
                   / "3ch-awg-tia-v103-old" / "config.sexp")


def load_registry(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", nargs="?", default=str(DEFAULT_PROFILE))
    parser.add_argument("--entity", help="only this scheme_list Entity")
    args = parser.parse_args()

    cfg, ctx = load_config(args.config)
    sheet_names = dict(ctx.sheet_names or {})
    via_reg_path = ctx.registry_path or registry_path_for_config(args.config)
    track_reg_path = ctx.track_registry_path or track_registry_path_for_config(args.config)
    via_reg, track_reg = load_registry(via_reg_path), load_registry(track_reg_path)
    owned = {e.get("uuid") for e in list(via_reg.values()) + list(track_reg.values())}
    print(f"profile: {args.config}")
    print(f"registries: vias {len(via_reg)} entries ({via_reg_path}), "
          f"tracks {len(track_reg)} entries ({track_reg_path})")
    scheme_keys = [k for k in list(via_reg) + list(track_reg) if k.startswith("scheme_list:")]
    print(f"registry keys starting with 'scheme_list:': {len(scheme_keys)} "
          "(expected 0 — Scheme List copper is not registered)")

    entities = [e for e in cfg.entities if e.scheme_list
                and (args.entity is None or e.name == args.entity)]
    if not entities:
        print("no scheme_list Entities in this profile")
        return 0

    adapter = KiCadBoardAdapter()
    try:
        adapter.refresh_board()
        fp_by_ref = {fp.ref: fp for fp in adapter.get_footprints()}
        live_vias, live_tracks = adapter.get_vias(), adapter.get_tracks()
        for entity in entities:
            print(f"\n=== entity {entity.name!r}  scheme_list {entity.scheme_list!r}  "
                  f"sheet {entity.sheet!r}")
            # Whole-segment match only: "channel_0" is a substring of many
            # unrelated keys ("pif_dvdd_channel_0|...").
            mentions = [k for k in list(via_reg) + list(track_reg)
                        if k.startswith(f"scheme_list:{entity.name}:")
                        or entity.name in k.split("|")]
            print(f"  1. registry keys naming the entity as a whole segment: {len(mentions)}")
            try:
                plans = plan_all_scheme_lists(adapter, cfg, sheet_names, only=[entity.name])
            except ValidationError as exc:
                print(f"  2. plan FAILS — {first_line(exc)}")
                continue
            if not plans:
                print("  2. no plan (the entity is not placed by any tree node) — "
                      "the copper checks need a placed entity; roles checked on the record refs")
                record = next((s for s in cfg.scheme_lists if s.name == entity.scheme_list), None)
                if record is not None:
                    refs = [c.ref for c in record.components]
                    roles = {r: (adapter.get_field_value(fp_by_ref[r], ROLE_FIELD_NAME)
                                 if r in fp_by_ref else None) for r in refs}
                    repeated = Counter(v for v in roles.values() if v)
                    print(f"  3. record refs {len(refs)}: without Role "
                          f"{sum(1 for v in roles.values() if not v)}; Roles occurring more than "
                          f"once: {sum(1 for n in repeated.values() if n > 1)}; record copper: vias "
                          f"{len(record.vias)}, tracks {len(record.tracks)}")
                continue
            for plan in plans:
                print(f"  2. plan mode {plan.mode}: moves {len(plan.moves)}, vias {len(plan.vias)}, "
                      f"tracks {len(plan.tracks)}")
                refs = [m.ref for m in plan.moves]
                roles = {ref: (adapter.get_field_value(fp_by_ref[ref], ROLE_FIELD_NAME)
                               if ref in fp_by_ref else None) for ref in refs}
                missing = sorted(r for r, v in roles.items() if not v)
                repeated = {v: n for v, n in Counter(v for v in roles.values() if v).items() if n > 1}
                print(f"  3. target refs {len(refs)}: without Role {len(missing)}"
                      + (f" ({', '.join(missing[:15])}{' …' if len(missing) > 15 else ''})" if missing else "")
                      + f"; Roles occurring more than once: {len(repeated)}"
                      + (" — " + ", ".join(f"{v} x{n}" for v, n in sorted(repeated.items())[:10])
                         if repeated else " — a single cell is possible"))
                via_hits = [next((lv for lv in live_vias if via_matches(lv, v)), None) for v in plan.vias]
                track_hits = [next((lt for lt in live_tracks if track_matches(lt, t)), None)
                              for t in plan.tracks]
                matched_v = [h for h in via_hits if h is not None]
                matched_t = [h for h in track_hits if h is not None]
                owned_v = sum(1 for h in matched_v if h.uuid in owned)
                owned_t = sum(1 for h in matched_t if h.uuid in owned)
                print(f"  4. planned copper already on the board exactly: vias "
                      f"{len(matched_v)}/{len(plan.vias)}, tracks {len(matched_t)}/{len(plan.tracks)}; "
                      f"of those ALREADY owned by a registry key: vias {owned_v}, tracks {owned_t}")
                if len(matched_v) < len(plan.vias) or len(matched_t) < len(plan.tracks):
                    print("     not every planned item sits on the board: adoption cannot "
                          "claim the rest — a conversion redraw would CREATE them (board not "
                          "synced with the record, or the record is stale)")
                if owned_v or owned_t:
                    print("     owned items are never adopted: a cell-path plan would create a "
                          "second copy next to them")
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
