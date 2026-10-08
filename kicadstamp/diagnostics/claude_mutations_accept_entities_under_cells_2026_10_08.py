# kicadstamp/diagnostics/claude_mutations_accept_entities_under_cells_2026_10_08.py
"""Claude's own acceptance rows for part 1 of plan_2026_10_05_entities_under_cells
(9e8aee41..aa7e775a), 2026-10-08.

The Demon's rig (M1-M11, K1) covers the plan's list. These rows mutate the
"who places an entityless cell" side (3б): a cell placed WITHOUT an entity must
keep today's behaviour — the live FPGA_PWR_BANK spokes depend on it. If the
index stops seeing a placer, the cell turns "unused" and loses its menu.

  * E1 a placed cell is treated as unused (menu cut, read-only)
  * E2 chain spokes are not seen as placers
  * E3 nested CellPlacements are not seen as placers
  * E4 top-level clone_placements are not seen as placers
  * K2 cosmetic comment -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_entities_under_cells_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_entities_under_cells_2026_10_08 as ds

ET, IDX = ds.ET, ds.IDX
G = ds.INDEX + ds.TREE_CFG

ROWS = [
    ("E1 a placed cell is treated as unused", ET,
     "        placed = index.placed_by_cell(uuid)\n        if placed:",
     "        placed = index.placed_by_cell(uuid)\n        if False:  # MUTATION",
     "die", G, ()),
    ("E2 chain spokes are not placers", IDX,
     '                if isinstance(spoke, dict) and spoke.get("cell") is not None:',
     "                if False:  # MUTATION",
     "die", G, ()),
    ("E3 nested CellPlacements are not placers", IDX,
     '            for nested in rec.get("clone_placements") or []:',
     "            for nested in []:  # MUTATION",
     "die", G, ()),
    ("E4 top-level clone_placements are not placers", IDX,
     '        for cp in (sections.get("clone_placements") or []):',
     "        for cp in []:  # MUTATION",
     "die", G, ()),
    ("K2 cosmetic comment (control)", IDX,
     "    entities_by_cell: dict = defaultdict(list)\n",
     "    entities_by_cell: dict = defaultdict(list)  # control\n",
     "survive", G, ()),
]

if __name__ == "__main__":
    ds.rig.MUTATIONS = ROWS
    ds.rig.main()
