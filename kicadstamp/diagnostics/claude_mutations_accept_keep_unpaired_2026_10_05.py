# kicadstamp/diagnostics/claude_mutations_accept_keep_unpaired_2026_10_05.py
"""Claude's own acceptance rows for bf17359 (soft keep_unpaired in a mixed
"Update from selection"), 2026-10-05.

Grown from deepseek_mutations_refresh_mixed_2026_10_05.py: the same machinery
(count == 1 or НЕДЕЙСТВИТЕЛЬНА, _drop_pyc, PYTHONDONTWRITEBYTECODE, -n auto,
ПРОМАХ on zero reds, a control that MUST survive) — only the rows are mine, at
the edges the Demon's M12/M15/M16/M17 do not cover:

  * C1 keep_unpaired clears only the via list     -> an unpaired TRACK deleted
  * C2 the named report never reaches the Log     -> Log line lost
  * C3 the report names vias only                 -> unpaired track unnamed
  * C4 remove_missing alone becomes a fatal again -> clean deletion lost
  * C5 both flags: remove_missing wins            -> keep-wins cell
  * C6 a record named by kind only (no net)       -> "named, not counted" lost
  * K2 a cosmetic comment                         -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_keep_unpaired_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = rig.GUARDS
REFRESH = rig.REFRESH
EDITOR = rig.EDITOR

ROWS = [
    ("C1 keep clears only the via list", REFRESH,
     "        via_removed = []\n        track_removed = []",
     "        via_removed = []  # MUTATION",
     "die", G, ()),
    ("C2 named report never reaches the Log", EDITOR,
     "            for line in plan.unpaired_reports:\n"
     "                selection_lines.append((line, _SELECTION_WARN))",
     "            for line in plan.unpaired_reports:\n"
     "                pass  # MUTATION",
     "die", G, ()),
    ("C3 the report names vias only", REFRESH,
     "               + [_record_label(rec, \"track\") for rec in track_records])",
     "               + [])  # MUTATION",
     "die", G, ()),
    ("C4 remove_missing alone fatal again", REFRESH,
     "    missing_is_fatal = not remove_missing and not keep_unpaired",
     "    missing_is_fatal = not keep_unpaired  # MUTATION",
     "die", G, ()),
    ("C5 both flags: remove_missing wins", REFRESH,
     "    if keep_unpaired:\n        unpaired_reports = _unpaired_kept_report(",
     "    if keep_unpaired and not remove_missing:  # MUTATION\n"
     "        unpaired_reports = _unpaired_kept_report(",
     "die", G, ()),
    ("C6 record named by kind only", REFRESH,
     "    return _(\"{kind} on {net}\").format(kind=kind, net=net)",
     "    return kind  # MUTATION",
     "die", G, ()),
    ("K2 cosmetic comment (control)", REFRESH,
     "    unpaired_reports: list[str] = []\n    if keep_unpaired:",
     "    unpaired_reports: list[str] = []  # control\n    if keep_unpaired:",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
