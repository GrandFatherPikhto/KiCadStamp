# tests/gui/docks/test_trees_move_flow.py
"""Сторожа Ч1 п.2–п.7 of plan_2026_10_14_tree_group_move_and_mount_rebase: the
PURE half of the group "Move to…" — which nodes a selection moves, and which
parents may take them (gui/docks/trees_move_flow.py).

No widgets, no dock, no config file: the module under guard walks a `Tree`'s
nodes and answers with plain lists, so the cells here read as data.

The TABLE of `group_roots`' cells (rule 35 — a transforming function gets its
field x branch table BEFORE the cells are written):

  selection                                    | expected group
  ---------------------------------------------|-------------------------------
  one node                                     | that node
  two siblings, clicked in REVERSE order       | both, DOCUMENT order
  ancestor + descendant (either click order)   | the ancestor only
  three levels (grandparent, parent, child)    | the grandparent only
  a node and its uncle                         | both, document order
  a node that is not in the tree at all        | ignored, the rest intact
  nothing                                      | nothing

And the branch table of the candidate list:

  candidate                                  | offered?
  -------------------------------------------|-------------------------------
  "(top level)"                              | when the rule accepts None
  a selected node / its subtree              | never (structural invariant)
  a node the rule rejects for ONE group node | never (the whole group must fit)
  anything else                              | yes, by ref
"""
from kicadstamp.trees import Tree, TreeAnchor, TreeNode

from gui.docks.trees_move_flow import (forbidden_subtrees, group_roots,
                                       move_candidates, split_untouchable)


def _node(ref, kind="external", children=()):
    return TreeNode(ref=ref, kind=kind, xy=(0.0, 0.0), polar=None,
                    rotation=0.0, name=None, group=None, children=list(children))


def _tree():
    """A ── A1 ── A1a
         └─ A2
       B
       C ── C1

    Returned as (tree, refs) — `refs` is the ref -> node map the cells read, so
    no cell has to re-walk the tree to find a node."""
    a1a = _node("A1a")
    a1 = _node("A1", children=[a1a])
    a2 = _node("A2")
    a = _node("A", children=[a1, a2])
    b = _node("B")
    c1 = _node("C1")
    c = _node("C", children=[c1])
    tree = Tree(name="t", anchor=TreeAnchor(is_origin=True), nodes=[a, b, c])
    refs = {node.ref: node for node in (a, a1, a1a, a2, b, c, c1)}
    return tree, refs


def _refs(nodes):
    return [node.ref for node in nodes]


# ── group_roots: Ч1 п.3 (предок + потомок) and Ч1 п.7 (порядок документа) ──

def test_a_single_selected_node_is_the_group_itself():
    tree, refs = _tree()
    assert _refs(group_roots(tree, [refs["B"]])) == ["B"]


def test_two_siblings_come_back_in_document_order():
    """The click order is NOT the order: the group comes back in document order,
    which is the order the moved nodes take under their new parent (Ч1 п.7 — the
    redraw reads that order as its tie-breaker)."""
    tree, refs = _tree()
    assert _refs(group_roots(tree, [refs["C"], refs["A"]])) == ["A", "C"]


def test_an_ancestor_swallows_the_selected_descendant():
    """Ч1 п.3: a selected descendant of a selected node rides with its ancestor —
    moving both would be a second, contradictory instruction."""
    tree, refs = _tree()
    assert _refs(group_roots(tree, [refs["A"], refs["A1a"]])) == ["A"]


def test_the_selection_order_does_not_change_that():
    """…and it does not depend on which row the user clicked first."""
    tree, refs = _tree()
    assert _refs(group_roots(tree, [refs["A1a"], refs["A"]])) == ["A"]


def test_three_levels_keep_only_the_topmost_node():
    tree, refs = _tree()
    assert _refs(group_roots(tree, [refs["A"], refs["A1"], refs["A1a"]])) == ["A"]


def test_a_node_and_its_uncle_are_both_kept_in_document_order():
    """The nephew is NOT swallowed: A1 is a child of A, and A is not selected, so
    the group is {A1, B} — the subtree rule is about ANCESTORS, not about depth."""
    tree, refs = _tree()
    assert _refs(group_roots(tree, [refs["B"], refs["A1"]])) == ["A1", "B"]


def test_a_node_that_is_not_in_the_tree_is_ignored():
    """The selection comes from the tree's own widget, so a foreign node means a
    bug elsewhere: it is dropped rather than raising, and it cannot drag the rest
    of the group with it."""
    tree, refs = _tree()
    foreign = _node("FOREIGN")
    assert _refs(group_roots(tree, [refs["B"], foreign])) == ["B"]


def test_an_empty_selection_has_no_group():
    tree, _refs_map = _tree()
    assert group_roots(tree, []) == []


# ── forbidden_subtrees / move_candidates: Ч1 п.4 ────────────────────────────

def test_forbidden_subtrees_is_every_selected_subtree_in_document_order():
    tree, refs = _tree()
    roots = group_roots(tree, [refs["A"], refs["C"]])
    assert _refs(forbidden_subtrees(tree, roots)) == \
        ["A", "A1", "A1a", "A2", "C", "C1"]


