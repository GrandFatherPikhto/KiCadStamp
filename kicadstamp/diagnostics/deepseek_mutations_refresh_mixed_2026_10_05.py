# kicadstamp/diagnostics/deepseek_mutations_refresh_mixed_2026_10_05.py
"""Acceptance mutations for plan_2026_10_04_refresh_mixed_cluster_selection.md
(mixed selection: narrow to ONE cell instance, subtract foreign copper, select
the read copper back), DeepSeek, 2026-10-05.

Grown from claude_mutations_accept_stale_live_review_2026_10_02.py (rule 38):
the same machinery — basename-resolved T under tests/, `_drop_pyc` for the
mutated file, the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ"
when nothing red came back, a refused filter that matches no row, and a control
that MUST survive.

WHAT IS BEING PROVEN. The guards are tests/selection/test_selection_narrowing.py
(the pure rule + the gui glue) and
tests/gui/docks/test_cell_editor_mixed_selection.py (the two workers and the
selection-after-read). Every row maps to a line of the plan's own mutation list.

  * M1  foreign registered copper not subtracted       -> kept set grows
  * M2  net_traces treated as own                       -> subtracted net stays
  * M3  this cell on ANOTHER instance seen as own       -> sheet step dropped
  * M4  own copper subtracted                           -> kept set shrinks
  * M5  narrowing also on a CLEAN selection             -> "read again" no-op lost
  * M6  first of two candidates taken                   -> ambiguity refusal lost
  * M7  no select-after-read (refresh)                  -> foreign selection kept
  * M8  selection-after-read loses the new records      -> kept_copper emptied
  * M9  Import does not narrow                          -> import keeps the mix
  * M10 the cell-cluster step dropped (roles only)      -> PIF cell goes red
  * M11 "Fill from selection" without the filtering     -> duplicate-role fatal
  * K1  a cosmetic comment                              -> MUST survive

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

GUARDS = ["test_selection_narrowing.py",
          "test_cell_editor_mixed_selection.py"]

NARROWING = "kicadstamp/selection_narrowing.py"
GLUE = "gui/mixed_selection.py"
EDITOR = "gui/docks/cell_editor.py"
IDENT = "gui/cell_identification.py"

MUTATIONS = [
    ("M1 foreign registered copper not subtracted", NARROWING,
     "        if _is_own_key(key, cell_identity, own_addresses, chosen_address):\n"
     "            kept.append(item)\n"
     "            continue",
     "        if True:  # MUTATION\n            kept.append(item)\n            continue",
     "die", GUARDS, ()),
    ("M2 net_traces treated as own", NARROWING,
     "        return False\n    identity = _match_own_identity(value, own_addresses)",
     "        return True  # MUTATION\n    identity = _match_own_identity(value, own_addresses)",
     "die", GUARDS, ()),
    ("M3 other instance seen as own", NARROWING,
     "    if r_sheet and c_sheet and r_sheet != c_sheet:\n        return False",
     "    if False:  # MUTATION\n        return False",
     "die", GUARDS, ()),
    ("M4 own copper subtracted", NARROWING,
     "    return _address_matches(own_addresses[identity], chosen_address)",
     "    return False  # MUTATION",
     "die", GUARDS, ()),
    ("M5 narrowing also on a clean selection", GLUE,
     "    if len(groups) <= 1:\n"
     "        return None  # clean selection (or none has a Cluster) — today's path",
     "    if False:\n"
     "        return None  # clean selection (or none has a Cluster) — today's path",
     "die", GUARDS, ()),
    ("M6 first of two candidates taken", NARROWING,
     "    if len(candidates) == 1:",
     "    if len(candidates) >= 1:  # MUTATION",
     "die", GUARDS, ()),
    ("M7 no select-after-read (refresh)", EDITOR,
     "            # hand, then read again (a clean selection follows the ordinary path).\n"
     "            if prelude is not None:",
     "            # hand, then read again (a clean selection follows the ordinary path).\n"
     "            if False:  # MUTATION",
     "die", GUARDS, ()),
    ("M8 selection-after-read loses new records", GLUE,
     "        kept_copper=list(sub_v.kept) + list(sub_t.kept),",
     "        kept_copper=[],  # MUTATION",
     "die", GUARDS, ()),
    ("M9 Import does not narrow", EDITOR,
     "            plan_footprints, plan_vias, plan_tracks, prelude, refusal = (\n"
     "                _narrow_for_read(payload, adapter, footprints, vias, tracks,\n"
     "                                 cfg, sheet_names))\n"
     "            if refusal:\n"
     "                return {\"selection_refusal\": refusal}\n"
     "            selection_lines = list(prelude.log_lines) if prelude else []\n"
     "            plan = build_import_plan(",
     "            plan_footprints, plan_vias, plan_tracks = (footprints, vias, tracks)\n"
     "            prelude, refusal = None, None\n"
     "            selection_lines = []\n"
     "            plan = build_import_plan(",
     "die", GUARDS, ()),
    ("M10 cell-cluster step dropped (roles only)", NARROWING,
     "    if cell_clusters_set and not any(",
     "    if False and not any(  # MUTATION",
     "die", GUARDS, ()),
    ("M11 Fill-from-selection without filtering", IDENT,
     "    cell_clusters_set = {str(c) for c in (cell_clusters or ()) if c}\n"
     "    if cell_clusters_set:",
     "    cell_clusters_set = {str(c) for c in (cell_clusters or ()) if c}\n"
     "    if False:  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", NARROWING,
     "    kept: list[Any] = []",
     "    kept: list[Any] = []  # control",
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
                            "--no-header", "-p", "no:cacheprovider"],
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
