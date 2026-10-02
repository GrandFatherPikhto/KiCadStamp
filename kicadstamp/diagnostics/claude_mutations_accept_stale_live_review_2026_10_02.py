# kicadstamp/diagnostics/claude_mutations_accept_stale_live_review_2026_10_02.py
"""Claude's acceptance of d198f43 (plan_2026_10_02_stale_live_list_after_prune),
2026-10-02. Grown from claude_mutations_accept_stale_live_2026_10_02.py (rule 38):
the parent's rows V1/T1/C1 kept, plus two rows on the HELPER itself, not only on
its call sites — H1 (the helper filters nothing) and X1 (the track phase fed the
VIA deletions: the two phases cross-wired).

Previous rig docstring follows.
Acceptance mutations for plan_2026_10_02_stale_live_list_after_prune.md
(stale live list after prune: the via/track positional pre-check must see the
board AFTER remove_by_ids), Claude, 2026-10-02. Grown from
claude_mutations_accept_dedupe_2026_10_02.py (rule 38): the same machinery —
basename-resolved T, `_drop_pyc` for the mutated file, the
`original.count(old) != 1` refusal, a verdict of "ПРОМАХ" when nothing red came
back, a refused filter that matches no row, and a control that MUST survive.

WHAT IS BEING PROVEN. The fix is `_live_items_after_deletion` applied in
kicadstamp/apply_pipeline.py Phase 2 (vias) and Phase 3 (tracks); the guards are
tests/pipeline/test_apply_pipeline_stale_live_after_delete.py.

  * V1 — the helper application removed in the via phase. This is the defect
    itself: the pre-check sees the pre-deletion list, so the via rename cells
    (cell rename / thermal array rename) lose their copper — 0 on the board —
    and go red.
  * T1 — the same in the track phase: the track rename cells lose their copper.
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

# Resolved by BASENAME under tests/ — a name found zero or two times is a
# finding, not a warning.
_T_BASENAMES = ["test_apply_pipeline_stale_live_after_delete.py"]

GUARDS = ["test_apply_pipeline_stale_live_after_delete.py"]

PIPELINE = "kicadstamp/apply_pipeline.py"

MUTATIONS = [
    ("V1 via phase: helper application removed", PIPELINE,
     "        live_vias = _live_items_after_deletion(live_vias, vias_to_delete)",
     "        live_vias = live_vias  # MUTATION: helper application removed",
     "die", GUARDS, ()),
    ("T1 track phase: helper application removed", PIPELINE,
     "        live_tracks = _live_items_after_deletion(live_tracks, tracks_to_delete)",
     "        live_tracks = live_tracks  # MUTATION: helper application removed",
     "die", GUARDS, ()),
    ("H1 helper filters nothing", PIPELINE,
     "    return [item for item in live_items if item.uuid not in deleted]",
     "    return list(live_items)", "die", GUARDS, ()),
    ("X1 track phase fed the via deletions", PIPELINE,
     "        live_tracks = _live_items_after_deletion(live_tracks, tracks_to_delete)",
     "        live_tracks = _live_items_after_deletion(live_tracks, vias_to_delete)",
     "die", GUARDS, ()),
    ("C1 cosmetic comment (control)", PIPELINE,
     "    if not deleted_uuids:",
     "    if not deleted_uuids:  # control",
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
    # Optional filters: `… V1` runs only the rows whose name starts with one of
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