#: What the stand-in rule returns when it REFUSES a candidate. It must not be
#: `candidate` itself: refusing "(top level)" means refusing a candidate that IS
#: None, so returning it would read as the genuine `None` of "allowed" and the
#: cell would prove nothing. The real rule returns the pinning MOUNT NODE (never
#: None), which is the shape this sentinel stands in for.
_PINNED = object()


def _rule(*rejected_refs):
    """A stand-in for the pivot-ref rule: it refuses the named candidates (None
    means "(top level)") for EVERY node, and records every question."""
    calls: list[tuple] = []

    def _predicate(node, candidate):
        calls.append((node.ref, None if candidate is None else candidate.ref))
        name = None if candidate is None else candidate.ref
        return _PINNED if name in rejected_refs else None

    return _predicate, calls


def test_candidates_offer_the_top_level_row_first():
    tree, refs = _tree()
    rows = move_candidates(tree, group_roots(tree, [refs["B"]]), _rule()[0])
    assert rows[0][1] is None
    assert rows[0][0] == "(top level)"


def test_candidates_exclude_every_selected_node_and_its_subtree():
    """Ч1 п.4: a node cannot move into its own subtree, so the picker must not
    even offer it — nor any descendant of a selected node."""
    tree, refs = _tree()
    rows = move_candidates(tree, group_roots(tree, [refs["A"], refs["C"]]),
                           _rule()[0])
    offered = [label for label, _node_ in rows]
    assert offered == ["(top level)", "B"], \
        "A/C (and their subtrees) are forbidden parents; B is the only one left"


def test_a_candidate_the_rule_rejects_for_one_node_is_not_offered():
    """`_pivot_ref_mount_parent` is per NODE: one member of the group pinned to a
    live component makes the next load a fatal, so the candidate goes — even
    though the other member would be happy with it."""
    tree, refs = _tree()
    roots = group_roots(tree, [refs["A1"], refs["B"]])
    rows = move_candidates(tree, roots, _rule("C")[0])
    assert "C" not in [label for label, _node_ in rows]


def test_the_top_level_row_is_not_offered_when_the_rule_refuses_it():
    """The top-level row goes through the SAME rule as every other candidate —
    it is not a hard-wired first row. (The REAL rule always allows it: a
    pivot-ref node hanging at the top level is legal — `_pivot_ref_mount_parent`
    returns None for candidate None. The cell pins the module's behaviour, not
    that rule: with a rule that refuses it, the row is GONE while every allowed
    node of the tree is still offered, in document order.)"""
    tree, refs = _tree()
    group = group_roots(tree, [refs["B"]])

    refused = [label for label, _node_ in
               move_candidates(tree, group, _rule(None)[0])]
    assert "A" in refused, "the other candidates are unaffected by that refusal"
    assert "(top level)" not in refused

    allowed = [label for label, _node_ in move_candidates(tree, group, _rule()[0])]
    assert allowed == ["(top level)", "A", "A1", "A1a", "A2", "C", "C1"], \
        "…and with a rule that allows it, the row is back, first"


def test_the_rule_is_the_injected_predicate_and_its_answer_decides():
    """The module owns NO copy of the pivot-ref rule: it asks the callable it was
    handed — with (node, candidate) pairs OF THE GROUP, the top-level row
    included — and believes the answer. A cell that only checked the outcome
    would pass with a re-implemented rule that happens to agree on this tree."""
    tree, refs = _tree()
    roots = group_roots(tree, [refs["A"], refs["C"]])

    strict, calls = _rule("B")
    strict_labels = [label for label, _node_ in
                     move_candidates(tree, roots, strict)]
    assert ("A", None) in calls, "the top-level row is asked about too"
    assert ("A", "B") in calls

    lenient_labels = [label for label, _node_ in
                      move_candidates(tree, roots, _rule()[0])]

    # The group is {A, C}: their subtrees are forbidden parents, so B is the one
    # candidate in play — and the injected rule is the ONLY thing that decides it.
    assert lenient_labels == ["(top level)", "B"]
    assert strict_labels == ["(top level)"]


# ── split_untouchable: Ч1 п.5 (медь в выделении) ────────────────────────────

def test_copper_nodes_are_split_out_of_the_group():
    """A copper node carries no position of its own (its copper is offset from its
    OWN anchor pad), so it cannot be re-hung with the group; the caller reports it
    in the Log instead of silently dropping it."""
    tree, refs = _tree()
    copper = _node("nt__a__b", kind="net_trace")
    legacy = _node("legacy_copper", kind="copper")
    selection = [refs["A"], copper, refs["B"], legacy]

    movable, excluded = split_untouchable(selection, {"net_trace", "copper"})

    assert _refs(movable) == ["A", "B"]
    assert _refs(excluded) == ["nt__a__b", "legacy_copper"]


def test_the_split_keeps_the_order_and_excludes_nothing_when_all_are_movable():
    tree, refs = _tree()
    movable, excluded = split_untouchable([refs["C"], refs["A"]], {"net_trace"})
    assert _refs(movable) == ["C", "A"], "order is the caller's, untouched"
    assert excluded == []
