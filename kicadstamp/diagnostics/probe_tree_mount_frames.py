"""Where a tree's rotations come from — mount by mount, node by node.

Denis, 2026-09-17, tree ch0_dac_buf: the reference component sits at 270°, and
to lay the decoupling parts out horizontally the same 270° has to be written
twice — on the mount node (opamp_4_n2v5) and on the node it holds
(pif_oa_n2v5_channel_0). This probe splits every angle into its parts so the
question "is that a duplication or two different things?" is answered from the
board, not from a formula on paper.

For every node of ONE tree it prints, walking the SAME composition the redraw
uses (tree_position.layout_tree_from_base):

  mount node
    - base: internal (the tree places the role) or live (the board), and the
      base angle it inherits — the component's (slot's) world angle;
    - the mount's own `rotation` and the resulting frame angle;
    - the PARENT frame angle (the tree frame, or the enclosing node's) and the
      difference frame - parent: 0 means the mount's rotation does nothing but
      cancel the component's angle (a "zero written through the body").
  placement node
    - `xy` as written (local), the same offset in BOARD axes and in the PARENT
      frame's axes;
    - own `rotation`, the world angle, and the angle relative to the parent
      frame — the rotation this node would need if the mount took its axes from
      the parent instead of from the component (the "parent axes" option
      discussed in design_2026_09_17_geometry_tree_and_user_tree);
    - LIVE check: every component of the placed cell, its expected board angle
      (slot angle + world angle) against the footprint's real angle.

A self-check compares this walk against layout_tree_from_base; any difference
means the probe drifted from the production code — its numbers are then not to
be trusted.

The config is read FROM DISK. Edits staged in the GUI working set and not yet
saved (File > Save) are invisible here — the header prints the file's mtime.

Read-only: reads the config and the live board, writes nothing. Module nodes and
copper containers are listed but their content is not unfolded.

Usage:
    python -m kicadstamp.diagnostics.probe_tree_mount_frames [config] [--tree NAME]
Defaults: profiles/3ch-awg-tia-v103/config.sexp, tree ch0_dac_buf.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.cell_frame import normalize_deg
from kicadstamp.config import load_config
from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.placement.services.component_resolver import ComponentResolver
from kicadstamp.tree_position import (layout_tree_from_base, mount_node_base,
                                      node_offset, node_position,
                                      rotate_offset_mm, tree_layout_base)
from kicadstamp.trees import resolve_internal_mount_match
from kicadstamp.utils.units import MM

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _ROOT / "profiles" / "3ch-awg-tia-v103" / "config.sexp"
_DEFAULT_TREE = "ch0_dac_buf"
# Above this the walk disagrees with production (not nm rounding any more).
_SELF_CHECK_MM = 1e-4
_SELF_CHECK_DEG = 1e-6
_ANGLE_TOL_DEG = 0.01


def _deg(a: float) -> str:
    return f"{normalize_deg(a):+8.3f}°"


def _xy(x_mm: float, y_mm: float) -> str:
    return f"({x_mm:+9.4f}, {y_mm:+9.4f})"


def _pos(p) -> str:
    return _xy(p.x / MM, p.y / MM)


def _local_mm(node) -> tuple[float, float]:
    off = node_offset(node)
    return off.x / MM, off.y / MM


def _live_component_angles(resolver, cfg, entity, world_rot: float, indent: str,
                           counters: dict) -> None:
    """Expected board angle of every component of the entity's cell against the
    live footprint. Mirrored placements are reported, not judged: their angle
    rule (180 - phi) is not what this probe is about."""
    cell = cfg.cells.get(entity.cell) if entity.cell else None
    if cell is None:
        print(f"{indent}live: entity has no cell ({entity.cell!r}) — skipped")
        return
    for slot in cell.components:
        try:
            fp = resolver.resolve_anchor_fp(None, slot.role, entity.sheet,
                                            entity.cluster, label=slot.role)
        except Exception as exc:  # noqa: BLE001 — a probe reports, never stops
            first = str(exc).strip().splitlines()
            print(f"{indent}live {slot.role:18s} not resolved: "
                  f"{first[0] if first else exc!r}")
            counters["unresolved"] += 1
            continue
        if entity.mirror:
            print(f"{indent}live {slot.role:18s} {fp.ref:6s} board {_deg(fp.angle_deg)} "
                  f"(mirrored placement — not compared)")
            continue
        expected = slot.angle_deg + world_rot
        delta = normalize_deg(fp.angle_deg - expected)
        flag = "OK" if abs(delta) <= _ANGLE_TOL_DEG else "DIFF"
        if flag == "DIFF":
            counters["angle_diff"] += 1
        print(f"{indent}live {slot.role:18s} {fp.ref:6s} slot {_deg(slot.angle_deg)} "
              f"+ world {_deg(world_rot)} = expected {_deg(expected)}, "
              f"board {_deg(fp.angle_deg)}  [{flag}]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config", nargs="?", default=str(_DEFAULT_CONFIG))
    ap.add_argument("--tree", default=_DEFAULT_TREE)
    args = ap.parse_args()

    config_path = Path(args.config)
    mtime = _dt.datetime.fromtimestamp(config_path.stat().st_mtime)
    cfg, ctx = load_config(str(config_path))
    sheet_names = dict(ctx.sheet_names or {})
    tree = next((t for t in cfg.trees if t.name == args.tree), None)
    if tree is None:
        print(f"tree {args.tree!r} not found; trees: "
              + ", ".join(sorted(t.name for t in cfg.trees)))
        return 2
    forest = {t.name: t for t in cfg.trees}
    entities = {e.name: e for e in cfg.entities}

    print(f"config: {config_path}  (saved {mtime:%Y-%m-%d %H:%M:%S} — unsaved "
          f"GUI edits are NOT seen)")

    adapter = KiCadBoardAdapter()
    counters = {"angle_diff": 0, "unresolved": 0, "self_check": 0,
                "pure_compensation": 0}
    try:
        adapter.refresh_board()
        resolver = ComponentResolver(adapter, cfg, sheet_names)
        base_pos, base_rot = tree_layout_base(adapter, cfg, tree, sheet_names, forest)
        print(f"\n=== tree {tree.name!r}: own rotation {_deg(tree.rotation)}, "
              f"layout base {_pos(base_pos)} at {_deg(base_rot)}")

        walked: dict[str, tuple] = {}

        def walk(nodes, pos, rot, depth: int) -> None:
            ind = "  " * depth
            for n in nodes:
                parent_pos, parent_rot = pos, rot
                if n.kind == "mount":
                    match = resolve_internal_mount_match(cfg, tree, n)
                    b_pos, b_rot = mount_node_base(
                        n, tree, base_pos, base_rot, adapter, cfg, sheet_names, forest)
                    frame_pos = node_position(n, b_pos, b_rot)
                    frame_rot = b_rot + n.rotation
                    a = n.anchor
                    print(f"\n{ind}mount {n.ref!r}: anchor {a.role}"
                          f"{' pad ' + str(a.anchor_pad) if a.anchor_pad else ''}"
                          f" — {'INTERNAL (the tree places it)' if match else 'LIVE (board)'}")
                    print(f"{ind}  base angle (component/slot) {_deg(b_rot)} "
                          f"+ mount rotation {_deg(n.rotation)} = frame {_deg(frame_rot)}")
                    rel = normalize_deg(frame_rot - parent_rot)
                    note = ""
                    if abs(rel) <= _ANGLE_TOL_DEG:
                        counters["pure_compensation"] += 1
                        note = ("  <- frame == parent axes: the mount's rotation only "
                                "cancels the component's angle")
                    print(f"{ind}  parent frame {_deg(parent_rot)}; frame - parent "
                          f"{_deg(rel)}{note}")
                    print(f"{ind}  frame origin {_pos(frame_pos)}")
                    walk(n.children, frame_pos, frame_rot, depth + 1)
                    continue
                if n.kind in ("module", "copper", "net_trace"):
                    print(f"{ind}{n.kind} {n.ref!r} — not unfolded")
                    continue
                abs_pos = node_position(n, parent_pos, parent_rot)
                abs_rot = parent_rot + n.rotation
                walked[n.ref] = (abs_pos, abs_rot)
                lx, ly = _local_mm(n)
                bx, by = rotate_offset_mm(lx, ly, parent_rot)
                print(f"\n{ind}{n.kind or 'node'} {n.ref!r}")
                print(f"{ind}  xy local {_xy(lx, ly)} -> board axes {_xy(bx, by)}")
                print(f"{ind}  own rotation {_deg(n.rotation)}; world {_deg(abs_rot)}; "
                      f"origin {_pos(abs_pos)}")
                entity = entities.get(n.ref)
                if entity is not None:
                    _live_component_angles(resolver, cfg, entity, abs_rot, ind + "  ",
                                           counters)
                walk(n.children, abs_pos, abs_rot, depth + 1)

        walk(tree.nodes, base_pos, base_rot, 0)

        # Parent-axes view of each mount's children: what the numbers would be if
        # the mount took its axes from its parent (mount rotation 0 there).
        print("\n--- parent-axes view (mount angle taken from the parent frame) ---")

        def parent_axes(nodes, parent_rot: float, depth: int) -> None:
            ind = "  " * depth
            for n in nodes:
                if n.kind == "mount":
                    b_pos, b_rot = mount_node_base(
                        n, tree, base_pos, base_rot, adapter, cfg, sheet_names, forest)
                    frame_rot = b_rot + n.rotation
                    for c in n.children:
                        if c.ref not in walked:
                            continue
                        lx, ly = _local_mm(c)
                        px, py = rotate_offset_mm(lx, ly, frame_rot - parent_rot)
                        world = walked[c.ref][1]
                        print(f"{ind}{n.ref} / {c.ref}: today mount {_deg(n.rotation)}, "
                              f"node {_deg(c.rotation)}, xy {_xy(lx, ly)}  ->  parent axes: "
                              f"mount 0, node {_deg(world - parent_rot)}, xy {_xy(px, py)}")
                    parent_axes(n.children, frame_rot, depth + 1)
                elif n.kind not in ("module", "copper", "net_trace"):
                    parent_axes(n.children, parent_rot + n.rotation, depth)

        parent_axes(tree.nodes, base_rot, 0)

        # Self-check against production.
        prod = layout_tree_from_base(tree, base_pos, base_rot, forest,
                                     adapter=adapter, cfg=cfg, sheet_names=sheet_names)
        for ref, (p, r) in walked.items():
            if ref not in prod:
                continue
            pp, pr = prod[ref]
            d_mm = (((p.x - pp.x) / MM) ** 2 + ((p.y - pp.y) / MM) ** 2) ** 0.5
            if d_mm > _SELF_CHECK_MM or abs(normalize_deg(r - pr)) > _SELF_CHECK_DEG:
                counters["self_check"] += 1
                print(f"SELF-CHECK DIFF {ref}: probe {_pos(p)} {_deg(r)} vs "
                      f"production {_pos(pp)} {_deg(pr)}")
    finally:
        adapter.close()

    print(f"\nsummary: mounts whose rotation only cancels the component angle: "
          f"{counters['pure_compensation']}; component angle mismatches: "
          f"{counters['angle_diff']}; unresolved components: "
          f"{counters['unresolved']}; self-check differences: "
          f"{counters['self_check']}")
    return 1 if counters["self_check"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
