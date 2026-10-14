# kicadstamp/diagnostics/deepseek_mutations_tree_mount_rebase_2026_10_14.py
"""Acceptance mutations for Ч2 of plan_2026_10_14_tree_group_move_and_mount_
rebase.md, DeepSeek, 2026-10-14: "Change anchor from selection…" on a mount node
(`gui/docks/trees_mount_rebase.py`), the shared selection classification that
feeds it (`gui/entity/anchor_workers.classify_selection_anchor`, Ч2-1) and the
single-path rollback cell Ч2-9 asked for.

Grown from deepseek_mutations_tree_group_move_dodelka_2026_10_14.py (rules 38/43):
the same machinery — basename-resolved T under tests/, `_drop_pyc` for the mutated
file, the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing
red came back, and a control that MUST survive. The first three rows are the
PREVIOUS rig's own (M1/M5/M7), kept here because this task MOVED code next to
them: if the extraction blunted them, that is a finding of THIS task.

WHAT IS BEING PROVEN, in the plan's terms:

  * S1  no rollback on the SINGLE re-hang path     -> red (Ч2-9: the hole that was
                                                      alive in the BASE)
  * S2  "moves" re-expressed like "stays"          -> red (the group would not move)
  * S3  "stays" forgets the base's ANGLE           -> red (the angle addendum)
  * S4  a child-mount is re-read with the rest     -> red (Ч0 п.5)
  * S5  "stays in place" offered when it cannot    -> red (Ч2 п.7)
  * S6  two footprints pass for an anchor          -> red (Ч2 п.1)
  * S7  a refused anchor is left on the node       -> red (the in-place resolve)
  * S8  the sheet is not resolved from the support -> red (Ч2 п.2, root = "/")

Run with the main checkout's interpreter; point it at another tree with
`KICADSTAMP_ACCEPT_ROOT`. An optional row-name prefix filter takes the rest of
argv.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(os.environ.get("KICADSTAMP_ACCEPT_ROOT", "."))


def _interpreter() -> str:
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    env = os.environ.get("KICADSTAMP_PYTHON")
    return env if env else sys.executable


PY_BIN = _interpreter()

GUARDS = ["test_trees_dock.py",
          "test_trees_dock_group_move.py",
          "test_trees_dock_group_selection.py",
          "test_trees_dock_mount_rebase.py",
          "test_trees_move_flow.py",
          "test_anchor_selection_core.py",
          "test_board_door_offenders.py"]

TREES_DOCK = "gui/docks/trees_dock.py"
MOVE_FLOW = "gui/docks/trees_move_flow.py"
MOUNT_REBASE = "gui/docks/trees_mount_rebase.py"

MUTATIONS = [
    # ── the Ч1 guards this task moved code beside ───────────────────────────
    ("M1 _reparented_offset always raises", TREES_DOCK,
     "    return reexpress_offset(node.xy, node.polar, node.rotation,\n"
     "                            (old_pos, old_deg), (new_pos, new_deg))",
     '    raise ValidationError("MUTATION M1: no recalculation, ever")',
     "die", GUARDS, ()),
    ("M5 group offsets are written, not rolled back", MOVE_FLOW,
     "    if not dock._reparent_nodes(tree, [node for node, _shift in shifts],\n"
     "                                new_parent, defer_rebuild=False):\n"
     "        for node, previous in written:\n"
     "            node.xy, node.polar, node.rotation = previous\n"
     "        return False\n"
     "    return True",
     "    if not dock._reparent_nodes(tree, [node for node, _shift in shifts],\n"
     "                                new_parent, defer_rebuild=False):\n"
     "        return False  # MUTATION M5: no rollback of the written offsets\n"
     "    return True",
     "die", GUARDS, ()),
    ("M7 no live-read fallback in plan_rehang", MOVE_FLOW,
     "        except Exception as exc:        # noqa: BLE001 — see the clauses below\n"
     "            reason = str(exc)\n"
     "            try:\n"
     "                shift = read_live(node)\n"
     "            except Exception as read_exc:   # noqa: BLE001 — the refusal path\n"
     "                plan.refused.append(\n"
     '                    (node, _("{first}; reading it from the board failed too: "\n'
     '                             "{second}").format(first=reason, second=read_exc)))\n'
     "                continue\n"
     "            plan.from_board.append((node, reason))",
     "        except Exception as exc:        # noqa: BLE001 — see the clauses below\n"
     "            plan.refused.append((node, str(exc)))  # MUTATION M7: no live read\n"
     "            continue",
     "die", GUARDS, ()),
    # ── Ч2-9: the single path rolls its numbers back too ────────────────────
    ("S1 no rollback on the SINGLE re-hang path", MOVE_FLOW,
     "        if not dock._reparent_node(tree, node, new_parent,\n"
     "                                   defer_rebuild=False):\n"
     "            node.xy, node.polar, node.rotation = previous",
     "        if not dock._reparent_node(tree, node, new_parent,\n"
     "                                   defer_rebuild=False):\n"
     "            pass  # MUTATION S1: no rollback on the single path",
     "die", GUARDS, ()),
    # ── Ч2: the two offers of the picker ────────────────────────────────────
    ("S2 'moves' re-expressed like 'stays'", MOUNT_REBASE,
     "    return reexpress_offset(xy, polar, rotation, (old_pos, old_deg),\n"
     "                            (old_pos, new_deg))",
     "    return reexpress_offset(xy, polar, rotation, (old_pos, old_deg),\n"
     "                            (new_pos, new_deg))  # MUTATION S2: a move "
     "that stays",
     "die", GUARDS, ()),
    ("S3 'stays' forgets the base's angle", MOUNT_REBASE,
     "        return reexpress_offset(xy, polar, rotation, (old_pos, old_deg),\n"
     "                                (new_pos, new_deg))",
     "        return reexpress_offset(xy, polar, rotation, (old_pos, old_deg),\n"
     "                                (new_pos, old_deg))  # MUTATION S3: no turn",
     "die", GUARDS, ()),
    # ── Ч2-5/Ч2-7: the children, and which rows the picker offers ───────────
    ("S4 a child-mount is re-read with the rest", MOUNT_REBASE,
     '        if child.kind == "mount" and child.anchor is not None:\n'
     "            continue",
     "        if False:  # MUTATION S4: do not skip a child-mount\n"
     "            continue",
     "die", GUARDS, ()),
    ("S5 'stays in place' offered when it cannot be honoured", MOUNT_REBASE,
     "    if plan.old_base is not None or not plan.child_failures:",
     "    if True:  # MUTATION S5: offer the unhonourable row",
     "die", GUARDS, ()),
    # ── Ч2 п.1/п.2: the selection, and the anchor left in place ─────────────
    ("S6 two footprints pass for one anchor", MOUNT_REBASE,
     '    if len(items) != 1 or read["kind"] not in ("pad", "footprint"):',
     '    if read["kind"] not in ("pad", "footprint"):  # MUTATION S6: no count',
     "die", GUARDS, ()),
    ("S7 a refused anchor is left on the node", MOUNT_REBASE,
     "    finally:\n"
     "        node.anchor = old_anchor\n"
     "    return plan",
     "    finally:\n"
     "        pass  # MUTATION S7: the new anchor stays on the node\n"
     "    return plan",
     "die", GUARDS, ()),
    ("S8 the sheet is not resolved from the support", MOUNT_REBASE,
     "    sheet = (sheet_of_names(resolve_sheet_path_names(footprint, sheet_names))\n"
     "             if footprint is not None else None)",
     "    sheet = None  # MUTATION S8: no sheet",
     "die", GUARDS, ()),
    # ── the control ─────────────────────────────────────────────────────────
    ("K1 cosmetic comment (control)", MOUNT_REBASE,
     '_SELECT_ONE = _(\n    "Change anchor: select exactly one footprint or one pad on the board")',
     '_SELECT_ONE = _(\n    "Change anchor: select exactly one footprint or one pad on the board")'
     "  # control",
     "survive", GUARDS, ()),
]


def _resolve_tests(basenames):
    """[repo-relative paths] for `basenames`, resolved under ROOT/tests.

    Refuses (SystemExit) when a name is not found, or is found TWICE (rule 38)."""
    found, problems = [], []
    for name in basenames:
        matches = sorted((ROOT / "tests").rglob(name))
        if len(matches) == 1:
            found.append(matches[0].relative_to(ROOT).as_posix())
        else:
            problems.append(f"{name}: {len(matches)} match(es)")
    if problems:
        raise SystemExit(
            "the acceptance rig cannot resolve its test list under tests/ — "
            "refusing to run with a blind or ambiguous T: " + "; ".join(problems))
    return found


def _drop_pyc(rel: str) -> None:
    """Drop the bytecode of the MUTATED file (the stale-.pyc trap, rule 38)."""
    f = ROOT / rel
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run(paths, extra=()):
    """(returncode, output); returncode None means the run did not finish."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q",
                            "--no-header", "-p", "no:cacheprovider", "-n", "auto"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    only = sys.argv[1:]
    rows = [row for row in MUTATIONS
            if not only or any(row[0].startswith(prefix) for prefix in only)]
    if only and not rows:
        raise SystemExit(f"no mutation row matches {only} — nothing was measured")
    print(f"корень: {ROOT}")
    print(f"строк: {len(rows)} из {len(MUTATIONS)}")
    print(f"{'мутация':<46} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 130)
    for name, rel, old, new, expect, tests, extra in rows:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<46} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} "
                  f"шаблон встречается {n} раз")
            continue
        try:
            f.write_text(original.replace(old, new, 1), encoding="utf-8")
            _drop_pyc(rel)
            code, out = run(_resolve_tests(tests), extra)
            failed = [l for l in out.splitlines() if l.startswith("FAILED")]
            if code is None:
                verdict, detail = "ЗАВИСЛА", "прогон не уложился в 300 с"
            elif code == 0:
                verdict, detail = "ВЫЖИЛА", "ни один сторож не покраснел"
            elif not failed or "no tests ran" in out:
                verdict, detail = "ПРОМАХ", f"ноль красных при выходе {code}"
            else:
                verdict = "УБИТА"
                detail = f"{len(failed)}: " + "; ".join(
                    l.split(" ")[1].split("::")[-1] for l in failed[:4])
            flag = ""
            if expect == "die" and verdict != "УБИТА":
                flag = "  <<< НАХОДКА: ждали смерти"
            if expect == "survive" and verdict == "УБИТА":
                flag = "  <<< НАХОДКА: ждали выживания"
            print(f"{name:<46} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()
