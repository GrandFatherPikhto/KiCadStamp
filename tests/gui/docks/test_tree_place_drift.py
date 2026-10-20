#!/usr/bin/env python3
"""Cells for gui/docks/tree_place_drift.py — point 2 of
plan_2026_10_20_cells_save_keeps_trees_cfg_fresh: the warning a PARTIAL curated
redraw owes the user when an ancestor it will NOT move is itself off its place in
the tree, while the nodes below it are placed from that tree pose.

Qt-free and board-free: the three seams the module uses (the two base resolvers
and the live read) are monkeypatched in the module's own namespace — they have
their own hosts and their own cells. The trees are built as LinkedTree/LinkedNode
dataclasses, so every number in a cell is an exact nanometre, not a rendering.

The numbers are the ones the live run of 20.10 measured (3CH-AWG-TIA-v103):
`fpga_vcca_35` exactly 3.0000 mm, `mcu_boot0_mcu` 16.5990 mm and +90 deg, and 43
control rows that agreed to the nanometre.
"""
from types import SimpleNamespace

import pytest

import gui.docks.tree_place_drift as tpd
from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.constants import ANGLE_TOLERANCE_DEG
from kicadstamp.domain.geometry import Vector2
from kicadstamp.exceptions import NonRigidClusterError, ValidationError
from kicadstamp.link_trees import LinkedAnchor, LinkedNode, LinkedTree
from kicadstamp.trees import TreeAnchor, TreeNode
from kicadstamp.utils.units import MM

# Derived from the SPEC constant, never from the product's own `_TOL_NM`: a
# boundary cell that took its threshold from the module under test would move
# with any mutation of it and could never catch one (that is exactly what the
# first run of the mutation harness showed).
TOL_NM = int(RIGID_TOLERANCE_MM * MM)


# ── builders ────────────────────────────────────────────────────────────────

def _node(ref, kind="placement", record_kind="placement", children=(),
          anchor=None):
    node = TreeNode(ref=ref, kind=kind, xy=None, polar=None, rotation=0.0,
                    name=None, group=None, children=[], anchor=anchor)
    linked = [c for c in children]
    if kind in ("mount", "module", "copper"):
        record = None
    else:
        record = SimpleNamespace(kind=record_kind, name=ref)
    return LinkedNode(node=node, record=record, is_external=False,
                      children=linked)


def _tree(name, nodes):
    return LinkedTree(name=name,
                      anchor=LinkedAnchor(anchor=TreeAnchor(is_origin=True),
                                          record=None, is_origin=True,
                                          is_external=False),
                      nodes=list(nodes))


def _live(x_nm, y_nm, deg=0.0):
    return SimpleNamespace(position=Vector2.from_xy(x_nm, y_nm),
                           rotation_deg=deg, mirror=False)


@pytest.fixture
def seams(monkeypatch):
    """Wire the three seams; returns a dict the cells adjust before calling.

    Every seam takes the record REF as its third positional argument, so a cell
    that needs one specific ancestor to differ from the rest can say so through
    `used_by_ref` / `live_by_ref` (the chain cell does exactly that).

    `nonrigid` models the STRICT contract of the live read: a non-rigid cluster
    raises only when `strict=True` was asked for, while a non-strict read would
    answer with a (guessed) pose `nonrigid` mm away — that is what makes the
    strict flag of cell 8 observable instead of decorative."""
    state = {"used": Vector2.from_xy(100 * MM, 100 * MM), "used_deg": 0.0,
             "live": _live(100 * MM, 100 * MM), "raises": None,
             "nonrigid": None, "used_by_ref": {}, "live_by_ref": {},
             "raises_by_ref": {}}

    monkeypatch.setattr(
        tpd, "resolve_base_live_position",
        lambda adapter, cfg, ref, *a, **k: state["used_by_ref"].get(
            ref, state["used"]))
    monkeypatch.setattr(
        tpd, "resolve_base_rotation_deg",
        lambda adapter, cfg, ref, *a, **k: state["used_deg"])

    def _read(adapter, cfg, ref, *a, **k):
        if ref in state["raises_by_ref"]:
            raise state["raises_by_ref"][ref]
        if state["raises"] is not None:
            raise state["raises"]
        if state["nonrigid"] is not None:
            if k.get("strict"):
                raise NonRigidClusterError("not a rigid copy",
                                           residual_mm=state["nonrigid"])
            return _live(100 * MM + int(state["nonrigid"] * MM), 100 * MM)
        return state["live_by_ref"].get(ref, state["live"])

    monkeypatch.setattr(tpd, "read_record_live_pose", _read)
    return state


