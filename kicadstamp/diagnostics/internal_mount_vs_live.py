"""Internal mount vs live mount — measure the difference on the live board.

plan_2026_09_11_internal_mount §Г.6.1: on a SYNCHRONIZED board (nothing moved by
hand since the last Apply) the INTERNAL mount base (computed from the tree's own
layout — the drift fix) must equal the LIVE base (the component's current board
position) to within the nanometre rounding the nm-grid rotate primitive leaves.
That equality is the measurement that validates the whole internal maths (sign,
mirror, slot angle).

This probe prints, for every mount node of every tree:
  - the internal base (the new method),
  - the live base (the old method),
  - their delta in mm.

It reads the config and the live board, writes NOTHING. Run it after Apply, then
move/rotate the tree and run it again: the internal base must follow the tree
while the live base stays where the components currently are.

Read-only. Usage:
    python -m kicadstamp.diagnostics.internal_mount_vs_live <config> [tree_name]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.link_trees import link_trees
from kicadstamp.tree_position import mount_node_base, tree_layout_base
from kicadstamp.trees import _walk_nodes
from kicadstamp.utils.units import MM

# Deviation above this (mm) is not nm-rounding noise any more.
_TOLERANCE_MM = 1e-4


def _fmt(pos) -> str:
    return f"({pos.x / MM:+10.4f}, {pos.y / MM:+10.4f})"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    config_path = sys.argv[1]
    want_tree = sys.argv[2] if len(sys.argv) > 2 else None

    cfg, ctx = load_config(config_path)
    sheet_names = dict(ctx.sheet_names or {})
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    forest = link_trees(cfg, cfg.trees)

    problems = 0
    for tree in cfg.trees:
        if want_tree and tree.name != want_tree:
            continue
        mounts = [n for n in _walk_nodes(tree.nodes) if n.kind == "mount"]
        if not mounts:
            continue
        base_pos, base_rot = tree_layout_base(
            adapter, cfg, tree, sheet_names, forest)
        print(f"\n=== tree {tree.name!r}  effective base at {_fmt(base_pos)} "
              f"rot={base_rot}")
        for node in mounts:
            internal, internal_rot = mount_node_base(
                node, tree, base_pos, base_rot, adapter, cfg, sheet_names, forest)
            live, live_rot = mount_node_base(
                node, None, base_pos, base_rot, adapter, cfg, sheet_names, forest)
            dx_mm = (internal.x - live.x) / MM
            dy_mm = (internal.y - live.y) / MM
            delta_mm = (dx_mm * dx_mm + dy_mm * dy_mm) ** 0.5
            flag = "OK" if delta_mm <= _TOLERANCE_MM else "DIFF"
            if flag == "DIFF":
                problems += 1
            print(f"  {node.ref:28s} internal={_fmt(internal)} rot={internal_rot:8.3f}"
                  f"  live={_fmt(live)} rot={live_rot:8.3f}"
                  f"  delta={delta_mm:9.5f} mm  [{flag}]")

    print(f"\n{problems} mount(s) differ by more than {_TOLERANCE_MM} mm.")
    if problems:
        print("On a synchronized board this must be 0 (up to nm rounding): a "
              "non-zero delta means the internal maths disagrees with the board "
              "— report it, do not paper over it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
