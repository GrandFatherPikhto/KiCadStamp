# kicadstamp/diagnostics/deepseek_mutations_trees_menus_2026_10_13.py
"""Acceptance mutations for plan_2026_10_13_trees_menus_cleanup.md, Ч3 (the node
"Add node" menu holds exactly FOUR kinds; an OLD node of a withdrawn kind still
opens/edits without losing its kind; a net_trace row is TAGGED; used records are
greyed and named), DeepSeek, 2026-10-13.

Grown from deepseek_mutations_cluster_confined_2026_10_13.py (rules 38/43): the
same machinery — basename-resolved T under tests/, `_drop_pyc` for the mutated
file, the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing
red came back, and a control that MUST survive. Rows are keyed by the LINE they
mutate, not by a cell number (rule 43).

WHAT IS BEING PROVEN. The guards are tests/gui/docks/test_trees_dock.py (the four
menu kinds, the retired/auto rows, the net_trace Ref list, the greyed used refs
and the refusal that NAMES the tree), test_trees_dock_node_ref_row.py (row text
follows the kind; a coordinate->placement / placement->mount change),
test_trees_dock_copper_pseudo_node.py (a net_trace row carries its tag) and
test_role_list_freshness.py (the address tab label for a component node).

  * M1  a node of a WITHDRAWN kind loses its kind (no "(retired)" row) -> red
  * M2  an AUTO node loses its "auto (retired)" row                    -> red
  * M3  a net_trace row is drawn WITHOUT its tag                       -> red
  * M4  the used-ref refusal stops NAMING the tree and node            -> red
  * M5  used rows are neither greyed nor hinted                        -> red
  * M6  the net_trace Ref list is empty again                          -> red
  * K1  a cosmetic comment                                             -> MUST survive

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
          "test_trees_dock_node_ref_row.py",
          "test_trees_dock_copper_pseudo_node.py",
          "test_role_list_freshness.py"]

TREES_DOCK = "gui/docks/trees_dock.py"
NODE_FORM = "gui/docks/trees_node_form.py"

MUTATIONS = [
    ("M1 retired-kind node loses its kind", NODE_FORM,
     "    elif existing_kind not in MENU_KINDS:\n"
     "        rows.append((_(\"{label} (retired)\").format(\n"
     "            label=node_kind_label(existing_kind)), existing_kind))\n"
     "    return rows",
     "    return rows  # MUTATION: a retired-kind node loses its kind",
     "die", GUARDS, ()),
    ("M2 auto node loses its retired row", NODE_FORM,
     "    if existing_kind is None:\n"
     "        rows.append((_(\"auto (retired)\"), None))",
     "    if existing_kind is None:\n"
     "        pass  # MUTATION: an auto node loses its row",
     "die", GUARDS, ()),
    ("M3 net_trace row drawn without a tag", TREES_DOCK,
     "        if node.kind is not None:\n"
     "            tag = node_kind_label(node.kind)\n"
     "            if tag:",
     "        if node.kind is not None:\n"
     "            tag = \"\" if node.kind == \"net_trace\" else node_kind_label(node.kind)  # MUTATION\n"
     "            if tag:",
     "die", GUARDS, ()),
    ("M4 used-ref refusal stops naming the place", NODE_FORM,
     "    return _(\"Record {ref!r} {where} — a record has exactly one position source; \"\n"
     "             \"move the node, or embed tree {tree!r} as a Tree node.\").format(\n"
     "                 ref=ref, where=used_ref_text(*place), tree=place[0])",
     "    return _(\"Record {ref!r} already has a node in this file — a record's \"\n"
     "             \"position source must be exactly one.\").format(ref=ref)  # MUTATION",
     "die", GUARDS, ()),
    ("M5 used rows neither greyed nor hinted", NODE_FORM,
     "        item = combo.model().item(row)\n"
     "        if item is not None:\n"
     "            item.setEnabled(False)\n"
     "        combo.setItemData(row, used_ref_text(*place), Qt.ItemDataRole.ToolTipRole)",
     "        pass  # MUTATION: used rows are neither greyed nor hinted",
     "die", GUARDS, ()),
    ("M6 net_trace Ref list empty again", TREES_DOCK,
     "        return [r.name for r in build_records(self._cfg) if r.kind == \"net_trace\"]",
     "        return []  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", NODE_FORM,
     "    return _(\"already stands in tree {tree!r} (node {node})\").format(\n"
     "        tree=tree_name, node=node_label)",
     "    return _(\"already stands in tree {tree!r} (node {node})\").format(  # control\n"
     "        tree=tree_name, node=node_label)",
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
    print(f"{'мутация':<44} {'ожидал':<9} {'вердикт':<16} что покраснело")
    print("-" * 125)
    for name, rel, old, new, expect, tests, extra in rows:
        f = ROOT / rel
        original = f.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"{name:<44} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} "
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
            print(f"{name:<44} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()
