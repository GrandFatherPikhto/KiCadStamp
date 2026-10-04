# kicadstamp/diagnostics/deepseek_mutations_u35_delete_cascade_2026_10_04.py
"""Acceptance mutations for the У3.5 delete-cascade fix + the Qt-slot safety
catch (Denis's decision 04.10.2026, finding of batch 6), 2026-10-04.

Grown from claude_mutations_accept_uuid_u4_rework_2026_10_03.py (rule 38): the
machinery is kept verbatim — basename-resolved guards, `_drop_pyc`, the
`original.count(old) != 1` refusal, "ПРОМАХ" when nothing red came back, and a
control that MUST survive. The rows are new; the subject is the delete cascade.

WHAT IS BEING PROVEN.
  * `gui/docks/entity_delete.py` — `delete_entry(cascade=True)` now builds every
    touched file's END STATE in memory first, then writes ONE file at a time in
    a fixed ORDER (referencing files first, the deleted record's OWN file last).
    Before this the primary removal was written to disk BEFORE the references
    were pruned, so under format 3 the writer refused the very file it had just
    deleted from ("cannot write — N reference(s) point at a uuid that is
    missing"), and a file holding both the record and its reference was written
    twice.
  * `gui/docks/config_tree.py` — `_on_delete` and `_on_export` catch
    `ValidationError` as well as `OSError` and report a RED Log line instead of
    letting the exception out of a Qt slot (SIGABRT) / showing a dialog.

Rows (guards run under the PRODUCT format, CURRENT_FORMAT = 2):
  * D1 — the write order reversed: the deleted record's file first. The order
    cell must go red (it is the only thing that observes the invariant).
  * D2 — references never pruned. The cascade cells must go red (a reference the
    cell expects gone is still there).
  * D3 — the cascade switched off entirely. Same.
  * S1 — `_on_delete` catches only OSError: the ValidationError escapes the slot
    and the guard cell (which asserts the process survived) dies.
  * S2 — `_on_export` catches only OSError: same on the export guard cell.
  * C1 — a cosmetic comment edit. MUST survive.

Run: `.venv/bin/python kicadstamp/diagnostics/deepseek_mutations_u35_delete_cascade_2026_10_04.py`
An optional row-name prefix filter takes the rest of argv.
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
# finding, not a warning (rule 38).
_T_BASENAMES = ["test_entity_delete.py", "test_config_tree.py"]

GUARDS = list(_T_BASENAMES)

ENTITY_DELETE = "gui/docks/entity_delete.py"
CONFIG_TREE = "gui/docks/config_tree.py"

MUTATIONS = [
    ("D1 write order reversed", ENTITY_DELETE,
     "    order = [p for p in cascade_files if p != entry_path]\n    order.append(entry_path)",
     "    order = [entry_path] + [p for p in cascade_files if p != entry_path]",
     "die", GUARDS, ("-k", "delete_cascade")),
    ("D2 references never pruned", ENTITY_DELETE,
     "            if _prune_file_data(_plan(path), field_name, name, on_match=lambda e: None):",
     "            if False:",
     "die", GUARDS, ("-k", "delete_cascade")),
    ("D3 cascade switched off", ENTITY_DELETE,
     "    if do_cascade:\n        for path in collect_graph_files(root_path):",
     "    if False:\n        for path in collect_graph_files(root_path):",
     "die", GUARDS, ("-k", "delete_cascade")),
    ("S1 _on_delete catches only OSError", CONFIG_TREE,
     "        except (OSError, ValidationError) as e:\n"
     "            # Qt-slot safety: a bare exception escaping a slot aborts PyQt6",
     "        except OSError as e:\n"
     "            # Qt-slot safety: a bare exception escaping a slot aborts PyQt6",
     "die", GUARDS, ("-k", "on_delete_logs")),
    ("S2 _on_export catches only OSError", CONFIG_TREE,
     "        except (OSError, ValidationError) as e:\n"
     "            # Catch ValidationError too: export_entries raises it for the",
     "        except OSError as e:\n"
     "            # Catch ValidationError too: export_entries raises it for the",
     "die", GUARDS, ("-k", "export")),
    ("C1 cosmetic comment (control)", ENTITY_DELETE,
     "    plans: Dict[Path, Dict[str, Any]] = {}\n",
     "    plans: Dict[Path, Dict[str, Any]] = {}  # control\n",
     "survive", GUARDS, ("-k", "delete_cascade")),
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
        # -n auto (pytest-xdist): the guard set is small, but the per-row
        # startup is cheap and the shape matches the other rigs (rule 42).
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q", "--no-header",
                            "-p", "no:cacheprovider", "-n", "auto"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    # Optional filters: `… D1` runs only the rows whose name starts with one of
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
