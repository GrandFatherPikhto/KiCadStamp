# kicadstamp/diagnostics/deepseek_mutations_explode_r1_2026_10_05.py
"""Acceptance mutations for plan_2026_10_05_explode_r1_core.md (§2/§3), DeepSeek,
2026-10-05.

Machinery (rule 38) is the shared one from
``deepseek_mutations_refresh_mixed_2026_10_05.py``: basename-resolved guards under
tests/, ``_drop_pyc`` for the mutated file, the ``count != 1`` refusal, a
``ПРОМАХ`` when nothing red came back, and a control that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/explode/test_explode_plan.py (the
read-only plan: instances, registry ownership, the net_traces table, the pad
LAYER rule, the vectors) and tests/explode/test_explode_journal.py (the journal
BEFORE the transaction, one transaction, the 0 nm check, restore by absolute
positions). Every row maps to a line of the plan's own mutation list.

  * X1  restore leaves the items where they are (not the recorded poses)
  * X2  the journal is written AFTER the transaction
  * X3  the journal lives under profiles/
  * X4  a second explode with a live journal is allowed
  * X5  the journal is removed even when the restore is incomplete
  * X6  the post-check ignores non-moved copper
  * X7  only the overlapping part of an instance leaves (not the whole one)
  * X8  the CELL's own copper leaves too
  * X9  another instance of the same foreign cell is treated as this one
  * X10 the pad's LAYERS are ignored (an SMD F.Cu pad connects a B.Cu track)
  * X11 tracks on DIFFERENT layers are connected
  * X12 a T-piece is classified as "cell" (no tick, no warning)
  * X13 an UNticked table piece travels anyway
  * X14 the vector does not take the frame out of the area
  * X15 a "cell" table piece is ticked by default
  * K1  a cosmetic comment — MUST survive

Run with the main checkout's interpreter; an optional row-name prefix filter
takes the rest of argv.
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_explode_plan.py", "test_explode_journal.py",
     "test_copper_connect.py", "test_pad_area.py"]
EXPLODE = "kicadstamp/explode.py"
JOURNAL = "kicadstamp/explode_journal.py"
GEOM = "kicadstamp/geometry/copper_connect.py"
CONN = "kicadstamp/explode_connectivity.py"

ROWS = [
    ("X1 restore leaves the items in place", JOURNAL,
     "        pairs.append((item, Pose.from_dict(rec[\"before\"])))",
     "        pairs.append((item, Pose.of(item)))  # MUTATION",
     "die", G, ()),
    ("X2 journal written after the transaction", JOURNAL,
     "    _write_atomic(path, journal)\n"
     "    try:\n"
     "        _commit(adapter, pairs, _(\"KiCadStamp: explode clusters\"))\n"
     "    except Exception:\n"
     "        try:\n"
     "            path.unlink()\n"
     "        except OSError:\n"
     "            pass\n"
     "        raise",
     "    try:\n"
     "        _commit(adapter, pairs, _(\"KiCadStamp: explode clusters\"))\n"
     "    except Exception:\n"
     "        try:\n"
     "            path.unlink()\n"
     "        except OSError:\n"
     "            pass\n"
     "        raise\n"
     "    _write_atomic(path, journal)  # MUTATION",
     "die", G, ()),
    ("X3 journal lives under profiles/", JOURNAL,
     "    base = os.environ.get(\"XDG_STATE_HOME\") or str(Path.home() / \".local\" / \"state\")\n"
     "    return Path(base) / \"kicadstamp\" / \"explode\"",
     "    base = os.environ.get(\"XDG_STATE_HOME\") or str(Path.home() / \".local\" / \"state\")\n"
     "    return Path(\"profiles\") / \"kicadstamp\" / \"explode\"  # MUTATION",
     "die", G, ()),
    ("X4 second explode with a live journal allowed", JOURNAL,
     "    existing = load_journal(path)\n    if existing is not None:",
     "    existing = load_journal(path)\n    if False:  # MUTATION",
     "die", G, ()),
    ("X5 journal removed on an incomplete restore", JOURNAL,
     "    if not gone and worst == 0:",
     "    if True:  # MUTATION",
     "die", G, ()),
    ("X6 post-check ignores non-moved copper", JOURNAL,
     "    if copper_before is not None:\n        for uuid, pose in copper_before.items():",
     "    if False:  # MUTATION\n        for uuid, pose in copper_before.items():",
     "die", G, ()),
    ("X7 only the overlapping part leaves the instance", EXPLODE,
     "        moving_groups.append((key, fps, _union_boxes(boxes)))",
     "        moving_groups.append((key, [f for f, b in zip(fps, boxes)  # MUTATION\n"
     "                                    if b is not None and _boxes_overlap(b, area)],\n"
     "                              _union_boxes(boxes)))",
     "die", G, ()),
    ("X8 the cell's own copper leaves too", EXPLODE,
     "        inst_key = _foreign_owner(key_str)\n"
     "        if inst_key is not None:\n"
     "            moving_copper[inst_key].append(item)",
     "        inst_key = _foreign_owner(key_str) or next(iter(moving_copper), None)  # MUTATION\n"
     "        if inst_key is not None:\n"
     "            moving_copper[inst_key].append(item)",
     "die", G, ()),
    ("X9 another instance of the same foreign cell", EXPLODE,
     "        for (inst_key, refs, pairs) in foreign_own:\n"
     "            for identity, addresses in pairs:\n"
     "                if is_own_key(key_str, identity, addresses, inst_key, refs):\n"
     "                    return inst_key",
     "        for (inst_key, refs, pairs) in foreign_own:\n"
     "            for identity, addresses in pairs:\n"
     "                if identity and identity in key_str:  # MUTATION\n"
     "                    return inst_key",
     "die", G, ()),
    ("X10 the pad's layers are ignored", CONN,
     "                if layers is not None and it.layer not in layers:\n"
     "                    continue",
     "                if False:  # MUTATION\n                    continue",
     "die", G, ()),
    ("X11 tracks of different layers are connected", CONN,
     "    if a.layer != b.layer:\n        return False",
     "    if False:  # MUTATION\n        return False",
     "die", G, ()),
    ("X12 a T-piece is classified as cell", CONN,
     "    if len(cell) >= 2:\n        return \"tee\"",
     "    if len(cell) >= 2:\n        return \"cell\"  # MUTATION",
     "die", G, ()),
    ("X13 an unticked table piece travels anyway", EXPLODE,
     "        if piece.ticked:\n            if piece.touches in vector_by_label:",
     "        if True:  # MUTATION\n            if piece.touches in vector_by_label:",
     "die", G, ()),
    ("X14 the vector does not clear the area", EXPLODE,
     "        t = max(0.0, min(ts)) if ts else 0.0",
     "        t = 0.0  # MUTATION",
     "die", G, ()),
    ("X15 a ticked cell piece does not leave", EXPLODE,
     "            else:  # \"cell\"/\"none\": its own ray from its own frame (tick = leaves)\n"
     "                box = _box_map(adapter, [piece.item]).get(piece.uuid)\n"
     "                if box is not None:\n"
     "                    ux, uy = _ray(area_center, _box_center(box))\n"
     "                    vector = _offset_to_leave(area, box, ux, uy, gap_nm)",
     "            else:  # MUTATION\n                pass",
     "die", G, ()),
    ("X20 a via as an exact point (5 um missed)", GEOM,
     "    return point_segment_distance(px, py, ax, ay, bx, by) <= r + w / 2.0",
     "    return point_segment_distance(px, py, ax, ay, bx, by) <= 0.0  # MUTATION",
     "die", G, ()),
    ("X21 T-junction not caught (starts only)", GEOM,
     "    return segment_segment_distance(ax, ay, bx, by, cx, cy, dx, dy) \\\n"
     "        <= (w_ab + w_cd) / 2.0",
     "    return math.hypot(ax - cx, ay - cy) <= (w_ab + w_cd) / 2.0  # MUTATION",
     "die", G, ()),
    ("X22 width ignored (parallel gap merged)", GEOM,
     "    return segment_segment_distance(ax, ay, bx, by, cx, cy, dx, dy) \\\n"
     "        <= (w_ab + w_cd) / 2.0",
     "    return segment_segment_distance(ax, ay, bx, by, cx, cy, dx, dy) <= 1_000_000_000  # MUTATION",
     "die", G, ()),
    ("X23 net_traces filter by sheet only", EXPLODE,
     "    for kind, entries in ((\"vias\", via_entries), (\"tracks\", track_entries)):",
     "    return False  # MUTATION\n"
     "    for kind, entries in ((\"vias\", via_entries), (\"tracks\", track_entries)):",
     "die", G, ()),
    ("X16 tee at ONE cell pad", CONN,
     "    if len(cell) >= 2:\n        return \"tee\"",
     "    if len(cell) >= 1:  # MUTATION\n        return \"tee\"",
     "die", G, ()),
    ("X17 tee without a warning", EXPLODE,
     "        if piece.touches == \"tee\":\n"
     "            warnings.append(_(\n"
     "                \"T-branch: the cell's inner part ({pads}) leaves with the \"\n"
     "                \"foreign cluster\").format(\n"
     "                    pads=_cell_pads_text(classes, piece.item)))",
     "        if piece.touches == \"tee\":\n"
     "            pass  # MUTATION",
     "die", G, ()),
    ("X18 multi merged into tee", CONN,
     "    if len(foreign) >= 2:\n        return \"multi\"",
     "    if len(foreign) >= 2:  # MUTATION\n        return \"tee\"",
     "die", G, ()),
    ("X19 record identity instead of cell identity", EXPLODE,
     "        if any(_address_is(addr, inst_key) for addr in addresses.values()):\n"
     "            out.append((cell_identity(cell_name, cell), addresses))",
     "        if any(_address_is(addr, inst_key) for addr in addresses.values()):\n"
     "            out.append((next(iter(addresses)), addresses))  # MUTATION",
     "die", G, ()),
    ("K1 cosmetic comment (control)", EXPLODE,
     "def plan_explode(adapter, cfg, config_path: str, cell_name: str, cluster: str,",
     "def plan_explode(adapter, cfg, config_path: str, cell_name: str, cluster: str,  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
