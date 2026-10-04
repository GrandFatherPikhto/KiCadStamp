# kicadstamp/diagnostics/claude_probe_r65_inverse_anchor_2026_10_04.py
"""R65 probe: a tree standing on a part it places ITSELF, base computed by the
INVERSE ("where must the tree origin be so the anchor part stays put?") in two
planner passes, plus a third verification pass.

NO PRODUCT EDITS. Run against a clean worktree:
    KICADSTAMP_PROBE_ROOT=<worktree> PYTHONDONTWRITEBYTECODE=1 \
        <worktree>/.venv/bin/python <this file>

Mechanics (all in the PLAN, nothing written anywhere):
  pass 1  plan the tree with the product base (live anchor pose), read where the
          anchor part LANDS in the plan (P) - via the Demon's SH0 probe helper
          _slot_pose_of_clone = the planner's own apply_clone_geometry;
  fix     rigid correction T mapping P onto the live pose L:
          rot' = rot + (L.a - P.a); pos' = L + R_(L.a-P.a)(pos - P)
          (rotation by the product's rotate_local_offset, not a formula here);
  pass 2  plan again with the corrected base;
  pass 3  verify: the anchor part in the pass-2 plan == L (else the planner is
          not rigid in its base and the method is wrong for that row).

The SH0 guard is switched OFF here (monkeypatch) so the drifting rows are planned
at all. The SH0 rows are re-used from the Demon's probe module unchanged.

Checks per row:
  baseline  drift per redraw with the PRODUCT base (guard off) - SH0's table;
  r65       drift per redraw with the corrected base - must be 0 in every row;
  rigid     the OTHER part relative to the anchor part (in the anchor's frame)
            is the same in the corrected plan as in the product plan - the
            correction moved the cluster as one body, did not deform it;
  equiv     rows that are a FIXED POINT today: the corrected plan == the product
            plan for every part (the change is invisible to them);
  follow    (M rows) the user moves/turns the anchor part by hand between
            redraws: the part stays where the user put it, the cluster follows.
"""
from __future__ import annotations

import importlib
import math
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["KICADSTAMP_PROBE_ROOT"]).resolve()
sys.path.insert(0, str(ROOT))

import kicadstamp.placement.entity_placement as ep  # noqa: E402
from kicadstamp.domain.geometry import Vector2  # noqa: E402
from kicadstamp.geometry.clone_geometry import apply_clone_geometry  # noqa: E402
from kicadstamp.geometry.spoke_layout import rotate_local_offset  # noqa: E402
from kicadstamp.utils.units import MM  # noqa: E402

P = importlib.import_module(
    "kicadstamp.diagnostics.deepseek_probe_tree_self_anchor_drift_2026_10_03")
assert Path(P.__file__).resolve().is_relative_to(ROOT), P.__file__

TOL_MM = 1e-5   # 10 nm: rotate_local_offset truncates mm -> nm with int()
TOL_DEG = 1e-6

_ORIG_BASE = ep._anchor_base
ep.check_tree_self_anchor_drift = lambda cfg, tree: None  # guard OFF

# tree name -> (board, role, cluster, clone_name, slot_role); empty = product base
_CTX: dict = {}
_RESIDUALS: list = []


def _norm(a: float) -> float:
    a = math.fmod(a, 360.0)
    if a > 180.0:
        a -= 360.0
    if a <= -180.0:
        a += 360.0
    return a


def _plan_pose(cfg, linked_tree, pos, rot, sheet_names, adapter, clone_name, role):
    clones: list = []
    ep._walk(linked_tree.nodes, pos, rot, clones, None,
             adapter=adapter, cfg=cfg, sheet_names=sheet_names,
             plain_tree=ep._plain_tree(cfg, linked_tree.name),
             tree_base_pos=pos, tree_base_rot=rot)
    return P._slot_pose_of_clone(cfg, clones, clone_name, role)


