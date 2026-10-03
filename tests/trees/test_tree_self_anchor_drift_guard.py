# tests/trees/test_tree_self_anchor_drift_guard.py
"""The self-anchor drift guard (plan_2026_10_03_tree_self_anchor_drift_guard, Ш2).

ONE parametrized row per cell of the plan's MEASURED tables (Ш0 rows A1-A15 /
B1 / C1 / C2 / D1 and Ш0.2 rows S1-S15, plus the probe's T1-T4 for the tree's own
angle and inner point), so a failing row never hides its neighbours (rule 35):
the guard must refuse exactly the rows the probe measured as drifting, with
exactly the measured shift and turn, and stay silent on exactly the rows it
measured as fixed points. The two EXISTING auto-anchor tests were adjusted for
the S14 row (Denis, 03.10.2026) -- see tests/trees/test_entity_placement.py.

The fix-hint branches have one test each: `anchor_role` cures the mount half
ONLY, a node's own offset and the angle sum have their own cures, and the
`(anchor (self (ref ...)))` suggestion is offered only when it would satisfy the
WHOLE condition (Denis, 03.10.2026). The guard's integration cells -- a profile
carrying such a tree still LOADS, and the neighbour tree of the same run is still
materialized -- live here; the cascade's "skipped, not ok" cell lives in
tests/gui/docks/test_cascade.py.

Names carry no plan number (rule 37): the plan and the row live in the docstring.
"""
import logging
from unittest.mock import MagicMock

import pytest

from kicadstamp.config import Config, Cell, Entity, TemplateComponentSlot
from kicadstamp.config import _load_cell, _load_entity
from kicadstamp.config.tree_instances import expand_tree_instances
from kicadstamp.exceptions import ValidationError
from kicadstamp.placement.entity_placement import materialize_entity_placements
from kicadstamp.trees import (
    Tree,
    TreeAnchor,
    TreeNode,
    check_mount_anchor_drift,
    check_tree_self_anchor_drift,
    find_tree_self_anchor_drifts,
    tree_from_dict,
)
from kicadstamp.utils.units import MM

# ── the synthetic geometry, identical to the probe's ───────────────────────
# R sits at a NONZERO stored offset (the fpga case); ZERO is the bbox-corner
# slot the legacy "zero slot" anchor derivation reads.
R = "FPGA"
ZERO = "OTHER"
_ANCHOR_OFFSET = (4.7504, -13.6943)


def _slot(role, along=0.0, across=0.0, angle=0.0):
    return TemplateComponentSlot(role=role, offset_along_mm=along,
                                 offset_across_mm=across, angle_deg=angle)


def _cell(*, anchor_role=None, anchor_xy=None, slot_angle=0.0, zero_angle=0.0):
    return Cell(name="c", components=[
        _slot(R, *_ANCHOR_OFFSET, angle=slot_angle),
        _slot(ZERO, angle=zero_angle),
    ], anchor_role=anchor_role, anchor_xy=anchor_xy)


def _node(ref="E", xy=(0.0, 0.0), rotation=0.0, kind="placement", children=()):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=rotation,
                    name=None, group=None, children=list(children))


def _tree(*, nodes, anchor=None, rotation=0.0, pivot_xy=None):
    return Tree(name="t", anchor=anchor if anchor is not None else TreeAnchor(role=R),
                nodes=nodes, rotation=rotation, pivot_xy=pivot_xy)


def _self_tree(*, nodes, self_ref="E"):
    return Tree(name="t", anchor=TreeAnchor(is_self=True, self_ref=self_ref),
                nodes=nodes)


def _cfg(cell, tree, *, entities=None, cells=None, trees=None):
    return Config(cells=cells or {"c": cell},
                  entities=entities or [Entity(name="E", cell="c")],
                  trees=trees or [tree])


def _drift_of(cfg, tree=None):
    """The single drift record of `tree` (or the only tree of cfg), None when
    the guard is silent."""
    tree = tree if tree is not None else cfg.trees[0]
    drifts = find_tree_self_anchor_drifts(cfg, tree)
    assert len(drifts) <= 1, "this helper is for single-violation cells"
    return drifts[0] if drifts else None


# ── the rows: (case, cfg builder, expected) ────────────────────────────────
# expected is None (a fixed point: the guard must stay silent) or
# (dx_mm, dy_mm, deg) — the shift and turn the probe measured per redraw.

