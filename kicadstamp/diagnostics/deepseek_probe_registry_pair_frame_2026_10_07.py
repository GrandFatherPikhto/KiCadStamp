# kicadstamp/diagnostics/deepseek_probe_registry_pair_frame_2026_10_07.py
"""Step 0 probe for plan_2026_10_07_registry_pair_frame_check.md (variant Б).

THE QUESTION (finding of the f3e116d0 acceptance): a registry key of copper is
``anchor|cell|role_part|index``, where ``index`` is the record's number in the
cell's list AT THE LAST REDRAW. Editing the cell's record lists ("Refresh
geometry from selection" strictly, "Add", "Subtract") shifts those numbers, and
nobody renumbers the registry — so the tier-1 pair (``registry_match``: uuid by
key, NO geometry check) can hand a live item to the WRONG record. Variant Б
answers with a geometric check of the pair IN THE CELL FRAME built from the
instance's LIVE components (the frame "Refresh geometry from selection" already
uses, so it does not depend on the tree). This probe MEASURES how far apart the
two are, so the tolerance and the rule for a NON-RIGID cluster are chosen from
numbers, not guessed.

WHAT IS PRINTED, per cell (``fpga``, ``dac_buf``) and per its instances
(``(cluster, sheet)`` addresses from the loaded config), all READ-ONLY:

  * the frame's ``residual_mm`` (how far the live cluster is from a rigid copy of
    the cell), its rotation/mirror/mount, and whether it is rigid
    (``cell_frame.RIGID_TOLERANCE_MM``);
  * for every pair found BY THE REGISTRY ALONE for this instance
    (``own_instance_context`` + ``own_registry_entries`` — the SAME "own key"
    filter «Select cell» uses): kind, ``role_part``, ``index``, uuid, and FOUR
    distances in mm (a via — its centre; a track — both ends, max,
    orientation-tolerant, the convention ``_greedy_nearest`` minimises):
      - ``d_pair``    — where the record puts the copper in the live frame vs
        THE LIVE COPPER THE REGISTRY'S UUID POINTS AT (the tier-1 pair itself;
        ``----`` when that uuid is not on the board);
      - ``d_nearest`` — the same but against the NEAREST live item of that kind:
        the pair measured by PLACE, which is what variant Б compares. It answers
        the same question when the registry's uuid bookkeeping is stale, and it
        is the column the tolerance has to be chosen from;
      - ``d_live_reg`` — the live item (the uuid's, else the nearest) vs the
        position the registry stored: "the board moved since the last redraw";
      - ``d_rec_reg``  — the frame's prediction vs the stored position: "the cell
        was edited since the last redraw";
  * how many pairs agree at 0.01 / 0.05 / 0.1 mm — separately for ``d_pair`` and
    for ``d_nearest`` — and how many are CLEARLY foreign (> 1.0 mm = ten times
    the widest candidate tolerance);
  * what CANNOT be checked this way: registry keys naming this cell whose
    ``index`` has NO record in the cell today (the shifted/stale keys this whole
    plan is about), keys whose uuid is not on the live board, and the cell's
    records the registry has NO key for (``_cell_record_slots`` minus the keys
    seen).

The frame is built by the VERY code path "Refresh geometry from selection" uses
(``cell_geometry_refresh._cell_selection_context`` with the cell's own
``anchor_role`` -> ``_cell_frame_for``) — the probe deliberately does NOT
re-implement it: step 1 of the plan extracts the ONE public builder from there,
and a second copy here would prove nothing.

READ-ONLY: it reads the config, the two registry files and the live board and
writes NOTHING (no registry, no config, no board, nothing on disk). Run it on a
COPY of the profile (rule 34: ``cp -r profiles/<profile> profiles/<profile>copy``)
— the copy's own registry files are read, and the one thing this probe must never
do is make the live profile look written to.

Usage:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_registry_pair_frame_2026_10_07 \
        [config.sexp] [--cell fpga] [--cell dac_buf]
Default config: profiles/3ch-awg-tia-v103-pairprobecopy/config.sexp.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.absent_copper_prune import (_cell_record_slots, _record_tail,
                                            own_instance_context,
                                            own_registry_entries)
from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.cell_geometry_refresh import (_cell_frame_for,
                                              _cell_selection_context)
from kicadstamp.config import load_config
from kicadstamp.constants import SPOKE_LEVEL_ROLE_PLACEHOLDER
from kicadstamp.registry import load_registry_entries
from kicadstamp.selection_narrowing import cell_record_addresses
from kicadstamp.trees import check_tree_self_anchor_drift
from kicadstamp.utils.units import MM

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = (_ROOT / "profiles" / "3ch-awg-tia-v103-pairprobecopy"
                   / "config.sexp")
_DEFAULT_CELLS = ("fpga", "dac_buf")

# The tolerances Denis and Claude choose from: how many pairs agree at each.
TOLERANCES_MM = (0.01, 0.05, 0.1)
# Ten times the widest candidate tolerance — "clearly foreign", not a rounding.
FOREIGN_MM = 1.0
_DASH = "----    "


def _fmt(value) -> str:
    return _DASH if value is None else f"{value:8.4f}"


@dataclass
class Ctx:
    """Everything the report reads: the config, its two registries and the live
    board items, read ONCE (rule: "two paths must not read at different
    times"). ``live_points`` caches each live item's mm points so the nearest
    search over ~2000 items per pair does not rebuild them every time."""

    config_path: str
    cfg: object
    sheet_names: dict
    adapter: object
    via_entries: dict = field(default_factory=dict)
    track_entries: dict = field(default_factory=dict)
    live_vias: dict = field(default_factory=dict)
    live_tracks: dict = field(default_factory=dict)
    entry_by_uuid: dict = field(default_factory=dict)
    live_pts: dict = field(default_factory=dict)

    def live(self, kind: str) -> dict:
        return self.live_vias if kind == "via" else self.live_tracks

    def points_of(self, kind: str) -> list:
        return self.live_pts[kind]

    def cache_points(self) -> None:
        for kind in ("via", "track"):
            self.live_pts[kind] = [(_live_points(item), item)
                                   for item in self.live(kind).values()]


# ── the cell's records (the `index` side of a registry key) ──────────────────

def _record_of(cell, kind: str, role_part: str, index: int):
    """``(points, None)`` for the cell record a registry tail names, or
    ``(None, reason)`` when the cell holds NO such record today — a key whose
    number has shifted past the end of the list, or a role the cell no longer
    has. Both levels production resolves are covered: a cell-level via/track
    under ``__spoke__`` and a component's via under its role.

    ``points`` — one ``(along_mm, across_mm)`` for a via, two for a track."""
    if kind == "via":
        if role_part == SPOKE_LEVEL_ROLE_PLACEHOLDER:
            records, where = list(cell.vias), "__spoke__ (cell level)"
        else:
            slot = next((s for s in cell.components
                         if getattr(s, "role", None) == role_part), None)
            if slot is None:
                return None, f"no component slot with role {role_part!r}"
            records = list(getattr(slot, "vias", None) or ())
            where = f"role {role_part!r}"
        if not 0 <= index < len(records):
            return None, (f"index {index} out of range — {where} holds "
                          f"{len(records)} record(s)")
        rec = records[index]
        return ((float(rec.offset_along_mm), float(rec.offset_across_mm)),), None

    if role_part != SPOKE_LEVEL_ROLE_PLACEHOLDER:
        return None, (f"track key with role part {role_part!r} — tracks are "
                      "stored at cell level only")
    tracks = list(cell.tracks)
    if not 0 <= index < len(tracks):
        return None, (f"index {index} out of range — the cell holds "
                      f"{len(tracks)} track(s)")
    t = tracks[index]
    return ((float(t.start_along_mm), float(t.start_across_mm)),
            (float(t.end_along_mm), float(t.end_across_mm))), None


# ── distances (mm) ───────────────────────────────────────────────────────────

def _predicted(frame, points) -> tuple:
    """Where the RECORD puts its copper, in the live board's mm — through the
    frame's own ``point_to_world_mm`` (the transform "Refresh geometry from
    selection" reads and writes the cell through)."""
    return tuple(frame.point_to_world_mm(along, across)
                 for along, across in points)


def _live_points(live) -> tuple:
    """``(x_mm, y_mm)`` of a live via's centre / a live track's two ends."""
    if hasattr(live, "position"):
        return ((live.position.x / MM, live.position.y / MM),)
    return ((live.start.x / MM, live.start.y / MM),
            (live.end.x / MM, live.end.y / MM))


def _registry_points(kind: str, entry) -> tuple:
    """The position the registry stored for one entry, in mm."""
    if kind == "via":
        return ((float(entry.x_mm), float(entry.y_mm)),)
    return ((float(entry.start_x_mm), float(entry.start_y_mm)),
            (float(entry.end_x_mm), float(entry.end_y_mm)))


def _max_pair_distance(a: tuple, b: tuple) -> float:
    """max endpoint distance between two same-length point tuples. A track may
    have been re-routed the other way round, so the orientation that fits better
    is taken — the SAME convention ``_greedy_nearest`` minimises."""
    if len(a) == 1:
        return math.dist(a[0], b[0])
    straight = max(math.dist(a[0], b[0]), math.dist(a[1], b[1]))
    flipped = max(math.dist(a[0], b[1]), math.dist(a[1], b[0]))
    return min(straight, flipped)


def _nearest(points: tuple, candidates: list):
    """``(distance_mm, item)`` of the CLOSEST live item to ``points``, or
    ``(None, None)`` on an empty board."""
    best, item = None, None
    for candidate_pts, candidate in candidates:
        d = _max_pair_distance(points, candidate_pts)
        if best is None or d < best:
            best, item = d, candidate
    return best, item


def _verdict(distance) -> str:
    if distance is None:
        return "uuid off-board"
    for tol in TOLERANCES_MM:
        if distance <= tol:
            return f"ok@{tol:g}"
    return "FOREIGN" if distance > FOREIGN_MM else "apart"


def _agreement(counters: dict, prefix: str) -> str:
    return (", ".join(f"<={tol} mm {counters[f'{prefix}_{tol}']}"
                      for tol in TOLERANCES_MM)
            + f"; clearly foreign (>{FOREIGN_MM} mm) "
              f"{counters[f'{prefix}_foreign']}")


# ── the cell's registry keys, and the trees that are not redrawn ─────────────

def _anchor_prefix(key: str) -> str:
    anchor = str(key or "").split("|")[0]
    return anchor.split(":", 1)[0] + ":" if ":" in anchor else anchor


def _key_census(cell, cell_identity, ctx: Ctx, own_checked: set) -> dict:
    """Every registry key that NAMES this cell, grouped by (kind, anchor prefix),
    with how many of them have NO record in the cell today (the shifted/stale
    keys this plan is about), how many point at copper that is not on the live
    board, and how many no instance of the cell claims as its own."""
    rows: dict = {}
    for kind, entries in (("via", ctx.via_entries),
                          ("track", ctx.track_entries)):
        for key, entry in (entries or {}).items():
            tail = _record_tail(key, cell_identity)
            if tail is None:
                continue
            bucket = rows.setdefault(
                (kind, _anchor_prefix(key)),
                {"keys": 0, "stale": 0, "off_board": 0, "not_own": 0})
            bucket["keys"] += 1
            # own_checked holds TRIPLES — a bare (role_part, index) tail never
            # matches it (the first run then reported every key as "not
            # claimed").
            if (kind, tail[0], tail[1]) not in own_checked:
                bucket["not_own"] += 1
            if _record_of(cell, kind, tail[0], tail[1])[0] is None:
                bucket["stale"] += 1
            if getattr(entry, "uuid", None) not in ctx.live(kind):
                bucket["off_board"] += 1
    return rows


def _tree_refs(nodes):
    """Every node ref of a tree, depth first."""
    for node in nodes or ():
        ref = getattr(node, "ref", None)
        if ref:
            yield ref
        yield from _tree_refs(getattr(node, "children", None))


def _refused_cells(cfg, refused: list) -> dict:
    """{cell name -> [(tree name, guard message), ...]} for the trees the drift
    guard refused. A refused tree is NOT redrawn, so nothing ever rebuilds that
    cell's registry keys — which is exactly why the index drift can be permanent
    there. Config only, no board."""
    by_ref = {getattr(e, "name", None): getattr(e, "cell", None)
              for e in (getattr(cfg, "entities", None) or ())}
    out: dict = {}
    refused_names = {name for name, _msg in refused}
    for tree in getattr(cfg, "trees", None) or ():
        if tree.name not in refused_names:
            continue
        msg = next(m for n, m in refused if n == tree.name)
        for ref in _tree_refs(getattr(tree, "nodes", None)):
            cell = by_ref.get(ref)
            if cell:
                out.setdefault(cell, []).append((tree.name, msg))
    return out


# ── one instance ─────────────────────────────────────────────────────────────

def _report_instance(cell_name: str, cell, ctx: Ctx, cluster, sheet,
                     counters: dict) -> set:
    """Print every registry pair of ONE instance and its frame's residual.
    Returns ``{(kind, role_part, index)}`` the instance claims as its own."""
    print(f"\n  instance (cluster={cluster!r}, sheet={sheet!r})")
    feet = own_instance_context(ctx.adapter, ctx.cfg, cell_name, cluster, sheet,
                                sheet_names=ctx.sheet_names)
    footprints, cell_identity, own_addresses, chosen_address, refs = feet
    print(f"    live footprints of the cluster: {len(footprints)}, "
          f"cell identity {cell_identity!r}, "
          f"{len(refs)} ref(s) claimed as this cell's")

    components = [{"role": s.role,
                   "offset_along_mm": float(s.offset_along_mm),
                   "offset_across_mm": float(s.offset_across_mm)}
                  for s in cell.components]
    # The frame of "Refresh geometry from selection" — the SAME two calls the
    # GUI makes (build_refresh_plan: _cell_selection_context -> _cell_frame_for),
    # with the SAME origin_role (the cell's own anchor_role).
    frame = None
    try:
        role_to_ref, matched, origin, mount, problems = _cell_selection_context(
            components, footprints, ctx.adapter, "probe",
            origin_role=cell.anchor_role)
        if problems:
            print("    role problems: " + " | ".join(
                " ".join(str(p).split()) for p in problems))
        if origin is not None:
            frame = _cell_frame_for(components, matched, footprints,
                                    role_to_ref, origin, mount)
    except Exception as e:  # noqa: BLE001 — a probe reports, never stops
        print(f"    FRAME NOT BUILT: {type(e).__name__}: "
              f"{' '.join(str(e).split())}")
    if frame is None:
        print("    frame: NOT BUILT — the pairs below cannot be checked by place")
    else:
        counters["frames"] += 1
        counters["non_rigid"] += 0 if frame.is_rigid else 1
        counters["worst_residual_mm"] = max(counters["worst_residual_mm"],
                                            frame.residual_mm)
        print(f"    frame: residual {_fmt(frame.residual_mm)} mm "
              f"(rigid <= {RIGID_TOLERANCE_MM}) "
              f"{'RIGID' if frame.is_rigid else 'NON-RIGID'}; "
              f"rotation {frame.rotation_deg:g} deg, mirror {frame.mirror}, "
              f"mount (along {frame.mount[0]:g}, across {frame.mount[1]:g})")

    entries = own_registry_entries(ctx.via_entries, ctx.track_entries,
                                   cell_identity, own_addresses,
                                   chosen_address, refs)
    checked = {(kind, role_part, index)
               for kind, role_part, index, _uuid in entries}
    print(f"    registry pairs for this instance: {len(entries)} "
          f"(vias {sum(1 for e in entries if e[0] == 'via')}, "
          f"tracks {sum(1 for e in entries if e[0] == 'track')})")
    print("      kind  role_part         idx  uuid                                  "
          "d_pair    d_nearest d_live_reg d_rec_reg  verdict")

    local = {"pair": 0, "nearest": 0}
    for kind, role_part, index, uuid in sorted(entries,
                                               key=lambda e: (e[0], e[1], e[2])):
        counters["pairs"] += 1
        counters["pairs_" + kind] += 1
        label = f"      {kind:5s} {role_part[:14]:14s} {index:3d}  {uuid}"
        points, reason = _record_of(cell, kind, role_part, index)
        if points is None:
            counters["key_without_record"] += 1
            print(f"{label}  ----     ----      ----        ----       "
                  f"KEY WITHOUT RECORD: {reason}")
            continue
        entry = ctx.entry_by_uuid.get(uuid)
        live = ctx.live(kind).get(uuid)
        predicted = None if frame is None else _predicted(frame, points)
        d_pair = d_nearest = None
        d_live_reg = d_rec_reg = None
        if predicted is not None:
            near_item = None
            d_nearest, near_item = _nearest(predicted, ctx.points_of(kind))
            if live is not None:
                d_pair = _max_pair_distance(predicted, _live_points(live))
                counters["pair_measured"] += 1
                for tol in TOLERANCES_MM:
                    if d_pair <= tol:
                        counters[f"pair_{tol}"] += 1
                if d_pair > FOREIGN_MM:
                    counters["pair_foreign"] += 1
                counters["worst_pair_mm"] = max(counters["worst_pair_mm"],
                                                d_pair)
                local["pair"] += 1
            else:
                counters["uuid_missing"] += 1
            counters["nearest_measured"] += 1
            for tol in TOLERANCES_MM:
                if d_nearest is not None and d_nearest <= tol:
                    counters[f"nearest_{tol}"] += 1
            if d_nearest is not None and d_nearest > FOREIGN_MM:
                counters["nearest_foreign"] += 1
            if d_nearest is not None:
                counters["worst_nearest_mm"] = max(counters["worst_nearest_mm"],
                                                   d_nearest)
                local["nearest"] += 1
            if entry is not None:
                stored = _registry_points(kind, entry)
                against = live if live is not None else near_item
                if against is not None:
                    d_live_reg = _max_pair_distance(stored,
                                                    _live_points(against))
                d_rec_reg = _max_pair_distance(predicted, stored)
        print(f"{label}  {_fmt(d_pair)}  {_fmt(d_nearest)} "
              f"{_fmt(d_live_reg)} {_fmt(d_rec_reg)}  {_verdict(d_pair)}")

    without_registry = len(_cell_record_slots(cell) - checked)
    counters["without_registry"] += without_registry
    print(f"    records of this cell with NO registry key (not checkable this "
          f"way): {without_registry}")
    counters["instances"] += 1
    return checked


# ── one cell ─────────────────────────────────────────────────────────────────

def _report_cell(cell_name: str, ctx: Ctx, counters: dict, refused: dict) -> None:
    cell = (getattr(ctx.cfg, "cells", {}) or {}).get(cell_name)
    print(f"\n=== cell {cell_name!r}")
    if cell is None:
        print("  NOT IN THE CONFIG")
        counters["cells_missing"] += 1
        return
    counters["cells"] += 1
    print(f"  in the config: {len(cell.components)} component(s), "
          f"{len(cell.vias)} via(s), {len(cell.tracks)} track(s); "
          f"anchor_role {cell.anchor_role!r}, anchor_xy {cell.anchor_xy}, "
          f"layer {cell.layer}")
    for tree_name, msg in refused.get(cell_name, ()):
        print(f"  NOTE: the drift guard REFUSED tree {tree_name!r}, which places "
              f"this cell — no redraw, so this cell's registry keys are never "
              f"rebuilt: {msg[:160]}")

    addresses = cell_record_addresses(ctx.cfg, cell_name)
    instances = sorted({(c, s) for c, s in addresses.values()},
                       key=lambda a: (str(a[0]), str(a[1])))
    print(f"  records placing it in the config: {len(addresses)}; distinct "
          f"instance address(es): {instances if instances else '<none>'}")
    if not instances:
        counters["no_instance"] += 1

    cell_identity = None
    own_checked: set = set()
    for cluster, sheet in instances:
        own_checked |= _report_instance(cell_name, cell, ctx, cluster, sheet,
                                        counters)
        if cell_identity is None:
            cell_identity = own_instance_context(
                ctx.adapter, ctx.cfg, cell_name, cluster, sheet,
                sheet_names=ctx.sheet_names)[1]

    print("  registry keys naming this cell (by kind and anchor prefix):")
    if cell_identity is None:
        print("    <no instance — nothing to census>")
        return
    rows = _key_census(cell, cell_identity, ctx, own_checked)
    if not rows:
        print("    (none)")
    for (kind, prefix), row in sorted(rows.items()):
        print(f"    {kind:5s} {prefix:10s} keys {row['keys']:4d}  "
              f"no record today {row['stale']:4d}  "
              f"uuid not on the board {row['off_board']:4d}  "
              f"not claimed by the instance(s) {row['not_own']:4d}")


# ── main ─────────────────────────────────────────────────────────────────────

def _refused_trees(cfg) -> list:
    """(tree name, guard message) for every tree the drift guard refuses — pure
    config, no board. A refused tree is NOT redrawn: the mirror of the registry
    index drift this plan is about."""
    out = []
    for tree in getattr(cfg, "trees", None) or ():
        try:
            msg = check_tree_self_anchor_drift(cfg, tree)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {' '.join(str(e).split())}"
        if msg:
            out.append((tree.name, " ".join(str(msg).split())))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config", nargs="?", default=str(_DEFAULT_CONFIG))
    ap.add_argument("--cell", action="append", default=None,
                    help="cell name to probe (repeatable)")
    args = ap.parse_args()
    cells = tuple(args.cell) if args.cell else _DEFAULT_CELLS
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"config {config_path} not found")
        return 2
    mtime = _dt.datetime.fromtimestamp(config_path.stat().st_mtime)
    print(f"config: {config_path} (saved {mtime:%Y-%m-%d %H:%M:%S})")
    print(f"cells: {', '.join(cells)}")

    cfg, config_ctx = load_config(str(config_path))
    ctx = Ctx(config_path=str(config_path), cfg=cfg,
              sheet_names=dict(config_ctx.sheet_names or {}), adapter=None)

    refused_list = _refused_trees(cfg)
    refused = _refused_cells(cfg, refused_list)
    print(f"trees refused by the drift guard (config only): "
          f"{len(refused_list)}")
    for name, msg in refused_list:
        print(f"  refused: {name}: {msg[:200]}")

    counters = {"cells": 0, "cells_missing": 0, "no_instance": 0,
                "instances": 0, "frames": 0, "non_rigid": 0, "pairs": 0,
                "pairs_via": 0, "pairs_track": 0,
                "pair_measured": 0, "nearest_measured": 0,
                "uuid_missing": 0, "key_without_record": 0,
                "without_registry": 0,
                "pair_foreign": 0, "nearest_foreign": 0,
                "worst_residual_mm": 0.0, "worst_pair_mm": 0.0,
                "worst_nearest_mm": 0.0,
                **{f"pair_{tol}": 0 for tol in TOLERANCES_MM},
                **{f"nearest_{tol}": 0 for tol in TOLERANCES_MM}}

    ctx.adapter = create_board_adapter(config_path=str(config_path))
    try:
        ctx.adapter.refresh_board()
        try:
            project = ctx.adapter.get_board_project()
        except Exception as e:  # noqa: BLE001
            project = f"<{type(e).__name__}: {' '.join(str(e).split())}>"
        print(f"live board: project {project}")
        footprints = list(ctx.adapter.get_footprints() or ())
        vias = list(ctx.adapter.get_vias() or ())
        tracks = list(ctx.adapter.get_tracks() or ())
        ctx.via_entries, ctx.track_entries, _owner = load_registry_entries(
            str(config_path), cfg)
        ctx.live_vias = {getattr(v, "uuid", None): v for v in vias}
        ctx.live_tracks = {getattr(t, "uuid", None): t for t in tracks}
        ctx.cache_points()
        for kind, entries in (("via", ctx.via_entries),
                              ("track", ctx.track_entries)):
            for entry in (entries or {}).values():
                uuid = getattr(entry, "uuid", None)
                if uuid:
                    ctx.entry_by_uuid[uuid] = entry
        print(f"live board: {len(footprints)} footprint(s), {len(vias)} via(s), "
              f"{len(tracks)} track(s)")
        print(f"registry: vias {len(ctx.via_entries)} key(s), tracks "
              f"{len(ctx.track_entries)} key(s)")
        on_board_v = sum(1 for e in ctx.via_entries.values()
                         if getattr(e, "uuid", None) in ctx.live_vias)
        on_board_t = sum(1 for e in ctx.track_entries.values()
                         if getattr(e, "uuid", None) in ctx.live_tracks)
        print(f"registry traceability on the live board: via uuid(s) "
              f"{on_board_v}/{len(ctx.via_entries)}, track uuid(s) "
              f"{on_board_t}/{len(ctx.track_entries)} - whatever is "
              f"missing here is not this board's copper")

        for cell_name in cells:
            _report_cell(cell_name, ctx, counters, refused)
    finally:
        ctx.adapter.close()

    print("\n=== SUMMARY")
    print(f"cells probed {counters['cells']} "
          f"(missing {counters['cells_missing']}, no instance "
          f"{counters['no_instance']}); instances {counters['instances']}; "
          f"frames built {counters['frames']} (non-rigid "
          f"{counters['non_rigid']}), worst frame residual "
          f"{counters['worst_residual_mm']:.4f} mm")
    print(f"registry pairs {counters['pairs']} (vias {counters['pairs_via']}, "
          f"tracks {counters['pairs_track']}); keys without a record "
          f"{counters['key_without_record']}; records without a key "
          f"{counters['without_registry']}; pair uuid not on the board "
          f"{counters['uuid_missing']}")
    print(f"by d_pair (the registry's uuid) {counters['pair_measured']} "
          f"measured: {_agreement(counters, 'pair')}; worst "
          f"{counters['worst_pair_mm']:.4f} mm")
    print(f"by d_nearest (the place) {counters['nearest_measured']} measured: "
          f"{_agreement(counters, 'nearest')}; worst "
          f"{counters['worst_nearest_mm']:.4f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
