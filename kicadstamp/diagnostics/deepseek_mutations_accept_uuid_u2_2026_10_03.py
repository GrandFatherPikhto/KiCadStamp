# kicadstamp/diagnostics/deepseek_mutations_accept_uuid_u2_2026_10_03.py
"""Mutations for the UUID step U2 — resolve by UUID in ONE point (plan §4),
Deepseek, 2026-10-03. Grown from claude_mutations_accept_uuid_u1_final_2026_10_03.py
(rule 38): the SAME machinery — basename-resolved T, `_drop_pyc`, the
`original.count(old) != 1` refusal, "ПРОМАХ" when nothing red came back, a refused
filter that matches no row, and a control that MUST survive.

WHAT IS BEING PROVEN. U2 adds the loader's normalization pass (each format-3
reference is resolved BY UUID and its name field overwritten with the target's
full name), the У2.2 "reference without a uuid" fatal, the tree anchor's
(point …) UUID, and the В33 short-name resolution in --only. Rows:

  * M1 — normalization off (kills every lying-hint cell, one per form).
  * M2 — normalization writes the HINT back instead of the UUID target.
  * M3 — the pass placed AFTER expand_tree_instances: the generated tree node
    refs are renamed copies carrying the template's UUIDs, and normalizing them
    afterwards rewrites the copy's ref back to the template entity's name.
  * M4 — the У2.2 fatal off (a uuid-less reference then reads as "dangling").
  * M5 — В33 several short matches take the first instead of refusing.
  * M6 — В33 the short match beats an exact full name.
  * M7 — the tree anchor's point UUID dropped by the s-expr writer.
  * M8 — the tree anchor's point UUID dropped by the s-expr parser.
  * C1 — a cosmetic comment edit. MUST survive.

A mutation that kills nothing is a miss, not a pass (rule 38): run with the
main checkout's interpreter; point it at another tree with
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

GUARDS = ["test_format3_normalize.py", "test_format3_equivalence.py",
          "test_config_format3.py", "test_cli_filters.py"]

LOADER = "kicadstamp/config/loader.py"
TREES = "kicadstamp/trees.py"
APPLY = "kicadstamp/apply_pipeline.py"

MUTATIONS = [
    ("M1 normalization off", LOADER,
     "        _normalize_format3_refs(data)", "        pass", "die", GUARDS, ()),
    ("M2 normalization keeps the hint", LOADER,
     "        ref.holder[ref.name_field] = target_names[uuid]",
     "        ref.holder[ref.name_field] = ref.holder.get(ref.name_field)",
     "die", GUARDS, ()),
    ("M3 pass after expand_tree_instances", LOADER,
     "    if current_format() >= 3:\n"
     "        data[\"folders\"] = _check_format3_graph(path)\n"
     "        _normalize_format3_refs(data)\n"
     "    # sheet_templates: expansion (2026-08-16) — must run after include\n"
     "    # resolution (a template can live in an included subsystem file) and\n"
     "    # BEFORE any per-entry loader/duplicate-name check, so template-generated\n"
     "    # entries are indistinguishable from hand-written ones (see\n"
     "    # kicadstamp/config/sheet_templates.py).\n"
     "    data = expand_sheet_templates(data)\n"
     "    # tree_instances: expansion (2026-09-02, plan tree_instances) — dict-level,\n"
     "    # after include resolution AND after sheet_templates (a template may live\n"
     "    # in an included file, or reference a sheet-template-generated entity),\n"
     "    # BEFORE any per-entry loader/duplicate-name check, so the materialized\n"
     "    # trees/entities flow through the SAME _load_tree/_load_entity path as\n"
     "    # hand-written ones (rule 2 seen_refs + duplicate-name checks for free).\n"
     "    # Unlike expand_sheet_templates, the raw 'tree_instances:' key is KEPT —\n"
     "    # the loader parses it into cfg.tree_instances below (see\n"
     "    # kicadstamp/config/tree_instances.py).\n"
     "    data = expand_tree_instances(data)\n",
     "    if current_format() >= 3:\n"
     "        data[\"folders\"] = _check_format3_graph(path)\n"
     "    # sheet_templates: expansion (2026-08-16) — must run after include\n"
     "    # resolution (a template can live in an included subsystem file) and\n"
     "    # BEFORE any per-entry loader/duplicate-name check, so template-generated\n"
     "    # entries are indistinguishable from hand-written ones (see\n"
     "    # kicadstamp/config/sheet_templates.py).\n"
     "    data = expand_sheet_templates(data)\n"
     "    # tree_instances: expansion (2026-09-02, plan tree_instances) — dict-level,\n"
     "    # after include resolution AND after sheet_templates (a template may live\n"
     "    # in an included file, or reference a sheet-template-generated entity),\n"
     "    # BEFORE any per-entry loader/duplicate-name check, so the materialized\n"
     "    # trees/entities flow through the SAME _load_tree/_load_entity path as\n"
     "    # hand-written ones (rule 2 seen_refs + duplicate-name checks for free).\n"
     "    # Unlike expand_sheet_templates, the raw 'tree_instances:' key is KEPT —\n"
     "    # the loader parses it into cfg.tree_instances below (see\n"
     "    # kicadstamp/config/tree_instances.py).\n"
     "    data = expand_tree_instances(data)\n"
     "    if current_format() >= 3:\n"
     "        _normalize_format3_refs(data)\n",
     "die", GUARDS, ()),
    ("M4 reference-without-uuid fatal off", LOADER,
     "        if uuid is None:", "        if uuid is None and False:",
     "die", GUARDS, ()),
    ("M5 В33 several matches take the first", APPLY,
     "            ambiguous.append((token, sorted(matches)))",
     "            requested.add(matches[0])", "die", GUARDS, ()),
    ("M6 В33 short match beats exact", APPLY,
     "        if token in name_set:", "        if False:", "die", GUARDS, ()),
    ("M7 tree anchor point uuid not written", TREES,
     "            if anchor.point_uuid is not None:\n"
     "                point_node.append([sym(\"uuid\"), anchor.point_uuid])",
     "            if False:\n"
     "                point_node.append([sym(\"uuid\"), anchor.point_uuid])",
     "die", GUARDS, ()),
    ("M8 tree anchor point uuid not parsed", TREES,
     "            point_uuid = sval(extra[1])", "            point_uuid = None",
     "die", GUARDS, ()),
    ("M9 normalization on the shared cache (К1)", LOADER,
     "        data = copy.deepcopy(data)\n        _normalize_format3_refs(data)",
     "        _normalize_format3_refs(data)", "die", GUARDS, ()),
    ("M10 В33 short from every identity (К2)", APPLY,
     "    for full in sorted(short_sources):", "    for full in all_names:",
     "die", GUARDS, ()),
    ("C1 cosmetic comment (control)", LOADER,
     "    graph_files = _f3_files(root_path)",
     "    graph_files = _f3_files(root_path)  # control", "survive", GUARDS, ()),
]


def _resolve_tests(basenames):
    """[repo-relative paths] for `basenames`, resolved under ROOT/tests.

    Refuses (SystemExit) when a name is not found, or is found TWICE: a blind T
    would make every mutation verdict vacuous, and an ambiguous one would run
    the wrong file — both are failures, not warnings (rule 38)."""
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
    """Drop the bytecode of the MUTATED file — a same-length control mutation
    with a same-second mtime is exactly the stale-.pyc trap (rule 38)."""
    f = ROOT / rel
    for cache in f.parent.rglob("__pycache__"):
        for pyc in cache.glob(f"{f.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


def run(paths, extra=()):
    """(returncode, output); returncode None means the run did not finish."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q", "--no-header",
                            "-p", "no:cacheprovider"],
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
            print(f"{name:<44} {expect:<9} {'НЕДЕЙСТВИТЕЛЬНА':<16} шаблон встречается {n} раз")
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