def _instances_row():
    """D1: a tree_instance copy expands into a NORMAL top-level tree whose own
    anchor is the same role and whose node places the copy's Entity — the guard
    must see it exactly like A1 (the expansion, not the guard, is what renames
    the refs)."""
    data = {
        "cells": {"C": {"layer": "F.Cu", "components": [
            {"role": R, "offset_along_mm": _ANCHOR_OFFSET[0],
             "offset_across_mm": _ANCHOR_OFFSET[1], "angle_deg": 0.0},
            {"role": ZERO, "offset_along_mm": 0.0, "offset_across_mm": 0.0,
             "angle_deg": 0.0},
        ]}},
        "entities": [{"name": "E", "cell": "C", "sheet": "Sh", "cluster": "Cl"}],
        "trees": [{"name": "tpl",
                   "anchor": {"role": R, "cluster": "Cl", "sheet": "Sh"},
                   "nodes": [{"ref": "E", "kind": "placement", "xy": [0.0, 0.0]}]}],
        "tree_instances": [{"template": "tpl", "name": "tpl_a",
                            "sheet": "Own_a", "cluster": "Cl"}],
    }
    expanded = expand_tree_instances(data)
    cells = {name: _load_cell(name, cdata)
             for name, cdata in (expanded.get("cells") or {}).items()}
    entities = [_load_entity(e) for e in (expanded.get("entities") or [])]
    trees = [tree_from_dict(t) for t in (expanded.get("trees") or [])]
    generated = next(t for t in trees if t.name == "tpl_a")
    return Config(cells=cells, entities=entities, trees=trees), generated


def _instances_cfg_and_tree():
    return _instances_row()


_ROWS = [
    # ── Ш0: the (role ...) rows ───────────────────────────────────────────
    ("A1 no anchor_role: the mount does not sit on the anchor slot",
     lambda: _cfg(_cell(), _tree(nodes=[_node()])), (*_ANCHOR_OFFSET, 0.0)),
    ("A4 anchor_role=OTHER: still the mount half",
     lambda: _cfg(_cell(anchor_role=ZERO), _tree(nodes=[_node()])),
     (*_ANCHOR_OFFSET, 0.0)),
    ("A5 anchor_role=R: the slot IS the mount -> fixed point",
     lambda: _cfg(_cell(anchor_role=R), _tree(nodes=[_node()])), None),
    ("A8 handle pivot 90: the sum turns the part",
     lambda: _cfg(_cell(anchor_role=R), _tree(nodes=[_node(rotation=90.0)])),
     (0.0, 0.0, 90.0)),
    ("A9 node xy=(2,-3): the node's own offset repeats",
     lambda: _cfg(_cell(anchor_role=R), _tree(nodes=[_node(xy=(2.0, -3.0))])),
     (2.0, -3.0, 0.0)),
    ("A10 anchor_xy at the slot: fixed point",
     lambda: _cfg(_cell(anchor_xy=_ANCHOR_OFFSET), _tree(nodes=[_node()])), None),
    ("A11 anchor_xy=(0,0): the mount is elsewhere",
     lambda: _cfg(_cell(anchor_xy=(0.0, 0.0)), _tree(nodes=[_node()])),
     (*_ANCHOR_OFFSET, 0.0)),
    ("A12 slot angle 90: the mount is right, the angle is not",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=90.0), _tree(nodes=[_node()])),
     (0.0, 0.0, 90.0)),
    ("A13 slot angle 315 -> a -45 deg turn",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=315.0), _tree(nodes=[_node()])),
     (0.0, 0.0, -45.0)),
    ("A14 slot 90 + handle 270: the sum is 360 -> fixed point",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=90.0),
                  _tree(nodes=[_node(rotation=270.0)])), None),
    ("A15 slot 90 + handle 315: a 45 deg turn",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=90.0),
                  _tree(nodes=[_node(rotation=315.0)])), (0.0, 0.0, 45.0)),
    # ── Ш0: nesting / surroundings ────────────────────────────────────────
    ("C1 the placing node is a CHILD of a module node of the same tree",
     lambda: _cfg(_cell(), _tree(nodes=[_node(ref="inner", kind="module",
                                              children=[_node()])])),
     (*_ANCHOR_OFFSET, 0.0)),
    # ── the tree's OWN angle and inner point (probe rows T1-T4) ───────────
    ("T1 tree rotation 45 + slot 315: the two cancel -> fixed point",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=315.0),
                  _tree(nodes=[_node()], rotation=45.0)), None),
    ("T2 tree rotation 45 + slot 0: a 45 deg turn",
     lambda: _cfg(_cell(anchor_role=R), _tree(nodes=[_node()], rotation=45.0)),
     (0.0, 0.0, 45.0)),
    ("T3 pivot (5,0): the pivot is not the anchor part",
     lambda: _cfg(_cell(anchor_role=R),
                  _tree(nodes=[_node()], pivot_xy=(5.0, 0.0))), (-5.0, 0.0, 0.0)),
    ("T4 tree rotation 45 + pivot (5,0) + slot 315",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=315.0),
                  _tree(nodes=[_node()], rotation=45.0, pivot_xy=(5.0, 0.0))),
     (-3.5355, 3.5355, 0.0)),
    # ── Ш0.2: the (self ...) rows ─────────────────────────────────────────
    ("S1 self anchor, no anchor_role, slot 0: fixed point",
     lambda: _cfg(_cell(), _self_tree(nodes=[_node()])), None),
    ("S3 self anchor, the SUBJECT slot turns (90)",
     lambda: _cfg(_cell(zero_angle=90.0), _self_tree(nodes=[_node()])),
     (0.0, 0.0, 90.0)),
    ("S7 self anchor with anchor_role, the subject slot turns (90)",
     lambda: _cfg(_cell(anchor_role=R, slot_angle=90.0),
                  _self_tree(nodes=[_node()])), (0.0, 0.0, 90.0)),
    ("S13 self anchor, handle rotation 90",
     lambda: _cfg(_cell(), _self_tree(nodes=[_node(rotation=90.0)])),
     (0.0, 0.0, 90.0)),
    ("S14 self anchor, node xy=(2,-3): POSITION drifts too",
     lambda: _cfg(_cell(anchor_role=R), _self_tree(nodes=[_node(xy=(2.0, -3.0))])),
     (2.0, -3.0, 0.0)),
    ("S15 self anchor, anchor_xy=(0,0) against the subject slot",
     lambda: _cfg(_cell(anchor_role=R, anchor_xy=(0.0, 0.0)),
                  _self_tree(nodes=[_node()])), (*_ANCHOR_OFFSET, 0.0)),
]


