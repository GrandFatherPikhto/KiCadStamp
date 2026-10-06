# kicadstamp/diagnostics/claude_mutations_accept_subtract_s2a_2026_10_06.py
"""Claude's own acceptance rows for С-2а (6a3dbd24..8bbd18ca), 2026-10-06.

Reuses the machinery of deepseek_mutations_subtract_selection_2026_10_06.py
(count == 1 or НЕДЕЙСТВИТЕЛЬНА, _drop_pyc, PYTHONDONTWRITEBYTECODE, -n auto,
ПРОМАХ on zero reds); only the rows are mine, at edges the Demon's M1-M10 skip:

  * D1 the cell's own via list is no longer dropped (component level only)
  * D2 the track list is no longer dropped
  * D3 no autostage after the drop
  * D4 none matching: the FIRST run is taken even when it planned nothing
  * D5 two matching instances taken as one (ambiguity lost)
  * D6 the removed-record lines are lost (only the count is printed)
  * K2 a cosmetic comment -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_subtract_s2a_2026_10_06.py
"""
from kicadstamp.diagnostics import deepseek_mutations_subtract_selection_2026_10_06 as rig

W = rig.WORKER
G = rig.G

ROWS = [
    ("D1 cell via list not dropped", W,
     "        dock._drop_records(vias, dock._vias)\n",
     "        pass  # MUTATION\n",
     "die", G, ()),
    ("D2 track list not dropped", W,
     "        dock._drop_records(tracks, dock._tracks)\n",
     "        pass  # MUTATION\n",
     "die", G, ()),
    ("D3 no autostage after the drop", W,
     "        dock._refresh_all_tables()\n        dock._autostage()",
     "        dock._refresh_all_tables()  # MUTATION",
     "die", G, ()),
    ("D4 none matching takes the first run", W,
     "                label = next(m[0] for m in maps if not m[3].empty)",
     "                label = maps[0][0]  # MUTATION",
     "die", G, ()),
    ("D5 two matching taken as one", W,
     "            if len(labels) > 1:",
     "            if len(labels) > 2:  # MUTATION",
     "die", G, ()),
    ("D6 removed-record lines lost", W,
     "        lines += [(record_report_line(\"-\", record, kind), \"warn\")\n"
     "                  for kind, record in removed]\n",
     "",
     "die", G, ()),
    ("K2 cosmetic comment (control)", W,
     "        dock = self._dock\n        vias = [r for kind, r in removed if kind == \"via\"]",
     "        dock = self._dock  # control\n        vias = [r for kind, r in removed if kind == \"via\"]",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.rig.MUTATIONS = ROWS
    rig.rig.main()
