# kicadstamp/diagnostics/demon_mutations_trees_delete_2026_10_22.py
"""Acceptance mutations for Ч1 of
plan_2026_10_22_trees_group_delete_and_no_apply (group delete of tree nodes, a
copper node leaving with its `net_traces:` record), 2026-10-22.

Machinery — deepseek_mutations_refresh_mixed_2026_10_05 (rule 38): the
``original.count(old) != 1`` refusal, ``_drop_pyc``, ``PYTHONDONTWRITEBYTECODE=1``,
``-n auto``, a verdict of «ПРОМАХ» when nothing red came back, and a control that
MUST survive. Every row maps to a line of the plan's own mutation list.

  * M1  only the CLICKED node is deleted (the reported defect)  -> the group cell red
  * M2  the record is never removed                             -> "records went" red
  * M3  a SHARED record is removed anyway                       -> the kept cell red
  * M4  every selected node is a root (a child deleted twice)   -> the child cell red
  * M5  a generated instance is deletable                       -> the instance cell red
  * M6  the record is looked for in the ROOT only               -> the include cell red
  * M7  the menu label always says "Delete node"                -> the count cell red
  * K1  a cosmetic comment                                      -> MUST survive

NOT text-expressible as a row, named here so the list is honest: «the handler is
a filter on the TREE WIDGET, not a dock-wide shortcut» is a property of WHERE
`installEventFilter` is called (gui/docks/trees_dnd.py, the widget's own birth
place); replacing it with a `QShortcut` would still leave the text of the filter
class intact. It is guarded by the cell that types Delete into a form FIELD
(test_the_delete_key_in_a_form_field_keeps_the_nodes) — which is exactly the
behaviour a dock-wide shortcut breaks.

An optional row-name prefix filter takes the rest of argv (rig's own behaviour)."""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

DELETE = "gui/docks/trees_delete.py"
GUARDS = ["test_trees_delete.py"]

MUTATIONS = [
    # The defect Denis reported: the menu deleted the ONE row it was opened on.
    ("M1 only the clicked node is deleted", DELETE,
     "    nodes = list(selection) if selection else (\n"
     "        [clicked] if isinstance(clicked, TreeNode) else [])",
     "    nodes = [clicked] if isinstance(clicked, TreeNode) else []  # MUTATION",
     "die", GUARDS, ()),
    ("M2 the record is never removed", DELETE,
     "    removed, not_found = remove_net_trace_records(\n"
     '        getattr(dock, "_root_path", None), orphaned)',
     "    removed, not_found = [], []  # MUTATION",
     "die", GUARDS, ()),
    ("M3 a shared record is removed anyway", DELETE,
     "    orphaned = [identity for identity in identities if identity not in remaining]",
     "    orphaned = list(identities)  # MUTATION",
     "die", GUARDS, ()),
    ("M4 a child of a selected parent is a root too", DELETE,
     "    roots = group_roots(tree, nodes)",
     "    roots = list(nodes)  # MUTATION",
     "die", GUARDS, ()),
    ("M5 a generated instance is deletable", DELETE,
     "    if dock._instance_of(tree) is not None:",
     "    if False:  # MUTATION",
     "die", GUARDS, ()),
    ("M6 the record is looked for in the ROOT only", DELETE,
     '        target = find_list_entry_file(root_path, "net_traces", {"name": identity})',
     "        target = root_path  # MUTATION",
     "die", GUARDS, ()),
    ("M7 the label always says one node", DELETE,
     "    count = len(selection) if selection else 1",
     "    count = 1  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", DELETE,
     '                      n=len(roots), refs=", ".join(node.ref for node in roots),',
     '                      n=len(roots), refs=", ".join(node.ref for node in roots),  # control',
     "survive", GUARDS, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