def _lines(seams, linked, names, cfg=None):
    return tpd.place_drift_lines(object(), cfg, [linked], names, {})


# ── the cells ───────────────────────────────────────────────────────────────

def test_an_ancestor_at_its_place_is_silent(seams):
    """CONTROL: the live run's 43 "same place" rows must produce nothing — the
    warning is about a deviation, never about the check having run."""
    child = _node("child")
    linked = _tree("t", [_node("parent", children=[child])])

    assert _lines(seams, linked, {"child"}) == []


def test_a_shifted_ancestor_names_every_selected_child_once(seams):
    """3.0000 mm — the node the live run measured (`fpga_vcca_35`). Two children
    under it mean ONE line naming BOTH, never one line per child."""
    seams["live"] = _live(100 * MM, 97 * MM)          # exactly 3 mm down
    kids = [_node("kid_a"), _node("kid_b")]
    linked = _tree("t", [_node("parent", children=kids)])

    lines = _lines(seams, linked, {"kid_a", "kid_b"})

    assert len(lines) == 1
    assert "parent" in lines[0] and "3.0000" in lines[0]
    assert "kid_a" in lines[0] and "kid_b" in lines[0]
    assert "NOT being redrawn" in lines[0]


def test_an_ancestor_inside_the_run_is_silent(seams):
    """The ancestor IS redrawn (whole-tree redraw): it lands on its planned
    place, so the deviation is not a warning — it is the fix."""
    seams["live"] = _live(100 * MM, 97 * MM)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    assert _lines(seams, linked, {"parent", "child"}) == []


def test_below_the_tolerance_is_silent(seams):
    """0.02 mm — a hand nudge below RIGID_TOLERANCE_MM without a second
    constant."""
    seams["live"] = _live(100 * MM + 20_000, 100 * MM)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    assert _lines(seams, linked, {"child"}) == []


@pytest.mark.parametrize("delta_nm, expected", [
    (TOL_NM - 1, 0),      # just under: silent
    (TOL_NM, 0),          # EXACTLY the tolerance: silent — the operator is ">"
    (TOL_NM + 1, 1),      # one nanometre over: a line
])
def test_the_threshold_is_compared_in_whole_nanometres(seams, delta_nm, expected):
    """The boundary, in whole nanometres: `>`, not `>=` (Denis, 20.10 — no
    fractional-millimetre compares)."""
    seams["live"] = _live(100 * MM + delta_nm, 100 * MM)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    assert tpd._TOL_NM == TOL_NM, (
        "the product must compare against RIGID_TOLERANCE_MM — no second "
        "constant of its own")
    assert len(_lines(seams, linked, {"child"})) == expected


def test_a_turned_ancestor_is_named(seams):
    """Same place, +90 deg — `mcu_boot0_mcu`'s turn in the live run. The nodes
    below it would be laid turned against the real part."""
    seams["live"] = _live(100 * MM, 100 * MM, deg=90.0)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1 and "+90.00" in lines[0]
    assert ANGLE_TOLERANCE_DEG < 90.0     # the constant really is the gate


def test_turning_within_the_angle_tolerance_is_silent(seams):
    """The other side of the same gate: a turn under ANGLE_TOLERANCE_DEG."""
    seams["live"] = _live(100 * MM, 100 * MM, deg=ANGLE_TOLERANCE_DEG / 2)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    assert _lines(seams, linked, {"child"}) == []


def test_an_internal_mount_ancestor_names_the_placing_component(seams, monkeypatch):
    """A mount node owns no record, so the line must name the COMPONENT whose
    plan pose the mount's children are laid from — never the mount itself.

    The placing component is a node of the SAME tree (that is what "internal"
    means), so the index resolves its record."""
    placing = _node("COMP")
    monkeypatch.setattr(tpd, "plain_tree_by_name", lambda cfg, name: object())
    monkeypatch.setattr(tpd, "resolve_internal_mount_match",
                        lambda cfg, tree, node: SimpleNamespace(node=placing.node))
    seams["live"] = _live(100 * MM, 97 * MM)
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [placing, mount])

    lines = _lines(seams, linked, {"child"}, cfg=SimpleNamespace(trees=[]))

    assert len(lines) == 1
    assert "COMP" in lines[0] and "m1" not in lines[0]