def _inverse_base(adapter, cfg, linked_tree, sheet_names, forest=None, visited=None):
    pos0, rot0 = _ORIG_BASE(adapter, cfg, linked_tree, sheet_names, forest, visited)
    ctx = _CTX.get(linked_tree.name)
    if ctx is None:
        return pos0, rot0
    board, role, cluster, clone_name, slot_role = ctx
    rot0 = rot0 or 0.0
    lx, ly, la = board.pose(role=role, cluster=cluster)
    # pass 1
    px, py, pa = _plan_pose(cfg, linked_tree, pos0, rot0, sheet_names, adapter,
                            clone_name, slot_role)
    # fix
    d_ang = _norm(la - pa)
    v = rotate_local_offset(pos0.x / MM - px, pos0.y / MM - py, d_ang)
    pos1 = Vector2.from_xy(int(round(lx * MM)) + v.x, int(round(ly * MM)) + v.y)
    rot1 = rot0 + d_ang
    # pass 3 (verification of pass 2)
    qx, qy, qa = _plan_pose(cfg, linked_tree, pos1, rot1, sheet_names, adapter,
                            clone_name, slot_role)
    _RESIDUALS.append((qx - lx, qy - ly, _norm(qa - la)))
    return pos1, rot1


ep._anchor_base = _inverse_base  # the seam materialize_entity_placements calls


def _all_poses(cfg, clones) -> dict:
    out = {}
    for clone in clones:
        cell = cfg.cells[clone.cell]
        layout = apply_clone_geometry(clone, cell, {c.role: "X" for c in cell.components},
                                      anchor_position=None, mirror=bool(clone.mirror))
        for comp in layout.components:
            out[(clone.name, comp.role)] = (comp.position.x / MM, comp.position.y / MM,
                                            comp.angle_deg % 360.0)
    return out


def _rel(a, b):
    """b in a's frame: offset rotated back by a's angle, angle difference."""
    v = rotate_local_offset(b[0] - a[0], b[1] - a[1], -a[2])
    return v.x / MM, v.y / MM, _norm(b[2] - a[2])


def _plan(cfg, board):
    return _all_poses(cfg, ep.materialize_entity_placements(board.adapter(), cfg, {}))


def _drift_two_passes(cfg, board, role, cluster, clone_name, slot_role):
    """Plan, move the anchor part to its planned pose (an apply), plan again."""
    x0, y0, a0 = board.pose(role=role, cluster=cluster)
    p1 = _plan(cfg, board)[(clone_name, slot_role)]
    d1 = (p1[0] - x0, p1[1] - y0, _norm(p1[2] - a0))
    board.move(role=role, cluster=cluster, x_mm=p1[0], y_mm=p1[1], angle=p1[2])
    p2 = _plan(cfg, board)[(clone_name, slot_role)]
    d2 = (p2[0] - p1[0], p2[1] - p1[1], _norm(p2[2] - p1[2]))
    board.move(role=role, cluster=cluster, x_mm=x0, y_mm=y0, angle=a0)  # restore
    return d1, d2


def _zero(d) -> bool:
    return abs(d[0]) < TOL_MM and abs(d[1]) < TOL_MM and abs(d[2]) < TOL_DEG


def _fmt(d) -> str:
    return f"({d[0]:+.4f}, {d[1]:+.4f}) ang{d[2]:+.1f}"


def _max_diff(a: dict, b: dict) -> float:
    assert a.keys() == b.keys(), (a.keys(), b.keys())
    m = 0.0
    for k in a:
        m = max(m, abs(a[k][0] - b[k][0]), abs(a[k][1] - b[k][1]),
                abs(_norm(a[k][2] - b[k][2])) * 1e-3)  # 1 mdeg ~ 1 um weight
    return m


FAILS: list = []


def run_row(label, cfg, board, tree_name, *, role, cluster, clone_name, slot_role,
            other_key=None):
    _CTX.clear()
    _RESIDUALS.clear()
    base_d1, _ = _drift_two_passes(cfg, board, role, cluster, clone_name, slot_role)
    product_plan = _plan(cfg, board)
    fixed_today = _zero(base_d1)

    _CTX[tree_name] = (board, role, cluster, clone_name, slot_role)
    r_d1, r_d2 = _drift_two_passes(cfg, board, role, cluster, clone_name, slot_role)
    r65_plan = _plan(cfg, board)
    _CTX.clear()

    anchor_key = (clone_name, slot_role)
    # the OTHER part of the SAME clone: another tree (D1: the template beside
    # its instance) is not corrected and would fake a deformation
    other_key = other_key or next(k for k in r65_plan
                                  if k[0] == clone_name and k != anchor_key)
    rel_prod = _rel(product_plan[anchor_key], product_plan[other_key])
    rel_r65 = _rel(r65_plan[anchor_key], r65_plan[other_key])
    rigid = _zero(tuple(a - b for a, b in zip(rel_prod, rel_r65)))
    res_ok = bool(_RESIDUALS) and all(_zero(r) for r in _RESIDUALS)
    equiv = _max_diff(product_plan, r65_plan) if fixed_today else None

    ok = _zero(r_d1) and _zero(r_d2) and rigid and res_ok and (
        equiv is None or equiv < TOL_MM)
    if not ok:
        FAILS.append(label)
    print(f"{label:<44} today {_fmt(base_d1):<30} r65 {_fmt(r_d1):<30}"
          f" rigid={'ok' if rigid else 'NO'} verify={'ok' if res_ok else 'NO'}"
          f" equiv={'-' if equiv is None else f'{equiv:.1e}mm'}"
          f"  {'OK' if ok else '*** FAIL'}")


