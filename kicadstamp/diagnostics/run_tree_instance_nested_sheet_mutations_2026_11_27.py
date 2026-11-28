# kicadstamp/diagnostics/run_tree_instance_nested_sheet_mutations_2026_11_27.py
"""Mutation rows for «a tree instance keeps the nested sheet paths» —
plan_2026_11_27_tree_instances_nested_sheet_paths §9 (М01–М15).

The contract the rows attack: a `Ch_L` point template with ONE top-level
placement and SIX neighbouring `net_trace` records yields its own sheet from
that placement (Н1), and every copied address — `Entity.sheet`, a mount/component
`(role ... (sheet ...))` and `net_trace.anchor_sheet` — follows the instance
by SEGMENT PREFIX (`ChannelL/ArmP -> ChannelR/ArmP`), while an external address
(`Power`, `ChannelL2/ArmP`) stays external (Р1–Р3). The GUI doors use the SAME
expander and hand the generated arm's OWN address to the selection.

  М01 the source is refused again when anything but ONE top node exists (Н1)
  М02 the placement copy goes back to `ent['sheet'] = destination` (Н2)
  М03 the net_trace copy goes back to `anchor_sheet = destination` (Н3)
  М04 the mount branch substitutes only an EXACT match
  М05 the component branch substitutes only an EXACT match
  М06 the segment test becomes a raw `startswith(source)`
  М07 an outside role address is re-pointed at the destination
  М08 the suffix keeps only its LAST segment
  М09 two unrelated placement roots: the first one wins
  М10 the copy is made by mutating the SOURCE Entity (no deepcopy)
  М11 only the dialog's validation path uses the OLD source derivation
  М12 only the staged-refresh path flattens the copies' sheets
  М13 the entity door hands the flat sheet (`ChannelR`) to the selection
  М14 the dialog's accept reads `connection.board` on the UI thread
  М15 a declaration Root expands the WHOLE template instead of the subtree
  K   cosmetic control (a comment must change nothing)

Safeguards as in every harness here (rule 38): a template matching more than
once is REFUSED; a row that reddens nothing is a MISS; the control must survive;
every file is restored in `finally`, verified by text AND the guard set is
re-run GREEN after every restore (a red restore run fails the rig, never
«killed by a broken build»); every row carries its own `-k`, so `-x` cannot stop
the run on a neighbour cell. Each red row prints its file, replacement, pytest
nodeid, exit code and the assertion fragment that went red.

Run: `.venv/bin/python -m kicadstamp.diagnostics.run_tree_instance_nested_sheet_mutations_2026_11_27`
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

POLICY = "kicadstamp/config/sheet_instance_policy.py"
CHECKS = "kicadstamp/config/tree_instance_checks.py"
INSTANCES = "kicadstamp/config/tree_instances.py"
VALIDATION = "gui/docks/instances_validation.py"
SYNC = "gui/docks/trees_instances_sync.py"
DOORS = "gui/entity_doors.py"
DIALOG = "gui/docks/instances_dialog.py"

TREE_CELLS = "tests/trees/test_tree_instance_nested_sheet_paths.py"
GUI_CELLS = "tests/gui/docks/test_instances_nested_sheet_paths.py"

GUARDS = f"{TREE_CELLS} {GUI_CELLS}"


def _only(expr: str, files: str = TREE_CELLS) -> str:
    """A per-row guard: ONE file and ONE cell expression (see the docstring)."""
    return f"{files} -k {expr}"


MUTATIONS = [
    ("М01 the source needs exactly one top node", CHECKS,
     "    roots = [n for n in top if n.get('kind') in (None, 'placement')]\n",
     "    if len(top) != 1:                              # MUTATION: Н1 again\n"
     "        return None\n"
     "    roots = [n for n in top if n.get('kind') in (None, 'placement')]\n",
     ["test_the_whole_case_expands_without_a_root"],
     _only("the_whole_case_expands_without_a_root")),

    ("М02 the placement copy goes flat", INSTANCES,
     "    ent['sheet'] = entity_sheet(ent.get('sheet'), old_sheet, sheet,\n"
     "                                ref=orig_ref, instance_name=instance_name)\n",
     "    ent['sheet'] = sheet                           # MUTATION: flat\n",
     ["test_every_entity_keeps_its_place_in_the_channel"],
     _only("every_entity_keeps_its_place_in_the_channel")),

    ("М03 the net_trace copy goes flat", INSTANCES,
     "        gen_nt['anchor_sheet'] = trace_anchor_sheet(\n"
     "            gen_nt.get('anchor_sheet'), old_sheet, sheet,\n"
     "            identity=orig_ref, instance_name=instance_name)\n",
     "        gen_nt['anchor_sheet'] = sheet             # MUTATION: flat\n",
     ["test_the_six_anchors_keep_their_arm"],
     _only("six_anchors_keep_their_arm")),

    ("М04 the mount substitutes only an EXACT match", INSTANCES,
     "        anchor['sheet'] = role_address(anchor_sheet, old_sheet, sheet,\n"
     "                                       ref=orig_ref, instance_name=instance_name)\n",
     "        anchor['sheet'] = (sheet if anchor_sheet == old_sheet   # MUTATION\n"
     "                           else anchor_sheet)\n",
     ["test_nested_role_addresses_follow_the_channel"],
     _only("nested_role_addresses_follow_the_channel")),

    ("М05 the component substitutes only an EXACT match", INSTANCES,
     "        anchor['sheet'] = role_address(anchor_sheet, old_sheet, sheet,\n"
     "                                       ref=orig_ref, kind=\"component\",\n"
     "                                       instance_name=instance_name)\n",
     "        anchor['sheet'] = (sheet if anchor_sheet == old_sheet   # MUTATION\n"
     "                           else anchor_sheet)\n",
     ["test_nested_role_addresses_follow_the_channel"],
     _only("nested_role_addresses_follow_the_channel")),

    ("М06 the segment test becomes a text prefix", POLICY,
     "    wanted = _parts(source)\n"
     "    parsed = _parts(address)\n"
     "    return len(parsed) >= len(wanted) and parsed[:len(wanted)] == wanted\n",
     "    return str(address).startswith(str(source))     # MUTATION: text prefix\n",
     ["test_external_and_similar_prefix_addresses_stay_external"],
     _only("external_and_similar_prefix")),

    ("М07 an outside role address is re-pointed", POLICY,
     "    rebased = rebase_sheet_path(address, source, target)\n"
     "    if rebased is not None:\n"
     "        return rebased\n"
     "    if kind == \"component\":\n",
     "    rebased = rebase_sheet_path(address, source, target)\n"
     "    if rebased is not None:\n"
     "        return rebased\n"
     "    return target                                  # MUTATION: outside -> target\n"
     "    if kind == \"component\":\n",
     ["test_external_and_similar_prefix_addresses_stay_external"],
     _only("external_and_similar_prefix")),

    ("М08 the suffix keeps only its last segment", POLICY,
     "    suffix = _parts(address)[len(_parts(source)):]\n",
     "    suffix = _parts(address)[-1:]                  # MUTATION: last only\n",
     ["test_multisegment_source_and_target_keep_deeper_suffix"],
     _only("multisegment_source_and_target")),

    ("М09 the first of two placement roots wins", CHECKS,
     "    if len(roots) != 1:\n"
     "        return None\n"
     "    if any(n.get('kind') not in ('net_trace', 'copper')\n"
     "           for n in top if n is not roots[0]):\n"
     "        return None\n"
     "    ref = roots[0].get('ref')\n"
     "    entity = entities_by_name.get(ref) if ref is not None else None\n"
     "    source = entity.get('sheet') if isinstance(entity, dict) else None\n"
     "    if not source or source == ROOT_SHEET:\n"
     "        return None\n"
     "    sheets, without = template_entry_sheets(nodes, entities_by_name)\n"
     "    if without or not sheets:\n"
     "        return None\n"
     "    if not all(follows_source(address, source) for address in sheets):\n"
     "        return None\n",
     "    if not roots:                                  # MUTATION: first root wins\n"
     "        return None\n"
     "    ref = roots[0].get('ref')\n"
     "    entity = entities_by_name.get(ref) if ref is not None else None\n"
     "    source = entity.get('sheet') if isinstance(entity, dict) else None\n"
     "    if not source or source == ROOT_SHEET:\n"
     "        return None\n",
     ["test_two_unrelated_placement_roots_refuse_atomically"],
     _only("two_unrelated_placement_roots")),

    ("М10 the copy mutates the source Entity", INSTANCES,
     "    ent = copy.deepcopy(entity)\n"
     "    ent['name'] = new_ref\n",
     "    ent = entity                                   # MUTATION: no deepcopy\n"
     "    ent['name'] = new_ref\n",
     ["test_the_template_itself_is_not_rewritten_and_the_input_is_not_mutated"],
     _only("template_itself_is_not_rewritten")),

    ("М11 only the dialog's validation uses the old source", VALIDATION,
     "    data = resolved_graph(str(root_path))\n",
     "    data = resolved_graph(str(root_path))\n"
     "    for _e in data.get(\"entities\") or []:          # MUTATION: legacy flatten\n"
     "        _e.pop(\"sheet\", None)\n",
     ["test_instances_dialog_creates_and_reopens_nested_channel"],
     _only("creates_and_reopens_nested_channel", GUI_CELLS)),

    ("М12 only the staged refresh flattens the copies", SYNC,
     "    fresh = list(cfg.trees)\n"
     "    if not dock._dirty and not merge_always:\n",
     "    _targets = {ti.name: ti.sheet                       # MUTATION\n"
     "                for ti in (getattr(cfg, \"tree_instances\", None) or [])}\n"
     "    for _e in cfg.entities:\n"
     "        for _name, _sheet in _targets.items():\n"
     "            if (_e.name or \"\").endswith(f\"__{_name}\") and _e.sheet:\n"
     "                _e.sheet = _sheet\n"
     "    fresh = list(cfg.trees)\n"
     "    if not dock._dirty and not merge_always:\n",
     ["test_template_edit_refreshes_nested_instance_without_restart"],
     _only("template_edit_refreshes_nested", GUI_CELLS)),

    ("М13 the entity door hands the flat sheet", DOORS,
     "    hub.cells_dock.select_cell_components_requested(\n"
     "        name, file_path, cluster, sheet,\n"
     "        expected_address=door_address(hub, entity, cluster, sheet))\n",
     "    flat = (sheet or \"\").split(\"/\")[0]                # MUTATION\n"
     "    hub.cells_dock.select_cell_components_requested(\n"
     "        name, file_path, cluster, flat,\n"
     "        expected_address=door_address(hub, entity, cluster, flat))\n",
     ["test_generated_arm_component_selection_uses_its_own_address"],
     _only("generated_arm_component_selection", GUI_CELLS)),

    ("М14 the dialog's accept reads the board on the UI thread", DIALOG,
     "        rows = self.rows()\n"
     "        problem = self._validate(rows)\n",
     "        self.parent().connection.board.get_footprints()  # MUTATION: unsigned\n"
     "        rows = self.rows()\n"
     "        problem = self._validate(rows)\n",
     ["test_instances_ui_paths_do_not_read_board_on_ui_thread"],
     _only("do_not_read_board_on_ui_thread", GUI_CELLS)),

    ("М15 Root expands the whole template", INSTANCES,
     "        nodes = [placed]\n"
     "    gen = copy.deepcopy(template)\n",
     "    gen = copy.deepcopy(template)                  # MUTATION: whole template\n",
     ["test_explicit_root_still_copies_only_its_subtree"],
     _only("explicit_root_still_copies_only")),

    ("K cosmetic control", POLICY,
     "def follows_source(address, source) -> bool:\n",
     "# a comment changes nothing\ndef follows_source(address, source) -> bool:\n",
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


def _fragment(out: str) -> str:
    """The first line that shows WHY the run went red — the assertion/refusal a
    reviewer can compare with the row, never just «failed»."""
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(("E   ", "E   assert", "ValidationError")):
            return stripped[:160]
    for line in out.splitlines():
        if line.startswith("FAILED ") or "AssertionError" in line:
            return line.strip()[:160]
    return "(no assertion line found)"


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
            print(f"[SKIP/MISS] {name}: matches {hits} times — template must "
                  f"match exactly once")
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
        # The plan's rule: after EVERY restore the guard set must be green again.
        green_code, _green_out = _run(guards)
        if green_code != 0:
            print(f"[FAIL] {name}: the guard set is NOT green after restore "
                  f"(exit {green_code})")
            return 2
        if expected:
            failed = [cell for cell in expected if f"FAILED {cell}" not in out
                      and f"::{cell}" not in out]
            if failed or code == 0:
                print(f"[MISS] {name}: nothing went red "
                      f"({failed or 'whole run green'})")
                misses.append(name)
                continue
            red += 1
            print(f"[red] {name}")
            print(f"      file: {rel}")
            print(f"      replacement: {new.strip()[:110]}")
            print(f"      nodes: {', '.join(expected)}")
            print(f"      exit: {code}")
            print(f"      fragment: {_fragment(out)}")
        else:
            if code == 0:
                print(f"[control survives] {name}  (exit 0, restore green)")
            else:
                print(f"[MISS] {name}: the cosmetic control reddened the run")
                misses.append(name)
    print(f"\nverdict: {red} as expected, {len(misses)} miss(es)")
    for miss in misses:
        print(f"  - {miss}")
    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
