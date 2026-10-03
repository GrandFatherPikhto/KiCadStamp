# kicadstamp/diagnostics/deepseek_probe_tree_self_anchor_drift_2026_10_03.py
"""Ш0 probe: the fixed-point condition of a tree whose OWN (role ...) anchor is
placed by a node of the SAME tree (plan_2026_10_03_tree_self_anchor_drift_guard).

NO PRODUCT EDITS. This measures the drift from the CODE paths the planner uses
(entity_placement materialization -> CellFrame materialization, cell_mount_offset
for the mount A), never from a hand-rolled formula:

  * build a synthetic config (in-memory Config/Cell/Entity/Tree) and a fake board
    (the deterministic MagicMock board shape tests/trees/test_tree_internal_mount.py
    and kicadstamp/diagnostics/tree_mount_baseline.py already use);
  * materialize the tree (materialize_entity_placements) and read the world pose
    of the tree's anchor-role component in the placed cell (CellFrame);
  * "move" that component on the fake board to its new pose (an apply does move
    it) and materialize again;
  * record the pose shift per pass.

A row is a branch of the position calculation. The axes (plan §Ш0):

  | axis            | values                                                   |
  |-----------------|----------------------------------------------------------|
  | cell            | anchor_role = tree anchor role / another role / absent;   |
  |                 | anchor_xy (v2) present / absent                           |
  | node            | xy = 0 / != 0; node angle 0 / != 0                        |
  | anchor part     | board angle 0 / 90 / 315                                  |
  | nesting         | node directly in the tree / in a tree embedded by a       |
  |                 | module / a copy from tree_instances                       |
  | match           | anchor role present in the cell but the entity's          |
  |                 | cluster/sheet name a DIFFERENT physical part              |

Run:
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_tree_self_anchor_drift_2026_10_03
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kipy.board_types import FootprintInstance

from kicadstamp.cell_frame import CellFrame
from kicadstamp.config import Cell, Config, Entity, TemplateComponentSlot
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.domain.geometry import Vector2
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.trees import Tree, TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

# ── the synthetic geometry ──────────────────────────────────────────────────
# The anchor role R sits at a NONZERO stored offset (the fpga case: the anchor
# component is not the bbox corner); OTHER is the bbox-corner component.
ANCHOR_ROLE = "R"
OTHER_ROLE = "OTHER"
_ANCHOR_OFFSET = (4.7504, -13.6943)
_COMPONENTS = (
    (ANCHOR_ROLE, _ANCHOR_OFFSET[0], _ANCHOR_OFFSET[1], 0.0),
    (OTHER_ROLE, 0.0, 0.0, 0.0),
)

ENTITY_SHEET = "Sh"
ENTITY_CLUSTER = "Cl"

# Board-absolute start of the anchor footprint.
_ANCHOR_START = (100.0, 50.0)


def _cell(anchor_role: str | None = None,
          anchor_xy: tuple[float, float] | None = None) -> Cell:
    return Cell(
        name="C",
        layer="F.Cu",
        components=[
            TemplateComponentSlot(role=r, offset_along_mm=o,
                                  offset_across_mm=a, angle_deg=ang)
            for r, o, a, ang in _COMPONENTS
        ],
        anchor_role=anchor_role,
        anchor_xy=anchor_xy,
    )


def _entity() -> Entity:
    return Entity(name="E", cell="C", sheet=ENTITY_SHEET, cluster=ENTITY_CLUSTER)


def _placement_node(ref: str = "E", xy=(0.0, 0.0), rot=0.0,
                    children=()) -> TreeNode:
    return TreeNode(ref=ref, kind="placement", xy=xy, polar=None, rotation=rot,
                    name=None, group=None, children=list(children))


class _FakeBoard:
    """A deterministic board whose footprints can be MOVED between passes — the
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
        """Move the (role, cluster) footprint to a new pose — the apply step."""
        for f in self._fps:
            if f["role"] == role and f["cluster"] == cluster:
                f["x_mm"], f["y_mm"], f["angle"] = x_mm, y_mm, angle
                return
        raise AssertionError(f"no footprint role={role!r} cluster={cluster!r}")

    def pose(self, *, role: str, cluster: str) -> tuple[float, float, float]:
        for f in self._fps:
            if f["role"] == role and f["cluster"] == cluster:
                return f["x_mm"], f["y_mm"], f["angle"]
        raise AssertionError(f"no footprint role={role!r} cluster={cluster!r}")


