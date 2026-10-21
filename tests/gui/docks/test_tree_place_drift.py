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
    """Wire the FOUR seams; returns a dict the cells adjust before calling.

    Every seam takes the record REF as its third positional argument, so a cell
    that needs one specific ancestor to differ from the rest can say so through
    `used_by_ref` / `live_by_ref` (the chain cell does exactly that).

    `nonrigid` models the STRICT contract of the live read: a non-rigid cluster
    raises only when `strict=True` was asked for, while a non-strict read would
    answer with a (guessed) pose `nonrigid` mm away — that is what makes the
    strict flag of cell 8 observable instead of decorative.

    The FOURTH seam is the plan-borne host (`plan_pose_subject`), faked here: it
    has its OWN cells (tests/trees/test_tree_plan_warnings.py). A cell declares a
    mount's SUBJECT through `subject_by_node`; otherwise a "placement" node is
    its own subject, exactly as the real host answers."""
    state = {"used": Vector2.from_xy(100 * MM, 100 * MM), "used_deg": 0.0,
             "live": _live(100 * MM, 100 * MM), "raises": None,
             "nonrigid": None, "used_by_ref": {}, "live_by_ref": {},
             "raises_by_ref": {}, "subject_by_node": {}, "subject_raises": None}

    def _subject(cfg, linked_node, tree_name):
        if state["subject_raises"] is not None:
            raise state["subject_raises"]
        override = state["subject_by_node"].get(linked_node.node.ref)
        if override is not None:
            return override
        node = linked_node.node
        record = linked_node.record
        if node.kind == "placement" and getattr(record, "kind", None) == "placement":
            return (node.ref, record)
        return None

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
    monkeypatch.setattr(tpd, "plan_pose_subject", _subject)
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


def test_an_internal_mount_ancestor_names_the_placing_component(seams):
    """A mount node owns no record, so the line must name the COMPONENT whose
    plan pose the mount's children are laid from — never the mount itself.

    WHICH node places the mount is the host's answer (its own cells live in
    tests/trees/test_tree_plan_warnings.py); here the mount's subject is
    declared through the seam, and the cell pins the LINE, not the classification."""
    placing = _node("COMP")
    seams["subject_by_node"] = {"m1": ("COMP", placing.record)}
    seams["live"] = _live(100 * MM, 97 * MM)
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [placing, mount])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1
    assert "COMP" in lines[0] and "m1" not in lines[0]


def test_a_live_mount_ancestor_is_silent(seams):
    """A mount whose role is OUTSIDE the tree is read from the board, so the
    nodes below it follow it — nothing to warn about (the host answers None)."""
    seams["live"] = _live(100 * MM, 97 * MM)
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [mount])

    assert _lines(seams, linked, {"child"}) == []


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


# ── acceptance of 21.10: the SUBJECT, the DISTANCE, the turn ────────────────

def test_a_mounts_placing_component_that_is_redrawn_is_silent(seams):
    """Д-1 (experience 7 of the acceptance): the subject of an internal mount is
    the PLACING component, and when that component is in the run it lands on its
    own plan — no gap is left, so a line about it would be FALSE.

    Before the fix the membership test was on the MOUNT node, and exactly this
    selection printed "COMP is NOT being redrawn"."""
    comp = _node("COMP")
    seams["subject_by_node"] = {"m1": ("COMP", comp.record)}
    seams["live"] = _live(100 * MM, 97 * MM)
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [comp, mount])

    assert _lines(seams, linked, {"COMP", "child"}) == []


def test_a_mounts_placing_component_outside_the_run_is_named(seams):
    """Д-1 control: with the placing component NOT in the run it is not moved, so
    the line must still come — about COMP, and only about it."""
    comp = _node("COMP")
    seams["subject_by_node"] = {"m1": ("COMP", comp.record)}
    seams["live"] = _live(100 * MM, 97 * MM)
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [comp, mount])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1 and "COMP" in lines[0]
    assert "NOT being redrawn" in lines[0]


def test_a_diagonal_shift_is_reported_as_the_distance(seams):
    """Д-2 (experience 8): (2.5, 2.5) mm is 3.5355 mm away, not 2.5000 — the line
    promises a DISTANCE, so the largest axis was the wrong number."""
    seams["live"] = _live(100 * MM + 2_500_000, 100 * MM + 2_500_000)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1
    assert "stands 3.5355 mm" in lines[0]


def test_both_axes_below_the_tolerance_can_still_be_a_deviation(seams):
    """Д-2, the comparison itself: 0.04 mm on BOTH axes is 0.0566 mm away, i.e.
    OVER RIGID_TOLERANCE_MM — the tolerance is compared with the distance, not
    with an axis (the axis version was silent here)."""
    seams["live"] = _live(100 * MM + 40_000, 100 * MM + 40_000)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    lines = _lines(seams, linked, {"child"})

    assert 40_000 < TOL_NM            # each axis alone is UNDER the tolerance
    assert len(lines) == 1
    assert "stands 0.0566 mm" in lines[0]


def test_a_full_turn_between_the_plan_and_the_board_is_no_deviation(seams):
    """Cell 12: the plan says 360 deg, the board says 0 deg — the SAME
    orientation. The turn goes through cell_frame.normalize_deg, so it is 0 and
    the check is silent; without that normalisation it would read -360 deg."""
    seams["used_deg"] = 360.0
    seams["live"] = _live(100 * MM, 100 * MM, deg=0.0)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    assert _lines(seams, linked, {"child"}) == []


def test_a_one_degree_turn_survives_the_round_trip_through_360(seams):
    """The control for cell 12: the same normalisation must not swallow a real
    turn — plan 360, board 1 is ONE degree, over ANGLE_TOLERANCE_DEG."""
    seams["used_deg"] = 360.0
    seams["live"] = _live(100 * MM, 100 * MM, deg=1.0)
    linked = _tree("t", [_node("parent", children=[_node("child")])])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1 and "+1.00" in lines[0]


def test_a_plan_borne_subject_with_no_readable_plan_pose_is_one_honest_line(seams):
    """Д-3м (decision of 21.10): an internal mount placed by a node that is NOT a
    "placement" record still lays its children from the tree, but
    `resolve_base_live_position` cannot give that pose. No deviation number and
    no silence — one "could not check" line with the reason in words.

    The subject comes from the seam here because `find_role_placement_matches`
    only matches kind "placement" nodes — this branch is the DEFENSIVE half of
    the rule, and the line (not the classification) is what this cell pins."""
    seams["subject_by_node"] = {
        "m1": ("COMP", SimpleNamespace(kind="clone", name="COMP"))}
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [_node("COMP", kind="clone", record_kind="clone"), mount])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1
    assert "could not check" in lines[0] and "'COMP'" in lines[0]
    assert "clone record" in lines[0]
    assert "mm from its place" not in lines[0]


def test_a_mount_the_classifier_refuses_is_one_honest_line(seams):
    """The safety net of 21.10: `resolve_internal_mount_match` raises config-level
    fatals (ambiguity / module crossing / cycle). Inside a worker that raise must
    become ONE "could not check" line — never silence, never a broken redraw."""
    seams["subject_raises"] = ValidationError(
        "tree 't': mount node 'm1' is anchored to a role this tree places twice")
    mount = _node("m1", kind="mount", children=[_node("child")],
                  anchor=TreeAnchor(role="FPGA"))
    linked = _tree("t", [mount])

    lines = _lines(seams, linked, {"child"})

    assert len(lines) == 1
    assert "could not check" in lines[0] and "'m1'" in lines[0]
    assert "places twice" in lines[0]
    assert "mm from its place" not in lines[0]
