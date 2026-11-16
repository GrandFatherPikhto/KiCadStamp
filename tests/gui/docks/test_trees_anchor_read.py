# tests/gui/docks/test_trees_anchor_read.py
"""Cells д, е, з, п–п5, р–р4, т/т2 of
plan_2026_11_16_tree_anchor_read_current_position — the GUI half of «Read current
position» on a TREE ANCHOR, plus the §1 corrections of 16.11.

The arithmetic (and cell ж, «nothing to read») is celled without Qt in
tests/trees/test_tree_anchor_read.py. Here are the decisions this layer owns:
WHEN the button is off, that a tree placed by a module is refused IN THE READ'S
OWN WORDS, that module CONTENT votes while children never do, and that the ANGLE
is compared absolute against absolute (the §1 hole: a turned anchor or a part
standing at 180°/270° refused falsely).

The board is faked at the two seams the read really uses — `child_live_pose` (the
one absolute reader, shared with the offset read) and `_anchor_base_live_position`
(the tree's own anchor, resolved for its ANGLE only) — plus the layout, so every
cell states its geometry explicitly.
"""
import logging
from types import SimpleNamespace

from PyQt6.QtWidgets import QLineEdit, QPushButton

import gui.docks.trees_anchor_read as anchor_read_mod
import gui.docks.trees_base_pose as base_pose_mod
import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_anchor_read import (apply_outcome, read_and_apply,
                                         read_tree_anchor, update_button_state)
from kicadstamp.domain.geometry import Vector2
from kicadstamp.tree_anchor_read import AnchorReadOutcome
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def _tree(name="main", *, self_ref=None, nodes=(), rotation=0.0):
    anchor = (TreeAnchor(ref=self_ref, is_self=True, self_ref=self_ref)
              if self_ref is not None else TreeAnchor(is_origin=True))
    return Tree(name=name, anchor=anchor, nodes=list(nodes), rotation=rotation)


def _node(ref, kind, *, xy=(0.0, 0.0), children=(), anchor=None):
    return TreeNode(ref=ref, kind=kind, xy=xy, polar=None, rotation=0.0,
                    name=None, group=None, children=list(children), anchor=anchor)


def _form(tree, *, trees=(), stored=None):
    button = QPushButton("Read current position")
    form = SimpleNamespace(_tree=tree, read_anchor_button=button,
                           _dock=SimpleNamespace(_trees=list(trees) or ([tree] if tree else []),
                                                 _cfg=None),
                           _sheet_names={}, _stored_anchor_shift=stored)
    form._live_adapter = lambda: None
    form._live_snapshot = lambda: []
    return form


def _fake_board(monkeypatch, *, rots, predicted, live, anchor_rot=0.0):
    """Wire the three seams of one read.

    `rots` — {ref: predicted_abs_deg}; `live` — {ref: (x_mm, y_mm, abs_deg)};
    `predicted` — {ref: (x_mm, y_mm)}."""
    asked: list = []

    monkeypatch.setattr(
        trees_dock_mod, "_anchor_base_live_position",
        lambda *a, **k: (Vector2.from_xy(0, 0), anchor_rot))
    monkeypatch.setattr(
        anchor_read_mod, "layout_tree_from_anchor",
        lambda *a, **k: {ref: (Vector2.from_xy_mm(*xy), rots.get(ref, 0.0))
                         for ref, xy in predicted.items()})

    def _child(cfg, adapter, sheet_names, ref, kind, **k):
        asked.append(ref)
        x, y, deg = live[ref]
        return Vector2.from_xy_mm(x, y), deg, False

    monkeypatch.setattr(base_pose_mod, "child_live_pose", _child)
    return asked


# ── д: a self anchor is live already, an embedded tree is read elsewhere ───

def test_d_a_self_anchor_disables_the_button_with_a_tooltip(qapp):
    form = _form(_tree(self_ref="U1"))

    update_button_state(form)

    assert form.read_anchor_button.isEnabled() is False
    assert "self" in form.read_anchor_button.toolTip().lower()


