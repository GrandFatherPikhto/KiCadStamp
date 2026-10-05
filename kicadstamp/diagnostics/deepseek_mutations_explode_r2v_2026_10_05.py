# kicadstamp/diagnostics/deepseek_mutations_explode_r2v_2026_10_05.py
"""Acceptance mutations for Р2в of plan
``plan_2026_10_05_explode_r2_r3_tab_and_reread.md`` — the "Explode" tab made a
PERMANENT tab of the central group, with its own CELL / INSTANCE lists. DeepSeek,
2026-10-05.

Machinery (rule 38) is the shared one from
``deepseek_mutations_refresh_mixed_2026_10_05.py``: basename-resolved guards under
tests/, ``_drop_pyc`` for the mutated file, the ``count != 1`` refusal, a
``ПРОМАХ`` when nothing red came back, and a control that MUST survive.

The guard is tests/gui/test_explode_page.py.

  * Q17 the "Explode" tab is not a tab of the CELL page (Р3а-0)
  * Q18 a door does NOT put the entity's address into the page's context
  * Q19 the lock disables the Config tree again (Р2 kept it, Р2в removed it)
  * Q20 the tab's config path is never set
  * K1  a cosmetic comment — MUST survive
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_page.py"]
DOCK_HUB = "gui/dock_hub.py"
PAGE = "gui/docks/explode_page.py"

ROWS = [
    ("Q17 the Explode tab is not on the cell page", DOCK_HUB,
     "        self.cell_anchor_view.add_explode_tab(self.explode_page)",
     "        pass  # MUTATION: the tab is not added to the cell page",
     "die", G, ()),
    ("Q18 the door skips the page context", DOCK_HUB,
     "        if cluster is not None:\n"
     "            self.cell_anchor_view.set_working_context(cluster, sheet)",
     "        if False:  # MUTATION\n"
     "            self.cell_anchor_view.set_working_context(cluster, sheet)",
     "die", G, ()),
    ("Q19 the lock disables the Config tree again", DOCK_HUB,
     "        self.left_tabs.tabBar().setEnabled(unlocked)\n"
     "        self.config_tree_dock.tree.setEnabled(unlocked)",
     "        self.left_tabs.tabBar().setEnabled(unlocked)\n"
     "        pass  # MUTATION",
     "die", G, ()),
    ("Q20 the lock leaves the page's tabs open", DOCK_HUB,
     "        view._tabs.tabBar().setEnabled(unlocked)",
     "        pass  # MUTATION",
     "die", G, ()),
    ("K1 cosmetic comment (control)", PAGE,
     "class ExplodePage(QWidget):",
     "class ExplodePage(QWidget):  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
