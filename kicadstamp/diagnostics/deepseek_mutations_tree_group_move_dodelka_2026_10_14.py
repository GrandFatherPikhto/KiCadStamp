# kicadstamp/diagnostics/deepseek_mutations_tree_group_move_dodelka_2026_10_14.py
"""Acceptance mutations for the ДОДЕЛКА half of plan_2026_10_14_tree_group_move_
and_mount_rebase.md, DeepSeek, 2026-10-14: the flow bodies were extracted out of
`gui/docks/trees_dock.py` into `gui/docks/trees_move_flow.py` (and the selection
helpers into `gui/docks/trees_selection.py`), and two new cells were added.

Grown from deepseek_mutations_trees_menus_2026_10_13.py (rules 38/43): the same
machinery — basename-resolved T under tests/, `_drop_pyc` for the mutated file,
the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing red
came back, and a control that MUST survive.

WHAT IS BEING PROVEN. The point of the extraction (ДОДЕЛКА п.1) is that it moves
code WITHOUT moving the guarding surface: the same 5-file set the Ч1 acceptance
used must still redden on the same mutations. On top of it the two new cells are
exercised (ДОДЕЛКА п.2/п.3):

  * M1  `_reparented_offset` always raises            -> red (the Ч1 acceptance's
                                                         own M1, repeated to prove
                                                         the extraction did not
                                                         blunt it)
  * M5  `apply_group_move` writes and does not roll back -> red (the new M5 cell)
  * M7  `plan_rehang` has no live-read fallback        -> red (the new M7 cell)
  * K1  a cosmetic comment                             -> MUST survive

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
          "test_trees_move_flow.py",
          "test_board_door_offenders.py"]

TREES_DOCK = "gui/docks/trees_dock.py"
MOVE_FLOW = "gui/docks/trees_move_flow.py"

MUTATIONS = [
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
    ("K1 cosmetic comment (control)", MOVE_FLOW,
     '_TREE_MOVE_DONE = _("moved: {nodes} — new parent: {parent}")',
     '_TREE_MOVE_DONE = _("moved: {nodes} — new parent: {parent}")  # control',
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
