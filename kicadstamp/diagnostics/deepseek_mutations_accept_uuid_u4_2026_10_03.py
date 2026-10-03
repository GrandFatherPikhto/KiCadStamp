# kicadstamp/diagnostics/deepseek_mutations_accept_uuid_u4_2026_10_03.py
"""DeepSeek's mutations for the UUID step U4 (writers, plan §5), 2026-10-03.
Grown from claude_mutations_accept_uuid_u2_review_2026_10_03.py (rule 38): the
SAME machinery — basename-resolved T, `_drop_pyc`, the `original.count(old) != 1`
refusal, "ПРОМАХ" when nothing red came back, a refused filter that matches no
row, and a control that MUST survive.

WHAT IS BEING PROVEN. U4 puts the format-3 stamp in the ONE serializer
(config_writer._serialize -> config/format3.stamp_format3), the active graph
root beside WORKING_SET, `set_reference`, the profile_copy stamp-disable, the
flatten conversion, and the Ф4 refusal in entity_export. Rows:

  * U1a — п.1 off: a record without a uuid is not given uuid4.
  * U1b — п.2 off: a new reference is not given the target's UUID.
  * U2a — п.3 off: a lying hint is not rewritten from the UUID.
  * U2b — п.3 off: a dangling UUID is not refused.
  * U3  — п.4 off: no folder row is minted.
  * U4  — п.2 off: a new reference naming nothing is not refused.
  * U5  — the gate too loose (>= 2): the stamp wakes in the format-2 product.
  * U6  — set_reference keeps the stale UUID sibling.
  * U7  — profile_copy does not disable the stamp.
  * U8  — flatten serializes with a bare dict_to_sexp again (NameError / no stamp).
  * U9  — dock_hub does not set the active root (worker/CLI path).
  * C1  — a cosmetic comment edit. MUST survive.

A mutation that kills nothing is a miss, not a pass (rule 38): run with the main
checkout's interpreter; point it at another tree with `KICADSTAMP_ACCEPT_ROOT`.
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
# finding, not a warning.
_T_BASENAMES = ["test_config_format3_writer.py", "test_config_format3_wiring.py",
                "test_phase3_wiring.py",
                "test_config_format3.py",
                "test_config_format_version.py", "test_format3_normalize.py",
                "test_format3_equivalence.py", "test_config_writer.py",
                "test_cli_flatten.py"]

GUARDS = list(_T_BASENAMES)

FORMAT3 = "kicadstamp/config/format3.py"
CW = "kicadstamp/config_writer.py"
PROFILE_COPY = "kicadstamp/config/profile_copy.py"
FLATTEN = "kicadstamp/flatten.py"
DOCK_HUB = "gui/dock_hub.py"

MUTATIONS = [
    ("U1a п.1 record uuid4 off", FORMAT3,
     '        holder["uuid"] = inherited_uuid or str(uuid4())',
     '        holder["uuid"] = inherited_uuid', "die", GUARDS, ()),
    ("U1b п.2 new ref uuid off", FORMAT3,
     "            ref.holder[ref.uuid_field] = target_uuid", "            pass",
     "die", GUARDS, ()),
    ("U2a п.3 hint rewrite off", FORMAT3,
     "                ref.holder[ref.name_field] = target_name", "                pass",
     "die", GUARDS, ()),
    ("U2b п.3 dangling refusal off", FORMAT3,
     "    if dangling:\n        raise", "    if False:\n        raise", "die", GUARDS, ()),
    ("U3 п.4 folder mint off", FORMAT3,
     '                new_rows.setdefault(section, {})[prefix] = str(uuid4())',
     "                pass", "die", GUARDS, ()),
    ("U4 п.2 unknown-name refusal off", FORMAT3,
     "            if target_uuid is None:\n                raise", "            if False:\n                raise",
     "die", GUARDS, ()),
    ("U5 gate too loose (>= 2)", CW,
     "    if stamp and current_format() >= 3:", "    if stamp and current_format() >= 2:",
     "die", GUARDS, ()),
    ("U6 set_reference keeps the uuid", CW,
     '    holder.pop(field + "_uuid", None)', "    pass", "die", GUARDS, ()),
    ("S2 profile_copy does not disable the stamp", PROFILE_COPY,
     "    with format3_stamp_disabled():", "    if True:", "die", GUARDS, ()),
    ("U8 flatten serializes bare again", FLATTEN,
     "    new_text = serialize_config(target, out, graph_root=root_path)",
     "    new_text = dict_to_sexp(out)", "die", GUARDS, ()),
    ("S3 dock_hub does not set the active root", DOCK_HUB,
     "        set_active_graph_root(root_path)", "        pass", "die", GUARDS, ()),
    ("S5 explicit graph_root ignored", CW,
     "    root = graph_root if graph_root is not None else active_graph_root()",
     "    root = active_graph_root()", "die", GUARDS, ()),
    ("S6 CLI does not set the active root", "kicadstamp/cli_main.py",
     '    set_active_graph_root(getattr(args, "config", None))',
     "    pass", "die", GUARDS, ()),
    ("N1a stamp not run at stage_write", CW,
     "        if current_format() >= 3:\n            data = _stamp_format3_for_write(path, data, None)",
     "        if False:\n            data = _stamp_format3_for_write(path, data, None)", "die", GUARDS, ()),
    ("N1b check reads disk again", FORMAT3,
     "        _f3_collect(cached_file_read(Path(f), _load_config_file), f, records, folders, refs)",
     "        _f3_collect(_load_config_file(Path(f)), f, records, folders, refs)", "die", GUARDS, ()),
    ("N1c index reads disk again", FORMAT3,
     "        _index_dict(index, data if is_current else cached_file_read(Path(f), _load_config_file), f)",
     "        _index_dict(index, data if is_current else _load_config_file(Path(f)), f)", "die", GUARDS, ()),
    ("H2a uuid inheritance off", FORMAT3,
     "        inherited_uuid = inherited.get((section, name)) if name is not None else None",
     "        inherited_uuid = None", "die", GUARDS, ()),
    ("H2c inheritance without the section check", FORMAT3,
     "        inherited_uuid = inherited.get((section, name)) if name is not None else None",
     "        inherited_uuid = next((u for (s, n), u in inherited.items() if n == name), None)",
     "die", GUARDS, ()),
    ("S2w writer ignores the disabled flag", CW,
     "    if is_stamp_disabled():\n        return data",
     "    if False:\n        return data", "die", GUARDS, ()),
    ("S3c root_changed not connected", DOCK_HUB,
     "        self.root_metadata_dock.root_changed.connect(self._on_root_changed_for_working_set)",
     "        pass", "die", GUARDS, ()),
    ("S6m main does not set the active root", "kicadstamp/cli_main.py",
     "    set_cli_active_root(args)", "    pass", "die", GUARDS, ()),
    ("C1 cosmetic comment (control)", FORMAT3,
     "    index = _Format3Index()",
     "    index = _Format3Index()  # control", "survive", GUARDS, ()),
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