def test_d_an_origin_anchor_keeps_the_button_on_and_says_what_it_does(qapp):
    form = _form(_tree())

    update_button_state(form)

    assert form.read_anchor_button.isEnabled() is True
    assert "Shift" in form.read_anchor_button.toolTip()


def test_d_a_tree_less_form_is_off_too(qapp):
    """The create-tree dialog has no tree to read an anchor of."""
    form = _form(None)

    update_button_state(form)

    assert form.read_anchor_button.isEnabled() is False


def test_t2_the_button_is_off_for_an_embedded_tree_and_names_module_and_owner(qapp):
    """т2 (§1, hole 3): a form whose tree is placed by a module says so in the
    TOOLTIP — the fact comes from `embedding_marker`, no second scan."""
    child = _tree("power")
    parent = _tree("main", nodes=[_node("power", "module")])
    form = _form(child, trees=[parent, child])

    update_button_state(form)

    assert form.read_anchor_button.isEnabled() is False
    tip = form.read_anchor_button.toolTip()
    assert "power" in tip and "main" in tip, tip


# ── е/т: an embedded tree is refused in the READ'S words, never a crash ───

def test_e_a_self_anchor_reads_nothing_and_says_why():
    form = _form(_tree(self_ref="U1"))

    outcome = read_tree_anchor(form)

    assert outcome.shift is None
    assert "self" in outcome.refusal.lower()


def test_t_read_and_apply_on_an_embedded_tree_does_not_crash(caplog, monkeypatch):
    """т (§1, hole 3): the click that crashed 16.11 18:13 — `(None, None)` reaching
    `apply_outcome(None)`. Now ONE outcome, one Log line in the READ'S words, the
    fields untouched."""
    child = _tree("power")
    parent = _tree("main", nodes=[_node("power", "module")])
    form = _form(child, trees=[parent, child])
    form.shift_x_edit = QLineEdit()
    form.shift_y_edit = QLineEdit()
    form.shift_x_edit.setText("1.000")

    with caplog.at_level(logging.WARNING):
        assert read_and_apply(form) is False

    said = "\n".join(record.message for record in caplog.records)
    assert "Read current position" in said, said
    assert "placed by module" in said and "main" in said, said
    assert "Redraw" not in said, said          # the Redraw line is not this action's
    assert form.shift_x_edit.text() == "1.000"
    assert form.shift_y_edit.text() == ""


# ── з: children do not vote (they are not even asked) ─────────────────────

def test_z_children_are_not_asked_and_do_not_vote(qapp, monkeypatch):
    child = _node("U1_CHILD", "placement", xy=(5.0, 5.0))
    top = _node("U1", "placement", xy=(0.0, 0.0), children=[child])
    tree = _tree(nodes=[top])
    form = _form(tree)
    form._live_adapter = lambda: object()
    asked = _fake_board(
        monkeypatch,
        rots={},
        predicted={"U1": (0.0, 0.0), "U1_CHILD": (5.0, 5.0)},
        live={"U1": (2.0, 0.0, 0.0), "U1_CHILD": (9.0, 9.0, 0.0)})

    outcome = read_tree_anchor(form)

    assert outcome.ok
    assert asked == ["U1"]                    # children are not voters
    assert outcome.count == 1
    assert outcome.shift == (2.0, 0.0)
    assert child.xy == (5.0, 5.0)             # nothing was touched


# ── п: Denis's `main` — mh stands still, the three modules' content moved ──

