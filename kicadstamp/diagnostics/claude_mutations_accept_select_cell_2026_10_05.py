# kicadstamp/diagnostics/claude_mutations_accept_select_cell_2026_10_05.py
"""Claude's acceptance rows for c74e106 (Н5 "Select cell" + м1-м3 of
plan_2026_10_04_refresh_mixed_cluster_selection), 2026-10-05.

The edges the Demon's S1-S4 do not cover: the instance refs that make an
`anchor:<ref>` key "own", the two doors (tree menu, DockHub delegate), and the
м2/м3 fixes. Same machinery as deepseek_mutations_refresh_mixed_2026_10_05.py.

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_select_cell_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = rig.GUARDS + ["test_select_cell.py", "test_dialog_min_width.py",
                  "test_refresh_by_cluster_h4a.py", "test_cell_editor.py",
                  "test_config_tree.py", "test_phase3_wiring.py"]
SELECT = "gui/select_cell.py"
EDITOR = rig.EDITOR
REFRESH = rig.REFRESH
NARROWING = rig.NARROWING

ROWS = [
    ("C1 instance refs lost (anchor: copper)", SELECT,
     "    refs = frozenset(\n        own_refs if own_refs is not None",
     "    refs = frozenset() if True else frozenset(  # MUTATION\n        own_refs if own_refs is not None",
     "die", G, ()),
    ("C2 DockHub delegate dropped", "gui/dock_hub.py",
     "        self.cells_dock.select_cell_requested(name, file_path, cluster, sheet)",
     "        pass  # MUTATION",
     "die", G, ()),
    ("C3 tree menu item emits nothing", "gui/docks/config_tree.py",
     "                    lambda: self.cell_select_requested.emit(\n"
     "                        old_name, file_path, None, None))",
     "                    lambda: None)  # MUTATION",
     "die", G, ()),
    ("C4 м3 address filter off", REFRESH,
     "        if chosen_cluster is None:\n            return True",
     "        if True:  # MUTATION\n            return True",
     "die", G, ()),
    ("C5 м2 retired/skip noisy again", NARROWING,
     "            if not (getattr(nt, \"retired\", False) or getattr(nt, \"skip\", False)):",
     "            if True:  # MUTATION",
     "die", G, ()),
    # ── Н5а (acceptance of 4e21c2a) ────────────────────────────────────────
    ("C7 entity item loses its instance", "gui/docks/config_tree.py",
     "                        c=entity.get(\"cluster\"), s=entity.get(\"sheet\"):",
     "                        c=None, s=None:  # MUTATION",
     "die", G, ()),
    ("C8 explicit instance ignored", SELECT,
     "    if cluster is not None:\n        return cluster, sheet",
     "    if False:  # MUTATION\n        return cluster, sheet",
     "die", G, ()),
    ("C9 one-record fallback off", "gui/docks/cell_editor.py",
     "        if len(instances) == 1:\n            cluster, sheet = instances[0]",
     "        if False:  # MUTATION\n            cluster, sheet = instances[0]",
     "die", G, ()),
    # ── Н5б (acceptance of 4e21c2a) ─────────────────────────────────────────
    ("C10 cell file not resolved", EDITOR,
     "                target = find_dict_entry_file(self._root_path, \"cells\", name)",
     "                target = None  # MUTATION",
     "die", G, ()),
    ("C11 same cell reloaded (edits lost)", EDITOR,
     "        if self.name_edit.text().strip() != name:",
     "        if True:  # MUTATION",
     "die", G, ()),
    ("C12 entity door sends its own file again", "gui/docks/config_tree.py",
     "                        self.cell_select_requested.emit(n, None, c, s))",
     "                        self.cell_select_requested.emit(n, file_path, c, s))  # MUTATION",
     "die", G, ()),
    ("K6 cosmetic comment (control)", SELECT,
     "    missing = 0\n",
     "    missing = 0  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
