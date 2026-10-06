# kicadstamp/diagnostics/claude_mutations_accept_refresh_by_cluster_2026_10_05.py
"""Claude's acceptance rows for cbc8bdc (Н4 of
plan_2026_10_04_refresh_mixed_cluster_selection: read a cell instance by its
cluster, components from the board, copper by the live-UUID rule), 2026-10-05.

The Demon shipped Н4 without a mutation rig, so these rows are the plan's own
"Строки мутаций" list, run on deepseek_mutations_refresh_mixed_2026_10_05.py's
machinery (count == 1 or НЕДЕЙСТВИТЕЛЬНА, _drop_pyc, PYTHONDONTWRITEBYTECODE,
-n auto, ПРОМАХ on zero reds, a control that MUST survive).

    .venv/bin/python kicadstamp/diagnostics/claude_mutations_accept_refresh_by_cluster_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = rig.GUARDS + ["test_cell_editor.py", "test_refresh_by_cluster_h4a.py"]
REFRESH = rig.REFRESH
EDITOR = rig.EDITOR
NARROWING = rig.NARROWING
MIXED = "gui/mixed_selection.py"

ROWS = [
    # ── Н4.1 instance choice ────────────────────────────────────────────────
    ("N1 exact role set required again", NARROWING,
     "    if len(own) == 1:\n        key, members = own[0]",
     "    if len(own) == 1 and _qualifies(own[0][0], own[0][1], roles):  # MUTATION\n"
     "        key, members = own[0]",
     "die", G, ()),
    ("N2 two sheets: first one taken", NARROWING,
     "        return InstanceChoice(chosen_key=None, candidate_groups=tuple(own),\n"
     "                              others=tuple(others))",
     "        return _pick([own[0]], others)  # MUTATION",
     "die", G, ()),
    ("N3 one sheet, many labels: roles ignored", NARROWING,
     "    role_matches = [(key, members) for key, members in own\n"
     "                    if _qualifies(key, members, roles)]",
     "    role_matches = list(own)  # MUTATION",
     "die", G, ()),
    ("N4 remembered sheet never used", MIXED,
     "        if not (remembered_cluster and remembered_sheet):",
     "        if True:  # MUTATION",
     "die", G, ()),
    # ── Н4.2/3 components from the board ────────────────────────────────────
    ("N5 components from the selection", MIXED,
     "    instance_fps = resolve_context_footprints(\n"
     "        adapter, board_footprints, chosen_cluster, chosen_sheet, sheet_names)",
     "    instance_fps = [m.item for m in choice.members] or resolve_context_footprints(  # MUTATION\n"
     "        adapter, board_footprints, chosen_cluster, chosen_sheet, sheet_names)",
     "die", G, ()),
    ("N6 new role not added", REFRESH,
     "            new_component_records.append(record)",
     "            pass  # MUTATION",
     "die", G, ()),
    ("N7 new role keeps the BOARD angle", REFRESH,
     "            record.update(_component_new_geo(fp, frame))",
     "            record.update(_component_new_geo(fp, frame)); record[\"angle_deg\"] = fp.angle_deg + 1.0  # MUTATION",
     "die", G, ()),
    ("N8 layer written always", REFRESH,
     "            if cell_layer is not None and layer_to_str(fp.layer) != cell_layer:",
     "            if True:  # MUTATION",
     "die", G, ()),
    ("N9 missing role not removed", REFRESH,
     "            removed_component_records = [c for c in components\n"
     "                                         if c.get(\"role\") in missing_roles]",
     "            removed_component_records = []  # MUTATION",
     "die", G, ()),
    ("N10 missing role's copper stays", REFRESH,
     "    if reconcile_components and missing_roles:\n        seen_v = {id(r) for r in via_removed}",
     "    if False:  # MUTATION\n        seen_v = {id(r) for r in via_removed}",
     "die", G, ()),
    ("N11 other roles' tracks removed too", REFRESH,
     "            if r.get(\"net_from_role\") in missing_roles and id(r) not in seen_t:",
     "            if id(r) not in seen_t:  # MUTATION",
     "die", G, ()),
    ("N12 duplicate role passes silently", REFRESH,
     "        if role in role_to_ref:\n            problems.append(",
     "        if role in role_to_ref and False:  # MUTATION\n            problems.append(",
     "die", G, ()),
    # ── Н4 п.5а net_traces outside the registry ────────────────────────────
    ("N18 unregistered net_traces tracks kept", MIXED,
     "        list(sub_t.kept), getattr(cfg, \"net_traces\", None), adapter,",
     "        list(sub_t.kept), None, adapter,  # MUTATION",
     "die", G, ()),
    ("N19 unregistered net_traces vias kept", MIXED,
     "        list(sub_v.kept), getattr(cfg, \"net_traces\", None), adapter,",
     "        list(sub_v.kept), None, adapter,  # MUTATION",
     "die", G, ()),
    ("N20 whole net of a net_trace subtracted", NARROWING,
     "        label = foreign.get(getattr(item, \"uuid\", None))",
     "        label = foreign.get(getattr(item, \"uuid\", None)) or next((str(getattr(n, \"net\", \"\")) for n in (net_traces or ()) if str(getattr(n, \"net\", \"\")) == str(getattr(item, \"net_name\", None))), None)  # MUTATION",
     "die", G, ()),
    # ── Н4.7 no dialogs ─────────────────────────────────────────────────────
    ("N21 refresh refusal not shown", EDITOR,
     "            # Н4.7 (Denis 2026-10-05): no dialogs — every problem is a red Log\n"
     "            # line, the working dialog is never covered by an \"OK\" window.\n"
     "            for line in _problem_lines(result[\"error\"]):\n"
     "                self._show_message(line, _ERROR_STYLE)",
     "            pass  # MUTATION",
     "die", G, ()),
    ("N22 refresh refusal back in a dialog", EDITOR,
     "            # Н4.7 (Denis 2026-10-05): no dialogs — every problem is a red Log\n"
     "            # line, the working dialog is never covered by an \"OK\" window.\n"
     "            for line in _problem_lines(result[\"error\"]):\n"
     "                self._show_message(line, _ERROR_STYLE)",
     "            QMessageBox.warning(self, \"x\", result[\"error\"])  # MUTATION",
     "die", G, ()),
    ("N23 import refusal back in a dialog", EDITOR,
     "            # Н4.7: the same red-Log refusals as the refresh path.\n"
     "            for line in _problem_lines(result[\"error\"]):\n"
     "                self._show_message(line, _ERROR_STYLE)",
     "            QMessageBox.warning(self, \"x\", result[\"error\"])  # MUTATION",
     "die", G, ()),
    ("N24 import reconciles components", EDITOR,
     "                cell_layer=payload.get(\"cell_layer\"),\n"
     "                reconcile_components=prelude is not None)",
     "                cell_layer=payload.get(\"cell_layer\"),\n"
     "                reconcile_components=False)  # MUTATION",
     "die", G, ()),
    # ── Н4а findings Ф1/Ф3 ──────────────────────────────────────────────────
    ("Ф3 reason never reaches the notes", NARROWING,
     "        if getattr(live, \"reason\", None):",
     "        if False:  # MUTATION",
     "die", G, ()),
    ("K3 cosmetic comment (control)", NARROWING,
     "    kept: list[Any] = []",
     "    kept: list[Any] = []  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