def follow_row(label, cfg, board, tree_name, *, role, cluster, clone_name, slot_role,
               moves):
    """The user moves/turns the anchor part by hand between redraws."""
    _CTX.clear()
    _CTX[tree_name] = (board, role, cluster, clone_name, slot_role)
    anchor_key = (clone_name, slot_role)
    plan0 = _plan(cfg, board)
    other_key = next(k for k in plan0 if k[0] == clone_name and k != anchor_key)
    rel0 = _rel(plan0[anchor_key], plan0[other_key])
    ok = True
    notes = []
    for (x, y, a) in moves:
        board.move(role=role, cluster=cluster, x_mm=x, y_mm=y, angle=a)
        plan = _plan(cfg, board)
        stays = _zero((plan[anchor_key][0] - x, plan[anchor_key][1] - y,
                       _norm(plan[anchor_key][2] - a)))
        follows = _zero(tuple(p - q for p, q in
                              zip(_rel(plan[anchor_key], plan[other_key]), rel0)))
        ok &= stays and follows
        o = plan[other_key]
        notes.append(f"part->({x:g},{y:g},{a:g}) stays={stays} other=({o[0]:.3f},"
                     f"{o[1]:.3f},{o[2]:.1f}) follows={follows}")
    _CTX.clear()
    if not ok:
        FAILS.append(label)
    print(f"{label:<44} {'OK' if ok else '*** FAIL'}")
    for n in notes:
        print(f"    {n}")


