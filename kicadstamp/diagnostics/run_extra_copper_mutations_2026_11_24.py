# kicadstamp/diagnostics/run_extra_copper_mutations_2026_11_24.py
"""Mutation rows for «extra copper as its own record» —
plan_2026_11_10_extra_copper_by_selection (п.1–п.9).

The contract the rows attack: a `net_traces:` record carries an OPTIONAL owner
marker (`category: extra`); the extra door writes ONLY the pieces the classifier
calls STUB, with the same builder the re-read uses; every other piece is refused
by name; a repeat refreshes the same record; and the inter-node re-read neither
matches an extra record nor calls it «no longer on the board».

  M1  the category is never written on the record
  M2  the category is not read from the file (a save would drop it)
  M3  the inter-node re-read matches extra records again (and calls them missing)
  M4  the re-read takes a piece that an extra record already owns
  M5  the INTERNODE refusal is gone
  M6  the «that copper is the cell's» refusal is gone
  M7  a FOREIGN pad is accepted
  M8  an UNMOORED piece is accepted
  M9  a repeat writes a NEW record instead of refreshing the old one
  M10 a tree instance loses the category on the copy
  M11 the «Extra copper» row lists the inter-node records too
  M12 the door always acts on the active tab
  M13 the shadow demands EQUALITY of pads again (a dead end has one pad — Н1)
  M14 the legacy net match runs BEFORE the shadow (Н1, the order of the checks)
  M15 an empty selection reads the whole board again (Н2)
  M16 the dock's Save strips the category again (Е2)
  M17 the repeat key ignores the net (Е3)
  M18 two new dead ends of one run get one name (no `existing_names.append`, Е4)
  M19 a new name may take one the config already holds (no `free_node_ref`, Е5)
  K   cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS; the control must survive; every
file is restored in `finally` and verified by text; every row carries its own
`-k`, so `-x` cannot stop the run on a neighbour cell.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_extra_copper_mutations_2026_11_24`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXTRA = "kicadstamp/extra_copper.py"
CATEGORY = "kicadstamp/net_trace_category.py"
ENTRIES = "kicadstamp/config/entries.py"
CAPTURE = "kicadstamp/internode_capture.py"
INSTANCES = "kicadstamp/config/tree_instances.py"
GROUPS = "gui/docks/trees_copper_groups.py"
DOOR = "gui/read_extra_copper.py"

NET_CELLS = "tests/net/test_extra_copper.py"
GUI_CELLS = "tests/gui/test_read_extra_copper.py"

GUARDS = f"{NET_CELLS} {GUI_CELLS}"


def _only(expr: str, files: str = NET_CELLS) -> str:
    """A per-row guard: ONE file and ONE cell expression (see the module docstring)."""
    return f"{files} -k {expr}"


MUTATIONS = [
    ("M1 the category is never written", EXTRA,
     "        record = dataclasses.replace(record, category=CATEGORY_EXTRA)\n",
     "        record = dataclasses.replace(record, category=None)   # MUTATION\n",
     ["test_a_a_dead_end_becomes_one_extra_record"],
     _only("dead_end_becomes_one_extra_record")),

    ("M2 the category is not read from the file", ENTRIES,
     "        category=data.get('category'),\n",
     "        category=None,                            # MUTATION\n",
     ["test_k_a_tree_instance_copies_the_category_and_its_own_sheet"],
     _only("tree_instance_copies_the_category")),

    ("M3 the inter-node re-read matches extra records", CAPTURE,
     "    tree_records = inter_node_records(cfg.net_traces, set(identities))\n",
     "    tree_records = [nt for nt in cfg.net_traces                # MUTATION\n"
     "                    if net_trace_effective_name(nt) in set(identities)]\n",
     ["test_zh_the_inter_node_reread_never_touches_an_extra_record"],
     _only("reread_never_touches_an_extra_record")),

    ("M4 the re-read takes a piece an extra record owns", CAPTURE,
     "    extra_shadow = extra_signatures(cfg.net_traces, set(identities))\n",
     "    extra_shadow = {}                              # MUTATION\n",
     ["test_z_a_bridge_over_an_extra_dead_end_is_not_taken"],
     _only("bridge_over_an_extra_dead_end")),

    ("M5 the INTERNODE refusal is gone", EXTRA,
     "        if verdict is CopperVerdict.INTERNODE:\n",
     "        if False:                                  # MUTATION\n",
     ["test_b_an_inter_node_piece_is_refused_not_taken"],
     _only("inter_node_piece_is_refused_not_taken")),

    ("M6 the cell-owned refusal is gone", EXTRA,
     "        if _cell_owned(classification.cell_copper, unit):\n",
     "        if False:                                  # MUTATION\n",
     ["test_v_a_cell_owned_piece_is_refused_with_the_cell_name"],
     _only("cell_owned_piece_is_refused")),

    ("M7 a FOREIGN pad is accepted", EXTRA,
     "        if verdict is CopperVerdict.FOREIGN:\n",
     "        if False:                                  # MUTATION\n",
     ["test_g_a_foreign_pad_and_an_unmoored_piece_are_two_refusals"],
     _only("foreign_pad_and_an_unmoored")),

    ("M8 an UNMOORED piece is accepted", EXTRA,
     "        if verdict is CopperVerdict.UNMOORED:\n",
     "        if False:                                  # MUTATION\n",
     ["test_g_a_foreign_pad_and_an_unmoored_piece_are_two_refusals"],
     _only("foreign_pad_and_an_unmoored")),

    ("M9 a repeat writes a new record", EXTRA,
     "        if signature and is_extra(nt):\n",
     "        if False:                                  # MUTATION\n",
     ["test_e_a_repeat_keeps_the_name_and_the_uuid"],
     _only("repeat_keeps_the_name_and_the_uuid")),

    ("M10 a tree instance loses the category", INSTANCES,
     "        gen_nt['anchor_sheet'] = sheet\n",
     "        gen_nt['anchor_sheet'] = sheet\n"
     "        gen_nt.pop('category', None)               # MUTATION\n",
     ["test_k_a_tree_instance_copies_the_category_and_its_own_sheet"],
     _only("tree_instance_copies_the_category")),

    ("M11 the «Extra copper» row lists everything", GROUPS,
     "    plain = [n for n in nodes if getattr(n, \"ref\", None) not in extra]\n",
     "    plain = list(nodes)                              # MUTATION\n",
     ["test_l_the_group_rows_split_extra_from_inter_node_copper"],
     _only("group_rows_split_extra")),

    ("M12 the door always acts on the active tab", DOOR,
     "    tree = _guarded_tree(dock, tree)\n",
     "    tree = _guarded_tree(dock)                       # MUTATION\n",
     ["test_m_the_door_acts_on_the_tree_it_was_handed"],
     _only("acts_on_the_tree_it_was_handed", GUI_CELLS)),

    # ── Н1/Н2 and the acceptance's own survivors (Е2–Е5) ───────────────────
    ("M13 the shadow demands equality of pads again", CATEGORY,
     "        if signature_ <= frozenset(signature):\n",
     "        if signature_ == frozenset(signature):         # MUTATION\n",
     ["test_z_a_bridge_over_an_extra_dead_end_is_not_taken"],
     _only("bridge_over_an_extra_dead_end")),

    ("M14 the legacy net match runs before the shadow", CAPTURE,
     "        shadow = shadow_of(extra_shadow, signature, unit.net_name) if old is None else None\n"
     "        if shadow is not None:\n",
     "        if old is None:\n"
     "            old = legacy_by_net.pop(unit.net_name, None)   # MUTATION: legacy first\n"
     "        shadow = shadow_of(extra_shadow, signature, unit.net_name) if old is None else None\n"
     "        if shadow is not None:\n",
     ["test_z2_a_legacy_record_does_not_swallow_the_shadowed_piece"],
     _only("legacy_record_does_not_swallow")),

    ("M15 an empty selection reads the whole board", EXTRA,
     "    if not narrowed or not items:\n",
     "    if not items:                                  # MUTATION\n",
     ["test_n2_an_empty_selection_reads_nothing_and_says_so"],
     _only("empty_selection_reads_nothing", GUI_CELLS)),

    ("M16 the dock's Save strips the category", "gui/docks/net_trace.py",
     "            if \"category\" in saved:\n",
     "            if False:                                  # MUTATION\n",
     ["test_save_keeps_the_extra_category"],
     _only("save_keeps_the_extra_category", "tests/gui/docks/test_net_trace_dock.py")),

    ("M17 the repeat key ignores the net", EXTRA,
     "        old = by_key.pop((signature, unit.net_name), None)\n",
     "        old = next((by_key.pop(k) for k in list(by_key)   # MUTATION\n"
     "                    if k[0] == signature), None)\n",
     ["test_e3_the_same_pad_on_another_net_is_a_new_record"],
     _only("same_pad_on_another_net")),

    ("M18 two new dead ends of one run get one name", EXTRA,
     "            existing_names.append(identity)\n",
     "            pass                                       # MUTATION\n",
     ["test_e4_two_dead_ends_of_one_run_get_two_names"],
     _only("two_dead_ends_of_one_run")),

    ("M19 a new name may take a local node's", EXTRA,
     "        if old is None and record.name:\n",
     "        if False:                                  # MUTATION\n",
     ["test_e5_a_name_a_local_node_holds_is_suffixed"],
     _only("name_a_local_node_holds")),

    ("K cosmetic control", CATEGORY,
     "def is_extra(record) -> bool:\n",
     "# a comment changes nothing\ndef is_extra(record) -> bool:\n",
     []),
]


def _run(guards: str = GUARDS) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               KICAD_API_SOCKET="ipc:///tmp/claude-nonexistent.sock")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *guards.split(), "-x", "-q",
         "--no-header", "-p", "no:randomly"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    red = 0
    misses = []
    for row in MUTATIONS:
        name, rel, old, new, expected = row[:5]
        guards = row[5] if len(row) > 5 else GUARDS
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        hits = text.count(old)
        if hits != 1:
            print(f"[SKIP/MISS] {name}: matches {hits} times — template must match exactly once")
            misses.append(name)
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            code, out = _run(guards)
        finally:
            path.write_text(text, encoding="utf-8")
            restored = path.read_text(encoding="utf-8") == text
        if not restored:
            print(f"[FAIL] {name}: the file was NOT restored")
            return 2
        if expected:
            failed = [cell for cell in expected if f"FAILED {cell}" not in out
                      and f"::{cell}" not in out]
            if failed or code == 0:
                print(f"[MISS] {name}: nothing went red ({failed or 'whole run green'})")
                misses.append(name)
            else:
                red += 1
                print(f"[red] {name}")
        else:
            if code == 0:
                print(f"[control survives] {name}")
            else:
                print(f"[MISS] {name}: the cosmetic control reddened the run")
                misses.append(name)
    print(f"\nverdict: {red} as expected, {len(misses)} miss(es)")
    for miss in misses:
        print(f"  - {miss}")
    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