@pytest.mark.parametrize(
    "case, build, expected", _ROWS,
    ids=[r[0] for r in _ROWS])
def test_the_guard_matches_the_measured_row(case, build, expected):
    """One row per measured cell (probe tables Ш0/Ш0.2 + T1-T4): the verdict AND
    the numbers the guard reports must equal what the probe measured, because a
    verdict that ignores the vector would be indistinguishable from a guard that
    refuses everything (rule 39a: run the check on a healthy sample too — A5,
    A10, A14, S1 and T1 are exactly that)."""
    cfg = build()
    drift = _drift_of(cfg)
    if expected is None:
        assert drift is None, case
        return
    assert drift is not None, case
    dx, dy, deg = expected
    assert (drift.offset.x / MM, drift.offset.y / MM) == pytest.approx(
        (dx, dy), abs=1e-4), case
    assert drift.rotation_deg == pytest.approx(deg, abs=1e-4), case


def test_an_anchor_part_placed_by_another_tree_is_silent():
    """The CONTROL the mount guard's docstring calls `dac_buf_tpl`: the tree's
    (role ...) anchor names a part NO node of THIS tree places (it lives in
    another tree / off this tree) — nothing to refuse, the tree redraws."""
    other = Tree(name="other", anchor=TreeAnchor(is_origin=True),
                 nodes=[_node()])
    cfg = _cfg(_cell(), _tree(nodes=[_node(ref="E2")]),
               entities=[Entity(name="E", cell="c"),
                         Entity(name="E2", cell="plain")],
               cells={"c": _cell(),
                      "plain": Cell(name="plain", components=[_slot(ZERO)])},
               trees=[_tree(nodes=[_node(ref="E2")]), other])
    assert check_tree_self_anchor_drift(cfg, cfg.trees[0]) is None


def test_an_anchor_cluster_naming_another_part_is_silent():
    """Ш0 row B1: the anchor's cluster does not match the placing Entity's, so
    the predicate finds no subject — a legitimate shape, not a violation."""
    tree = Tree(name="t",
                anchor=TreeAnchor(role=R, anchor_cluster="ClX"),
                nodes=[_node()])
    cfg = _cfg(_cell(), tree)
    assert check_tree_self_anchor_drift(cfg, tree) is None


