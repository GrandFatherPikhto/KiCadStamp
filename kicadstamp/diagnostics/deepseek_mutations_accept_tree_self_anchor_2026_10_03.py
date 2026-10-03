# kicadstamp/diagnostics/deepseek_mutations_accept_tree_self_anchor_2026_10_03.py
"""Acceptance rig for the self-anchor drift guard (plan_2026_10_03_
tree_self_anchor_drift_guard, Ш3.1). Grown from
claude_mutations_accept_uuid_u2_review_2026_10_03.py (rule 38): the SAME
machinery — basename-resolved T, `_drop_pyc`, the `original.count(old) != 1`
refusal (a template that is not unique means the rig cannot know which line it
edited, so the row is INVALID rather than green), "ПРОМАХ" when a non-zero exit
brought back no FAILED line (a run that did not collect the tests is not a
verdict), and a control row that MUST survive.

WHAT IS BEING PROVEN. Ш1 added `find_tree_self_anchor_drifts` /
`check_tree_self_anchor_drift` in kicadstamp/trees.py, the refusal in
`materialize_entity_placements` (entity_placement.py) and the drop-with-reason in
the two curated cascade entry points (gui/docks/cascade.py). Rows:

  * M1 — the condition itself weakened to "always ok".
  * M2 — the reported shift sign flipped (a guard that only tests "nonzero"
    would pass; the cells assert the VECTOR).
  * M3 — the tree's own angle and inner point dropped from the composition:
    exactly the regression that produced a live FALSE POSITIVE on ch0_dac_buf.
  * M4 — the embedded-tree exclusion (match.tree is not tree) dropped: the
    module-crossing control must go red (Ш0 row C2).
  * M5/M6 — the hint branches broken: the mount cure dropped, and the
    (anchor (self ...)) suggestion offered unconditionally.
  * M7 — only the FIRST violation of a tree reported.
  * M8 — the guard call removed from the materializer (the refusal off).
  * M9 — the cascade drop removed: an inert run reports ok again.
  * M10 — the subject-role rule relaxed to `anchor_identity`'s third fallback
    (the first component): the guard then PREEMPTS a genuine config fatal.
  * M11 — the drift tolerance blown up to swallow every real drift.
  * M13 — the refusal logged at WARNING instead of ERROR (the plan's "red line").
  * M0 — a cosmetic comment edit. MUST survive.

Run it with the repo's own interpreter (the rig picks `.venv/bin/python`):
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m kicadstamp.diagnostics.\
deepseek_mutations_accept_tree_self_anchor_2026_10_03
An optional row-name prefix filter takes the rest of argv; a filter matching NO
row is a refusal, not an empty table.
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
GUARDS = ["test_tree_self_anchor_drift_guard.py", "test_entity_placement.py",
          "test_cascade.py"]

TREES = "kicadstamp/trees.py"
EP = "kicadstamp/placement/entity_placement.py"
CASCADE = "gui/docks/cascade.py"

MUTATIONS = [
    ("M1 condition weakened (always ok)", TREES,
     "        if drift.position_drifts or drift.angle_drifts:\n",
     "        if False:\n", "die", GUARDS, ()),
    ("M2 reported shift sign flipped", TREES,
     "        offset=Vector2.from_xy(placed.position.x, placed.position.y),",
     "        offset=Vector2.from_xy(-placed.position.x, -placed.position.y),",
     "die", GUARDS, ()),
    ("M3 tree's own angle/pivot dropped", TREES,
     "        base_pos, base_rot = tree_effective_base(\n"
     "            tree, Vector2.from_xy(0, 0), 0.0, forest)\n",
     "        base_pos, base_rot = Vector2.from_xy(0, 0), 0.0\n", "die", GUARDS, ()),
    ("M4 embedded-tree exclusion dropped", TREES,
     "        if match.tree is not tree:\n            continue\n",
     "        if False:\n            continue\n", "die", GUARDS, ()),
    ("M5 mount-cure hint dropped", TREES,
     "    if drift.mount_differs_from_slot:\n", "    if False:\n", "die", GUARDS, ()),
    ("M6 self hint offered unconditionally", TREES,
     "    if (not drift.angle_drifts and not drift.node_has_offset\n"
     "            and _tree_is_role_anchored(drift.tree)\n"
     "            and _self_switch_fixes_mount(cfg, drift)):\n",
     "    if True:\n", "die", GUARDS, ()),
    ("M7 only the first violation reported", TREES,
     "        for d in drifts)", "        for d in drifts[:1])", "die", GUARDS, ()),
    ("M8 materializer refusal off", EP,
     "        drift = (check_tree_self_anchor_drift(cfg, plain_tree)\n"
     "                 if plain_tree is not None else None)\n",
     "        drift = None\n", "die", GUARDS, ()),
    ("M9 cascade drop removed (inert ok, FOREST path)", CASCADE,
     "    skips, reasons = _self_anchor_drift_skips(cfg, linked, selected_refs)\n"
     "    for reason in reasons.values():\n"
     "        logger.error(reason)\n"
     "    results: List[Tuple[str, bool, Optional[str]]] = _skipped_results(skips, names)\n"
     "    names = [n for n in names if n not in skips]\n",
     "    skips, reasons = _self_anchor_drift_skips(cfg, linked, selected_refs)\n"
     "    for reason in reasons.values():\n"
     "        logger.error(reason)\n"
     "    results: List[Tuple[str, bool, Optional[str]]] = _skipped_results(skips, names)\n",
     "die", GUARDS, ()),
    ("M9b cascade drop removed (inert ok, SINGLE-TREE path)", CASCADE,
     "    skips, reasons = _self_anchor_drift_skips(cfg, [tree], selected_refs)\n"
     "    for reason in reasons.values():\n"
     "        logger.error(reason)\n"
     "    results: List[Tuple[str, bool, Optional[str]]] = _skipped_results(skips, names)\n"
     "    names = [n for n in names if n not in skips]\n",
     "    skips, reasons = _self_anchor_drift_skips(cfg, [tree], selected_refs)\n"
     "    for reason in reasons.values():\n"
     "        logger.error(reason)\n"
     "    results: List[Tuple[str, bool, Optional[str]]] = _skipped_results(skips, names)\n",
     "die", GUARDS, ()),
    ("M10 subject rule falls back to the first component", TREES,
     "    if len(zero) != 1:\n        return None\n    return zero[0].role\n",
     "    return (zero or cell.components)[0].role\n", "die", GUARDS, ()),
    ("M11 drift tolerance swallows every drift", TREES,
     "_DRIFT_TOL_NM = 10\n", "_DRIFT_TOL_NM = 10_000_000_000\n", "die", GUARDS, ()),
    ("M13 refusal logged at WARNING", EP,
     "            logger.error(drift)\n", "            logger.warning(drift)\n",
     "die", GUARDS, ()),
    ("M0 cosmetic comment (control)", TREES,
     "_DRIFT_TOL_DEG = 1e-6\n", "_DRIFT_TOL_DEG = 1e-6  # control\n",
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
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q",
                            "--no-header", "-p", "no:cacheprovider"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    return r.returncode, r.stdout + r.stderr


def main():
    # Optional filters: `… M1` runs only the rows whose name starts with one of
    # them. A filter that matches NOTHING is a refusal, not an empty table.
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
            print(f"{name:<46} {expect:<9} {verdict:<16} {detail}{flag}")
        finally:
            f.write_text(original, encoding="utf-8")
            _drop_pyc(rel)


if __name__ == "__main__":
    main()
