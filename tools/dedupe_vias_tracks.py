#!/usr/bin/env python3
"""
dedupe_vias_tracks.py — finds vias/tracks stacked on top of each other at
(near-)identical position+net(+layer) on the CURRENTLY OPEN board and
deletes all but one of each duplicate group.

Why this exists (see techdocs/handoff/ 2026-07-29 Power duplicate incident):
registry.json bookkeeping (kicadstamp/registry.py) only protects against
duplicates within ONE registry file. If the same physical clone_placement
ever gets applied under two different registry_path/track_registry_path
(e.g. registry_path added to a config after some vias were already created
under the auto-derived path, or a config file renamed without migrating its
registry) — each reconcile() only knows about its OWN registry's UUIDs and
happily creates a second copy on top of the first; neither side can detect
the other. Confirmed live: boards/3ch-awg-tia/profiles/registries/
power_board.tracks.registry.json and power.tracks.registry.json both
contain an entry for the exact same registry key
("role:CONN_PM5V:::1:5.0000:48.0000|ldo_adj|__spoke__|0"), same coordinates,
different UUIDs — two real, separately-tracked copies of the same track.

This tool deliberately ignores registry.json entirely and looks at GROUND
TRUTH — the live board via IPC — so it works regardless of which registry
(if any) is out of sync, or whether there's a registry at all. It does NOT
touch registry.json: once the extra live via/track is gone, the next normal
`apply` run's reconcile() will see the stale registry entry pointing at a
UUID that's no longer on the board and prune it on its own (see
registry.py's "registry is out of sync... recreating as if the entry never
existed" path) — no manual JSON editing needed.

Grouping (greedy, not full transitive clustering — fine here since real
duplicates come from re-running an identical planner, so they land at
near-exactly the same spot, not spread across a chain of near-misses):
  vias:   same net, position within POSITION_TOLERANCE_MM of the group's
          first member. drill/diameter are NOT part of the key (mirrors
          PlacementRegistry._live_matches's own tolerance-only comparison)
          but a mismatch inside a group is printed as a warning.
  tracks: same net and layer, (start, end) as an UNORDERED pair (A->B and
          B->A are the same physical track) within tolerance.

Keeps an arbitrary member of each group (first one returned by the
adapter) and deletes the rest — arbitrary is fine because, by definition of
the grouping key, the kept and deleted items are physically indistinguishable.

Usage:
  python tools/dedupe_vias_tracks.py --dry-run
  python tools/dedupe_vias_tracks.py
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, List, Sequence, Tuple, TypeVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.utils.units import MM
from kicadstamp.constants import POSITION_TOLERANCE_MM
from kicadstamp.domain.board import Via, Track
from kicadstamp.domain.geometry import BoardLayer

T = TypeVar("T")


def _layer_str(layer: BoardLayer) -> str:
    return "B.Cu" if layer == BoardLayer.BL_B_Cu else "F.Cu"


def _find_duplicate_groups(items: Sequence[T], key_fn: Callable[[T], tuple],
                            pos_fn: Callable[[T], Tuple[float, ...]],
                            tol_mm: float) -> List[List[T]]:
    """Groups items sharing key_fn(item) whose pos_fn(item) coordinates are
    all within tol_mm of the group's first member. Returns only groups with
    2+ members (the actual duplicates)."""
    by_key = defaultdict(list)
    for it in items:
        by_key[key_fn(it)].append(it)

    duplicate_groups: List[List[T]] = []
    for bucket in by_key.values():
        groups: List[Tuple[Tuple[float, ...], List[T]]] = []
        for it in bucket:
            pos = pos_fn(it)
            for rep_pos, group_items in groups:
                if all(abs(a - b) <= tol_mm for a, b in zip(pos, rep_pos)):
                    group_items.append(it)
                    break
            else:
                groups.append((pos, [it]))
        duplicate_groups.extend(group_items for _, group_items in groups if len(group_items) > 1)
    return duplicate_groups


def _via_key(via: Via) -> tuple:
    return (via.net_name,)


def _via_pos(via: Via) -> Tuple[float, float]:
    return (via.position.x / MM, via.position.y / MM)


def _track_key(track: Track) -> tuple:
    return (track.net_name, track.layer)


def _track_pos(track: Track) -> Tuple[float, float, float, float]:
    s = (track.start.x / MM, track.start.y / MM)
    e = (track.end.x / MM, track.end.y / MM)
    s, e = (s, e) if s <= e else (e, s)
    return (s[0], s[1], e[0], e[1])


def _report_via_group(group: List[Via]) -> None:
    net = group[0].net_name or "?"
    x_mm, y_mm = _via_pos(group[0])
    # drill_mm/diameter_mm are already in mm (domain DTO conversion) — no / MM.
    sizes = {(round(v.drill_mm, 4), round(v.diameter_mm, 4)) for v in group}
    print(f"  via net={net!r} @ ({x_mm:.4f}, {y_mm:.4f}) mm — {len(group)} copies")
    if len(sizes) > 1:
        print(f"    [warning] drill/diameter differ within this group: {sorted(sizes)}")
    for v in group:
        print(f"    {v.uuid}")


def _report_track_group(group: List[Track]) -> None:
    net = group[0].net_name or "?"
    layer = _layer_str(group[0].layer)
    sx, sy, ex, ey = _track_pos(group[0])
    print(f"  track net={net!r} layer={layer} @ ({sx:.4f},{sy:.4f}) -> ({ex:.4f},{ey:.4f}) mm — "
          f"{len(group)} copies")
    for t in group:
        print(f"    {t.uuid}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="Only report duplicates, delete nothing")
    args = ap.parse_args()

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()

    via_groups = _find_duplicate_groups(adapter.get_vias(), _via_key, _via_pos, POSITION_TOLERANCE_MM)
    track_groups = _find_duplicate_groups(adapter.get_tracks(), _track_key, _track_pos, POSITION_TOLERANCE_MM)

    if not via_groups and not track_groups:
        print("No duplicates found.")
        return

    deleted = 0
    if via_groups:
        print(f"Duplicate via groups: {len(via_groups)}")
        for group in via_groups:
            _report_via_group(group)
            for v in group[1:]:
                if args.dry_run:
                    print(f"    [dry-run] would delete {v.uuid}")
                else:
                    adapter.remove_by_id(v.uuid)
                    deleted += 1

    if track_groups:
        print(f"Duplicate track groups: {len(track_groups)}")
        for group in track_groups:
            _report_track_group(group)
            for t in group[1:]:
                if args.dry_run:
                    print(f"    [dry-run] would delete {t.uuid}")
                else:
                    adapter.remove_by_id(t.uuid)
                    deleted += 1

    if args.dry_run:
        total_extra = sum(len(g) - 1 for g in via_groups) + sum(len(g) - 1 for g in track_groups)
        print(f"\n[dry-run] would delete {total_extra} duplicate item(s), keeping one per group.")
    else:
        print(f"\nDeleted {deleted} duplicate item(s), kept one per group.")


if __name__ == "__main__":
    main()