def test_p_the_module_content_votes_and_disagreement_is_refused(monkeypatch):
    """п: `mh` at Δ = 0 while `power`/`fpga`/`mcu` content stands (−0.01, −2.99)
    off its layout. The button must REFUSE and name all four votes — answering
    "the shift is unchanged" here is exactly the 2.99 mm Redraw that would drag
    the routing along."""
    main = _tree(nodes=[_node("mh", "placement"),
                        _node("power", "module"),
                        _node("fpga", "module"),
                        _node("mcu", "module")])
    power = _tree("power", nodes=[_node("J1", "placement")])
    fpga = _tree("fpga", nodes=[_node("IC1", "placement")])
    mcu = _tree("mcu", nodes=[_node("U4", "placement")])
    form = _form(main, trees=[main, power, fpga, mcu])
    form._live_adapter = lambda: object()
    asked = _fake_board(
        monkeypatch,
        rots={},
        predicted={"mh": (55.54, 60.99), "J1": (55.05, 81.98),
                   "IC1": (114.05, 105.98), "U4": (109.05, 75.98)},
        live={"mh": (55.54, 60.99, 0.0),
              "J1": (55.04, 78.99, 0.0),
              "IC1": (114.04, 102.99, 0.0),
              "U4": (109.04, 72.99, 0.0)})

    outcome = read_tree_anchor(form)

    assert outcome.shift is None
    assert sorted(asked) == ["IC1", "J1", "U4", "mh"]
    line = outcome.refusal
    assert "did not move together" in line
    assert "mh (0.000, 0.000)" in line                     # the still one, first
    assert "power/J1 (-0.010, -2.990)" in line             # labelled by its module
    assert "fpga/IC1" in line and "mcu/U4" in line


def test_p2_content_that_agrees_moves_the_shift(monkeypatch):
    """п2: only modules on top and their content agrees → the shift is computed
    once, from the content, not from a non-existent top-level node."""
    main = _tree(nodes=[_node("a", "module"), _node("b", "module")])
    a = _tree("a", nodes=[_node("R1", "placement")])
    b = _tree("b", nodes=[_node("R2", "placement")])
    form = _form(main, trees=[main, a, b])
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={},
                predicted={"R1": (10.0, 10.0), "R2": (20.0, 10.0)},
                live={"R1": (12.0, 10.0, 0.0), "R2": (22.0, 10.0, 0.0)})

    outcome = read_tree_anchor(form)

    assert outcome.ok, outcome.refusal
    assert outcome.count == 2
    assert outcome.shift == (2.0, 0.0)


def test_p3_a_nested_module_votes_with_the_whole_chain_in_its_label(monkeypatch):
    """п3: module inside module — the content of the innermost tree votes, and its
    label carries the chain so a refusal can be acted on."""
    main = _tree(nodes=[_node("a", "module")])
    a = _tree("a", nodes=[_node("b", "module")])
    b = _tree("b", nodes=[_node("R1", "placement")])
    form = _form(main, trees=[main, a, b])
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={},
                predicted={"R1": (10.0, 10.0)},
                live={"R1": (10.0, 13.0, 0.0)})

    outcome = read_tree_anchor(form)

    assert outcome.ok, outcome.refusal
    assert outcome.shift == (0.0, 3.0)


def test_p4_a_module_that_names_no_tree_is_refused_by_name():
    """п4: a module node whose ref is no tree of this config cannot be measured —
    refuse with its name instead of measuring the rest and staying silent."""
    main = _tree(nodes=[_node("mh", "placement"), _node("ghost", "module")])
    form = _form(main)
    form._live_adapter = lambda: object()

    outcome = read_tree_anchor(form)

    assert outcome.shift is None
    assert "ghost" in outcome.refusal
    assert "references no tree" in outcome.refusal


def test_p5_two_voters_with_one_local_name_are_refused():
    """п5: a `component` node's ref is a LOCAL name, so an embedded tree may reuse
    its host's — the layout's pose map is keyed by ref, so the two poses cannot be
    told apart. Refuse, naming both labels."""
    main = _tree(nodes=[_node("conn", "component",
                              anchor=TreeAnchor(ref="J5", is_origin=False)),
                        _node("sub", "module")])
    sub = _tree("sub", nodes=[_node("conn", "component",
                                    anchor=TreeAnchor(ref="J6", is_origin=False))])
    form = _form(main, trees=[main, sub])
    form._live_adapter = lambda: object()

    outcome = read_tree_anchor(form)

    assert outcome.shift is None
    assert "two nodes named" in outcome.refusal
    assert "conn" in outcome.refusal and "sub/conn" in outcome.refusal


