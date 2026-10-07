# kicadstamp/diagnostics/claude_mutations_accept_uuid_path_a_2026_10_07.py
"""Claude's own acceptance rows for 33f680de (plan_2026_10_05_uuid_tails part 2:
the unreachable legacy net_trace branch of tree_instances removed), 2026-10-07.

Reuses the machinery of deepseek_mutations_uuid_path_a_removal_2026_10_07.py;
guards widened to the mount file, where the gate and pivot cells live:

  * G1 the copy's anchor_sheet is not set to the instance sheet
  * G2 the node's ref is not mapped to the renamed copy (pivot_ref/ref_map)
  * G3 the gate asks for a sheet on no node at all (mount loses it too)
  * K2 cosmetic comment -> MUST survive

    KICADSTAMP_ACCEPT_ROOT=<tree> .venv/bin/python \
        kicadstamp/diagnostics/claude_mutations_accept_uuid_path_a_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_uuid_path_a_removal_2026_10_07 as ds

T = "kicadstamp/config/tree_instances.py"
G = list(ds.G) + ["test_tree_instances_mount.py"]

ROWS = [
    ("G1 copy anchor_sheet not set", T,
     "        gen_nt['anchor_sheet'] = sheet\n        literal = _literal_item_nets(gen_nt)",
     "        literal = _literal_item_nets(gen_nt)  # MUTATION",
     "die", G, ()),
    ("G2 node ref not mapped to the copy", T,
     "        new_ref = f\"{_record_identity(record)}__{instance_name}\"\n",
     "        new_ref = f\"{_record_identity(record)}__{instance_name}\"\n"
     "        orig_ref = object()  # MUTATION: ref_map entry goes nowhere\n",
     "die", G, ()),
    ("G3 gate never asks for a sheet", T,
     "def _template_needs_old_sheet(nodes: list) -> bool:\n",
     "def _template_needs_old_sheet(nodes: list) -> bool:\n    return False  # MUTATION\n",
     "die", G, ()),
    ("K2 cosmetic comment (control)", T,
     "        gen_nt['anchor_sheet'] = sheet\n",
     "        gen_nt['anchor_sheet'] = sheet  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
