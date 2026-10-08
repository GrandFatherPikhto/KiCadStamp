# kicadstamp/diagnostics/deepseek_mutations_tree_node_ref_2026_10_08.py
"""Acceptance mutations for "Apply в форме узла не обновляет строку дерева после
смены Ref" (techdocs/handoff/deepseek/plan/plan_2026_10_08_tree_node_ref_apply.md),
DeepSeek, 2026-10-08.

Grown from deepseek_mutations_entities_part3a_2026_10_08.py (rule 38), whose
machinery is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards:
basename-resolved tests under tests/, `_drop_pyc`, the `original.count(old) != 1`
refusal (a non-unique template is a MISS, not a kill), ПРОМАХ when nothing turns
red, and a control that MUST survive.

WHAT IS BEING PROVEN (the row update in gui/docks/trees_node_row.py and its ONE
call site in NodeFormWidget.apply()):
  * M1 Apply does not touch the row (the call site is removed)
  * M2 the `_node_items` key is not translated (the old key is left behind)
  * M3 the row text is computed by hand, not through the render's `_node_item_text`
  * K1 a cosmetic comment -> MUST survive

and the §п.3 rules of the SAME session (the node's `ref_uuid`, the tree-level
cascade, the loader probe — plan_2026_10_08_tree_node_ref_apply, "Решение по п.3"):
  * M4 the new ref's uuid is not resolved at all
  * M5 `copy_node_onto` does not carry `ref_uuid` with `ref`
  * M6 the refusal for a ref naming no record is skipped
  * M7 the tree's pivot-ref does not follow the renamed node
  * M8 the tree's (self) anchor does not follow it
  * M9 a stale `ref_uuid` survives a switch to a record-free kind
  * M10 the LOADER probe is removed
  * M11 the probe walks each tree with its OWN `seen_refs`
  * M12 the form's "used refs" set is left stale after an Apply
  * M13 the kind -> section table is not consulted (every kind resolves as
        "placement")
  * K2 a cosmetic comment in the new module -> MUST survive

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_tree_node_ref_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

ROW_TEST = ["test_trees_dock_node_ref_row.py"]
OWNER_TEST = ["test_form_identity.py"]
DOCK = "gui/docks/trees_dock.py"
ROW = "gui/docks/trees_node_row.py"
IDENTITY = "kicadstamp/config/form_identity.py"

MUTATIONS = [
    # 1 — Apply goes back to only marking the dock dirty: the row keeps the old
    # text and the old registry key (the reported defect, п.2 of the plan).
    ("M1 Apply stops updating the row", ROW,
     "        form._dock._mark_dirty()\n"
     "        # The row is a SEPARATE object keyed by the node's ref — move its\n"
     "        # registry key to the new ref and repaint it through the render's own\n"
     "        # routine (no _rebuild_tabs: it would tear this form down).\n"
     "        refresh_node_row(form._dock, form._tree, form._existing, old_ref)\n",
     "        form._dock._mark_dirty()\n",
     "die", ROW_TEST, ()),
    # 2 — the SAME item is left registered under the OLD ref too: the new key is
    # added, but the stale old key survives (10 readers of _node_items).
    ("M2 the old _node_items key is left behind", ROW,
     "    item = items.pop(old_ref, None)\n",
     "    item = items.get(old_ref)  # MUTATION: old key stays\n",
     "die", ROW_TEST, ()),
    # 3 — the row text is a SECOND calculation (bare ref), not the render's own
    # `_node_item_text`: the kind tag on a Kind edit never appears.
    ("M3 row text is not _node_item_text", ROW,
     "    if tree is not None:\n"
     "        # The render's own text/marks routine — the row is repainted from the\n"
     "        # node the form just committed, never by a second calculation here.\n"
     "        refresh = getattr(dock, \"_refresh_tree_marks\", None)\n"
     "        if refresh is not None:\n"
     "            refresh(tree)\n",
     "    if tree is not None:\n"
     "        item.setText(0, node.ref)  # MUTATION: no kind tag\n",
     "die", ROW_TEST, ()),
    # K1 — a cosmetic comment changes nothing: MUST survive.
    ("K1 a cosmetic comment", ROW,
     "It lives OUTSIDE trees_dock.py on purpose: that file is a giant (7955 lines) and",
     "It lives OUTSIDE trees_dock.py on purpose: that file is a GIANT (7955 lines) and",
     "survive", ROW_TEST, ()),
    # ── the §п.3 rules ─────────────────────────────────────────────────────
    # 4 — the new ref never gets its record's uuid: the writer stamp puts the OLD
    # name back and the next load rolls the edit back (the whole defect).
    ("M4 the ref_uuid is not resolved", ROW,
     "            built.ref_uuid = node_ref_uuid(cfg, built.kind, built.ref)\n",
     "            built.ref_uuid = None  # MUTATION\n",
     "die", ROW_TEST, ()),
    # 5 — the resolved uuid is not copied onto the node (the copy routine drops it).
    ("M5 copy_node_onto drops ref_uuid", ROW,
     "    target.ref_uuid = built.ref_uuid\n"
     "    target.kind = built.kind\n",
     "    target.kind = built.kind\n",
     "die", ROW_TEST, ()),
    # 6 — a ref naming NO record is applied anyway: the config is written and the
    # next open refuses it (the node keeps the name that has no record).
    ("M6 the no-record refusal is skipped", ROW,
     "    if cfg is not None:\n"
     "        try:\n"
     "            built.ref_uuid = node_ref_uuid(cfg, built.kind, built.ref)\n"
     "        except ValidationError as exc:\n"
     "            return _refusal_text(exc, node)\n",
     "    if cfg is not None:\n"
     "        try:\n"
     "            built.ref_uuid = node_ref_uuid(cfg, built.kind, built.ref)\n"
     "        except ValidationError:\n"
     "            pass  # MUTATION: the refusal is skipped\n",
     "die", ROW_TEST, ()),
    # 7 — the tree's pivot-ref keeps the OLD ref: the rename is either refused by
    # the probe or the row loses its (handle) mark.
    ("M7 the pivot-ref does not follow", ROW,
     "    if tree.pivot_ref is not None and tree.pivot_ref == old_ref:\n"
     "        tree.pivot_ref = new_ref\n",
     "    if tree.pivot_ref is not None and tree.pivot_ref == old_ref:\n"
     "        pass  # MUTATION: the pivot-ref is not moved\n",
     "die", ROW_TEST, ()),
    # 8 — the same for the tree's (self) anchor.
    ("M8 the self anchor does not follow", ROW,
     "    if anchor is not None and anchor.self_ref == old_ref:\n"
     "        anchor.self_ref = new_ref\n",
     "    if anchor is not None and anchor.self_ref == old_ref:\n"
     "        pass  # MUTATION: the self anchor is not moved\n",
     "die", ROW_TEST, ()),
    # 9 — a stale ref_uuid survives a switch to a record-free kind: the file is
    # written with a uuid beside a LOCAL kind and the next load is a fatal.
    ("M9 a stale ref_uuid survives", ROW,
     "    target.ref_uuid = built.ref_uuid\n",
     "    target.ref_uuid = built.ref_uuid or target.ref_uuid  # MUTATION\n",
     "die", ROW_TEST, ()),
    # 10 — the loader probe is removed: an edit the next open refuses is written
    # (the probe tree is still built, nothing validates it).
    ("M10 the loader probe is removed", ROW,
     "            tree_from_dict(data, seen)\n",
     "            pass  # MUTATION: no loader probe\n",
     "die", ROW_TEST, ()),
    # 11 — the probe walks each tree with a FRESH seen_refs: a ref another tree
    # already carries slips through (the refusal the loader would raise).
    ("M11 the seen_refs is not shared", ROW,
     "            tree_from_dict(data, seen)\n",
     "            tree_from_dict(data, set())  # MUTATION: per-tree seen\n",
     "die", ROW_TEST, ()),
    # 12 — the form's "used refs" set is left stale: an edit that freed a ref is
    # refused (the modal is not what the cell expects).
    ("M12 the used-refs set is stale", ROW,
     "        # This form's \"used refs\" set was captured when it OPENED: the edit just\n"
     "        # made one ref free and another taken, so the uniqueness check (and the\n"
     "        # \"(used)\" hints) must not keep judging by the old set.\n"
     "        form._used_refs = form._dock._used_refs()\n",
     "        pass  # MUTATION: the set is left stale\n",
     "die", ROW_TEST, ()),
    # 13 — the kind -> section table is not consulted: every kind resolves against
    # the entities, so every OTHER section's cells must fail.
    ("M13 the kind table is bypassed", IDENTITY,
     "    section = _F3_NODE_KIND_TARGET.get(kind)\n",
     "    section = _F3_NODE_KIND_TARGET.get(\"placement\")  # MUTATION\n",
     "die", OWNER_TEST, ()),
    # K2 — a cosmetic comment in the new module: MUST survive.
    ("K2 a cosmetic comment in the new module", ROW,
     "never a second copy of either",
     "NEVER a second copy of either",
     "survive", ROW_TEST, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