def test_a_voter_missing_from_the_layout_is_refused_by_name(monkeypatch):
    """The §1 remark on df8d1442: a voting node the layout skipped (a mount base
    that did not resolve, for one) used to be dropped silently, so the REST could
    answer "the shift is unchanged" while a node went unmeasured."""
    main = _tree(nodes=[_node("U1", "placement"), _node("U2", "placement")])
    form = _form(main)
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={},
                predicted={"U1": (0.0, 0.0)},
                live={"U1": (0.0, 0.0, 0.0)})

    outcome = read_tree_anchor(form)

    assert outcome.shift is None
    assert "U2" in outcome.refusal
    assert "not in the layout" in outcome.refusal


# ── р: the ANGLE is absolute against absolute, by modulo 360 ──────────────

def test_r_a_part_standing_at_180_or_270_is_not_a_turn(monkeypatch):
    """р: predicted and live are BOTH absolute and may live on either side of the
    0° wrap: 180° and 270° (read as −90°) are the same pose. The old comparison
    subtracted one from the other as-is and refused."""
    main = _tree(nodes=[_node("U1", "placement"), _node("U2", "placement")])
    form = _form(main)
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={"U1": 180.0, "U2": 270.0},
                predicted={"U1": (0.0, 0.0), "U2": (10.0, 0.0)},
                live={"U1": (0.0, 0.0, 180.0), "U2": (10.0, 0.0, -90.0)})

    outcome = read_tree_anchor(form)

    assert outcome.ok, outcome.refusal
    assert outcome.count == 2


def test_r2_a_turned_anchor_does_not_shift_the_angle_check(monkeypatch):
    """р2: the anchor stands at 90° and the part at 0° — both numbers are
    absolute, so they agree. Comparing a RELATIVE live angle against the layout's
    absolute one would refuse here (the §1 hole)."""
    main = _tree(nodes=[_node("U1", "placement")])
    form = _form(main)
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={"U1": 0.0},
                predicted={"U1": (0.0, 0.0)},
                live={"U1": (0.0, 0.0, 0.0)}, anchor_rot=90.0)

    outcome = read_tree_anchor(form)

    assert outcome.ok, outcome.refusal


def test_r3_a_part_really_turned_is_still_refused(monkeypatch):
    """р3: the acceptance above must not swallow a real turn."""
    main = _tree(nodes=[_node("U1", "placement")])
    form = _form(main)
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={"U1": 0.0},
                predicted={"U1": (0.0, 0.0)},
                live={"U1": (0.0, 0.0, 0.5)})

    outcome = read_tree_anchor(form)

    assert outcome.shift is None
    assert "is turned" in outcome.refusal
    assert "U1" in outcome.refusal


def test_r4_a_tree_with_its_own_angle_still_reads(monkeypatch):
    """р4: the tree carries its own angle; the LAYOUT already folds it into the
    predicted pose and the board's live pose is absolute, so it cancels. The
    anchor's angle is the only one the shift conversion needs."""
    main = _tree(nodes=[_node("U1", "placement")], rotation=15.0)
    form = _form(main)
    form._live_adapter = lambda: object()
    _fake_board(monkeypatch, rots={"U1": 15.0},
                predicted={"U1": (10.0, 10.0)},
                live={"U1": (10.0, 12.0, 15.0)})

    outcome = read_tree_anchor(form)

    assert outcome.ok, outcome.refusal
    assert outcome.shift == (0.0, 2.0)


# ── the write: the shift fields only, never a node's xy ──────────────────

def test_the_outcome_writes_the_shift_fields_and_never_a_node_xy(qapp):
    """The write is the two Shift fields, converted by the form's OWN handler —
    no node xy, no anchor point, no tree angle."""
    child = _node("U1_CHILD", "placement", xy=(5.0, 5.0))
    top = _node("U1", "placement", xy=(0.0, 0.0), children=[child])
    tree = _tree(nodes=[top])
    form = _form(tree)
    form.shift_x_edit = QLineEdit()
    form.shift_y_edit = QLineEdit()
    form._stored_anchor_shift = None
    form._conversion_base_deg = lambda: (0.0, 0.0)
    form._on_shift_edited = lambda: None

    assert apply_outcome(form, AnchorReadOutcome(shift=(2.0, 0.0), count=1))

    assert form.shift_x_edit.text() == "2.0"
    assert form.shift_y_edit.text() == "0.0"
    assert (top.xy, child.xy) == ((0.0, 0.0), (5.0, 5.0))