def main() -> None:
    R, OTHER, CL = P.ANCHOR_ROLE, P.OTHER_ROLE, P.ENTITY_CLUSTER
    print("R65 probe: inverse anchor base (2 planner passes + verification)")
    print("today = drift per redraw with the product base (guard off); "
          "r65 = with the inverse base")
    print("-" * 150)

    direct = [
        ("A1  no anchor_role, part 0", P._cell(), (0, 0), 0, 0),
        ("A2  no anchor_role, part 90", P._cell(), (0, 0), 0, 90),
        ("A3  no anchor_role, part 315", P._cell(), (0, 0), 0, 315),
        ("A4  anchor_role=OTHER", P._cell(anchor_role=OTHER), (0, 0), 0, 0),
        ("A5  anchor_role=R, part 0", P._cell(anchor_role=R), (0, 0), 0, 0),
        ("A6  anchor_role=R, part 90", P._cell(anchor_role=R), (0, 0), 0, 90),
        ("A7  anchor_role=R, part 315", P._cell(anchor_role=R), (0, 0), 0, 315),
        ("A8  anchor_role=R, node rot 90", P._cell(anchor_role=R), (0, 0), 90, 0),
        ("A9  anchor_role=R, node xy (2,-3)", P._cell(anchor_role=R), (2, -3), 0, 0),
        ("A9b anchor_role=R, xy (2,-3), part 90", P._cell(anchor_role=R), (2, -3), 0, 90),
        ("A10 anchor_xy = R offset", P._cell(anchor_xy=P._ANCHOR_OFFSET), (0, 0), 0, 0),
        ("A11 anchor_xy = 0 corner", P._cell(anchor_xy=(0.0, 0.0)), (0, 0), 0, 0),
        ("A12 anchor_role=R, slot 90", P._cell(anchor_role=R, anchor_slot_angle=90.0), (0, 0), 0, 0),
        ("A13 anchor_role=R, slot 315", P._cell(anchor_role=R, anchor_slot_angle=315.0), (0, 0), 0, 0),
        ("A14 slot 90, node rot 270", P._cell(anchor_role=R, anchor_slot_angle=90.0), (0, 0), 270, 0),
        ("A15 slot 90, node rot 315", P._cell(anchor_role=R, anchor_slot_angle=90.0), (0, 0), 315, 0),
        ("A15b slot 90, rot 315, xy(2,-3), part 315",
         P._cell(anchor_role=R, anchor_slot_angle=90.0), (2, -3), 315, 315),
    ]
    for label, cell, xy, rot, part in direct:
        cfg, board, tree = P._row_direct(cell=cell, node_xy=xy, node_rot=rot,
                                         board_angle=part)
        run_row(label, cfg, board, tree.name, role=R, cluster=CL,
                clone_name="E", slot_role=R)

    for label, cell, tree_rot, pivot in [
        ("T1  tree rot 45, slot 315", P._cell(anchor_role=R, anchor_slot_angle=315.0), 45.0, None),
        ("T2  tree rot 45, slot 0", P._cell(anchor_role=R), 45.0, None),
        ("T3  pivot (5,0)", P._cell(anchor_role=R), 0.0, (5.0, 0.0)),
        ("T4  tree rot 45, pivot (5,0), slot 315",
         P._cell(anchor_role=R, anchor_slot_angle=315.0), 45.0, (5.0, 0.0)),
    ]:
        cfg, board, tree = P._row_direct(cell=cell, tree_rotation=tree_rot,
                                         pivot_xy=pivot)
        run_row(label, cfg, board, tree.name, role=R, cluster=CL,
                clone_name="E", slot_role=R)

    cfg, board, tree = P._row_module_child(cell=P._cell())
    run_row("C1  child of a module node", cfg, board, tree.name, role=R,
            cluster=CL, clone_name="E", slot_role=R)
    cfg, board, tree = P._row_module_child(cell=P._cell(), board_angle=90.0)
    run_row("C1b child of a module node, part 90", cfg, board, tree.name, role=R,
            cluster=CL, clone_name="E", slot_role=R)

    cfg, board, tree, clone = P._row_instances(cell=P._cell())
    run_row("D1  tree_instances copy", cfg, board, tree.name, role=R,
            cluster=CL, clone_name=clone, slot_role=R)

    print("-" * 150)
    self_rows = []
    for ar in (None, R, OTHER):
        for sa in (0.0, 90.0):
            for part in (0.0, 90.0):
                self_rows.append((f"S  self ar={ar or 'none'} slot {sa:.0f} part {part:.0f}",
                                  ar, sa, part, 0.0, (0.0, 0.0), None))
    self_rows += [
        ("S13 self ar=R node rot 90", R, 0.0, 0.0, 90.0, (0.0, 0.0), None),
        ("S14 self ar=R node xy (2,-3)", R, 0.0, 0.0, 0.0, (2.0, -3.0), None),
        ("S15 self ar=R anchor_xy 0 (A != s)", R, 0.0, 0.0, 0.0, (0.0, 0.0), (0.0, 0.0)),
        ("S16 self ar=R slot 90 rot 45 xy(2,-3) part 315", R, 90.0, 315.0, 45.0,
         (2.0, -3.0), None),
    ]
    for label, ar, sa, part, rot, xy, axy in self_rows:
        cfg, board, tree, subject = P._row_self(
            cell=P._self_cell(ar, sa, axy), node_xy=xy, node_rot=rot,
            board_angle=part)
        run_row(label, cfg, board, tree.name, role=subject, cluster=CL,
                clone_name="E", slot_role=subject)

    print("-" * 150)
    print("follow: the anchor part is moved/turned BY HAND between redraws")
    cfg, board, tree = P._row_direct(cell=P._cell(), node_xy=(2.0, -3.0),
                                     node_rot=30.0)
    follow_row("M1  role anchor, drifting config", cfg, board, tree.name, role=R,
               cluster=CL, clone_name="E", slot_role=R,
               moves=[(100, 50, 0), (130, 70, 0), (130, 70, 90), (90, 40, 215)])
    cfg, board, tree, subject = P._row_self(cell=P._self_cell(R, 90.0),
                                            node_xy=(2.0, -3.0), node_rot=45.0)
    follow_row("M2  self anchor, drifting config", cfg, board, tree.name,
               role=subject, cluster=CL, clone_name="E", slot_role=subject,
               moves=[(100, 50, 0), (100, 50, 90), (60, 80, 300)])

    print("-" * 150)
    print(f"rows failed: {len(FAILS)}")
    for f in FAILS:
        print(f"    {f}")


if __name__ == "__main__":
    main()
