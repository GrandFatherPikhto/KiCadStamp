# kicadstamp/diagnostics/deepseek_mutations_cluster_confined_2026_10_13.py
"""Acceptance mutations for plan_2026_10_13_cluster_confined_roles.md (an
entity carrying `cluster` takes its components ONLY from ITS cluster; the baked
cell net_template never selects), DeepSeek, 2026-10-13.

Grown from deepseek_mutations_refresh_mixed_2026_10_05.py (rule 38/43): the same
machinery — basename-resolved T under tests/, `_drop_pyc` for the mutated file,
the `original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing red
came back, and a control that MUST survive. Rows are keyed by the LINE they
mutate, not by a cell number (rule 43).

WHAT IS BEING PROVEN. The guards are
tests/placement/test_clone_reuse_cell_across_power_rails.py (cells К1–К9 of the
plan's table), tests/placement/test_clone_role_resolver.py (К11, plus every
by-nets/by-selection narrowing cell it already had),
tests/gui/docks/test_spoke_cell_identification.py (the read path: К10) and
tests/utils/test_strip_fatal_block.py (both footers).

  * M1  foreign/untagged Cluster taken again            -> К6/К7/К11 red
  * M2  filter skipped when ONE candidate survives       -> К6 red (the theft itself)
  * M3  baked literal selects again                      -> К2 red
  * M4  refdes copies checked AFTER the cascade          -> К3 red (proximity picks one)
  * M5  the refusal speaks "spoke / Fill from selection" -> К10 red
  * M6  the read-only footer falls back to the false one -> К10/С5b red
  * K1  a cosmetic comment                               -> MUST survive

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

GUARDS = ["test_clone_reuse_cell_across_power_rails.py",
          "test_clone_role_resolver.py",
          "test_spoke_cell_identification.py",
          "test_strip_fatal_block.py"]

NARROWING = "kicadstamp/placement/services/role_narrowing.py"
RESOLVER = "kicadstamp/placement/services/clone_role_resolver.py"
REASONS = "kicadstamp/role_conflict_reasons.py"
LIVE = "gui/docks/live_position.py"
EXCEPTIONS = "kicadstamp/exceptions.py"

MUTATIONS = [
    ("M1 foreign/untagged Cluster taken again", NARROWING,
     "    if not cluster:\n"
     "        return list(candidates)\n"
     "    return [fp for fp in candidates\n"
     "            if cluster_prefix_match(\n"
     "                adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '', cluster)]",
     "    return list(candidates)  # MUTATION",
     "die", GUARDS, ()),
    ("M2 filter skipped for a single candidate", NARROWING,
     "    if not cluster:\n"
     "        return list(candidates)",
     "    if not cluster or len(candidates) == 1:  # MUTATION\n"
     "        return list(candidates)",
     "die", GUARDS, ()),
    ("M3 baked literal selects again", RESOLVER,
     "                    else:\n"
     "                        baked_net = resolved",
     "                    else:\n"
     "                        baked_net = resolved\n"
     "                        expected_net = resolved  # MUTATION\n"
     "                        net_source = \"net_template\"",
     "die", GUARDS, ()),
    ("M4 refdes copies checked after the cascade", RESOLVER,
     "        # ONE refdes standing twice INSIDE the own cluster is refused BEFORE any\n"
     "        # narrowing (plan, cascade step 2): neither a net nor a sheet, a selection\n"
     "        # or physical proximity can tell two copies of one symbol apart, so \"the\n"
     "        # closest copy\" would be a silent guess.\n"
     "        if placement_cluster and has_refdes_copies(own):",
     "        if False:  # MUTATION",
     "die", GUARDS, ()),
    ("M5 refusal speaks the withdrawn spoke wording", REASONS,
     "            _(\"role {role!r}: {ref} stands on the board {count} times — delete the \"\n"
     "              \"extra copy: {copies}\").format(",
     "            _(\"role {role!r} occurs {count} times in cluster {cluster!r} — this is a \"\n"
     "              \"spoke cell: select one pair and use “Fill from selection”\").format(",
     "die", GUARDS, ()),
    ("M6 read-only footer falls back to the false one", EXCEPTIONS,
     "    return format_fatal_error(title, problems, verdict=read_only_verdict())",
     "    return format_fatal_error(title, problems)  # MUTATION",
     "die", GUARDS, ()),
    ("K1 cosmetic comment (control)", NARROWING,
     "    if not cluster:\n"
     "        return list(candidates)",
     "    if not cluster:\n"
     "        return list(candidates)  # control",
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
