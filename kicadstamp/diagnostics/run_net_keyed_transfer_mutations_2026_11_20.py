# kicadstamp/diagnostics/run_net_keyed_transfer_mutations_2026_11_20.py
"""Mutation rows for Т1 of plan_2026_11_20_cell_read_takes_inter_node_copper
(Денис, 20.11): a selected piece the REGISTRY already knows as a ``net_traces``
piece (a ``net:`` key) is TRANSFERRED to the cell, never subtracted — the live
case «subtracted from selection: … net:b166cc84-…: 4».

The contract the rows attack, piece by piece:

  M1 the `net:` key means "subtract" again (the extraction is off)
  M2 the kind is not checked against the live item (Denis's guard, т3г)
  M3 the key's index is not checked against the record's own list (т3б, first half)
  M4 the piece's net is not compared with the record's piece net (т3б, second half)
  M5 the keyed transfer needs the explode door (Р1 says EVERY read)
  M6 the boundary Р2 is not asked (a record between other nodes hands copper over)
  M7 only the ADDRESS half of the boundary counts (the refs half is dropped)
  K  cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than once
is REFUSED; a row that reddens nothing is a MISS; the control must survive; every
file is restored in `finally` and verified by text. NO `-x`: a row names EVERY
cell it must redden, so the run has to reach all of them.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_net_keyed_transfer_mutations_2026_11_20`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PLANNER = "kicadstamp/net_trace_planner.py"
MIXED = "gui/mixed_selection.py"

GUARDS = "tests/selection/test_cell_read_takes_inter_node_copper.py"

T1 = "test_t1_keyed_pieces_of_the_record_go_to_the_cell"
T1B = "test_t1b_the_key_alone_needs_no_explode_door"
T3 = "test_t3_the_index_comes_from_the_key_when_the_walk_finds_nothing"
T3B = "test_t3b_a_shortened_record_or_another_net_subtracts_with_a_line"
T3V = "test_t3v_a_via_and_a_track_of_one_record_both_go_to_their_own_section"
T3G = "test_t3g_a_kind_mismatch_subtracts_with_a_line"
T4 = "test_t4_a_record_between_other_nodes_is_subtracted_and_named"
T4B = "test_t4b_a_pieces_far_end_on_this_instance_makes_the_record_touch_it"

MUTATIONS = [
    ("M1 the `net:` key means subtract again", MIXED,
     "        if key and key.startswith(NET_TRACE_KEY_PREFIX) and key not in orphans:\n"
     "            keyed.append((item, kind, kinds.get(getattr(item, \"uuid\", None)), key))\n"
     "            continue\n",
     "        if False:  # MUTATION: `net:` is foreign again\n"
     "            keyed.append((item, kind, kinds.get(getattr(item, \"uuid\", None)), key))\n"
     "            continue\n",
     [T1, T1B, T3, T3V]),

    ("M2 the kind is not checked against the live item", MIXED,
     "        if registry_kind != run_kind:\n",
     "        if False:  # MUTATION: the kind is trusted blindly\n",
     [T3G]),

    ("M3 the key's index is not checked against the record", PLANNER,
     "        if index >= len(getattr(nt, section, ()) or ()):\n"
     "            continue\n",
     "        if False:  # MUTATION: a shortened record still \"has\" the piece\n"
     "            continue\n",
     [T3B]),

    ("M4 the piece's net is not compared with the record's", MIXED,
     "        if piece_net and live_net and piece_net != live_net:\n",
     "        if False:  # MUTATION: any net will do\n",
     [T3B]),

    ("M5 the keyed transfer needs the explode door", MIXED,
     "    keyed_transfers, keyed_kept, keyed_removed, keyed_lines = _keyed_decisions(\n"
     "        net_keyed, net_traces, adapter, sheet_names, touches=touches)\n",
     "    keyed_transfers, keyed_kept, keyed_removed, keyed_lines = (\n"
     "        _keyed_decisions(net_keyed, net_traces, adapter, sheet_names,\n"
     "                         touches=touches)\n"
     "        if transfer_ok else ((), [], {}, []))  # MUTATION: only under explode\n",
     [T1B]),

    ("M6 the boundary is not asked (Р2 off)", MIXED,
     "        if touches is not None and not touches(net_trace_effective_name(nt)):\n",
     "        if False:  # MUTATION: any record may hand its copper over\n",
     [T4]),

    ("M7 the refs half of the boundary is not asked", MIXED,
     "    refs = net_trace_refs(adapter, nt, sheet_names)\n"
     "    return bool(refs & frozenset(chosen_refs or ()))\n",
     "    refs = net_trace_refs(adapter, nt, sheet_names)\n"
     "    return False  # MUTATION: only the address counts\n",
     [T4B]),

    ("K cosmetic control", MIXED,
     "def _keyed_decisions(net_keyed, net_traces, adapter, sheet_names,\n"
     "                     touches=None) -> tuple:\n",
     "# a comment changes nothing\n"
     "def _keyed_decisions(net_keyed, net_traces, adapter, sheet_names,\n"
     "                     touches=None) -> tuple:\n",
     []),
]


def _run(guards: str = GUARDS) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               KICAD_API_SOCKET="ipc:///tmp/claude-nonexistent.sock")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *guards.split(), "-q", "--no-header"],
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
