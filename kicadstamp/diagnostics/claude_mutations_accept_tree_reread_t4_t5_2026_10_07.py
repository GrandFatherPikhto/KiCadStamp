# kicadstamp/diagnostics/claude_mutations_accept_tree_reread_t4_t5_2026_10_07.py
"""Claude's own acceptance rows for 026313db + a619e984 (plan_2026_10_05_tree_reread_modules,
T4 + T5), 2026-10-07.

The Demon's rig (M1-M20, K1) covers every row the plan listed. These rows mutate
what it leaves unguarded:

  * C1 a NESTED module owns its nodes (not the topmost one)  -> "nested -> topmost"
  * C2 two OWN nodes (owner None) classify as MODULE         -> the tree's own bridge lost
  * C3 the tree's OWN node gets the `<cluster>/<sheet>` label -> existing names drift
  * C4 the B2 worker never closes its adapter                 -> socket leak
  * C5 the B1 worker never selects                            -> silent no-op
  * C6 a read-only template instance is not refused           -> the plan's refusal
  * C7 the report drops the "module N" counter                -> T4-1 report line
  * K2 cosmetic comment -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_tree_reread_t4_t5_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_tree_reread_modules_2026_10_05 as ds

NODES, CAPTURE, COPPER, SELECT = ds.NODES, ds.CAPTURE, ds.COPPER, ds.SELECT
G = ds.GUARDS

ROWS = [
    ("C1 nested module owns its nodes", NODES,
     "                    walk(nested.nodes, owner if owner is not None else node)",
     "                    walk(nested.nodes, node)  # MUTATION",
     "die", G, ()),
    ("C2 two own nodes -> MODULE", COPPER,
     "            if (len(owner_ids) == 1\n"
     "                    and module_owner_by_ref.get(unit.pads[0].ref) is not None):",
     "            if len(owner_ids) == 1:  # MUTATION",
     "die", G, ()),
    ("C3 own node gets the module label", NODES,
     "        if self.owners.get(key) is None:\n            return label\n",
     "        pass  # MUTATION\n",
     "die", G, ()),
    ("C4 B2 worker without finally close", SELECT,
     '            "missing": missing,\n'
     "        }\n"
     "    finally:\n"
     "        if adapter is not None:\n"
     "            adapter.close()",
     '            "missing": missing,\n'
     "        }\n"
     "    finally:\n"
     "        if adapter is not None:\n"
     "            pass  # MUTATION",
     "die", G, ()),
    ("C5 B1 worker never selects", SELECT,
     "        adapter.select_items(selected)\n"
     "        return {\n"
     '            "tree": payload["tree"].name,\n'
     '            "pieces": len(units),',
     "        pass  # MUTATION\n"
     "        return {\n"
     '            "tree": payload["tree"].name,\n'
     '            "pieces": len(units),',
     "die", G, ()),
    ("C6 read-only instance not refused", SELECT,
     "    if tree is None or dock._warn_read_only_instance(tree):",
     "    if tree is None:  # MUTATION",
     "die", G, ()),
    ("C7 report drops the module counter", CAPTURE,
     "_DISCARD_ORDER = (CopperVerdict.FOREIGN, CopperVerdict.MODULE,\n",
     "_DISCARD_ORDER = (CopperVerdict.FOREIGN,  # MUTATION\n",
     "die", G, ()),
    ("K2 cosmetic comment (control)", SELECT,
     "    tree = _guarded_tree(dock)\n    if tree is None:\n        return\n"
     "    _run_select_copper(dock, trigger, run_select_inter_node_copper_worker,",
     "    tree = _guarded_tree(dock)  # control\n    if tree is None:\n        return\n"
     "    _run_select_copper(dock, trigger, run_select_inter_node_copper_worker,",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.MUTATIONS = ROWS
    ds.main()
