# kicadstamp/diagnostics/deepseek_mutations_entities_part3a_2026_10_08.py
"""Acceptance mutations for "Доделка 3а" of plan_2026_10_05_entities_under_cells.md
(the refusal names the SELECTION's own entity of the same cell), DeepSeek,
2026-10-08.

Grown from deepseek_mutations_entities_part3_2026_10_08.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing turns
red, and a control that MUST survive.

WHAT IS BEING PROVEN (the new cell lives in
tests/selection/test_selection_narrowing.py):
  * M1 the refusal forgets the selection's own entity (the part-3 wording returns)
  * M2 the lookup returns the cell's FIRST entity, not the one the address names
  * M3 the lookup compares clusters by equality, not `record_address_matches`
  * K1 a cosmetic comment -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_entities_part3a_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

SEL_TEST = ["test_selection_narrowing.py"]
NARROW = "kicadstamp/selection_narrowing.py"
MIXED = "gui/mixed_selection.py"

MUTATIONS = [
    # 1 — the refusal drops the selection's own entity: the part-3 wording comes
    # back and the user is again left with the wrong pair (доделка 3а, п.1).
    ("M1 the refusal forgets the selection's entity", MIXED,
     "                not_the_entity_line(\n"
     "                    expected_address.entity_name, own_cluster, own_sheet,\n"
     "                    cell_entity_naming_address(cfg, cell_name, own_cluster,\n"
     "                                               own_sheet)),\n",
     "                not_the_entity_line(\n"
     "                    expected_address.entity_name, own_cluster, own_sheet),\n",
     "die", SEL_TEST, ()),
    # 2 — the lookup names the cell's FIRST entity instead of the one the
    # selection's address names (the address rule is not consulted at all).
    ("M2 the lookup returns the cell's first entity", NARROW,
     "        if record_address_matches(\n"
     "                (getattr(e, \"cluster\", None), getattr(e, \"sheet\", None)),\n"
     "                (cluster, sheet)):\n"
     "            return str(getattr(e, \"name\", \"\") or \"\") or None\n",
     "        return str(getattr(e, \"name\", \"\") or \"\") or None  # MUTATION\n",
     "die", SEL_TEST, ()),
    # 3 — plain equality instead of the ONE `record_address_matches`: a board
    # cluster tag refining the config's stops naming the entity.
    ("M3 the lookup compares clusters by equality", NARROW,
     "        if record_address_matches(\n"
     "                (getattr(e, \"cluster\", None), getattr(e, \"sheet\", None)),\n"
     "                (cluster, sheet)):\n",
     "        if (getattr(e, \"cluster\", None) == cluster  # MUTATION\n"
     "                and getattr(e, \"sheet\", None) == sheet):\n",
     "die", SEL_TEST, ()),
    # K1 — a cosmetic comment changes nothing: MUST survive.
    ("K1 a cosmetic comment", MIXED,
     "            # The selection names ANOTHER instance — nothing is read from it.",
     "            # The SELECTION names another instance — nothing is read from it.",
     "survive", SEL_TEST, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
