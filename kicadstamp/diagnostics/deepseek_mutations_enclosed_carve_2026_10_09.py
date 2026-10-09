# kicadstamp/diagnostics/deepseek_mutations_enclosed_carve_2026_10_09.py
"""Demon's acceptance rows for «Select enclosed copper» CARVE
(plan ``plan_2026_10_09_enclosed_copper_carve.md``; Denis 2026-10-09).

Grown from ``deepseek_mutations_select_enclosed_copper_2026_10_05.py`` (rule 38):
the SAME machinery (``deepseek_mutations_refresh_mixed_2026_10_05`` — basename-
resolved T under tests/, ``_drop_pyc``, the ``original.count(old) != 1`` refusal,
"ПРОМАХ" on zero reds, a control that MUST survive). Guards: the CORE rule
(``tests/explode/test_enclosed_copper.py``) and the GUI menu / worker / door
(``tests/gui/test_select_enclosed_copper.py``).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_enclosed_carve_2026_10_09.py

WHAT CHANGED SINCE THE 2026-10-05 RIG. The carve replaced "a foreign pad drops
the piece WHOLE" with "the foreign-reaching branch is cut and the remainder is
re-checked". So:

  * E5 was renamed (a foreign pad is not foreign at all — the carve never runs);
  * E7 was renamed (the carve is skipped for a piece that touches instance pads —
    it still passes as a plain take, which the ``carved`` assert catches);
  * E1–E4, E6, E8–E18 are carried as they were (E18's guard line is untouched
    because the plan's ``keep_whole_when_emptied`` flag was measured REDUNDANT
    and dropped — the re-check alone holds the property, so the flag had no
    killable row and no home in the code);
  * C1–C4 are the carve's own rows.

A C-row's docstring (in the cell it kills) names the mutation; this list is the
same mapping in the rig's own terms.
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_enclosed_copper.py", "test_select_enclosed_copper.py"]
CORE = "kicadstamp/enclosed_copper.py"
CONN = "kicadstamp/explode_connectivity.py"
GUI = "gui/select_enclosed_copper.py"

ROWS = [
    ("E1 only copper goes into the selection", CORE,
     "    result = EnclosedResult(items=list(instance_fps))",
     "    result = EnclosedResult()  # MUTATION",
     "die", G, ()),
    ("E2 the threshold is 1 again", CORE,
     "MIN_INSTANCE_PADS = 2",
     "MIN_INSTANCE_PADS = 1  # MUTATION",
     "die", G, ()),
    ("E4 a component without a Cluster is not foreign", CORE,
     "        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or None\n"
     "        if cluster:\n"
     "            label = str(cluster)\n"
     "        else:\n"
     "            label = f\"fp:{getattr(fp, 'ref', None) or getattr(fp, 'uuid', '?')}\"\n"
     "        out.setdefault(label, []).append(fp)",
     "        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or None\n"
     "        if not cluster:  # MUTATION\n"
     "            continue\n"
     "        out.setdefault(str(cluster), []).append(fp)",
     "die", G, ()),
    ("E5 a foreign pad is not foreign (carve never runs)", CORE,
     "        if foreign_labels(piece.classes):",
     "        if False:  # MUTATION",
     "die", G, ()),
    ("E6 own touch by exact point coincidence (5 um cell)", CONN,
     "    return area.segment_touches(item.start, item.end, margin=half)",
     "    return area.segment_touches(item.start, item.end, margin=0.0)  # MUTATION",
     "die", G, ()),
    ("E7 the carve is skipped (piece has instance pads)", CORE,
     "        if foreign_labels(piece.classes):",
     "        if foreign_labels(piece.classes) and not cell_pads(piece.classes):  # MUTATION",
     "die", G, ()),
    ("E8 the instance rule copied, not taken from the resolver", GUI,
     "        choice = resolve_action_instance(load_cfg(root), root, name, None, None, None)",
     "        from .select_cell import InstanceChoice, cell_instances  # MUTATION\n"
     "        choice = InstanceChoice(\"choose\",\n"
     "                                candidates=tuple(cell_instances(load_cfg(root), name)))",
     "die", G, ()),
    # ── С1-2: the trimming ──────────────────────────────────────────────────
    ("E9 a hanging track is not trimmed", CONN,
     "            if dangling:",
     "            if False:  # MUTATION",
     "die", G, ()),
    ("E10 the trim is one pass (no fixed point)", CONN,
     "    while changed:",
     "    if changed:  # MUTATION",
     "die", G, ()),
    ("E11 a middle via is trimmed", CONN,
     "    return neighbours <= 1",
     "    return neighbours <= 2  # MUTATION",
     "die", G, ()),
    ("E12 a T-junction end counts as dangling", CONN,
     "        elif other.layer == layer and _point_touches_item(point, half, other):",
     "        elif False:  # MUTATION",
     "die", G, ()),
    ("E13 exact coincidence for the endpoint pad (5 um cell)", CONN,
     "    return area.contains(point, margin=half)",
     "    return point.x == area.center.x and point.y == area.center.y  # MUTATION",
     "die", G, ()),
    # ── С2: the countered scope and the three touch primitives ───────────────
    ("E14 the counters are the whole board again", CORE,
     "        if not pads_touched:",
     "        if False:  # MUTATION",
     "die", G, ()),
    ("E15 a via inside an instance pad is dangling", CONN,
     "        if area.contains(via.position, margin=half):\n"
     "            return False",
     "        if False:  # MUTATION\n            return False",
     "die", G, ()),
    ("E16 an endpoint pad is touched without its layers", CONN,
     "    if layers is not None and layer not in layers:\n"
     "        return False",
     "    if False:  # MUTATION\n        return False",
     "die", G, ()),
    ("E17 an end touches a track of another layer", CONN,
     "        elif other.layer == layer and _point_touches_item(point, half, other):",
     "        elif _point_touches_item(point, half, other):  # MUTATION",
     "die", G, ()),
    ("E18 the empty-piece guard is removed", CONN,
     "    if items and not alive:\n        return list(items), 0",
     "    if False:  # MUTATION\n        return list(items), 0",
     "die", G, ()),
    # ── the CARVE (plan_2026_10_09_enclosed_copper_carve) ───────────────────
    ("C1 the carve is off (the old whole-drop rule)", CORE,
     "        if foreign_labels(piece.classes):\n"
     "            # CARVE: the branches that reach the foreign pad are cut off as",
     "        if foreign_labels(piece.classes):\n"
     "            result.not_taken_foreign += 1  # MUTATION: drop whole\n"
     "            continue\n"
     "            # CARVE: the branches that reach the foreign pad are cut off as",
     "die", G, ()),
    ("C2 the re-check is off (A — F — B slips through)", CORE,
     "    for sub in copper_pieces(tracks, vias, islands, foreign_pads):\n"
     "        if foreign_labels(sub.classes):\n"
     "            continue",
     "    for sub in copper_pieces(tracks, vias, islands, foreign_pads):\n"
     "        if False:  # MUTATION\n"
     "            continue",
     "die", G, ()),
    ("C3 the carved counter is not counted", CORE,
     "            result.carved += 1",
     "            result.carved += 0  # MUTATION",
     "die", G, ()),
    ("C4 the carve does not prune (foreign branch kept)", CORE,
     "            # foreign pad BETWEEN two instance pads (A — F — B).\n"
     "            kept, removed = prune_dangling(piece.items, instance_pads)",
     "            # foreign pad BETWEEN two instance pads (A — F — B).\n"
     "            kept, removed = list(piece.items), 0  # MUTATION: no carve",
     "die", G, ()),
    ("K1 cosmetic comment (control)", CORE,
     "MIN_INSTANCE_PADS = 2",
     "MIN_INSTANCE_PADS = 2  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
