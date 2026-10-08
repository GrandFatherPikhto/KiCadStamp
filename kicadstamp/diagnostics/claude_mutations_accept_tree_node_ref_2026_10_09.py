# kicadstamp/diagnostics/claude_mutations_accept_tree_node_ref_2026_10_09.py
"""Claude's acceptance rows for п.3 of plan_2026_10_08_tree_node_ref_apply
(commit 8efc08e2) — on top of Demon's rig machinery.

  * C1 the probe ignores the Parent combo's re-hang — a handle moved under a
       mount node passes Apply and the next load refuses the tree
  * C2 a refusal leaves no red line under the form
  * C3 the cascade's ONE Log line is not written
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_tree_node_ref_2026_10_09.py
"""
from kicadstamp.diagnostics import deepseek_mutations_tree_node_ref_2026_10_08 as ds

ROW = "gui/docks/trees_node_row.py"
T = ["test_trees_dock_node_ref_row.py", "test_form_identity.py", "test_trees_dock.py"]

ROWS = [
    ("C1 the probe ignores the re-hang", ROW,
     "    if selected_parent is not current_parent:\n"
     "        dock._detach_node(probe, probe_node)\n",
     "    if False:  # MUTATION\n"
     "        dock._detach_node(probe, probe_node)\n",
     "die", T, ()),
    ("C2 a refusal leaves no red line", ROW,
     "    form.apply_status_label.setText(text)\n"
     "    show_message(text, _ERROR_STYLE, logger)\n",
     "    show_message(text, _ERROR_STYLE, logger)  # MUTATION\n",
     "die", T, ()),
    ("C3 the cascade line is not logged", ROW,
     "        if cascade_note is not None:\n"
     "            logger.info(cascade_note)\n",
     "        if False:  # MUTATION\n"
     "            logger.info(cascade_note)\n",
     "die", T, ()),
    ("K1 cosmetic comment (control)", ROW,
     "def cascade_tree_refs(tree, old_ref: str, new_ref: str) -> list[str]:\n",
     "def cascade_tree_refs(tree, old_ref: str, new_ref: str) -> list[str]:  # control\n",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
