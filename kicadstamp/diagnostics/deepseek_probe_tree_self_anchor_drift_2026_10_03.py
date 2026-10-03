# kicadstamp/diagnostics/deepseek_probe_tree_self_anchor_drift_2026_10_03.py
"""SH0 + SH0.2 probe: the fixed-point condition of a tree whose OWN anchor part is
placed by a node of the SAME tree (plan_2026_10_03_tree_self_anchor_drift_guard).

SH0 measured (anchor (role R ...)) trees. SH0.2 -- the re-acceptance of SH0,
finding 3 -- adds the canonical "the tree stands on its own node" form,
(anchor (self (ref "E"))): self x anchor_role (none / R / OTHER) x the ANCHOR
SUBJECT slot angle (0 / 90) x the part's board angle (0 / 90), plus one node
rotation row for the accumulated-rotation half.

NO PRODUCT EDITS. This measures the drift from the CODE paths the planner uses
(entity_placement materialization -> clone_geometry.apply_clone_geometry for
BOTH the position and the angle of a placed component), never from a formula of
this probe: the angle used to be recomputed here as
`slot.angle_deg + clone.rotation_deg`, which SH0's re-acceptance flagged
(finding 1).

  * build a synthetic config (in-memory Config/Cell/Entity/Tree) and a fake board
    (the deterministic MagicMock board shape tests/trees/test_tree_internal_mount.py
    and kicadstamp/diagnostics/tree_mount_baseline.py already use);
  * materialize the tree (materialize_entity_placements) and read the world pose
    of the tree's anchor-role component in the placed cell
    (apply_clone_geometry -- the planner's own forward mapping);
  * "move" that component on the fake board to its new pose (an apply does move
    it) and materialize again;
  * record the pose shift per pass.

The printed table is ASCII only (a Windows cp1251 console must not crash on it).

Run:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_tree_self_anchor_drift_2026_10_03
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kipy.board_types import FootprintInstance

from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot
from kicadstamp.config import _load_cell, _load_entity
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.geometry.clone_geometry import apply_clone_geometry
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.trees import (
    Tree,
    TreeAnchor,
    TreeNode,
    find_role_placement_matches,
    tree_from_dict,
)
from kicadstamp.utils.units import MM

# -- the synthetic geometry --------------------------------------------------
# The anchor role R sits at a NONZERO stored offset (the fpga case: the anchor
# component is not the bbox corner); OTHER is the bbox-corner component.
ANCHOR_ROLE = "R"
OTHER_ROLE = "OTHER"
_ANCHOR_OFFSET = (4.7504, -13.6943)

ENTITY_SHEET = "Sh"
ENTITY_CLUSTER = "Cl"

# Board-absolute start of the anchor footprint.
_ANCHOR_START = (100.0, 50.0)

# "Zero" thresholds for the verdict. NOT 1e-6: apply_clone_geometry composes the
# placement through rotate_local_offset, which quantises mm -> nm with int()
# (a 1 nm = 1e-6 mm truncation step), so a genuinely fixed point can come back a
# few nm off. 10 nm is still five orders of magnitude below a real drift (mm).
_ZERO_TOL_MM = 1e-5
_ZERO_TOL_DEG = 1e-6


def _components(anchor_slot_angle: float = 0.0,
                other_slot_angle: float = 0.0) -> list:
    return [
        TemplateComponentSlot(role=ANCHOR_ROLE, offset_along_mm=_ANCHOR_OFFSET[0],
                              offset_across_mm=_ANCHOR_OFFSET[1],
                              angle_deg=anchor_slot_angle),
        TemplateComponentSlot(role=OTHER_ROLE, offset_along_mm=0.0,
                              offset_across_mm=0.0, angle_deg=other_slot_angle),
    ]


def _cell(anchor_role: str | None = None,
          anchor_xy: tuple[float, float] | None = None,
          anchor_slot_angle: float = 0.0,
          other_slot_angle: float = 0.0) -> Cell:
    return Cell(name="C", layer="F.Cu",
                components=_components(anchor_slot_angle, other_slot_angle),
                anchor_role=anchor_role, anchor_xy=anchor_xy)


def _entity() -> Entity:
    return Entity(name="E", cell="C", sheet=ENTITY_SHEET, cluster=ENTITY_CLUSTER)


def _placement_node(ref: str = "E", xy=(0.0, 0.0), rot=0.0,
                    children=()) -> TreeNode:
    return TreeNode(ref=ref, kind="placement", xy=xy, polar=None, rotation=rot,
                    name=None, group=None, children=list(children))


def _module_node(ref: str = "inner", xy=(0.0, 0.0), children=()) -> TreeNode:
    return TreeNode(ref=ref, kind="module", xy=xy, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children))


class _FakeBoard:
    """A deterministic board whose footprints can be MOVED between passes -- the
    apply step of the measurement. `get_footprints` rebuilds fresh MagicMocks
    from the current dicts, exactly like the fake in tree_mount_baseline.py."""

    def __init__(self, footprints: list[dict]) -> None:
        self._fps = [dict(f) for f in footprints]

    def _build(self) -> list:
        out = []
        for f in self._fps:
            fp = MagicMock(spec=FootprintInstance)
            fp.ref = f["ref"]
            fp._role = f["role"]
            fp._cluster = f["cluster"]
            fp.position = Vector2.from_xy_mm(f["x_mm"], f["y_mm"])
            fp.angle_deg = f["angle"]
            out.append(fp)
        return out

    def adapter(self):
        adapter = MagicMock()
        adapter.get_footprints.return_value = self._build()
        adapter.has_field.return_value = True

        def _field(fp, name):
            if name == "Role":
                return getattr(fp, "_role", None)
            if name == CLUSTER_FIELD_NAME:
                return getattr(fp, "_cluster", None)
            return None

        adapter.get_field_value.side_effect = _field
        adapter.get_selected_items.return_value = []
        return adapter

    def move(self, *, role: str, cluster: str, x_mm: float, y_mm: float,
             angle: float) -> None:
        for f in self._fps:
            if f["role"] == role and f["cluster"] == cluster:
                f["x_mm"], f["y_mm"], f["angle"] = x_mm, y_mm, angle
                return
        raise AssertionError(f"no footprint role={role!r} cluster={cluster!r}")

    def shift(self, *, role: str, cluster: str, dx: float, dy: float) -> None:
        for f in self._fps:
            if f["role"] == role and f["cluster"] == cluster:
                f["x_mm"] += dx
                f["y_mm"] += dy
                return
        raise AssertionError(f"no footprint role={role!r} cluster={cluster!r}")

    def pose(self, *, role: str, cluster: str) -> tuple[float, float, float]:
        for f in self._fps:
            if f["role"] == role and f["cluster"] == cluster:
                return f["x_mm"], f["y_mm"], f["angle"]
        raise AssertionError(f"no footprint role={role!r} cluster={cluster!r}")


def _slot_pose_of_clone(cfg: Config, clones, clone_name: str,
                        slot_role: str) -> tuple[float, float, float]:
    """World (x_mm, y_mm, angle_deg) of `slot_role` in the placed cell of the
    clone named `clone_name` -- read from clone_geometry.apply_clone_geometry,
    the PLANNER'S OWN forward mapping, for the position AND the angle. No
    formula of this probe and no CellFrame duplicate of that mapping; the
    materialized clone carries an absolute xy and no anchor, so
    anchor_position=None makes its xy the placement origin (the same
    composition the calculator performs)."""
    clone = next(c for c in clones if c.name == clone_name)
    cell = cfg.cells[clone.cell]
    layout = apply_clone_geometry(clone, cell, {slot_role: "X"},
                                  anchor_position=None, mirror=bool(clone.mirror))
    comp = next(c for c in layout.components if c.role == slot_role)
    return comp.position.x / MM, comp.position.y / MM, comp.angle_deg % 360.0


def _self_loop(cfg: Config, board: _FakeBoard,
               *, anchor_cluster: str = ENTITY_CLUSTER,
               anchor_role: str = ANCHOR_ROLE, clone_name: str = "E",
               slot_role: str | None = None) -> dict:
    """Two passes: materialize, move the anchor footprint to the placed pose,
    materialize again. Records the pose shift of the anchor part per pass.

    `anchor_role` is the role of the footprint carrying the tree's anchor
    identity: the tree's (role ...) role, or -- for a (self ...) anchor -- the
    role `_entity_own_zero_slot_live_position` reads live (cell.anchor_role
    when set, else the single zero-offset slot). `slot_role` is the SAME part
    inside the placed cell and defaults to it."""
    slot_role = slot_role or anchor_role
    adapter = board.adapter()
    x0, y0, a0 = board.pose(role=anchor_role, cluster=anchor_cluster)

    p1 = _slot_pose_of_clone(cfg, materialize_entity_placements(adapter, cfg, {}),
                             clone_name, slot_role)
    d1 = (p1[0] - x0, p1[1] - y0, (p1[2] - a0) % 360.0)

    board.move(role=anchor_role, cluster=anchor_cluster,
               x_mm=p1[0], y_mm=p1[1], angle=p1[2])
    p2 = _slot_pose_of_clone(
        cfg, materialize_entity_placements(board.adapter(), cfg, {}),
        clone_name, slot_role)
    d2 = (p2[0] - p1[0], p2[1] - p1[1], (p2[2] - p1[2]) % 360.0)
    return {"pass1": d1, "pass2": d2}


def _matches(cfg: Config, tree: Tree) -> list:
    """The guard's own predicate: which nodes does find_role_placement_matches
    say place the tree's anchor role (role + sheet + cluster narrowing)?"""
    anchor = tree.anchor
    if anchor is None or anchor.role is None:
        return []
    return find_role_placement_matches(
        cfg, tree, anchor.role, sheet=anchor.anchor_sheet,
        cluster=anchor.anchor_cluster)


