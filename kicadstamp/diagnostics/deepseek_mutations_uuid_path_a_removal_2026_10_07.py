# kicadstamp/diagnostics/deepseek_mutations_uuid_path_a_removal_2026_10_07.py
"""Acceptance rows for part 2 of plan_2026_10_05_uuid_tails (DeepSeek,
2026-10-07): the unreachable LEGACY literal-net net_trace branch ("path A") of
`config/tree_instances.py` is REMOVED (Д1/4).

  * M1 the old identity is back                -> a nameless record keeps its net
                                                  as the ref and its nets are left
                                                  for a sheet rewrite
  * M2 the lift stops minting a name           -> a nameless record survives into
                                                  the format-3 graph
  * M3 the gate demands the template sheet for a net_trace node again
  * M4 the old-shape refusal loses the node name
  * K1 a cosmetic comment                      -> MUST survive

Guards: tests/trees/test_tree_instances.py (the rewritten Path B cells, the
direct-expansion cell and the old-profile refusal cell).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_uuid_path_a_removal_2026_10_07.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

TREE_INST = "kicadstamp/config/tree_instances.py"
FORMAT_VERSION = "kicadstamp/config/format_version.py"
FORMAT3 = "kicadstamp/config/format3.py"
# M4 targets the ONE detail line the refusal appends; for a TREE node the node's
# own ref is already in the refusal's `label` (so the tree cell cannot see the
# detail go), while for a `clone_placements`/`anchor_point` reference the hint
# exists ONLY in that detail — hence the part-1 cell is the guard for M4.
G = ["test_tree_instances.py", "test_format3_reference_hint.py"]

ROWS = [
    # M1 — path A's identity restored: a nameless record keeps its net as the
    # node ref (the shape whose nets were rewritten by leading-sheet
    # substitution) instead of being renamed like any other record.
    ("M1 the legacy identity is back", TREE_INST,
     "        new_ref = f\"{_record_identity(record)}__{instance_name}\"",
     "        new_ref = (orig_ref if not record.get('name') else "
     "f\"{_record_identity(record)}__{instance_name}\")  # MUTATION",
     "die", G, ()),
    # M2 — the 2->3 lift stops minting a name: a nameless net_traces record
    # survives into the format-3 graph (the precondition path A needed).
    ("M2 the lift stops minting a name", FORMAT_VERSION,
     "        if not isinstance(rec, dict) or rec.get(\"name\"):\n            continue",
     "        if True:  # MUTATION\n            continue",
     "die", G, ()),
    # M3 — the §И.4 gate demands the template's own sheet for a net_trace node
    # again (the pre-Д1/4 behaviour): a named record on a sheetless role anchor
    # is refused.
    ("M3 the gate demands the sheet for net_trace again", TREE_INST,
     "        if node.get('kind') == 'mount':\n            return True\n"
     "        if _template_needs_old_sheet(node.get('children') or []):",
     "        if node.get('kind') in ('mount', 'net_trace'):  # MUTATION\n"
     "            return True\n"
     "        if _template_needs_old_sheet(node.get('children') or []):",
     "die", G, ()),
    # M4 — the refusal of the OLD profile shape loses the node name (the one
    # thing that tells the user WHICH node must be renamed).
    ("M4 the old-shape refusal loses the node name", FORMAT3,
     "            message += _reference_refusal_detail(hint, names_by_section.get(target, set()))",
     "            message += \"\"  # MUTATION",
     "die", G, ()),
    # K1 — cosmetic comment (control): must survive.
    ("K1 cosmetic comment (control)", TREE_INST,
     "logger = logging.getLogger(__name__)",
     "logger = logging.getLogger(__name__)  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
