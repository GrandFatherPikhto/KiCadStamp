# kicadstamp/diagnostics/deepseek_mutations_entities_under_cells_2026_10_08.py
"""Acceptance mutations for part 1 of plan_2026_10_05_entities_under_cells.md
(entities as children of their cell/imprint), DeepSeek, 2026-10-08.

Grown from deepseek_mutations_select_enclosed_copper_2026_10_05.py, which grew
from deepseek_mutations_refresh_mixed_2026_10_05.py (rule 38): the SAME
machinery — basename-resolved tests under tests/, `_drop_pyc`, the
`original.count(old) != 1` refusal (a non-unique template is a miss, not a
kill), ПРОМАХ on zero reds, a control that MUST survive.

WHAT IS BEING PROVEN. The guards are:
  * tests/gui/docks/test_entity_index.py      — the pure raw-graph index;
  * tests/gui/docks/test_entities_under_cells.py — the tree shape, the own-file
    routing (п.4), the orphan menu / "Point to …" (3а) and the read-only
    entityless cell (3б);
  * tests/gui/docks/test_config_tree.py       — the orphan leaf's own file.

Every row maps to a line of the plan's "Строки мутаций" list, plus the file
block's own row (Denis's fork of п.4). The tree plumbing lives in
gui/docks/entity_tree.py (the mixin) since 2026-10-08 — rows M1/M5/M6/M7/M8/M10
target that module; M9a/M11 stay in the giant.

NOTE on M6: the format-3 writer stamp RESOLVES a new reference by full name, so
it would fill a forgotten uuid anyway. The guard for "Point writes the uuid"
therefore runs with `format3_stamp_disabled()` — it pins the uuid the TREE
writes, not the one the writer would rescue.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_entities_under_cells_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

INDEX = ["test_entity_index.py"]
TREE = ["test_entities_under_cells.py"]
TREE_CFG = ["test_entities_under_cells.py", "test_config_tree.py"]

CFG = "gui/docks/config_tree.py"
ET = "gui/docks/entity_tree.py"
IDX = "gui/docks/entity_index.py"
VIEW = "gui/docks/cell_anchor_view.py"

ROWS = [
    # 1 — the entity file is taken from the visible ancestor, not the record (п.4).
    ("M1 entity file from the ancestor again", ET,
     "        own = item.data(0, _ROLE_OWN_FILE)\n"
     "        if own is not None:\n"
     "            return own",
     "        own = item.data(0, _ROLE_OWN_FILE)\n"
     "        if False:  # MUTATION\n"
     "            return own",
     "die", TREE, ()),
    # 2 — the link is by the name hint instead of the UUID.
    ("M2 link by the name hint, not the uuid", IDX,
     "        if ref.cell_uuid and ref.cell_uuid in cells_by_uuid:",
     "        if ref.data.get(\"cell\") in cells_by_name:  # MUTATION",
     "die", INDEX, ()),
    # 3 — the second entity of one cell is not shown.
    ("M3 second entity of one cell hidden", IDX,
     "            entities_by_cell[ref.cell_uuid].append(ref)",
     "            if not entities_by_cell[ref.cell_uuid]:  # MUTATION\n"
     "                entities_by_cell[ref.cell_uuid].append(ref)",
     "die", INDEX + TREE, ()),
    # 4 — the imprint's entity is not shown.
    ("M4 imprint entity hidden", IDX,
     "        elif ref.imprint_uuid and ref.imprint_uuid in imprints_by_uuid:",
     "        elif False:  # MUTATION",
     "die", INDEX + TREE, ()),
    # 5 — an orphan disappears from the tree completely.
    ("M5 orphan disappears from the tree", ET,
     "        orphans = [ref for ref in index.orphans if ref.file_path == node.path]",
     "        orphans = []  # MUTATION",
     "die", TREE_CFG, ()),
    # 6 — "Point to cell…" writes the name but no cell_uuid (format 3 dangling).
    ("M6 Point writes the name without the uuid", ET,
     "        updated[field + \"_uuid\"] = index.target_uuid(section, chosen)",
     "        updated[field + \"_uuid\"] = None  # MUTATION",
     "die", TREE, ()),
    # 7 — the orphan hint is own code, not the loader's close_name_hint.
    ("M7 orphan hint is own code", ET,
     "        return _(\"refers to a missing {kind} {name!r} ({uuid})\").format(\n"
     "            kind=kind, name=name, uuid=data.get(field + \"_uuid\")) + \\\n"
     "            close_name_hint(name, index.names_for(section))",
     "        return _(\"refers to a missing {kind} {name!r} ({uuid})\").format(\n"
     "            kind=kind, name=name, uuid=data.get(field + \"_uuid\"))  # MUTATION",
     "die", TREE, ()),
    # 8 — a cell WITH an entity is marked too (the marker is unconditional).
    ("M8 cell WITH an entity is marked too", ET,
     "        if index.has_entity_for_cell(uuid):\n            return",
     "        if False:  # MUTATION\n            return",
     "die", TREE, ()),
    # 9а — an unused, entityless cell keeps the full editable menu.
    ("M9a unused cell keeps the full menu", CFG,
     "                if item.data(0, _ROLE_CELL_MARK) == _CELL_UNUSED:",
     "                if False:  # MUTATION",
     "die", TREE, ()),
    # 9б — the read-only page is not applied (fields stay editable).
    ("M9b read-only page not applied", VIEW,
     "        self._tabs.setEnabled(not self._read_only)",
     "        self._tabs.setEnabled(True)  # MUTATION",
     "die", TREE, ()),
    # 10 — the "Entities" section is shown even with no orphans.
    ("M10 Entities section shown without orphans", ET,
     "        if not orphans:\n            return",
     "        if not orphans:\n            pass  # MUTATION",
     "die", TREE, ()),
    # 11 — the file block takes the visible ancestor's file again.
    ("M11 file block takes the ancestor file", CFG,
     "        file_differs = file_path != self._nearest_file_path(item)",
     "        file_differs = False  # MUTATION",
     "die", TREE, ()),
    # control — a cosmetic comment MUST survive.
    ("K1 cosmetic comment (control)", IDX,
     "    stack = [root]",
     "    stack = [root]  # control",
     "survive", TREE, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