def _placed_anchor_pose(cfg: Config, adapter, slot_role: str
                        ) -> tuple[float, float, float, object]:
    """(x_mm, y_mm, angle_deg, clone) of the anchor-role slot in the placed cell
    — read through the REAL materialization + CellFrame (the same two steps
    tree_mount_baseline.snapshot uses), never a hand-rolled formula."""
    clones = materialize_entity_placements(adapter, cfg, {})
    clone = next(c for c in clones if c.name == "E")
    cell = cfg.cells[clone.cell]
    frame = CellFrame(
        placement_origin=Vector2.from_xy(int(round(clone.xy[0] * MM)),
                                         int(round(clone.xy[1] * MM))),
        rotation_deg=clone.rotation_deg,
        mirror=bool(clone.mirror),
        mount=cell_mount_offset(cell),
    )
    slot = next(s for s in cell.components if s.role == slot_role)
    wx, wy = frame.point_to_world_mm(slot.offset_along_mm, slot.offset_across_mm)
    ang = (slot.angle_deg + clone.rotation_deg) % 360.0
    return wx, wy, ang, clone


def _measure(cfg: Config, board: _FakeBoard, *, anchor_cluster: str,
             loop: bool = True) -> dict:
    """Two passes: materialize, move the anchor footprint to the placed pose,
    materialize again. Records the pose shift of the anchor part per pass."""
    adapter = board.adapter()
    x0, y0, a0 = board.pose(role=ANCHOR_ROLE, cluster=anchor_cluster)

    p1 = _placed_anchor_pose(cfg, adapter, ANCHOR_ROLE)
    d1 = (p1[0] - x0, p1[1] - y0, (p1[2] - a0) % 360.0)

    if not loop:
        return {"loop": False, "pass1": d1, "pass2": None}

    board.move(role=ANCHOR_ROLE, cluster=anchor_cluster,
               x_mm=p1[0], y_mm=p1[1], angle=p1[2])
    p2 = _placed_anchor_pose(cfg, board.adapter(), ANCHOR_ROLE)
    d2 = (p2[0] - p1[0], p2[1] - p1[1], (p2[2] - p1[2]) % 360.0)
    return {"loop": True, "pass1": d1, "pass2": d2,
            "fixed": max(abs(v) for v in d1) < 1e-6}


