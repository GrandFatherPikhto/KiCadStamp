# kicadstamp/diagnostics/deepseek_mutations_refresh_by_cluster_2026_10_05.py
"""Demon's own acceptance rows for Н4а (the Н4а rework of
plan_2026_10_04_refresh_mixed_cluster_selection: the cells the first acceptance
could not see — N4, N5, N7, N8, N11, N18–N20, N24 — and the findings Ф1–Ф3).

Run on the shared machinery of deepseek_mutations_refresh_mixed_2026_10_05.py
(count == 1 or НЕДЕЙСТВИТЕЛЬНА, _drop_pyc, PYTHONDONTWRITEBYTECODE, -n auto,
ПРОМАХ on zero reds, a control that MUST survive). The newly added guards live in
tests/selection/test_refresh_by_cluster_h4a.py; they are IN the guard list below.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_refresh_by_cluster_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = rig.GUARDS + ["test_cell_editor.py", "test_refresh_by_cluster_h4a.py"]
REFRESH = rig.REFRESH
EDITOR = rig.EDITOR
NARROWING = rig.NARROWING
MIXED = "gui/mixed_selection.py"

ROWS = [
    # ── N4/N5: instance choice + components from the BOARD ──────────────────
    ("N4 remembered sheet never used", MIXED,
     "        if not (remembered_cluster and remembered_sheet):",
     "        if True:  # MUTATION",
     "die", G, ()),
    ("N5 components from the selection", MIXED,
     "    instance_fps = resolve_context_footprints(\n"
     "        adapter, board_footprints, chosen_cluster, chosen_sheet, sheet_names)",
     "    instance_fps = [m.item for m in choice.members] or resolve_context_footprints(  # MUTATION\n"
     "        adapter, board_footprints, chosen_cluster, chosen_sheet, sheet_names)",
     "die", G, ()),
    # ── N7/N8: the new record's angle and layer ─────────────────────────────
    ("N7 new role keeps the BOARD angle", REFRESH,
     "            record.update(_component_new_geo(fp, frame))",
     "            record.update(_component_new_geo(fp, frame)); record[\"angle_deg\"] = fp.angle_deg + 1.0  # MUTATION",
     "die", G, ()),
    ("N8 layer written always", REFRESH,
     "            if cell_layer is not None and layer_to_str(fp.layer) != cell_layer:",
     "            if True:  # MUTATION",
     "die", G, ()),
    # ── N11: only the REMOVED role's copper goes ────────────────────────────
    ("N11 other roles' tracks removed too", REFRESH,
     "            if r.get(\"net_from_role\") in missing_roles and id(r) not in seen_t:",
     "            if id(r) not in seen_t:  # MUTATION",
     "die", G, ()),
    # ── N18–N20: net_traces outside the registry ────────────────────────────
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
    # ── N24: import reconciles components ───────────────────────────────────
    ("N24 import reconciles components", EDITOR,
     "                cell_layer=payload.get(\"cell_layer\"),\n"
     "                reconcile_components=prelude is not None)",
     "                cell_layer=payload.get(\"cell_layer\"),\n"
     "                reconcile_components=False)  # MUTATION",
     "die", G, ()),
    # ── findings Ф1–Ф3 ──────────────────────────────────────────────────────
    ("Ф1 failed board read deletes", NARROWING,
     "    if not getattr(ctx, \"board_read_ok\", True):",
     "    if False:  # MUTATION",
     "die", G, ()),
    ("Ф2 removed role references not reported", REFRESH,
     "            refs = referencing_records_for_roles(config, missing_roles)",
     "            refs = {}  # MUTATION",
     "die", G, ()),
    ("Ф3 reason never reaches the notes", NARROWING,
     "        if getattr(live, \"reason\", None):",
     "        if False:  # MUTATION",
     "die", G, ()),
    ("K4 cosmetic comment (control)", REFRESH,
     "    match_vias = ([r for r in vias if r.get(\"net_from_role\") not in missing_roles]",
     "    match_vias = ([r for r in vias if r.get(\"net_from_role\") not in missing_roles]  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
