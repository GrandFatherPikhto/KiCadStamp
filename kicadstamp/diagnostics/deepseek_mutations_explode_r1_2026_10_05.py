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

G = ["test_explode_plan.py", "test_explode_journal.py"]
EXPLODE = "kicadstamp/explode.py"
JOURNAL = "kicadstamp/explode_journal.py"

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
    ("X10 the pad's layers are ignored", EXPLODE,
     "    if through or pad_layers is None:\n"
     "        return True\n"
     "    return item_layer in pad_layers",
     "    return True  # MUTATION",
     "die", G, ()),
    ("X11 tracks of different layers are connected", EXPLODE,
     "                track_same.setdefault((it.layer, *gkey(point)), []).append(i)",
     "                track_same.setdefault(gkey(point), []).append(i)  # MUTATION",
     "die", G, ()),
    ("X12 a T-piece is classified as cell", EXPLODE,
     "    foreign = {c for c in classes if c != \"cell\"}\n"
     "    if \"cell\" in classes:\n"
     "        return \"tee\"          # cell pads on both sides AND a foreign pad",
     "    foreign = {c for c in classes if c != \"cell\"}\n"
     "    if \"cell\" in classes:\n"
     "        return \"cell\"  # MUTATION",
     "die", G, ()),
    ("X13 an unticked table piece travels anyway", EXPLODE,
     "        if piece.ticked:\n            if piece.touches in vector_by_label:",
     "        if True:  # MUTATION\n            if piece.touches in vector_by_label:",
     "die", G, ()),
    ("X14 the vector does not clear the area", EXPLODE,
     "        t = max(0.0, min(ts)) if ts else 0.0",
     "        t = 0.0  # MUTATION",
     "die", G, ()),
    ("X15 a cell piece is ticked by default", EXPLODE,
     "            ticked = touches not in _UNTOUCHED",
     "            ticked = True  # MUTATION",
     "die", G, ()),
    ("K1 cosmetic comment (control)", EXPLODE,
     "def plan_explode(adapter, cfg, config_path: str, cell_name: str, cluster: str,",
     "def plan_explode(adapter, cfg, config_path: str, cell_name: str, cluster: str,  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
