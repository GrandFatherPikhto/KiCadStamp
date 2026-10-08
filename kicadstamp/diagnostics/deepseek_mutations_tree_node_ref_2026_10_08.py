# kicadstamp/diagnostics/deepseek_mutations_tree_node_ref_2026_10_08.py
"""Acceptance mutations for "Apply в форме узла не обновляет строку дерева после
смены Ref" (techdocs/handoff/deepseek/plan/plan_2026_10_08_tree_node_ref_apply.md),
DeepSeek, 2026-10-08.

Grown from deepseek_mutations_entities_part3a_2026_10_08.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing turns
red, and a control that MUST survive.

WHAT IS BEING PROVEN (the row update in gui/docks/trees_node_row.py and its ONE
call site in NodeFormWidget.apply()):
  * M1 Apply does not touch the row (the call site is removed)
  * M2 the `_node_items` key is not translated (the old key is left behind)
  * M3 the row text is computed by hand, not through the render's `_node_item_text`
  * K1 a cosmetic comment -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_tree_node_ref_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

ROW_TEST = ["test_trees_dock_node_ref_row.py"]
DOCK = "gui/docks/trees_dock.py"
ROW = "gui/docks/trees_node_row.py"

MUTATIONS = [
    # 1 — Apply goes back to only marking the dock dirty: the row keeps the old
    # text and the old registry key (the reported defect, п.2 of the plan).
    ("M1 Apply stops updating the row", DOCK,
     "            self._dock._mark_dirty()\n"
     "            # The row is a SEPARATE object keyed by the node's ref — move its\n"
     "            # registry key to the new ref and repaint it through the render's\n"
     "            # own routine (no _rebuild_tabs: it would tear this form down).\n"
     "            refresh_node_row(self._dock, self._tree, self._existing, old_ref)\n",
     "            self._dock._mark_dirty()\n",
     "die", ROW_TEST, ()),
    # 2 — the SAME item is left registered under the OLD ref too: the new key is
    # added, but the stale old key survives (10 readers of _node_items).
    ("M2 the old _node_items key is left behind", ROW,
     "    item = items.pop(old_ref, None)\n",
     "    item = items.get(old_ref)  # MUTATION: old key stays\n",
     "die", ROW_TEST, ()),
    # 3 — the row text is a SECOND calculation (bare ref), not the render's own
    # `_node_item_text`: the kind tag on a Kind edit never appears.
    ("M3 row text is not _node_item_text", ROW,
     "    if tree is not None:\n"
     "        # The render's own text/marks routine — the row is repainted from the\n"
     "        # node the form just committed, never by a second calculation here.\n"
     "        refresh = getattr(dock, \"_refresh_tree_marks\", None)\n"
     "        if refresh is not None:\n"
     "            refresh(tree)\n",
     "    if tree is not None:\n"
     "        item.setText(0, node.ref)  # MUTATION: no kind tag\n",
     "die", ROW_TEST, ()),
    # K1 — a cosmetic comment changes nothing: MUST survive.
    ("K1 a cosmetic comment", ROW,
     "It lives OUTSIDE trees_dock.py on purpose: that file is a giant (7955 lines) and",
     "It lives OUTSIDE trees_dock.py on purpose: that file is a GIANT (7955 lines) and",
     "survive", ROW_TEST, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
