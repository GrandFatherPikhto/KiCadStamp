# kicadstamp/diagnostics/claude_mutations_accept_uuid_u2_review_2026_10_03.py
"""Claude's re-acceptance of U2 after the К1/К2 rework (a338e76), 2026-10-03.
Grown from claude_mutations_accept_uuid_u2_2026_10_03.py (rule 38): same machinery
and rows, plus:

  * K1 — the deep copy before the normalization pass removed: the pass mutates
    the objects cached_file_read shares on a hit again.
  * K2 — the В33 short spellings built from every identity again (net names
    included), not only from a record's `name:`.

Previous rig docstring follows.
Claude's acceptance of the UUID step U2 (load-time resolve by UUID, plan §4),
2026-10-03, on the snapshot 7326572 of the uncommitted working tree. Grown from
claude_mutations_accept_uuid_u1_final_2026_10_03.py (rule 38): same machinery;
the U1 rows kept (R8 and T2 re-aimed — U2 rewrote those lines), plus U2 rows:

  * M1/M2 — the normalization pass off (the assignment / the call).
  * M3 — the pass moved AFTER the template expansions (copies keep the hint).
  * M4 — the "reference without a UUID" fatal off (У2.2).
  * X1–X3 — one new reference form dropped: tree_instances anchor point,
    sheet_templates nested placements, a tree's own anchor point.
  * A1–A3 — tree anchor point: parser / writer drop the uuid; sheet_templates
    refuses the template's `uuid` again.
  * B1–B3 — --only by В33: several -> take the first; exact no longer wins;
    a single short match not taken.

Previous rig docstring follows.
Claude's third acceptance of U1 (28398bc, the narrow rework П1–П3), 2026-10-03.
Grown from claude_mutations_accept_uuid_u1_review_2026_10_03.py (rule 38): same
machinery and rows; R1/N1 re-aimed at the rewritten record tuples (the old
patterns no longer match — the rework changed them), plus:

  * P2 — the "record without a name" fatal off (П2).
  * D1 — the diamond dedupe in _f3_files off: a file reached twice would feed
    its records twice and false-fatal as a duplicate name.

Previous rig docstring follows.
Claude's re-acceptance of the UUID step U1 after the rework (8515d21), 2026-10-03.
Grown from claude_mutations_accept_uuid_u1_2026_10_02.py (rule 38): same machinery;
the rows are re-aimed at the rewritten checks (per-file collection, graph-wide
names/UUIDs, folders merge — В39) and extended to every finding Н2–Н8:

  * G1/G2 — the gate too strict / too loose (G2 also guarded by a format-2 test).
  * R1–R3, R2f — no uuid / duplicate record uuid / dangling / folder-vs-record uuid.
  * R4–R9 — one reference form dropped: spokes, nested cell placements,
    entity.imprint, points->points, tree node refs, nested tree nodes.
  * N1/N2 — the duplicate full name fatal off; the free sections not collected.
  * F1–F3 — folder uuid mismatch off; include: refuses `folders` again; the
    dangling fatal names the ROOT instead of the referencing file (Н7).
  * T1–T4 — the tree parser drops the ref uuid / accepts any extra child / lets a
    local kind carry a uuid; the tree writer drops the ref uuid.
  * W1 — a folders-only section is not written (the writer hole found in rework).
  * S1 — the stub 2->3 lift step back to a uuid-less cell.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of the UUID step U1 (e8c2c36, plan_2026_10_02_uuid_format_2_to_3
§У1), 2026-10-02. Grown from claude_mutations_accept_stale_live_review_2026_10_02.py
(rule 38): the same machinery — basename-resolved T, `_drop_pyc`, the
`original.count(old) != 1` refusal, "ПРОМАХ" when nothing red came back, a refused
filter that matches no row, and a control that MUST survive.

WHAT IS BEING PROVEN. U1 adds the format-3 grammar (record uuid, nested reference
uuid, per-section folder table), the model fields (variant B: `<field>_uuid`
beside the string), and the load checks in kicadstamp/config/loader.py, gated on
current_format() >= 3 (after the lift). Rows:

  * G1/G2 — the gate: too strict (`> 3`, checks never run) and too loose (`>= 2`,
    checks wake in the format-2 product and every live config dies).
  * R1–R3 — each load fatal switched off (no uuid / duplicate uuid / dangling).
  * R4–R6 — one reference form dropped from the dangling check (chain spokes,
    nested cell placements, entity.imprint): does any cell exercise that form?
  * P1/P2 — the parser loses the reference uuid / never sees the folder table.
  * J1/J2 — the JSON loader drops entity.cell_uuid / tva.anchor_point_uuid.
  * S1 — the stub 2->3 step back to a UUID-less cell: the two lift cells must go
    red, which is what proves the gate runs AFTER the lift.
  * C1 — a cosmetic comment edit. MUST survive.

A mutation that kills nothing is a miss, not a pass (rule 38): run with the
main checkout's interpreter; point it at another tree with
`KICADSTAMP_ACCEPT_ROOT`. An optional row-name prefix filter takes the rest of
argv.

Previous rig docstring follows.
Claude's acceptance of d198f43 (plan_2026_10_02_stale_live_list_after_prune),
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
_T_BASENAMES = ["test_config_format3.py", "test_config_format_version.py",
                "test_audit_cell_net_templates.py",
                "test_format3_normalize.py", "test_format3_equivalence.py",
                "test_cli_filters.py"]

GUARDS = ["test_config_format3.py", "test_config_format_version.py",
          "test_audit_cell_net_templates.py",
                "test_format3_normalize.py", "test_format3_equivalence.py",
                "test_cli_filters.py"]

LOADER = "kicadstamp/config/loader.py"
SEXP = "kicadstamp/config/sexp_format.py"
INCLUDES = "kicadstamp/config/includes.py"
TREES = "kicadstamp/trees.py"
FV_TEST = "tests/config/test_config_format_version.py"

MUTATIONS = [
    ("G1 gate too strict (> 3)", LOADER,
     "    if current_format() >= 3:", "    if current_format() > 3:", "die", GUARDS, ()),
    ("G2 gate too loose (>= 2)", LOADER,
     "    if current_format() >= 3:", "    if current_format() >= 2:", "die", GUARDS, ()),
    ("R1 record-without-uuid fatal off", LOADER,
     "    for section, name, uuid, f, i in records:\n        if not uuid:",
     "    for section, name, uuid, f, i in records:\n        if False:", "die", GUARDS, ()),
    ("R2 duplicate record uuid off", LOADER,
     "        if uuid in owner:\n", "        if False:\n", "die", GUARDS, ()),
    ("R2f folder-vs-record uuid off", LOADER,
     "        if uuid in owner and owner[uuid][0] != desc:", "        if False:", "die", GUARDS, ()),
    ("R3 dangling-reference fatal off", LOADER,
     "        if uuid not in uuids_by_section.get(target, set()):", "        if False:",
     "die", GUARDS, ()),
    ("R4 chain spoke refs not collected", LOADER,
     '            if section == "chains":', "            if False:", "die", GUARDS, ()),
    ("R5 nested cell placement refs not collected", LOADER,
     '        for ncp in cell.get("clone_placements") or []:', "        for ncp in []:",
     "die", GUARDS, ()),
    ("R6 entity.imprint ref not collected", LOADER,
     '_F3_REF_TARGET = {"cell": "cells", "imprint": "imprints", "anchor_point": "points"}',
     '_F3_REF_TARGET = {"cell": "cells", "anchor_point": "points"}', "die", GUARDS, ()),
    ("R7 points->points refs not collected", LOADER,
     '    for name, p in (data.get("points") or {}).items():',
     "    for name, p in {}.items():", "die", GUARDS, ()),
    ("R8 tree node refs not collected", LOADER,
     '            if target is not None and n.get("ref") is not None:',
     "            if False:", "die", GUARDS, ()),
    ("R9 nested tree nodes not walked", LOADER,
     '        _f3_walk_nodes(n.get("children"), out)', "        pass", "die", GUARDS, ()),
    ("N1 duplicate full name fatal off", LOADER,
     "        if key in name_src:", "        if False:", "die", GUARDS, ()),
    ("P2 record-without-a-name fatal off", LOADER,
     "        if name is None:\n            raise", "        if False:\n            raise",
     "die", GUARDS, ()),
    ("D1 diamond dedupe off", LOADER,
     "        if p not in seen:", "        if True:", "die", GUARDS, ()),
    ("N2 free sections not collected", LOADER,
     "    for section in _F3_FREE_SECTIONS:", "    for section in ():", "die", GUARDS, ()),
    ("F1 folder uuid mismatch off", LOADER,
     "        elif prev != uuid:", "        elif False:", "die", GUARDS, ()),
    ("F2 include: refuses folders again", INCLUDES,
     "and k not in ('include', 'folders'))", "and k not in ('include',))", "die", GUARDS, ()),
    ("F3 dangling fatal names the root (Н7)", LOADER,
     '"record").format(path=f, target=target)]))',
     '"record").format(path=root_path, target=target)]))', "die", GUARDS, ()),
    ("T1 tree parser drops the ref uuid", TREES,
     "        ref_uuid = sval(extra[1])", "        ref_uuid = None", "die", GUARDS, ()),
    ("T2 tree ref accepts any extra child", TREES,
     'and sval(extra[0]) == "uuid"):\n            _fatal(_("{location}: only a (uuid',
     'and sval(extra[0]) == "uuid"):\n            (lambda *a: None)(_("{location}: only a (uuid', "die", GUARDS, ()),
    ("T3 local kind may carry a uuid (s-expr)", TREES,
     "    if ref_uuid is not None and kind in _LOCAL_REF_KINDS:", "    if False:",
     "die", GUARDS, ()),
    ("T4 tree writer drops the ref uuid", TREES,
     "    if node.ref_uuid is not None:\n        ref_node.append",
     "    if False:\n        ref_node.append", "die", GUARDS, ()),
    ("W1 folders-only section not written", SEXP,
     "    for section, table in folders.items():\n        if section in emitted",
     "    for section, table in {}.items():\n        if section in emitted", "die", GUARDS, ()),
    ("S1 stub lift step back to a uuid-less cell", FV_TEST,
     '{"components": [], "uuid": det_uuid(f"lifted{n}")}', '{"components": []}',
     "die", GUARDS, ()),
    ("M1 normalization assignment off", LOADER,
     "        ref.holder[ref.name_field] = target_names[uuid]", "        pass", "die", GUARDS, ()),
    ("M2 normalization call off", LOADER,
     "        _normalize_format3_refs(data)\n    # sheet_templates",
     "        pass\n    # sheet_templates", "die", GUARDS, ()),
    ("M3 normalization after the expansions", LOADER,
     "        _normalize_format3_refs(data)\n    # sheet_templates: expansion (2026-08-16) — must run after include\n    # resolution (a template can live in an included subsystem file) and\n    # BEFORE any per-entry loader/duplicate-name check, so template-generated\n    # entries are indistinguishable from hand-written ones (see\n    # kicadstamp/config/sheet_templates.py).\n    data = expand_sheet_templates(data)\n    # tree_instances: expansion (2026-09-02, plan tree_instances) — dict-level,\n    # after include resolution AND after sheet_templates (a template may live\n    # in an included file, or reference a sheet-template-generated entity),\n    # BEFORE any per-entry loader/duplicate-name check, so the materialized\n    # trees/entities flow through the SAME _load_tree/_load_entity path as\n    # hand-written ones (rule 2 seen_refs + duplicate-name checks for free).\n    # Unlike expand_sheet_templates, the raw 'tree_instances:' key is KEPT —\n    # the loader parses it into cfg.tree_instances below (see\n    # kicadstamp/config/tree_instances.py).\n    data = expand_tree_instances(data)\n",
     "    # sheet_templates: expansion (2026-08-16) — must run after include\n    # resolution (a template can live in an included subsystem file) and\n    # BEFORE any per-entry loader/duplicate-name check, so template-generated\n    # entries are indistinguishable from hand-written ones (see\n    # kicadstamp/config/sheet_templates.py).\n    data = expand_sheet_templates(data)\n    # tree_instances: expansion (2026-09-02, plan tree_instances) — dict-level,\n    # after include resolution AND after sheet_templates (a template may live\n    # in an included file, or reference a sheet-template-generated entity),\n    # BEFORE any per-entry loader/duplicate-name check, so the materialized\n    # trees/entities flow through the SAME _load_tree/_load_entity path as\n    # hand-written ones (rule 2 seen_refs + duplicate-name checks for free).\n    # Unlike expand_sheet_templates, the raw 'tree_instances:' key is KEPT —\n    # the loader parses it into cfg.tree_instances below (see\n    # kicadstamp/config/tree_instances.py).\n    data = expand_tree_instances(data)\n    if current_format() >= 3:\n        _normalize_format3_refs(data)\n", "die", GUARDS, ()),
    ("M4 reference-without-uuid fatal off", LOADER,
     '        if uuid is None:\n            raise ValidationError(format_fatal_error(\n                _("format 3: {label} has no uuid")',
     '        if False:\n            raise ValidationError(format_fatal_error(\n                _("format 3: {label} has no uuid")',
     "die", GUARDS, ()),
    ("X1 tree_instances anchor point form dropped", LOADER,
     '    for ti in data.get("tree_instances") or []:', "    for ti in []:", "die", GUARDS, ()),
    ("X2 sheet_templates forms dropped", LOADER,
     '    for tpl_name, tpl in (data.get("sheet_templates") or {}).items():',
     "    for tpl_name, tpl in {}.items():", "die", GUARDS, ()),
    ("X3 tree anchor point form dropped", LOADER,
     '        anchor = tree.get("anchor")\n        if isinstance(anchor, dict) and anchor.get("point") is not None:',
     '        anchor = tree.get("anchor")\n        if False:', "die", GUARDS, ()),
    ("A1 tree anchor parser drops point uuid", TREES,
     "            point_uuid = sval(extra[1])", "            point_uuid = None", "die", GUARDS, ()),
    ("A2 tree anchor writer drops point uuid", TREES,
     "            if anchor.point_uuid is not None:\n                point_node.append",
     "            if False:\n                point_node.append", "die", GUARDS, ()),
    ("A3 sheet_templates refuses uuid again", "kicadstamp/config/sheet_templates.py",
     "_TEMPLATE_KEYS = ('sheets', 'uuid') + _TEMPLATE_SECTIONS",
     "_TEMPLATE_KEYS = ('sheets',) + _TEMPLATE_SECTIONS", "die", GUARDS, ()),
    ("B1 --only several -> first", "kicadstamp/apply_pipeline.py",
     "        elif len(matches) > 1:\n            ambiguous.append((token, sorted(matches)))",
     "        elif len(matches) > 1:\n            requested.add(matches[0])", "die", GUARDS, ()),
    ("B2 --only exact no longer wins", "kicadstamp/apply_pipeline.py",
     "        if token in name_set:", "        if token in name_set and token not in by_short:",
     "die", GUARDS, ()),
    ("B3 --only single short match not taken", "kicadstamp/apply_pipeline.py",
     "        if len(matches) == 1:", "        if False:", "die", GUARDS, ()),
    ("K1 normalization on the shared cache", LOADER,
     "        data = copy.deepcopy(data)\n        _normalize_format3_refs(data)",
     "        _normalize_format3_refs(data)", "die", GUARDS, ()),
    ("K2 short names from every identity", "kicadstamp/apply_pipeline.py",
     "    for full in sorted(short_sources):", "    for full in all_names:", "die", GUARDS, ()),
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
