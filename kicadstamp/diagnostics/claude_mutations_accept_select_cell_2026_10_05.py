# kicadstamp/diagnostics/claude_mutations_accept_select_cell_2026_10_05.py
"""Claude's acceptance rows for c74e106 (Н5 "Select cell" + м1-м3 of
plan_2026_10_04_refresh_mixed_cluster_selection), 2026-10-05.

The edges the Demon's S1-S4 do not cover: the instance refs that make an
`anchor:<ref>` key "own", the two doors (tree menu, DockHub delegate), and the
м2/м3 fixes. Same machinery as deepseek_mutations_refresh_mixed_2026_10_05.py.

Re-pointed 2026-10-08 at d2fcf0be: C1, C3, C7, C9, C10, C11 and K6 had gone
INVALID (code moved to absent_copper_prune / resolve_action_instance / the
entity-tree mixin; the two CellDock doors share a body). C3 and C7 SURVIVE the
full suite: their old guards read the SOURCE text, not the emitted signal —
handed to the Demon as cells (plan_2026_10_05_entities_under_cells, 1а п.6).

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_select_cell_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = rig.GUARDS + ["test_select_cell.py", "test_dialog_min_width.py",
                  "test_refresh_by_cluster_h4a.py", "test_cell_editor.py",
                  "test_config_tree.py", "test_phase3_wiring.py"]
SELECT = "gui/select_cell.py"
# Moved since 2026-10-05 (re-pointed 2026-10-08): own_instance_context lives in
# the prune module, the cells: menu block in the entity-tree mixin.
PRUNE = "kicadstamp/absent_copper_prune.py"
ENTITY_TREE = "gui/docks/entity_tree.py"
EDITOR = rig.EDITOR
REFRESH = rig.REFRESH
NARROWING = rig.NARROWING

ROWS = [
    ("C1 instance refs lost (anchor: copper)", PRUNE,
     "    refs = frozenset(\n"
     "        (own_refs.values() if isinstance(own_refs, Mapping) else own_refs)\n"
     "        if own_refs is not None",
     "    refs = frozenset() if True else frozenset(  # MUTATION\n"
     "        (own_refs.values() if isinstance(own_refs, Mapping) else own_refs)\n"
     "        if own_refs is not None",
     "die", G, ()),
    ("C2 DockHub delegate dropped", "gui/dock_hub.py",
     "        self.cells_dock.select_cell_requested(name, file_path, cluster, sheet)",
     "        pass  # MUTATION",
     "die", G, ()),
    ("C3 tree menu item emits nothing", ENTITY_TREE,
     "            lambda: self.cell_select_requested.emit(\n"
     "                old_name, file_path, None, None))",
     "            lambda: None)  # MUTATION",
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
     "                        c=entity.get(\"cluster\"), s=entity.get(\"sheet\"):\n"
     "                        self.cell_select_requested.emit(n, None, c, s))",
     "                        c=None, s=None:  # MUTATION\n"
     "                        self.cell_select_requested.emit(n, None, c, s))",
     "die", G, ()),
    ("C8 explicit instance ignored", SELECT,
     "    if cluster is not None:\n        return cluster, sheet",
     "    if False:  # MUTATION\n        return cluster, sheet",
     "die", G, ()),
    ("C9 one-record fallback off", SELECT,
     "    if len(instances) == 1:\n        return InstanceChoice(\"single\"",
     "    if False:  # MUTATION\n        return InstanceChoice(\"single\"",
     "die", G, ()),
    # ── Н5б (acceptance of 4e21c2a) ─────────────────────────────────────────
    ("C10 cell file not resolved", EDITOR,
     "a second copy).\"\"\"\n"
     "        if self.name_edit.text().strip() != name:\n"
     "            target = file_path\n"
     "            if target is None:\n"
     "                target = find_dict_entry_file(self._root_path, \"cells\", name)",
     "a second copy).\"\"\"\n"
     "        if self.name_edit.text().strip() != name:\n"
     "            target = file_path\n"
     "            if target is None:\n"
     "                target = None  # MUTATION",
     "die", G, ()),
    ("C11 same cell reloaded (edits lost)", EDITOR,
     "a second copy).\"\"\"\n"
     "        if self.name_edit.text().strip() != name:",
     "a second copy).\"\"\"\n"
     "        if True:  # MUTATION",
     "die", G, ()),
    ("C12 entity door sends its own file again", "gui/docks/config_tree.py",
     "                        self.cell_select_requested.emit(n, None, c, s))",
     "                        self.cell_select_requested.emit(n, file_path, c, s))  # MUTATION",
     "die", G, ()),
    ("K6 cosmetic comment (control)", SELECT,
     "    return InstanceChoice(\"choose\", candidates=tuple(instances))\n",
     "    return InstanceChoice(\"choose\", candidates=tuple(instances))  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
