"""probe_in_place_entity_2026_11_04.py — Ч0.5 of
plan_2026_10_28_entity_outside_trees_in_place (§8.2 п. 1).

WHAT IT MEASURES. A record (Entity with cell+cluster, no imprint) that stands in NO
tree must still find its own copper: §2 of the plan says its live place is the place
its copper is planned at. This probe answers three questions about the LIVE board,
before a single line of the new branch exists:

1. what the live cluster frame of the record is (`_live_cluster_frame`, strict — a
   cluster that is not a rigid copy of the cell refuses);
2. what the redraw plan WOULD put on the board at that frame — the SAME
   `apply_clone_geometry` / `ClonePositionCalculator` chain the apply pipeline runs,
   fed with the live role map the frame itself resolved;
3. how many of those planned vias/tracks/components are ALREADY on the board, by
   the SAME predicates the product's geometry tier uses (`via_matches`,
   `track_matches` — re-exported by `kicadstamp.registry`), plus the worst component
   deviation.

The role map is NOT guessed here: each cell role is resolved by
`resolve_footprint_by_cluster_role` (Cluster + Role, narrowed by the record's Sheet),
the exact resolver `_live_cluster_frame` uses, and the calculator's own role
resolution is pinned to that map for this run. That is the ONE thing the future
branch will do differently (it will carry the same map in `clone.refs`), so the
numbers below are the numbers that branch will produce.

READ-ONLY, and it says so to the board: no `select_items`, no update/create/commit/
save, no file written. The FIRST thing after connecting is a board-name check; any
board but Denis's HP-Power-v099 is refused and nothing is read.

Run (KiCad open on HP-Power-v099):

    .venv/bin/python -m kicadstamp.diagnostics.probe_in_place_entity_2026_11_04

Exit codes: 0 — every record's plan matches the board (worst deviation
            <= RIGID_TOLERANCE_MM and every via/track found);
            1 — no board / wrong board / KiCad not reachable / config unreadable;
            2 — the board answered but a number is a mismatch (the plan's СТОП).
"""
from __future__ import annotations

import dataclasses
import sys
import traceback

from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.config import load_config
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import NonRigidClusterError, ValidationError
from kicadstamp.placement.entity_placement import _to_clone
from kicadstamp.placement.services.clone_position_calculator import (
    ClonePositionCalculator,
    clone_anchor_id,
)
from kicadstamp.placement.services.clone_role_resolver import (
    clone_uses_selection_mode,
)
from kicadstamp.placement.services.coordinate_position_calculator import (
    resolve_footprint_by_cluster_role,
)
from kicadstamp.registry import track_matches, via_matches
from kicadstamp.trees import _walk_nodes
from kicadstamp.utils.units import MM

from gui.docks.live_position import _live_cluster_frame

#: The ONE board this probe may read — Денис's REAL board. Anything else is refused
#: before a single footprint is read.
EXPECTED_BOARD = "HP-Power-v099.kicad_pcb"

#: The profile the numbers of §9 are taken from (trees=0, 4 entities — the shape the
#: whole plan is about).
PROFILE = "profiles/HP-Power/HP-Power.sexp"

#: Cell-side expectations, read from the profile's own `cells:` section in Ч0
#: (11 components, 4 vias, 28 tracks per `dcdc24p15n5`). Kept as numbers so a
#: surprising count is REPORTED, never silently averaged in.
EXPECTED_CELL_COPPER = {
    "dcdc24p15n5": (11, 4, 28),
}

#: Records to measure: every Entity of the profile, in the profile's own order.
RECORDS = ("dcdc24p15n5_sw_a", "dcdc24p15n5_sw_b", "driver_sw_a", "driver_sw_b")


def _mm(vec: Vector2) -> str:
    return f"({vec.x / MM:+9.4f}, {vec.y / MM:+9.4f})"


