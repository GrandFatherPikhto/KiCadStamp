# kicadstamp/diagnostics/deepseek_mutations_accept_uuid_u1_2026_10_02.py
"""Acceptance mutations for the format 3 (UUID) grammar/model — У1 of
plan_2026_10_02_uuid_format_2_to_3.md, DeepSeek, 2026-10-02.

Grown (rule 38) from claude_mutations_accept_stale_live_review_2026_10_02.py:
the same machinery — basename-resolved T, `_drop_pyc` for the mutated file, the
``original.count(old) != 1`` refusal, a verdict of "ПРОМАХ" when nothing red
came back, a refused filter that matches no row, and a control that must
survive.

WHAT IS BEING PROVEN. The guards are tests/config/test_config_format3.py.

  * U1w — the writer drops the record `uuid`         (sexp_format._record_to_sexp)
  * U2w — the writer drops the folder table          (sexp_format.dict_to_sexp)
  * U3w — the reference UUID is not nested           (sexp_format._ref_field_to_sexp)
  * U1l — the "record without uuid" fatal is off     (loader._check_format3_graph)
  * U2l — the duplicate-uuid fatal is off            (loader._check_format3_graph)
  * U3l — the dangling-reference fatal is off        (loader._check_format3_graph)
  * C1  — a cosmetic comment edit. MUST survive.

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

# Resolved by BASENAME under tests/ — a name found zero or two times is a
# finding, not a warning.
_T_BASENAMES = ["test_config_format3.py"]

GUARDS = ["test_config_format3.py"]

SEXP = "kicadstamp/config/sexp_format.py"
LOADER = "kicadstamp/config/loader.py"

MUTATIONS = [
    ("U1w writer drops the record uuid", SEXP,
     '        if key.endswith("_uuid"):',
     '        if key.endswith("_uuid") or key == "uuid":',
     "die", GUARDS, ()),
    ("U2w writer drops the folder table", SEXP,
     '    folders = data.get("folders") or {}',
     '    folders = {}',
     "die", GUARDS, ()),
    ("U3w reference uuid not nested in the node", SEXP,
     "    if uuid is not None:\n        node.append([sym(\"uuid\"), uuid])",
     "    if False:\n        node.append([sym(\"uuid\"), uuid])",
     "die", GUARDS, ()),
    ("U1l record-without-uuid fatal off", LOADER,
     "    for section, name, uuid, f in records:\n        if not uuid:",
     "    for section, name, uuid, f in records:\n        if False:",
     "die", GUARDS, ()),
    ("U2l duplicate-uuid fatal off", LOADER,
     "        if uuid in owner:",
     "        if False:",
     "die", GUARDS, ()),
    ("U3l dangling-reference fatal off", LOADER,
     "        if uuid not in uuids_by_section.get(target, set()):",
     "        if False:",
     "die", GUARDS, ()),
    ("C1 cosmetic comment (control)", LOADER,
     "    records: list = []",
     "    records: list = []  # control",
     "survive", GUARDS, ()),
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
    # Optional filters: `… U1w` runs only the rows whose name starts with one of
    # them. A filter that matches NOTHING is a refusal, not an empty table.
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
                # A crash at the end of the run is not a verdict on its own: a
                # green report with a non-zero exit would mean the guard never
                # saw it (rule 38).
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