def _row_direct(*, cell: Cell, node_xy=(0.0, 0.0), node_rot=0.0,
                board_angle=0.0, anchor_cluster=ENTITY_CLUSTER,
                anchor_sheet=None, extra_board=(), loop=True) -> dict:
    anchor = TreeAnchor(role=ANCHOR_ROLE, anchor_sheet=anchor_sheet,
                        anchor_cluster=anchor_cluster)
    cfg = Config(
        cells={"C": cell}, entities=[_entity()],
        trees=[Tree(name="t", anchor=anchor,
                    nodes=[_placement_node(xy=node_xy, rot=node_rot)])],
    )
    board = _FakeBoard([
        {"ref": "X", "role": ANCHOR_ROLE, "cluster": anchor_cluster,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": board_angle},
        *extra_board,
    ])
    return _measure(cfg, board, anchor_cluster=anchor_cluster, loop=loop)


def _fmt(d) -> str:
    if d is None:
        return "—"
    return f"({d[0]:+.4f}, {d[1]:+.4f}) ∠{d[2]:+.1f}°"


def main() -> None:
    rows: list[tuple[str, dict]] = []

    # ── 1. the branch table: cell anchor × node xy/angle × board angle ──────
    combos = [
        ("A1  no anchor_role,  xy=0,  rot=0, part 0°",
         _cell(), (0.0, 0.0), 0.0, 0.0),
        ("A2  no anchor_role,  xy=0,  rot=0, part 90°",
         _cell(), (0.0, 0.0), 0.0, 90.0),
        ("A3  no anchor_role,  xy=0,  rot=0, part 315°",
         _cell(), (0.0, 0.0), 0.0, 315.0),
        ("A4  anchor_role=OTHER, xy=0, rot=0, part 0°",
         _cell(anchor_role=OTHER_ROLE), (0.0, 0.0), 0.0, 0.0),
        ("A5  anchor_role=R,     xy=0,  rot=0, part 0°",
         _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 0.0, 0.0),
        ("A6  anchor_role=R,     xy=0,  rot=0, part 90°",
         _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 0.0, 90.0),
        ("A7  anchor_role=R,     xy=0,  rot=0, part 315°",
         _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 0.0, 315.0),
        ("A8  anchor_role=R,     xy=0,  rot=90, part 0°",
         _cell(anchor_role=ANCHOR_ROLE), (0.0, 0.0), 90.0, 0.0),
        ("A9  anchor_role=R,     xy=(2,-3), rot=0, part 0°",
         _cell(anchor_role=ANCHOR_ROLE), (2.0, -3.0), 0.0, 0.0),
        ("A10 anchor_xy=R offset, xy=0, rot=0, part 0°",
         _cell(anchor_xy=_ANCHOR_OFFSET), (0.0, 0.0), 0.0, 0.0),
        ("A11 anchor_xy=0 corner, xy=0, rot=0, part 0°",
         _cell(anchor_xy=(0.0, 0.0)), (0.0, 0.0), 0.0, 0.0),
    ]
    for label, cell, node_xy, node_rot, board_angle in combos:
        rows.append((label, _row_direct(
            cell=cell, node_xy=node_xy, node_rot=node_rot,
            board_angle=board_angle, anchor_cluster=ENTITY_CLUSTER)))

    # ── 2. the "different part" branch: anchor cluster names ANOTHER part ───
    # The tree anchor is role R + cluster ClX; the node places entity E whose
    # cluster is Cl — the anchor subject is a DIFFERENT physical part, so the
    # placed copy never feeds the base back.
    rows.append((
        "B1  anchor cluster != entity cluster (other part)",
        _row_direct(cell=_cell(), anchor_cluster="ClX",
                    extra_board=[{"ref": "W", "role": ANCHOR_ROLE,
                                  "cluster": ENTITY_CLUSTER,
                                  "x_mm": 200.0, "y_mm": 20.0, "angle": 0.0}],
                    loop=False)))

    # ── 3. nesting: the placing node lives in a tree EMBEDDED by a module ───
    # Outer tree `t` carries the role anchor and a module node -> inner tree
    # `inner` places E. The inner tree is a TOP-LEVEL tree with its OWN (origin)
    # anchor, so the inner node is NOT laid from `t`'s base at all.
    inner_cell = _cell()
    inner_tree = Tree(name="inner", anchor=TreeAnchor(is_origin=True),
                      nodes=[_placement_node()])
    module_node = TreeNode(ref="inner", kind="module", xy=(0.0, 0.0), polar=None,
                           rotation=0.0, name=None, group=None, children=[])
    outer_tree = Tree(name="t",
                      anchor=TreeAnchor(role=ANCHOR_ROLE,
                                        anchor_cluster=ENTITY_CLUSTER),
                      nodes=[module_node])
    cfg_mod = Config(cells={"C": inner_cell}, entities=[_entity()],
                     trees=[outer_tree, inner_tree])
    board_mod = _FakeBoard([
        {"ref": "X", "role": ANCHOR_ROLE, "cluster": ENTITY_CLUSTER,
         "x_mm": _ANCHOR_START[0], "y_mm": _ANCHOR_START[1], "angle": 0.0}])
    rows.append(("C1  placing node inside a MODULE-embedded tree",
                 _measure(cfg_mod, board_mod, anchor_cluster=ENTITY_CLUSTER,
                          loop=False)))

    # ── print ───────────────────────────────────────────────────────────────
    print("Ш0: drift of the tree's OWN anchor part per redraw pass")
    print("vector = (dx_mm, dy_mm) + angle_deg; pass1 = first apply; "
          "pass2 = second apply")
    print("-" * 100)
    print(f"{'row':<52} {'pass1':<26} {'pass2':<26} verdict")
    print("-" * 100)
    for label, res in rows:
        if res.get("loop") is False:
            verdict = "no feedback loop (anchor = other part)"
        elif res.get("fixed"):
            verdict = "FIXED POINT"
        else:
            verdict = "DRIFTS"
        print(f"{label:<52} {_fmt(res.get('pass1')):<26} "
              f"{_fmt(res.get('pass2')):<26} {verdict}")
    print("-" * 100)
    print("NOTE: the module row is marked 'no feedback loop' only because the "
          "inner tree has its OWN anchor; a module-crossing match still needs a "
          "decision in the guard (see report).")


if __name__ == "__main__":
    main()