def _board_name(adapter) -> str | None:
    """The bare filename of the open PCB document, or None when the adapter cannot
    say (a lost socket, a reader that does not have it)."""
    getter = getattr(adapter, "get_board_filename", None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 — a probe reports, it does not raise
        return None


def _role_map(adapter, cell, cluster: str, sheet: str, sheet_names) -> dict:
    """role -> live footprint of ONE instance, through the resolver the frame itself
    uses (Cluster + Role, narrowed by the record's Sheet)."""
    label = f"cell {getattr(cell, 'name', '?')!r} on cluster {cluster!r}"
    return {slot.role: resolve_footprint_by_cluster_role(
        adapter, cluster, slot.role, label, sheet=sheet or None,
        sheet_names=sheet_names)
        for slot in cell.components}


def _trees_placing(cfg) -> dict:
    """{entity name: [tree names]} — which trees place each record. A record that
    stands in NO tree is the whole point of the plan; a record that DOES is measured
    the same way, but the branch will never see it, and §9 must say which is which."""
    out: dict = {}
    for tree in (getattr(cfg, "trees", None) or ()):
        for node in _walk_nodes(tree.nodes):
            if node.kind == "placement":
                out.setdefault(node.ref, []).append(tree.name)
    return out


def _measure_record(adapter, cfg, sheet_names, entity, live_vias, live_tracks,
                    placed_by):
    """One record: frame, plan, and the board comparison.

    Returns ``(lines, problems)`` — every line is printed; a non-empty ``problems``
    list is the plan's СТОП (exit code 2)."""
    lines: list[str] = []
    problems: list[str] = []
    cell = cfg.cells.get(entity.cell)
    name = entity.name
    cluster = entity.cluster or ""
    sheet = getattr(entity, "sheet", None) or ""
    trees = placed_by.get(name) or []
    lines.append(f"\n=== {name}  cell={entity.cell!r} cluster={cluster!r} sheet={sheet!r}")
    lines.append("    placed by tree: " + (", ".join(trees) if trees
                                           else "NO tree (the branch's own case)"))
    if cell is None:
        lines.append(f"    SKIP: cell {entity.cell!r} is not in the config")
        return lines, problems

    # ── 1. the live frame (strict: a cluster that is not a rigid copy refuses) ──
    try:
        origin, rotation, mirror = _live_cluster_frame(
            adapter, cell, cluster, sheet, sheet_names, strict=True)
    except NonRigidClusterError as exc:
        lines.append(f"    REFUSED by the strict read: {exc}")
        return lines, problems
    except ValidationError as exc:
        lines.append(f"    REFUSED by the read: {exc}")
        return lines, problems
    lines.append(f"    frame: origin {_mm(origin)} nm, rotation {rotation:.3f}°, "
                 f"mirror={mirror}")

    # ── 2. the plan the branch would place at that frame ────────────────────────
    role_to_fp = _role_map(adapter, cell, cluster, sheet, sheet_names)
    # ClonePlacement.refs is {role: REFDES}, not {role: Footprint} — the resolver's
    # step 0 looks the refdes up in its own by-ref index.
    role_refs = {role: fp.ref for role, fp in role_to_fp.items()}
    lines.append("    roles: " + ", ".join(
        f"{role}={ref}" for role, ref in sorted(role_refs.items())))
    selection_mode = clone_uses_selection_mode(
        _to_clone(entity, origin, rotation), adapter=adapter, cell=cell,
        sheet_names=sheet_names)
    lines.append(f"    clone_uses_selection_mode: {selection_mode}  "
                 f"(the planned branch refuses a record that needs a selection)")

    clone = dataclasses.replace(_to_clone(entity, origin, rotation),
                                mirror=mirror, refs=dict(role_refs))
    calc = ClonePositionCalculator(adapter, cfg, sheet_names=sheet_names)
    # Pin the calculator's OWN role resolution to the map the frame just resolved:
    # the calculator is asked for geometry, not for a second identification.
    calc._resolve_roles = (
        lambda placement, cell_, anchor_position, parent_rot: dict(role_refs))
    try:
        components, vias, tracks = calc.compute_raw_positions([clone])
    except ValidationError as exc:
        first = next((ln.strip() for ln in str(exc).splitlines() if ln.strip()), "")
        lines.append(f"    REFUSED by the plan: {first or exc}")
        problems.append(f"{name}: the plan refused ({first or exc})")
        return lines, problems

    expected = EXPECTED_CELL_COPPER.get(entity.cell)
    if expected is not None and expected != (len(components), len(vias), len(tracks)):
        problems.append(
            f"{name}: the plan carries {len(components)} component(s), {len(vias)} "
            f"via(s), {len(tracks)} track(s); the cell holds "
            f"{expected[0]}/{expected[1]}/{expected[2]}")
    lines.append(f"    plan: {len(components)} component(s), {len(vias)} via(s), "
                 f"{len(tracks)} track(s)   [anchor {clone_anchor_id(clone)}]")

    # ── 3a. components: worst deviation between the plan and the live footprints ─
    worst_mm = 0.0
    worst_ref = "-"
    for comp in components:
        fp = adapter.get_footprint(comp.ref)
        if fp is None:
            problems.append(f"{name}: planned component {comp.ref} is not on the board")
            continue
        dx = (comp.dest.x - fp.position.x) / MM
        dy = (comp.dest.y - fp.position.y) / MM
        deviation = (dx * dx + dy * dy) ** 0.5
        if deviation > worst_mm:
            worst_mm, worst_ref = deviation, comp.ref
    lines.append(f"    components: worst deviation {worst_mm:.6f} mm ({worst_ref}), "
                 f"tolerance {RIGID_TOLERANCE_MM} mm")
    if worst_mm > RIGID_TOLERANCE_MM:
        problems.append(f"{name}: worst component deviation {worst_mm:.4f} mm exceeds "
                        f"{RIGID_TOLERANCE_MM} mm")

    # ── 3b. copper: the product's own geometry predicates, per item ─────────────
    matched_vias = 0
    for via in vias:
        if any(via_matches(live, via) for live in live_vias):
            matched_vias += 1
        else:
            lines.append(f"    via MISS   {_mm(via.position)} net={via.net_name} "
                         f"drill={via.drill_mm} dia={via.diameter_mm}")
    matched_tracks = 0
    for track in tracks:
        if any(track_matches(live, track) for live in live_tracks):
            matched_tracks += 1
        else:
            lines.append(
                f"    track MISS ({track.start.x / MM:+.4f},{track.start.y / MM:+.4f})"
                f" -> ({track.end.x / MM:+.4f},{track.end.y / MM:+.4f}) "
                f"w={track.width_mm} net={track.net_name} layer={track.layer}")
    lines.append(f"    copper on the board: {matched_vias}/{len(vias)} via(s), "
                 f"{matched_tracks}/{len(tracks)} track(s)")
    if matched_vias != len(vias) or matched_tracks != len(tracks):
        problems.append(f"{name}: copper matched {matched_vias}/{len(vias)} via(s), "
                        f"{matched_tracks}/{len(tracks)} track(s)")
    return lines, problems


def main(argv: list[str] | None = None) -> int:
    from kicadstamp.adapter_factory import create_board_adapter

    cfg, ctx = load_config(PROFILE)
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    print(f"config : {PROFILE}")
    print(f"trees  : {len(getattr(cfg, 'trees', []) or [])}, "
          f"entities: {len(getattr(cfg, 'entities', []) or [])}, "
          f"sheets: {len(sheet_names)}")

    adapter = None
    problems: list[str] = []
    try:
        adapter = create_board_adapter(config_path=PROFILE)
        adapter.refresh_board()
        name = _board_name(adapter)
        print(f"board  : {name!r}")
        if (name or "").rsplit("/", 1)[-1] != EXPECTED_BOARD:
            print(f"REFUSING: this probe reads only {EXPECTED_BOARD!r}. "
                  f"Nothing was read from the open board.")
            return 1

        live_vias = list(adapter.get_vias() or ())
        live_tracks = list(adapter.get_tracks() or ())
        print(f"live   : {len(live_vias)} via(s), {len(live_tracks)} track(s) on the board")

        placed_by = _trees_placing(cfg)
        for record in RECORDS:
            entity = next((e for e in cfg.entities if e.name == record), None)
            if entity is None:
                print(f"\n=== {record}: NOT in the config (skipped)")
                continue
            lines, found = _measure_record(adapter, cfg, sheet_names, entity,
                                           live_vias, live_tracks, placed_by)
            print("\n".join(lines))
            problems.extend(found)

        print("\n=== SUMMARY")
        if not problems:
            print("OK: every record's plan stands where the board has it")
            return 0
        for problem in problems:
            print(f"MISMATCH: {problem}")
        return 2
    except Exception:  # noqa: BLE001 — a probe reports, the caller reads
        traceback.print_exc()
        return 1
    finally:
        if adapter is not None:
            adapter.close()


if __name__ == "__main__":
    sys.exit(main())