def test_a_match_inside_an_embedded_tree_is_not_refused():
    """Ш0 row C2 (measured): the outer tree's base does NOT lay an embedded
    tree's content — moving the outer anchor does not move it — so it cannot
    drag the outer anchor either. The guard must exclude `match.tree != tree`,
    or every module-embedding profile would be refused."""
    inner = Tree(name="inner", anchor=TreeAnchor(is_origin=True),
                 nodes=[_node()])
    outer = Tree(name="t", anchor=TreeAnchor(role=R),
                 nodes=[_node(ref="inner", kind="module")])
    cfg = Config(cells={"c": _cell()},
                 entities=[Entity(name="E", cell="c")],
                 trees=[outer, inner])
    assert check_tree_self_anchor_drift(cfg, outer) is None


def test_the_guard_is_silent_where_the_live_read_would_fatal():
    """A cell with no `anchor_role` and no single zero-offset component is a
    CONFIG fatal in the live self-anchor read, and the guard must not preempt it
    with a skip of its own (measured: this cell made
    test_auto_anchor_no_zero_slot_is_fatal_not_silent go red on the guard's first
    draft). Self guard silent -> the materializer reaches the read -> the fatal
    surfaces."""
    cfg = Config(cells={"c": Cell(name="c", components=[_slot(R, 1.0, 0.0)])},
                 entities=[Entity(name="E", cell="c")],
                 trees=[_self_tree(nodes=[_node()])])
    assert check_tree_self_anchor_drift(cfg, cfg.trees[0]) is None
    with pytest.raises(ValidationError, match="zero-offset"):
        materialize_entity_placements(None, cfg, {})


def test_the_instances_copy_row_is_refused_like_a_direct_tree():
    """Ш0 row D1 as a guard cell: after tree_instances expansion the copy is an
    ordinary top-level tree, so the guard must refuse it with the SAME vector."""
    cfg, generated = _instances_cfg_and_tree()
    drift = _drift_of(cfg, generated)
    assert drift is not None
    assert (drift.offset.x / MM, drift.offset.y / MM) == pytest.approx(
        _ANCHOR_OFFSET, abs=1e-4)


# ── the fix hints: one test per branch (Denis, 03.10.2026) ─────────────────

def test_the_mount_hint_names_anchor_role_and_the_slot_it_must_sit_on():
    """A != s is the ONLY half `anchor_role` cures, so the hint names the role
    and the numbers — and nothing else, because nothing else is broken."""
    cfg = _cfg(_cell(), _tree(nodes=[_node()]))
    msg = check_tree_self_anchor_drift(cfg, cfg.trees[0])
    assert "set the cell's anchor_role to 'FPGA'" in msg
    assert "anchor_xy to (4.7504, -13.6943)" in msg
    assert "move the node to xy 0" not in msg
    assert "rotation to" not in msg


def test_the_node_offset_hint_says_anchor_role_cannot_cure_it():
    """o != 0 has its own cure, and saying "set anchor_role" there would be a
    wrong diagnosis (Denis, 03.10.2026)."""
    cfg = _cfg(_cell(anchor_role=R), _tree(nodes=[_node(xy=(2.0, -3.0))]))
    msg = check_tree_self_anchor_drift(cfg, cfg.trees[0])
    assert "move the node to xy 0" in msg
    assert "(2.0000, -3.0000)" in msg
    # The hint SAYS anchor_role cannot cure it, so "anchor_role" IS in the text —
    # what must be absent is the mount cure itself.
    assert "set the cell's anchor_role" not in msg


def test_the_angle_hint_gives_the_rotation_that_zeroes_the_sum():
    """The angle half's cure is the node's own `rotation`, with BOTH numbers of
    the sum printed: the rotation already above the node and the slot angle."""
    cfg = _cfg(_cell(anchor_role=R, slot_angle=90.0), _tree(nodes=[_node()]))
    msg = check_tree_self_anchor_drift(cfg, cfg.trees[0])
    assert "rotation to -90.0 deg" in msg
    assert "0.0 deg" in msg            # the rotation above the node
    assert "90.0 deg" in msg           # the anchor part's slot angle
    assert "anchor_role" not in msg


def test_the_self_hint_is_offered_only_when_it_would_satisfy_the_whole_condition():
    """`(anchor (self (ref ...)))` always makes the mount half zero, so it is
    offered when the mount half is what is broken — and NOT when the angle or the
    node's own offset is (measured S14/S15: the switch does not cure either), and
    never for a tree that is ALREADY self-anchored."""
    direct = _cfg(_cell(), _tree(nodes=[_node()]))
    assert "(anchor (self (ref \"E\")))" in \
        check_tree_self_anchor_drift(direct, direct.trees[0])

    angled = _cfg(_cell(anchor_role=R, slot_angle=90.0), _tree(nodes=[_node()]))
    assert "(anchor (self" not in \
        check_tree_self_anchor_drift(angled, angled.trees[0])

    offset = _cfg(_cell(), _tree(nodes=[_node(xy=(2.0, -3.0))]))
    assert "(anchor (self" not in \
        check_tree_self_anchor_drift(offset, offset.trees[0])

    already_self = _cfg(_cell(), _self_tree(nodes=[_node(xy=(2.0, -3.0))]))
    assert "(anchor (self" not in \
        check_tree_self_anchor_drift(already_self, already_self.trees[0])


