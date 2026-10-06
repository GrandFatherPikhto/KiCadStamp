# kicadstamp/diagnostics/deepseek_mutations_celldock_anchor_role_clobber_2026_10_05.py
"""Demon's acceptance rows for the CellDock ``anchor_role`` clobber fix
(plan_2026_10_05_celldock_anchor_role_clobber.md; Claude, 2026-10-05).

Guards: tests/gui/test_cell_anchor_role_clobber.py. Run on
deepseek_mutations_refresh_mixed_2026_10_05.py's machinery (count == 1 or
НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, ПРОМАХ on zero
reds, a control that MUST survive) — grown from it, not written fresh (rule 38).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_celldock_anchor_role_clobber_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

EDITOR = "gui/docks/cell_editor.py"
GUARD = "gui/docks/cell_form_guard.py"
G = ["test_cell_anchor_role_clobber.py"]

ROWS = [
    ("A1 the anchor is selected before the roles are poured", EDITOR,
     "            self._refresh_role_choices()\n"
     "            self._loaded_anchor_role = entry.get(\"anchor_role\") or None\n"
     "            self._anchor_role_touched = False",
     "            self._loaded_anchor_role = entry.get(\"anchor_role\") or None\n"
     "            self._anchor_role_touched = False  # MUTATION",
     "die", G, ()),
    ("A2 an unknown role falls back to the first role", GUARD,
     "    return \"\", _(\"anchor_role {role!r} is not a role of this cell — kept; \"\n"
     "                 \"fix it in Cell anchor\").format(role=saved)",
     "    return (sorted(roles)[0] if roles else \"\"), None  # MUTATION",
     "die", G, ()),
    ("A3 the rewrite invariant is dropped (widget is truth)", GUARD,
     "    if not touched:\n        return (loaded_role or None)",
     "    if False:  # MUTATION\n        return (loaded_role or None)",
     "die", G, ()),
    ("A4 the user's pick is ignored", GUARD,
     "    return (widget_text or \"\").strip() or None",
     "    return (loaded_role or None)  # MUTATION",
     "die", G, ()),
    ("K1 cosmetic comment (control)", GUARD,
     "__all__ = [\"anchor_role_selection\", \"effective_anchor_role\"]",
     "__all__ = [\"anchor_role_selection\", \"effective_anchor_role\"]  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