# -- row builders ------------------------------------------------------------

def _row_direct(*, cell: Cell, node_xy=(0.0, 0.0), node_rot=0.0,
                board_angle=0.0, anchor_cluster=ENTITY_CLUSTER,
                anchor_sheet=None, tree_rotation=0.0, pivot_xy=None
                ) -> tuple[Config, _FakeBoard, Tree]:
    anchor = TreeAnchor(role=ANCHOR_ROLE, anchor_sheet=anchor_sheet,
                        anchor_cluster=anchor_cluster)
    tree = Tree(name="t", anchor=anchor,
                nodes=[_placement_node(xy=node_xy, rot=node_rot)],
                rotation=tree_rotation, pivot_xy=pivot_xy)
    cfg = Config(cells={"C": cell}, entities=[_entity()], trees=[tree])
    board = _FakeBoard([
        {"ref": "X", "role": ANCHOR_ROLE, "cluster": anchor_cluster,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": board_angle}])
    return cfg, board, tree


def _row_module_child(*, cell: Cell, board_angle=0.0) -> tuple:
    """The placing node is a CHILD of a module node in the SAME (outer) tree --
    so the outer tree's base DOES lay it (trees.py walks a module node's own
    children as ordinary nodes of this tree)."""
    inner = Tree(name="inner", anchor=TreeAnchor(is_origin=True), nodes=[])
    outer = Tree(
        name="t",
        anchor=TreeAnchor(role=ANCHOR_ROLE, anchor_cluster=ENTITY_CLUSTER),
        nodes=[_module_node(children=[_placement_node()])])
    cfg = Config(cells={"C": cell}, entities=[_entity()], trees=[outer, inner])
    board = _FakeBoard([
        {"ref": "X", "role": ANCHOR_ROLE, "cluster": ENTITY_CLUSTER,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": board_angle}])
    return cfg, board, outer


def _row_embedded(*, cell: Cell) -> tuple:
    """The placing node lives in a tree EMBEDDED by a module node (a DIFFERENT
    tree with its OWN anchor). find_role_placement_matches(outer) still reports
    it, but the outer tree's base does NOT lay it -- measured by moving the outer
    anchor part and checking the embedded clone does not move."""
    inner = Tree(name="inner", anchor=TreeAnchor(is_origin=True),
                 nodes=[_placement_node()])
    outer = Tree(
        name="t",
        anchor=TreeAnchor(role=ANCHOR_ROLE, anchor_cluster=ENTITY_CLUSTER),
        nodes=[_module_node()])
    cfg = Config(cells={"C": cell}, entities=[_entity()], trees=[outer, inner])
    board = _FakeBoard([
        {"ref": "X", "role": ANCHOR_ROLE, "cluster": ENTITY_CLUSTER,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": 0.0}])
    return cfg, board, outer


def _row_instances(*, cell: Cell) -> tuple:
    """A copy from tree_instances: expand a role-anchored template into a
    generated top-level tree and build the Config from the expanded dict."""
    data = {
        "cells": {"C": {"layer": "F.Cu", "components": [
            {"role": ANCHOR_ROLE, "offset_along_mm": _ANCHOR_OFFSET[0],
             "offset_across_mm": _ANCHOR_OFFSET[1],
             "angle_deg": cell.components[0].angle_deg},
            {"role": OTHER_ROLE, "offset_along_mm": 0.0,
             "offset_across_mm": 0.0, "angle_deg": 0.0},
        ], **({"anchor_role": cell.anchor_role} if cell.anchor_role else {})}},
        "entities": [{"name": "E", "cell": "C", "sheet": ENTITY_SHEET,
                      "cluster": ENTITY_CLUSTER}],
        "trees": [{"name": "tpl",
                   "anchor": {"role": ANCHOR_ROLE, "cluster": ENTITY_CLUSTER,
                              "sheet": ENTITY_SHEET},
                   "nodes": [{"ref": "E", "kind": "placement", "xy": [0.0, 0.0]}]}],
        "tree_instances": [{"template": "tpl", "name": "tpl_a",
                            "sheet": "Own_a", "cluster": ENTITY_CLUSTER}],
    }
    expanded = expand_tree_instances(data)
    cells = {name: _load_cell(name, cdata)
             for name, cdata in (expanded.get("cells") or {}).items()}
    entities = [_load_entity(e) for e in (expanded.get("entities") or [])]
    trees = [tree_from_dict(t) for t in (expanded.get("trees") or [])]
    gen = next(t for t in trees if t.name == "tpl_a")
    cfg = Config(cells=cells, entities=entities, trees=trees)
    board = _FakeBoard([
        {"ref": "X", "role": ANCHOR_ROLE, "cluster": ENTITY_CLUSTER,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": 0.0}])
    return cfg, board, gen, "E__tpl_a"


def _anchor_subject_role(cell: Cell) -> str:
    """The role a SELF anchor reads live as its subject: cell.anchor_role when
    set, else the single zero-offset component -- the exact rule of
    entity_placement._entity_own_zero_slot_live_position (surrogate_role)."""
    if cell.anchor_role is not None:
        return cell.anchor_role
    return next(c.role for c in cell.components
                if c.offset_along_mm == 0.0 and c.offset_across_mm == 0.0)


def _self_cell(anchor_role: str | None, subject_angle: float,
               anchor_xy: tuple[float, float] | None = None) -> Cell:
    """The cell of a self-anchor row: the ANCHOR SUBJECT slot (the one the self
    anchor reads live) carries `subject_angle`; the other slot stays at 0.
    `anchor_xy` (S15) stores an explicit mount that does NOT sit on the subject
    slot, so A != s even though the anchor subject is the cell's own mount
    surrogate."""
    subject = _anchor_subject_role(_cell(anchor_role=anchor_role))
    if subject == ANCHOR_ROLE:
        return _cell(anchor_role=anchor_role, anchor_slot_angle=subject_angle,
                     anchor_xy=anchor_xy)
    return _cell(anchor_role=anchor_role, other_slot_angle=subject_angle,
                 anchor_xy=anchor_xy)


def _row_self(*, cell: Cell, node_xy=(0.0, 0.0), node_rot=0.0,
              board_angle=0.0) -> tuple[Config, _FakeBoard, Tree, str]:
    """An (anchor (self (ref "E"))) tree (plan SH0.2): the base is the LIVE
    position of the component THIS tree places -- the node the ref names, read
    through entity_placement._self_anchor_base (the subject is cell.anchor_role
    when set, else the single zero-offset slot). Returns the subject role too."""
    anchor = TreeAnchor(is_self=True, self_ref="E")
    tree = Tree(name="t", anchor=anchor,
                nodes=[_placement_node(xy=node_xy, rot=node_rot)])
    cfg = Config(cells={"C": cell}, entities=[_entity()], trees=[tree])
    subject = _anchor_subject_role(cell)
    board = _FakeBoard([
        {"ref": "X", "role": subject, "cluster": ENTITY_CLUSTER,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": board_angle}])
    return cfg, board, tree, subject


# -- output helpers ----------------------------------------------------------

def _fmt_vec(d) -> str:
    if d is None:
        return "-"
    return f"({d[0]:+.4f}, {d[1]:+.4f}) ang{d[2]:+.1f}"


def _verdict(res) -> str:
    if res is None:
        return "not measured"
    p1, p2 = res["pass1"], res["pass2"]
    stable = (abs(p1[0] - p2[0]) < _ZERO_TOL_MM
              and abs(p1[1] - p2[1]) < _ZERO_TOL_MM
              and abs(p1[2] - p2[2]) < _ZERO_TOL_DEG)
    pos = max(abs(p1[0]), abs(p1[1])) < _ZERO_TOL_MM
    ang = abs(p1[2]) < _ZERO_TOL_DEG
    if pos and ang:
        return "FIXED POINT"
    if not stable:
        return "DRIFTS (not constant)"
    if not pos:
        return "DRIFTS (position)"
    return "DRIFTS (angle)"


def main() -> None:
    print("SH0: drift of a tree's OWN anchor part per redraw pass")
    print("vec = (dx_mm, dy_mm) ang_deg; matches = find_role_placement_matches"
          "(tree, anchor role+sheet+cluster)")
    print("-" * 118)

    def report(label, cfg, tree, res, *, note="", clone="E"):
        ms = _matches(cfg, tree)
        trees_txt = ",".join(sorted({m.tree.name for m in ms})) or "-"
        print(f"{label:<46} matches={len(ms)} in[{trees_txt}]")
        print(f"    pass1 {_fmt_vec(res['pass1'])}   pass2 {_fmt_vec(res['pass2'])}"
              f"   -> {_verdict(res)} {note}")

    # -- 1. direct rows: cell anchor x node xy/angle x slot angle x board angle
    direct = [
        ("A1  no anchor_role, slot 0, rot 0, part 0", _cell(), (0.0, 0.0), 0.0, 0.0),
        ("A2  no anchor_role, slot 0, rot 0, part 90", _cell(), (0.0, 0.0), 0.0, 90.0),
        ("A3  no anchor_role, slot 0, rot 0, part 315", _cell(), (0.0, 0.0), 0.0, 315.0),
        ("A4  anchor_role=OTHER, slot 0, rot 0", _cell(anchor_role=OTHER_ROLE), (0.0, 0.0), 0.0, 0.0),
        ("A5  anchor_role=R, slot 0, rot 0, part 0", _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 0.0, 0.0),
        ("A6  anchor_role=R, slot 0, rot 0, part 90", _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 0.0, 90.0),
        ("A7  anchor_role=R, slot 0, rot 0, part 315", _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 0.0, 315.0),
        ("A8  anchor_role=R, slot 0, rot 90", _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 90.0, 0.0),
        ("A9  anchor_role=R, slot 0, xy=(2,-3)", _cell(anchor_role=ANCHOR_ROLE), (2.0, -3.0), 0.0, 0.0),
        ("A10 anchor_xy=R offset, slot 0, rot 0", _cell(anchor_xy=_ANCHOR_OFFSET), (0.0, 0.0), 0.0, 0.0),
        ("A11 anchor_xy=0 corner, slot 0, rot 0", _cell(anchor_xy=(0.0, 0.0)), (0.0, 0.0), 0.0, 0.0),
        # the slot ANGLE axis (the second half of the condition)
        ("A12 anchor_role=R, slot 90, rot 0", _cell(anchor_role=ANCHOR_ROLE, anchor_slot_angle=90.0), (0.0, 0.0), 0.0, 0.0),
        ("A13 anchor_role=R, slot 315, rot 0", _cell(anchor_role=ANCHOR_ROLE, anchor_slot_angle=315.0), (0.0, 0.0), 0.0, 0.0),
        ("A14 anchor_role=R, slot 90, rot 270", _cell(anchor_role=ANCHOR_ROLE, anchor_slot_angle=90.0), (0.0, 0.0), 270.0, 0.0),
        ("A15 anchor_role=R, slot 90, rot 315", _cell(anchor_role=ANCHOR_ROLE, anchor_slot_angle=90.0), (0.0, 0.0), 315.0, 0.0),
    ]
    for label, cell, xy, rot, board_angle in direct:
        cfg, board, tree = _row_direct(cell=cell, node_xy=xy, node_rot=rot,
                                       board_angle=board_angle)
        report(label, cfg, tree, _self_loop(cfg, board, anchor_cluster=ENTITY_CLUSTER))

    # -- 1b. the TREE's OWN angle and inner point (tree_effective_base): the
    #        live `ch0_dac_buf` tree turned out to carry (rotation 45.0) while
    #        its cell's anchor slot sits at -45, i.e. the two CANCEL -- the SH1
    #        guard must know this axis or it reports a false positive on live
    #        data. Measured here, not argued: tree rotation 45 with a slot at
    #        315 (sum 360) must be a fixed point; tree rotation 45 with a slot
    #        at 0 must drift by +45; a pivot with nothing else wrong must still
    #        drift (the pivot is not the anchor part).
    tree_rows = [
        ("T1  tree rot 45, slot 315 (sum 360)",
         _cell(anchor_role=ANCHOR_ROLE, anchor_slot_angle=315.0),
         (0.0, 0.0), 0.0, 0.0, 45.0, None),
        ("T2  tree rot 45, slot 0", _cell(anchor_role=ANCHOR_ROLE),
         (0.0, 0.0), 0.0, 0.0, 45.0, None),
        ("T3  pivot (5,0), slot 0", _cell(anchor_role=ANCHOR_ROLE),
         (0.0, 0.0), 0.0, 0.0, 0.0, (5.0, 0.0)),
        ("T4  tree rot 45, pivot (5,0), slot 315",
         _cell(anchor_role=ANCHOR_ROLE, anchor_slot_angle=315.0),
         (0.0, 0.0), 0.0, 0.0, 45.0, (5.0, 0.0)),
    ]
    for label, cell, xy, rot, board_angle, tree_rot, pivot in tree_rows:
        cfg_t, board_t, tree_t = _row_direct(
            cell=cell, node_xy=xy, node_rot=rot, board_angle=board_angle,
            tree_rotation=tree_rot, pivot_xy=pivot)
        report(label, cfg_t, tree_t,
               _self_loop(cfg_t, board_t, anchor_cluster=ENTITY_CLUSTER))

    # -- 2. "different part": the anchor cluster names ANOTHER part -- the
    #       guard's own predicate must return NO match (not a verdict by fiat).
    cfg_b1, board_b1, tree_b1 = _row_direct(
        cell=_cell(), anchor_cluster="ClX")
    board_b1._fps.append({"ref": "W", "role": ANCHOR_ROLE, "cluster": "ClX",
                          "x_mm": 200.0, "y_mm": 20.0, "angle": 0.0})
    ms_b1 = _matches(cfg_b1, tree_b1)
    print(f"{'B1  anchor cluster ClX != entity cluster Cl':<46} matches={len(ms_b1)} "
          f"-> guard silent (no self-anchor subject)")

    # -- 3. nesting: node as a CHILD of a module node in the SAME tree
    cell_n = _cell()
    cfg_n, board_n, tree_n = _row_module_child(cell=cell_n)
    report("C1  node is a CHILD of a module node (same tree)", cfg_n, tree_n,
           _self_loop(cfg_n, board_n, anchor_cluster=ENTITY_CLUSTER))

    # -- 4. nesting: node inside an EMBEDDED tree (different tree) -- measured
    #       by moving the outer anchor and checking the clone does not move.
    cell_n2 = _cell()
    cfg_n2, board_n2, tree_n2 = _row_embedded(cell=cell_n2)
    before = _slot_pose_of_clone(
        cfg_n2, materialize_entity_placements(board_n2.adapter(), cfg_n2, {}),
        "E", ANCHOR_ROLE)
    board_n2.shift(role=ANCHOR_ROLE, cluster=ENTITY_CLUSTER, dx=50.0, dy=50.0)
    after = _slot_pose_of_clone(
        cfg_n2, materialize_entity_placements(board_n2.adapter(), cfg_n2, {}),
        "E", ANCHOR_ROLE)
    moved = max(abs(after[0] - before[0]), abs(after[1] - before[1])) > 1e-6
    ms_n2 = _matches(cfg_n2, tree_n2)
    trees_n2 = ",".join(sorted({m.tree.name for m in ms_n2})) or "-"
    print(f"{'C2  node inside a MODULE-embedded tree':<46} matches={len(ms_n2)} "
          f"in[{trees_n2}]")
    print(f"    outer anchor moved 50,50 -> embedded clone moved? {moved}"
          f"   -> {'DRIFTS' if moved else 'NO dependence on the outer anchor'}"
          f" (guard must exclude match.tree != tree)")

    # -- 5. nesting: a copy from tree_instances
    cfg_i, board_i, tree_i, clone_i = _row_instances(cell=_cell())
    report("D1  copy from tree_instances (generated tpl_a)", cfg_i, tree_i,
           _self_loop(cfg_i, board_i, anchor_cluster=ENTITY_CLUSTER,
                      clone_name=clone_i))

    # -- 6. SH0.2: (anchor (self (ref "E"))) rows -- the canonical "the tree
    #       stands on its own node" form. Axes: anchor_role (none / R / OTHER)
    #       x the ANCHOR SUBJECT slot angle (0 / 90) x the part's board angle
    #       (0 / 90), plus one node-rotation row for the rho half.
    print("-" * 118)
    print("SH0.2: (anchor (self (ref \"E\"))) rows -- subject = cell.anchor_role")
    print("       when set, else the single zero-offset slot (the live read of")
    print("       _entity_own_zero_slot_live_position)")
    self_rows = []
    for anchor_role in (None, ANCHOR_ROLE, OTHER_ROLE):
        for subject_angle in (0.0, 90.0):
            for board_angle in (0.0, 90.0):
                label = (f"S  anchor_role={anchor_role or 'none'} "
                         f"slot {subject_angle:.0f} part {board_angle:.0f}")
                self_rows.append((label, anchor_role, subject_angle, board_angle,
                                  0.0, (0.0, 0.0), None))
    self_rows.append(("S13 anchor_role=R slot 0 node rot 90", ANCHOR_ROLE,
                      0.0, 0.0, 90.0, (0.0, 0.0), None))
    # S14/S15 -- the two ways a SELF row can still move in POSITION, i.e. the
    # two claims "the position term of a self anchor is 0 by construction" does
    # NOT cover: the node's own offset o != 0 (S14) and an explicit anchor_xy
    # that does not sit on the anchor subject slot, so A != s (S15). Both are
    # outside the plan's 12 self cells; they are measured because the SH1 guard
    # has to know whether they are violations before promising any fix text.
    self_rows.append(("S14 anchor_role=R slot 0 node xy=(2,-3)", ANCHOR_ROLE,
                      0.0, 0.0, 0.0, (2.0, -3.0), None))
    self_rows.append(("S15 anchor_role=R anchor_xy=0 (A != s)", ANCHOR_ROLE,
                      0.0, 0.0, 0.0, (0.0, 0.0), (0.0, 0.0)))
    self_verdicts = []
    for (label, anchor_role, subject_angle, board_angle, node_rot,
         node_xy, anchor_xy) in self_rows:
        cfg_s, board_s, _tree_s, subject = _row_self(
            cell=_self_cell(anchor_role, subject_angle, anchor_xy),
            node_xy=node_xy, node_rot=node_rot, board_angle=board_angle)
        res = _self_loop(cfg_s, board_s, anchor_role=subject, slot_role=subject)
        verdict = _verdict(res)
        self_verdicts.append((label, verdict))
        print(f"{label:<46} subject role {subject!r}")
        print(f"    pass1 {_fmt_vec(res['pass1'])}   pass2 {_fmt_vec(res['pass2'])}"
              f"   -> {verdict}")

    drifted = [(label, verdict) for label, verdict in self_verdicts
               if verdict != "FIXED POINT"]
    print(f"SH0.2 summary: {len(self_verdicts)} self row(s), "
          f"{len(drifted)} NOT a fixed point")
    for label, verdict in drifted:
        print(f"    NOT FIXED: {label} -> {verdict}")

    print("-" * 118)
    print("Condition (derived): drift vector = R_phi(o + R_rho(s - A)); a fixed")
    print("point needs o + R_rho(s - A) = 0 (position) and rho + slot.angle = 0")
    print("(orientation), with o/rho the node's offline offset/accumulated")
    print("rotation, s the anchor-role slot offset, A the cell mount.")
    print("R_phi is the LIVE anchor angle: it rotates the vector but cannot")
    print("zero it; the offline part (o, rho, s, A) decides the fixed point.")


if __name__ == "__main__":
    main()