def test_a_live_mount_ancestor_is_silent(seams, monkeypatch):
    """A mount whose role is OUTSIDE the tree is read from the board, so the
    nodes below it follow it — nothing to warn about."""
    monkeypatch.setattr(tpd, "plain_tree_by_name", lambda cfg, name: object())
    monkeypatch.setattr(tpd, "resolve_internal_mount_match",
                        lambda cfg, tree, node: None)
    seams["live"] = _live(100 * MM, 97 * MM)
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [mount])

    assert _lines(seams, linked, {"child"}, cfg=SimpleNamespace(trees=[])) == []


def test_a_clone_ancestor_is_silent(seams):
    """A clone's live pose IS read from the board, so it is not plan-borne and a
    warning would be false."""
    seams["live"] = _live(100 * MM, 97 * MM)
    parent = _node("parent", kind="clone", record_kind="clone")
    linked = _tree("t", [parent])
    parent.children.append(_node("child"))

    assert _lines(seams, linked, {"child"}) == []


def test_an_unreadable_live_pose_is_one_honest_line(seams):
    """No number is invented: the line says the check could not be made."""
    seams["raises"] = ValidationError("role 'X' not found on any component")
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1
    assert "could not check" in lines[0] and "parent" in lines[0]
    assert "mm from its place" not in lines[0]


def test_a_non_rigid_cluster_reports_the_residual_and_no_deviation(seams):
    """Cell 8: a cluster that is not a rigid copy of its cell is a GUESS, so the
    line carries the residual — and NOT a deviation number (the honest_live_read
    decision of 20.10).

    The fake honours `strict`, so dropping the flag would answer with a guessed
    pose 2.654 mm away and the cell would read a DEVIATION instead — red."""
    seams["nonrigid"] = 2.654
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1
    assert "2.654" in lines[0] and "not a rigid copy" in lines[0]
    assert "mm from its place" not in lines[0]


def test_a_shifted_grandfather_is_named_when_father_and_child_are_redrawn(seams):
    """Cell 10 (Denis, 20.10): the family is redrawn EXCEPT the grandfather —
    the line must be about the grandfather, and the walk must not stop at the
    redrawn father."""
    seams["live"] = _live(100 * MM, 97 * MM)
    grandchild = _node("grandchild")
    father = _node("father", children=[grandchild])
    linked = _tree("t", [_node("grandfather", children=[father])])

    lines = _lines(seams, linked, {"father", "grandchild"})

    assert len(lines) == 1 and "grandfather" in lines[0]
    assert "father" in lines[0] and "grandchild" in lines[0]


def test_the_walk_goes_past_an_unshifted_father(seams):
    """The CHAIN, not just the nearest parent: only the grandchild is redrawn,
    the father stands exactly on its place and the GRANDFATHER is the shifted
    one. A rule that examined the nearest ancestor (or stopped at the first one)
    would report NOTHING here, because the nearest one is on its place."""
    seams["live_by_ref"] = {"grandfather": _live(100 * MM, 97 * MM)}
    grandchild = _node("grandchild")
    father = _node("father", children=[grandchild])
    linked = _tree("t", [_node("grandfather", children=[father])])

    lines = _lines(seams, linked, {"grandchild"})

    assert len(lines) == 1 and "grandfather" in lines[0]
    assert "father" not in lines[0].replace("grandfather", "")


def test_nothing_selected_below_a_shifted_ancestor_is_silent(seams):
    """Cell 11: an ancestor that stands off its place but has NOTHING selected
    under it is never a subject — the walk starts from the run's own names."""
    seams["live"] = _live(100 * MM, 90 * MM)
    shifted = _node("shifted", children=[_node("child")])
    elsewhere = _node("elsewhere")
    linked = _tree("t", [shifted, elsewhere])

    assert _lines(seams, linked, {"elsewhere"}) == []


def test_every_ancestor_of_a_run_across_two_trees_is_reachable(seams):
    """A run's names may span trees (module content): the parent chain is built
    over the WHOLE forest, so a cross-tree ancestor is still found."""
    seams["live"] = _live(100 * MM, 97 * MM)
    child = _node("child")
    other = _tree("other", [_node("parent", children=[child])])

    lines = tpd.place_drift_lines(object(), None, [other], {"child"}, {})

    assert len(lines) == 1 and "parent" in lines[0]
