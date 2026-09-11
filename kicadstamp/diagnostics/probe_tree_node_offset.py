"""Where does a tree node's stored offset actually put its Entity?

Denis 2026-09-11: "опять поменялись местами X и Y при размещении
pif_oa_n2v5_channel_0 в дереве ch0_dac_buf". Since d68a595 the node editor
shows the BOARD frame while the config stores the BASE's LOCAL frame, so the
two sets of numbers legitimately differ whenever the base is rotated. This
probe prints BOTH, plus what the stored value WOULD have to be for the node to
reproduce the cluster's current live position — so "wrong" and "different
frame" can be told apart.

Read-only. Usage:
    python -m kicadstamp.diagnostics.probe_tree_node_offset <config> <tree> [node_ref]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config
from kicadstamp.explore import Board
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver, resolve_anchor_pad_position,
)
from kicadstamp.tree_position import (
    _anchor_base_live_position, node_position, relative_rotation_deg,
)
from kicadstamp.utils.units import MM

from gui.docks.live_position import _live_cluster_frame


def _fmt(x, y) -> str:
    return f"({x:+9.4f}, {y:+9.4f})"


def main() -> int:
    config_path, tree_name = sys.argv[1], sys.argv[2]
    want_ref = sys.argv[3] if len(sys.argv) > 3 else None

    cfg, ctx = load_config(config_path)
    sheet_names = dict(ctx.sheet_names or {})
    board = Board.connect()
    adapter = board.adapter
    adapter.refresh_board()

    tree = next(t for t in cfg.trees if t.name == tree_name)
    anchor_pos, anchor_rot = _anchor_base_live_position(adapter, cfg, tree, sheet_names)
    print(f"tree {tree_name!r}: anchor live at "
          f"{_fmt(anchor_pos.x / MM, anchor_pos.y / MM)} rot={anchor_rot}")

    for node in tree.nodes:
        if want_ref and node.ref != want_ref:
            continue
        print(f"\n=== node {node.ref!r}  stored xy={node.xy} rotation={node.rotation}")

        # ── the node's BASE (own anchor, else the tree anchor) ───────────────
        if node.own_anchor is not None:
            a = node.own_anchor
            resolver = ComponentResolver(adapter, cfg, sheet_names)
            fp = resolver.resolve_anchor_fp(None, a.role, a.anchor_sheet,
                                            a.anchor_cluster, label=a.role)
            base_pos, base_rot = fp.position, fp.angle_deg
            if a.anchor_pad:
                base_pos = resolve_anchor_pad_position(adapter, fp, a.anchor_pad, a.role)
            print(f"    base: own anchor role={a.role!r} pad={a.anchor_pad!r} "
                  f"-> {fp.ref} at {_fmt(base_pos.x / MM, base_pos.y / MM)} rot={base_rot}")
        else:
            base_pos, base_rot = anchor_pos, anchor_rot
            print(f"    base: tree anchor at "
                  f"{_fmt(base_pos.x / MM, base_pos.y / MM)} rot={base_rot}")

        # ── where the stored offset PUTS the entity ─────────────────────────
        target = node_position(node, base_pos, base_rot or 0.0)
        print(f"    stored offset places the mount at "
              f"{_fmt(target.x / MM, target.y / MM)}")

        # ── where the entity's cluster ACTUALLY is ──────────────────────────
        entity = next((e for e in cfg.entities if e.name == node.ref), None)
        if entity is None or not entity.cell or entity.cell not in cfg.cells:
            print("    (no Entity/cell to compare against)")
            continue
        origin, live_rot, mirror = _live_cluster_frame(
            adapter, cfg.cells[entity.cell], entity.cluster,
            getattr(entity, "sheet", None) or "", sheet_names)
        print(f"    live cluster {entity.cluster!r} mount at "
              f"{_fmt(origin.x / MM, origin.y / MM)} rot={live_rot} mirror={mirror}")

        dx = (target.x - origin.x) / MM
        dy = (target.y - origin.y) / MM
        print(f"    ERROR (placed - live) = {_fmt(dx, dy)}  |d|={((dx*dx+dy*dy)**0.5):.4f} mm")

        # ── what the stored value SHOULD be ────────────────────────────────
        board_off = ((origin.x - base_pos.x) / MM, (origin.y - base_pos.y) / MM)
        local = rotate_local_offset(board_off[0], board_off[1], -(base_rot or 0.0))
        print(f"    board-frame offset (what the FORM should show): {_fmt(*board_off)}")
        print(f"    local-frame offset (what the CONFIG should hold): "
              f"{_fmt(local.x / MM, local.y / MM)}")
        if live_rot is not None and base_rot is not None:
            print(f"    rotation: live={live_rot} base={base_rot} "
                  f"-> config should hold {relative_rotation_deg(live_rot, base_rot)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
