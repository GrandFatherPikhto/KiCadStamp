# tests/gui/docks/test_trees_anchor_read.py
"""Cells д and е of plan_2026_11_16_tree_anchor_read_current_position — the GUI
half of «Read current position» on a TREE ANCHOR.

The arithmetic (and cell ж, «nothing to read») is celled without Qt in
tests/trees/test_tree_anchor_read.py; here are the two decisions the FORM owns:
WHEN the button is off, and that a tree placed by a module is refused through the
existing check (`refuse_embedded_tree`), never a second one.
"""
import logging
from types import SimpleNamespace

from PyQt6.QtWidgets import QLineEdit, QPushButton

import gui.docks.trees_anchor_read as anchor_read_mod
import gui.docks.trees_dock as trees_dock_mod
from gui.docks.trees_anchor_read import (apply_outcome, read_tree_anchor,
                                         update_button_state)
from kicadstamp.domain.geometry import Vector2
from kicadstamp.tree_anchor_read import AnchorReadOutcome
from kicadstamp.trees import Tree, TreeAnchor, TreeNode


def _tree(name="main", *, self_ref=None, nodes=()):
    anchor = (TreeAnchor(ref=self_ref, is_self=True, self_ref=self_ref)
              if self_ref is not None else TreeAnchor(is_origin=True))
    return Tree(name=name, anchor=anchor, nodes=list(nodes))


def _form(tree):
    button = QPushButton("Read current position")
    form = SimpleNamespace(_tree=tree, read_anchor_button=button,
                           _dock=None, _sheet_names={},
                           _stored_anchor_shift=None)
    form._live_adapter = lambda: None
    form._live_snapshot = lambda: []
    return form


# ── д: a self anchor is live already — the button is off, with the reason ──

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


# ── е: a tree placed by a module is refused by the EXISTING check ─────────

def _embedded_pair():
    child = _tree("child", nodes=[])
    parent = _tree("parent", nodes=[
        TreeNode(ref="child", kind="module", xy=None, polar=None, rotation=0.0,
                 name=None, group=None, children=[])])
    return parent, child


def test_e_an_embedded_tree_is_refused_through_the_existing_check(caplog):
    parent, child = _embedded_pair()
    form = _form(child)
    form._dock = SimpleNamespace(_trees=[parent, child], _cfg=None)

    with caplog.at_level(logging.WARNING):
        outcome, refusal = read_tree_anchor(form)

    assert outcome is None and refusal is None      # the check reported it
    said = "\n".join(record.message for record in caplog.records)
    assert "placed by module" in said and "parent" in said, said


def test_e_a_self_anchor_reads_nothing_and_says_why():
    form = _form(_tree(self_ref="U1"))
    form._dock = SimpleNamespace(_trees=[form._tree], _cfg=None)

    outcome, refusal = read_tree_anchor(form)

    assert outcome is None
    assert "self" in refusal.lower()


# ── з: children do not vote (a fake board at the two documented seams) ────

def _placed_tree():
    # The child places ITSELF too (kind `placement`): only the TOP-LEVEL rule
    # keeps it out of the vote, so a wrapper that walked into children shows up.
    child = TreeNode(ref="U1_CHILD", kind="placement", xy=(5.0, 5.0), polar=None,
                     rotation=0.0, name=None, group=None, children=[])
    top = TreeNode(ref="U1", kind="placement", xy=(0.0, 0.0), polar=None,
                   rotation=0.0, name=None, group=None, children=[child])
    return _tree(nodes=[top]), top, child


def test_z_children_are_not_asked_and_do_not_vote(qapp, monkeypatch):
    tree, top, child = _placed_tree()
    form = _form(tree)
    form._dock = SimpleNamespace(_trees=[tree], _cfg=None, _sheet_names={})
    form._live_adapter = lambda: object()
    asked: list[str] = []

    monkeypatch.setattr(
        trees_dock_mod, "_anchor_base_live_position",
        lambda *a, **k: (Vector2.from_xy(0, 0), 0.0))
    monkeypatch.setattr(
        anchor_read_mod, "layout_tree_from_anchor",
        lambda *a, **k: {"U1": (Vector2.from_xy(0, 0), 0.0),
                         "U1_CHILD": (Vector2.from_xy(5 * 10 ** 6, 5 * 10 ** 6),
                                      0.0)})

    def fake_offset(cfg, adapter, sheet_names, tree_, parent, ref, kind, **k):
        asked.append(ref)
        if ref == "U1_CHILD":
            return (9.0, 9.0), 0.0      # would disagree — must never be asked
        return (2.0, 0.0), 0.0

    monkeypatch.setattr(trees_dock_mod, "_resolve_live_offset", fake_offset)

    outcome, refusal = read_tree_anchor(form)

    assert refusal is None and outcome.ok
    assert asked == ["U1"]                       # children are not voters
    assert outcome.count == 1
    assert outcome.shift == (2.0, 0.0)
    assert child.xy == (5.0, 5.0)                # nothing was touched


# ── the write: the shift fields only, never a node's xy ──────────────────

def test_the_outcome_writes_the_shift_fields_and_never_a_node_xy(qapp):
    """The write is the two Shift fields, converted by the form's OWN handler —
    no node xy, no anchor point, no tree angle."""
    tree, top, child = _placed_tree()
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
