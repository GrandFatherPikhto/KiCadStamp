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

  * Q17 the "Explode" tab is not a tab of the central group
  * Q18 the INSTANCE list auto-picks the first candidate (own rule, no default)
  * Q19 the lock disables the Config tree again (Р2 kept it, Р2в removed it)
  * Q20 the permanent tab's root is never set (CELL list stays empty)
  * K1  a cosmetic comment — MUST survive
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_page.py"]
DOCK_HUB = "gui/dock_hub.py"
PAGE = "gui/docks/explode_page.py"

ROWS = [
    ("Q17 the Explode tab is not permanent", DOCK_HUB,
     "        self.left_tabs.addTab(self.explode_page, _(\"Explode\"))",
     "        pass  # MUTATION: the tab is not in the central group",
     "die", G, ()),
    ("Q18 the INSTANCE list auto-picks the first", PAGE,
     "                self.instance_combo.setCurrentIndex(-1)   # several: NO default",
     "                self.instance_combo.setCurrentIndex(0)  # MUTATION",
     "die", G, ()),
    ("Q19 the lock disables the Config tree again", DOCK_HUB,
     "        self.left_tabs.tabBar().setEnabled(not active)\n"
     "        if active:\n"
     "            self.left_tabs.setCurrentWidget(self.explode_page)",
     "        self.left_tabs.tabBar().setEnabled(not active)\n"
     "        self.config_tree_dock.tree.setEnabled(not active)  # MUTATION\n"
     "        if active:\n"
     "            self.left_tabs.setCurrentWidget(self.explode_page)",
     "die", G, ()),
    ("Q20 the permanent tab's root is never set", DOCK_HUB,
     "        self._safe_call(\"explode_page.set_root_path\",\n"
     "                        self.explode_page.set_root_path, path)",
     "        pass  # MUTATION: the CELL list is never filled",
     "die", G, ()),
    ("K1 cosmetic comment (control)", PAGE,
     "class ExplodePage(QWidget):",
     "class ExplodePage(QWidget):  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