def test_every_violating_node_of_a_tree_is_collected_not_only_the_first():
    """Plan Ш1: "все нарушения дерева собраны, не первое" — two nodes of one
    tree place cells carrying the anchor role, both are broken, and the ONE
    message names both with their own shift and their own hint."""
    tree = Tree(name="t", anchor=TreeAnchor(role=R),
                nodes=[_node(ref="A"), _node(ref="B", xy=(2.0, -3.0))])
    cfg = Config(
        cells={"c_a": _cell(), "c_b": _cell(anchor_role=R)},
        entities=[Entity(name="A", cell="c_a"), Entity(name="B", cell="c_b")],
        trees=[tree])
    msg = check_tree_self_anchor_drift(cfg, tree)
    assert "node 'A'" in msg and "node 'B'" in msg
    assert "set the cell's anchor_role to 'FPGA'" in msg      # A's cure
    assert "move the node to xy 0" in msg                     # B's cure
    assert msg.count("drifts by") == 2


# ── integration cells ─────────────────────────────────────────────────────

_MINIMAL_PROFILE = """(kicadstamp-config
  (version 2)
  (cells
    (cell "bad_cell"
      (components
        (component (role "FPGA") (offset_along_mm 2.0) (offset_across_mm 1.0))
      )
    )
  )
  (entities
    (entity (name "bad_e") (cell "bad_cell"))
  )
  (trees
    (tree
      (name "bad")
      (anchor (role "FPGA"))
      (node (ref "bad_e") (kind placement) (xy 0.0 0.0))
    )
  )
)
"""


def test_a_profile_carrying_a_drifting_tree_still_loads(tmp_path):
    """Denis, 03.10.2026: the refusal is a REDRAW-time ERROR plus a per-tree
    skip — NEVER a load fatal (it would close the whole profile) and never a
    warning (the silent drift would continue). So the profile LOADS, the load
    guard (check_mount_anchor_drift) accepts it, and only the redraw refuses."""
    from kicadstamp.config import load_config

    path = tmp_path / "config.sexp"
    path.write_text(_MINIMAL_PROFILE, encoding="utf-8")
    cfg, _ctx = load_config(str(path))

    check_mount_anchor_drift(cfg)                      # the LOAD guard: silent
    msg = check_tree_self_anchor_drift(cfg, cfg.trees[0])
    assert msg is not None and "NOT redrawn" in msg


def test_the_same_run_still_materializes_the_neighbour_tree(caplog):
    """"Остальные деревья прогона идут как обычно": the drifting tree is refused
    with a RED line (ERROR — never a WARNING, which would keep the drift silent),
    and the neighbour tree of the SAME call materializes untouched.

    The adapter here has an EMPTY board on purpose: the refusal must happen
    BEFORE any board read of the refused tree, or a busy/unreachable board would
    turn a config refusal into a confusing live error."""
    tree = Tree(name="bad", anchor=TreeAnchor(role=R),
                nodes=[_node(ref="bad_e")])
    good = Tree(name="good", anchor=TreeAnchor(is_origin=True),
                nodes=[_node(ref="good_e", xy=(1.0, 2.0))])
    cfg = Config(
        cells={"bad_cell": _cell(), "good_cell": Cell(
            name="good_cell", components=[_slot("R_G")])},
        entities=[Entity(name="bad_e", cell="bad_cell"),
                  Entity(name="good_e", cell="good_cell")],
        trees=[tree, good])
    empty = MagicMock()
    empty.get_footprints.return_value = []
    empty.get_field_value.return_value = None
    empty.has_field.return_value = True
    empty.get_selected_items.return_value = []

    with caplog.at_level(logging.ERROR,
                         logger="kicadstamp.placement.entity_placement"):
        clones = materialize_entity_placements(empty, cfg, {})

    assert [c.name for c in clones] == ["good_e"]
    assert [(c.xy[0], c.xy[1]) for c in clones] == [(1.0, 2.0)]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "tree 'bad'" in errors[0].getMessage()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
