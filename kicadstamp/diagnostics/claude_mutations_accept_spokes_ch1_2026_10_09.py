# kicadstamp/diagnostics/claude_mutations_accept_spokes_ch1_2026_10_09.py
"""Claude's acceptance rows for Ч1 / Д2 of plan_2026_10_08_remove_spokes
(commits b1d01f9a, 475c5dd6) — on top of Demon's rig machinery.

  * C1 the WARNING lists every key again (no truncation) — the live wall of ~355 keys
  * C2 the full list no longer reaches DEBUG
  * K1 a cosmetic comment — MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_spokes_ch1_2026_10_09.py
"""
from kicadstamp.diagnostics import deepseek_mutations_spokes_ch1_2026_10_08 as ds

UP = "kicadstamp/config/registry_upgrade.py"
T = ["test_registry_upgrade_on_disk.py", "test_format3_through_real_step.py",
     "test_registry_equivalence.py"]

ROWS = [
    ("C1 the WARNING lists every key", UP,
     "    shown = \", \".join(items[:limit])\n",
     "    shown = \", \".join(items)  # MUTATION\n",
     "die", T, ()),
    ("C2 the full list does not reach DEBUG", UP,
     "    if items:\n        logger.debug(",
     "    if False:  # MUTATION\n        logger.debug(",
     "die", T, ()),
    ("K1 cosmetic comment (control)", UP,
     "_LISTED_LIMIT = 10\n",
     "_LISTED_LIMIT = 10  # control\n",
     "survive", T, ()),
]


if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
