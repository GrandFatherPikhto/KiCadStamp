# tests/gui/test_overrides_tree_mark.py
"""Т5г/С26 (plan_2026_09_18_field_overrides_store): the Components tree is a
SHOWCASE, not a picker.

Since Т5г its rows show the value IN FORCE — OURS when the store has one — which
is what a picker must offer (С25). A showcase that silently showed that value
would hide the fact that the board carries something else, so such a row is
MARKED. The mark is exactly "effective ≠ physical", which needs no bookkeeping of
its own and cannot drift from the diff, which reads the same two values (С27).
"""
from kicadstamp.explore import Selected

from gui.docks.role_cluster_tree import RoleClusterTreeDock


def _selected(ref, role, board_role):
    """A snapshot row the way explore.Board.builds it: `role` is the value in
    force, `board_role` what physically lies on the board."""
    return Selected(ref=ref, role=role, cluster=None, sheet=[], nets={},
                    fp=object(), board_role=board_role)


def test_c26_a_row_whose_value_came_from_the_store_is_marked(main_window):
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([
        _selected("C1", "OURS", "ON_BOARD"),
        _selected("C2", "SAME", "SAME"),
    ])

    texts = _leaf_texts(dock)

    assert any(t.startswith("C1") and "from the store" in t for t in texts)
    # ... and a row whose value IS the board's stays quiet: a mark on every row
    # would say nothing at all.
    assert any(t == "C2" for t in texts)


def test_c26_no_mark_when_nothing_came_from_the_store(main_window):
    """The negative half, in the shape that matters most: the PRE-STORE world (no
    layer at all, so `role == board_role` everywhere) must look exactly as it did
    before Т5г — no marker anywhere."""
    dock = RoleClusterTreeDock(main_window)
    dock.set_footprints([_selected("C1", "C_IN", "C_IN"),
                         _selected("C2", "OUT_AMP", "OUT_AMP")])

    assert all("from the store" not in text for text in _leaf_texts(dock))


def _leaf_texts(dock):
    """Every item's text, groups included — the leaves are what carries the mark,
    but walking the whole model keeps this independent of grouping mode."""
    out = []
    model = dock.tree.model()

    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row)
            out.append(child.text())
            walk(child)

    walk(model.invisibleRootItem())
    return out
