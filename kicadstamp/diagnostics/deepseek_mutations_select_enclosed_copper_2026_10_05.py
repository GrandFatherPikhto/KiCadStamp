# kicadstamp/diagnostics/deepseek_mutations_select_enclosed_copper_2026_10_05.py
"""Demon's acceptance rows for «Select enclosed copper»
(plan_2026_10_05_select_enclosed_copper.md; Denis 2026-10-05; С1 rework).

Guards: tests/explode/test_enclosed_copper.py (the core rule + the С1-2 trimming)
and tests/gui/test_select_enclosed_copper.py (menu / worker / door), added to the
shared machinery's list below. Run on
deepseek_mutations_refresh_mixed_2026_10_05.py's machinery (count == 1 or
НЕДЕЙСТВИТЕЛЬНА, `_drop_pyc`, PYTHONDONTWRITEBYTECODE, `-n auto`, ПРОМАХ on zero
reds, a control that MUST survive) — grown from it, not written fresh (rule 38).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_select_enclosed_copper_2026_10_05.py

E1–E8 are the original plan's list; E9–E13 are С1's own list (the threshold, the
trimming and its touch primitives).
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
    # E3 ("a piece with no pads is taken") is GONE with С2-1, not dropped
    # silently: the candidate gate («no instance pad -> continue», E14) now runs
    # BEFORE the min-pads check, so a 0-pad piece can no longer reach the taking
    # branch through one line. Its property is pinned by E14 plus the
    # "not_taken_* == 0" asserts of test_a_piece_touching_no_instance_pad_is_not_a_candidate.
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
    ("E5 another instance of the same cell counted as own", CORE,
     "        if foreign_labels(piece.classes):",
     "        if False:  # MUTATION",
     "die", G, ()),
    ("E6 own touch by exact point coincidence (5 um cell)", CONN,
     "    return area.segment_touches(item.start, item.end, margin=half)",
     "    return area.segment_touches(item.start, item.end, margin=0.0)  # MUTATION",
     "die", G, ()),
    ("E7 a foreign pad on a T-branch is ignored", CORE,
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
    ("K1 cosmetic comment (control)", CORE,
     "MIN_INSTANCE_PADS = 2",
     "MIN_INSTANCE_PADS = 2  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
