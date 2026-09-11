"""Capture / compare the tree-mount conversion baseline (task Y.7.2).

The acceptance criterion of plan_2026_09_11_tree_mount_nodes.md (Y.7.2) is
BIT-EXACT equality of a tree's layout AND of every materialized cell component
before and after the mount-node rewrite. The "before" config uses the
own_anchor grammar, which that task deleted — so the comparison cannot be
re-run inside one pytest session after the migration. It is therefore captured
ONCE here, against a deterministic fake board, and frozen as JSON; the
permanent test (tests/test_tree_mount_conversion.py) then asserts the CONVERTED
config reproduces the frozen numbers.

This script used to live in the gitignored `diagnostics/`, which is why the
frozen JSON was never committed and the acceptance test was red everywhere but
the author's machine (fixed in plan_2026_09_11_trees_dock_single_panel.md Z.1).
It now sits inside the package, which IS tracked, next to the other probe
scripts.

THE BASELINE IS FROZEN AND CAN NO LONGER BE REGENERATED. `--out` needs the
PRE-migration config (own_anchor grammar), which the post-migration parser
rejects outright ("a nested (anchor ...) is only valid on a (kind mount)
node"). So config.sexp cannot be snapshotted any more, and recapturing the
numbers is only possible from a pre-B1 checkout. Do not try to "fix" a diff by
re-running --out: the only meaningful operation here is a RE-VERIFY of the
converted config against the frozen numbers:

    python -m kicadstamp.diagnostics.tree_mount_baseline \
        tests/fixtures/trees_and_overlay/config.converted.sexp \
        --compare tests/fixtures/trees_and_overlay/expected_geometry.json

If the fake board below (_FAKE_FOOTPRINTS) ever changes, the identical tuple
in tests/test_tree_mount_conversion.py MUST change with it, or the frozen
numbers stop describing the board the test builds.

Deterministic by construction: the fake board below answers every role lookup
the tree needs (AD_DAC + its pads 3/11/18, OP_AMP + its pads 4/8) with FIXED
positions and a deliberately non-zero base rotation, so a rotation-invariance
slip in the conversion shows up immediately. The numbers are arbitrary on
purpose: the test only needs the SAME board on both sides.

Usage:
    python -m kicadstamp.diagnostics.tree_mount_baseline <config> --out <json>
    python -m kicadstamp.diagnostics.tree_mount_baseline <config> --compare <json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kipy.board_types import FootprintInstance

from kicadstamp.cell_frame import CellFrame
from kicadstamp.config import load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.tree_position import layout_tree_from_base
from kicadstamp.utils.units import MM

_ORIGIN = Vector2.from_xy(0, 0)

# A deliberately rotated base: a conversion that drops the base rotation must
# fail this comparison, not pass it (design 2026-09-11 §3.8 / §7.11).
_BASE_ANGLE = 30.0

# (ref, role, cluster, x_mm, y_mm, angle_deg, {pad: (dx_mm, dy_mm)})
_FAKE_FOOTPRINTS = (
    ("IC2", "AD_DAC", "DAC_BUF", 220.55, 113.4881, _BASE_ANGLE,
     {"3": (0.0, 0.0), "11": (1.7, 3.2), "18": (4.9, 2.0)}),
    ("U7", "OP_AMP", "DAC_BUF", 232.575, 116.3506, _BASE_ANGLE,
     {"4": (-1.95, -4.225), "8": (0.0, 0.0)}),
)


def _fake_adapter():
    """A duck-typed board carrying exactly the anchors the fixture's tree
    resolves: two footprints with fixed poses and pads."""
    footprints = []
    for ref, role, cluster, x_mm, y_mm, angle, pads in _FAKE_FOOTPRINTS:
        fp = MagicMock(spec=FootprintInstance)
        fp.ref = ref
        fp._role = role
        fp._cluster = cluster
        fp.position = Vector2.from_xy_mm(x_mm, y_mm)
        fp.angle_deg = angle
        fp._pads = [
            MagicMock(number=num, net_name="X",
                      position=Vector2.from_xy_mm(x_mm + dx, y_mm + dy))
            for num, (dx, dy) in pads.items()
        ]
        footprints.append(fp)

    adapter = MagicMock()
    adapter.get_footprints.return_value = footprints
    adapter.has_field.return_value = True

    def _field(fp, name):
        if name == "Role":
            return getattr(fp, "_role", None)
        if name == CLUSTER_FIELD_NAME:
            return getattr(fp, "_cluster", None)
        return None

    adapter.get_field_value.side_effect = _field
    adapter.get_selected_items.return_value = []
    adapter.get_pad_by_number.side_effect = lambda fp, num: next(
        (p for p in getattr(fp, "_pads", []) if p.number == num), None)
    return adapter


def _round(value: float) -> float:
    return round(float(value), 6)


def snapshot(config_path: str) -> dict:
    """The full geometry fingerprint of one config: every tree node's absolute
    position/rotation under a zero base, plus every materialized cell component
    in world mm."""
    cfg, ctx = load_config(config_path)
    sheet_names = dict(ctx.sheet_names or {})
    adapter = _fake_adapter()

    data: dict = {"trees": {}, "clones": {}}
    for tree in cfg.trees:
        laid = layout_tree_from_base(
            tree, _ORIGIN, 0.0, None,
            adapter=adapter, cfg=cfg, sheet_names=sheet_names)
        data["trees"][tree.name] = {
            ref: [_round(pos.x / MM), _round(pos.y / MM), _round(rot)]
            for ref, (pos, rot) in sorted(laid.items())
        }

    for clone in materialize_entity_placements(adapter, cfg, sheet_names):
        cell = cfg.cells[clone.cell]
        frame = CellFrame(
            placement_origin=Vector2.from_xy(
                int(clone.xy[0] * MM), int(clone.xy[1] * MM)),
            rotation_deg=clone.rotation_deg,
            mirror=bool(clone.mirror),
            mount=cell_mount_offset(cell),
        )
        components = {}
        for slot in cell.components:
            wx, wy = frame.point_to_world_mm(
                slot.offset_along_mm or 0.0, slot.offset_across_mm or 0.0)
            components[slot.role] = [_round(wx), _round(wy)]
        data["clones"][clone.name] = {
            "cell": clone.cell,
            "xy": [_round(clone.xy[0]), _round(clone.xy[1])],
            "rotation_deg": _round(clone.rotation_deg),
            "mirror": bool(clone.mirror),
            "components": components,
        }
    return data


def compare(expected: dict, actual: dict) -> list[str]:
    """Every difference, as human-readable lines (empty == bit-exact)."""
    diffs: list[str] = []
    for tree, nodes in expected["trees"].items():
        got = actual["trees"].get(tree)
        if got is None:
            diffs.append(f"tree {tree!r}: missing in the converted config")
            continue
        for ref, want in nodes.items():
            if ref not in got:
                diffs.append(f"tree {tree!r} node {ref!r}: missing")
                continue
            if got[ref] != want:
                diffs.append(f"tree {tree!r} node {ref!r}: {want} -> {got[ref]}")
        for ref in got:
            if ref not in nodes:
                diffs.append(f"tree {tree!r} node {ref!r}: unexpected")
    for name, want in expected["clones"].items():
        got = actual["clones"].get(name)
        if got is None:
            diffs.append(f"clone {name!r}: missing in the converted config")
            continue
        for key in ("cell", "xy", "rotation_deg", "mirror"):
            if got[key] != want[key]:
                diffs.append(f"clone {name!r}.{key}: {want[key]} -> {got[key]}")
        for role, wpos in want["components"].items():
            gpos = got["components"].get(role)
            if gpos != wpos:
                diffs.append(
                    f"clone {name!r} component {role!r}: {wpos} -> {gpos}")
    for name in actual["clones"]:
        if name not in expected["clones"]:
            diffs.append(f"clone {name!r}: unexpected")
    return diffs


def main() -> int:
    config_path = sys.argv[1]
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
        out.write_text(json.dumps(snapshot(config_path), indent=2, sort_keys=True)
                       + "\n", encoding="utf-8")
        print(f"wrote {out}")
        return 0
    if "--compare" in sys.argv:
        expected = json.loads(
            Path(sys.argv[sys.argv.index("--compare") + 1]).read_text(encoding="utf-8"))
        diffs = compare(expected, snapshot(config_path))
        if diffs:
            print(f"DIFFERS ({len(diffs)}):")
            for line in diffs:
                print(f"  {line}")
            return 1
        print("BIT-EXACT: layout and materialized components match the baseline")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
