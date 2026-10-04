# kicadstamp/diagnostics/claude_mutations_accept_uuid_u3_4_2026_10_04.py
"""Claude's acceptance of U3.4 (0b343c7, plan §7 Б: the through cell
tests/config/test_format3_through_real_step.py — a format-2 graph applied, lifted
by the PRODUCT open path through the REAL 2->3 step, applied again: 0/0),
2026-10-04. Grown from claude_mutations_accept_uuid_u3_3_2026_10_04.py (rule 38):
the machinery and ALL its rows are kept, plus the T rows, each guarded by the
through file ALONE so a kill proves the NEW cells see it:

  * T1/T2 — load_config no longer lifts the graph / the registries.
  * T3 — every open lifts every config file again (idempotency lost).
  * T3r — every open lifts the registry again (idempotency lost).
  * T4 — the registry record part falls back to the NAME.
  * T5 — the 2->3 step is not registered.
  * T6 — net_traces drops out of the step's record walk.
  * T7/T8 — the step LOSES the sheet_templates / tree_instances block: the cell
    claims both forms; a form whose copies collapse onto another form's key is
    not covered.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U3.3 (353003c + 9fab1ec, plan §7, Р-У3.5: refuse the
on-disk format upgrade on a failed .bak; cells Н4/Н5 of the stub), 2026-10-04.
Grown from
claude_mutations_accept_uuid_u3_2_2026_10_04.py (rule 38): the machinery and ALL
its rows are kept — Z2b survived the U3.2 acceptance and MUST die now (Н4) —
plus:

  * Z5 — a section dropped from the stub's own table (Н5).
  * B1/B2 — a failed .bak logged and skipped again (config / registry).
  * B3 — a WRITE failure after a good .bak becomes fatal (rule 4 lost).
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of the U3.1 rework (357501e, findings Н1–Н3 + Х) and U3.2
(ab23c5e, plan §7: the format-3 test stub on the product migration seed),
2026-10-04. Grown from claude_mutations_accept_uuid_u3_1_2026_10_04.py (rule 38):
the machinery and ALL its rows are kept — Y6b and Y10–Y13 survived the U3.1
acceptance and MUST die now (cells Н1–Н3); the rest must stay dead. New rows on
the stub (tests/fakes/format3.py), guarded by every test file that mints with it:

  * Z1 — det_uuid back on its own private seed (the old `…00ab` namespace).
  * Z2 — the stub mints no folder rows at all.
  * Z2b — the stub mints folder rows for the dict sections only (list-section
    names with a `/` get no row).
  * Z2c — the stub CLOBBERS a folder row passed in (the merge promise).
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U3.1 (56aaac0, plan §7: the format 2->3 converter step
and the single R-1 seed builder), 2026-10-04. Grown from
claude_mutations_accept_uuid_u5_tail_2026_10_04.py (rule 38): the machinery is
kept, the guards and rows are new (a different subject). Rows = the U3.1 lines
of plan §7 "Строки мутаций", plus what the Demon's own rig does not cover:

  * Y1 — the record seed carries the file path (the cross-file reference dangles).
  * Y2 — a reference seeded from the wrong target section.
  * Y3/Y3r — a record / a reference that already has a uuid is reseeded.
  * Y4/Y4b — one §0 section dropped from the record stamp (entities / chains).
  * Y5/Y5b — one reference form dropped (points targets / tree node -> entities).
  * Y6/Y6b — folder rows not created / an existing folder row overwritten.
  * Y7–Y7d — В36: no name minted / off-by-one position / taken name not
    suffixed / WARNING downgraded.
  * Y8 — a node without kind is let through (guessed).
  * Y9 — the step not registered in STEPS.
  * Y10 — the deep copy dropped (the caller's dict is mutated).
  * Y11–Y13 — the seed itself drifts: separator / the folder: marker / the
    namespace. Live UUIDs must be stable FOREVER — a cell must pin a literal.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U5.5 (afc9c5d, plan §6: the display, K11b and the U5.4
acceptance tail), 2026-10-04. Grown from
claude_mutations_accept_uuid_u5_4_2026_10_04.py (rule 38): the machinery is
kept; the guard list now STARTS with test_registry_upgrade_on_disk.py (the
import-cycle fix is what lets it collect first); rows:

  * V1 — find_live_copper bypasses the one net-trace key builder (K11b).
  * V2 — the builder's template part left on the name.
  * V3/V4 — copper_select shows the UUID (the anchor part / the template part).
  * V5/V6 — copper_select / placer back on a private registry-path guess.
  * V7 — registries_empty_for lets the unlifted-registry fatal escape.
  * V8 — the registry <-> placement import cycle reintroduced.
  * H2/H6/H11 — the U5.4 survivors, now with cells.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U5.4 + the Н3 rework (86f41e1 + eb6360f, plan §6:
registry lift on open, an unlifted registry refused, one registry-path decision),
2026-10-04. Grown from claude_mutations_accept_uuid_u5_3_2026_10_04.py (rule 38):
the machinery is kept, the guards and rows are new:

  * H1/H2 — the refusal unhooked from the via / track loader.
  * H3 — the refusal itself off (schema 1 passes it).
  * H4 — the format-3 reader accepts schema 1 again.
  * H5/H6 — the lift / the apply ignore an explicit registry_path: (the two
    consumers answer "which file" differently again — the Н3 path half).
  * H7 — the lift writes no .bak.
  * H8 — only the via registry is lifted.
  * H9 — the lift call removed from load_config (the main equivalence cell).
  * H10 — the lift's gate `< 2` (format 2 files lifted).
  * H11 — registries_empty_for back on the default paths.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U5.3 (d4837b2, plan §6: registry keys carry the record
UUID under the format-3 gate), 2026-10-04. Grown from
claude_mutations_accept_uuid_u5_2_2026_10_04.py (rule 38): the machinery is
kept, the guards and rows are new:

  * K1 — Р-У5.7 off: a record without a uuid falls back to its name.
  * K2 — the helper's gate `>= 2`.
  * K3–K7 — one builder left on the name: clone `name:`, `point:`, entity
    `name:`, `thermal:`, `net:`.
  * K8/K9/K11 — the template_name part left on the name: clone/nested cell,
    chain spoke cell, net_trace identity (plan + match/adopt).
  * K10 — _to_clone drops the Entity's uuid (the transient clone's key splits
    from entity_anchor_id).
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U5.2 (c067ec3, plan §6: derived UUIDs of generated
template copies), 2026-10-04. Grown from
claude_mutations_accept_registry_nested_prefix_2026_10_04.py (rule 38): the
machinery is kept, the guards and rows are new:

  * D1 — a tree_instances copy keeps the ORIGINAL's uuid (copies glue together).
  * D2 — NS_DERIVED = NS_MIGRATION.
  * D3/D3n — the generated node's ref_uuid left on the original (entity / net_trace).
  * D4 — a net_trace copy keeps the original's uuid.
  * D5 — a sheet_templates copy gets no uuid at all.
  * D6/D9 — the seed drops the sheet / the instance name (copies of different
    sheets / instances collide).
  * D7 — the post-expansion uniqueness check unhooked.
  * D8 — the sheet_templates gate `>= 2`.
  * B4 — the U5.1b survivor (the _resolve_one_level call site bypasses
    nested_anchor_id): the planner-key cell of this step must kill it now.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U5.1b (2395d6b, plan §6 acceptance of U5.0/U5.1): a
nested cell placement's anchor_id protected from an --only prune, 2026-10-04.
Grown from claude_mutations_accept_registry_point_prefix_2026_10_04.py (rule 38):
the machinery and the guards are kept, the rows are new:

  * B1/B2 — _compute_all_anchor_ids back to the OUTER id only, for clones / for
    entities (the bug itself, per source).
  * B3 — the recursion stops at depth 1 (a nested-in-nested key unprotected).
  * B4 — the call site in _resolve_one_level stops using the shared builder (the
    real keys and the protection set drift apart).
  * B5 — the cycle guard off (an in-memory cycle must not hang the protection).
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of U5.1 (e9b0dd3, plan §6): the --only prune protection
of the point: anchor, 2026-10-04. Grown from
claude_mutations_accept_uuid_u4_rework2_2026_10_03.py (rule 38): the machinery
is kept, the rows are new (a different subject):

  * R1 — `point:` dropped from PROTECTED_ANCHOR_PREFIXES again.
  * R2 — reconcile reads the old inline tuple instead of the constant (the
    constant and the code drift apart).
  * R3 — `anchor:` dropped from the constant (a long-working form: the
    enumeration of builders must catch any prefix, not only the new one).
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's re-acceptance of U4 after the second rework (6f8963b, finding Н2),
2026-10-03. Grown from claude_mutations_accept_uuid_u4_rework_2026_10_03.py
(rule 38): same machinery and rows (P1 re-aimed — the rework rewrote step 1),
plus:

  * H2a — an in-place edit no longer inherits: every uuid-less record gets uuid4.
  * H2c — the inheritance ignores the section (a same name in another section
    hands its uuid over).
  * H2w — the previous content read from the disk only, not the working set (an
    unsaved record edited again from a form loses its staged uuid).

S2 / S3c / S6m survived the previous acceptance; they must die now.

Previous rig docstring follows.
Claude's re-acceptance of U4 after the Н1 rework (e2dd548), 2026-10-03.
Grown from claude_mutations_accept_uuid_u4_2026_10_03.py (rule 38): same machinery
and rows (P5 re-aimed — the rework rewrote that line), the new wiring test file
added to the guards, plus:

  * N1a — the stamp not run at stage time (the working set holds uuid-less records).
  * N1b/N1c — the format-3 check / the stamp index read the disk again, not the
    working set.
  * S3c — the slot that sets the active root disconnected from root_changed (the
    new S3 cell calls the slot directly).
  * S6m — main() no longer calls set_cli_active_root (the new S6 cell calls the
    helper directly).
  * W1 — the ValidationError -> OSError wrap removed (now shared by the stage and
    the serializer paths; a bare ValidationError aborts PyQt6 from a dock slot).

Previous rig docstring follows.
Claude's acceptance of the UUID step U4 (writers, 77bfd4c, plan §5), 2026-10-03.
Grown from claude_mutations_accept_self_anchor_guard_2026_10_03.py (rule 38): the
machinery is kept, the rows are new:

  * S1–S6 — wiring: the stamp gate; profile_copy's stamp-off ignored; the GUI /
    the CLI not setting the active root; set_reference keeping the stale uuid;
    an explicit graph_root ignored.
  * P1–P5 — the stamp itself: no uuid4 for a new record; a new reference not
    resolved / its refusal off; the hint not rewritten / the dangling refusal
    off; folders not minted / В39 reuse off; the file being written indexed
    from disk instead of from the dict being written.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's acceptance of the tree self-anchor drift guard (bb91957,
plan_2026_10_03_tree_self_anchor_drift_guard Ш1–Ш3), 2026-10-03. Grown from
claude_mutations_accept_uuid_u2_review_2026_10_03.py (rule 38): the machinery
is kept, the rows are new (a different subject):

  * W1/W2 — the guard unhooked from materialize_entity_placements; ERROR
    downgraded to a warning.
  * D1–D4 — the drift test: an embedded match no longer excluded; the angle
    half / the position half ignored; the tree's own effective base (angle +
    inner point) ignored — the live ch*_dac_buf false positive.
  * H1–H3 — hint branches: anchor_role, node offset, the conditional self hint
    offered to a self-anchored tree.
  * K1/K2 — cascade: the refused tree's names not dropped; dropped but not
    reported as skipped.
  * C1 — a cosmetic comment edit. MUST survive.

Previous rig docstring follows.
Claude's re-acceptance of U2 after the К1/К2 rework (a338e76), 2026-10-03.
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
# NOT test_registry.py: the only file of that name is under tests/integration_tests
# and WRITES TO THE LIVE BOARD.
# test_registry_upgrade_on_disk.py FIRST: since the U5.5 cycle fix it must
# collect on its own (row V8 checks exactly that).
_T_BASENAMES = ["test_config_format3_migration.py", "test_config_format3.py",
                "test_config_format3_wiring.py", "test_config_format3_writer.py",
                "test_config_format_version.py", "test_config_format_upgrade_on_disk.py"]

GUARDS = list(_T_BASENAMES)
# The stub is minted by more files than the U3.1 subject touched: every file that
# imports mint_format3* guards the Z rows.
STUB_GUARDS = GUARDS + ["test_format3_equivalence.py", "test_registry_equivalence.py",
                        "test_registry_upgrade_on_disk.py"]
# U3.3: the `.bak` refusal lives in the config sweep AND the registry sweep; the
# tree_instances file caught the Demon's own regression (rule 39).
BAK_GUARDS = GUARDS + ["test_registry_upgrade_on_disk.py", "test_tree_instances.py",
                       "test_registry_equivalence.py"]

# U3.4: the through cell ALONE — a T kill must come from the new cells.
THROUGH = ["test_format3_through_real_step.py"]

FV = "kicadstamp/config/format_version.py"
UU = "kicadstamp/config/uuids.py"
ST = "tests/fakes/format3.py"
UD = "kicadstamp/config/upgrade_on_disk.py"
RU = "kicadstamp/config/registry_upgrade.py"
LD = "kicadstamp/config/loader.py"
RG = "kicadstamp/registry.py"
F3 = "kicadstamp/config/format3.py"
_STEP_END = "    return data\n\n\n# version -> the converter"

_REC = "        if uuid or name is None:\n            continue"
_REF = "        if ref.uuid:\n            continue"

MUTATIONS = [
    ("Y1 record seed carries the file path", FV,
     '            holder["uuid"] = migration_uuid(section, name)',
     '            holder["uuid"] = migration_uuid(section, f"{ctx.path}|{name}")', "die", GUARDS, ()),
    ("Y2 reference seeded from a wrong section", FV,
     "ref.holder[ref.uuid_field] = migration_uuid(ref.target, hint)",
     'ref.holder[ref.uuid_field] = migration_uuid("cells", hint)', "die", GUARDS, ()),
    ("Y3 record with a uuid reseeded", FV, _REC,
     "        if name is None:\n            continue", "die", GUARDS, ()),
    ("Y3r reference with a uuid reseeded", FV, _REF,
     "        if False:\n            continue", "die", GUARDS, ()),
    ("Y4 entities dropped from the record stamp", FV, _REC,
     '        if uuid or name is None or section == "entities":\n            continue', "die", GUARDS, ()),
    ("Y4b chains dropped from the record stamp", FV, _REC,
     '        if uuid or name is None or section == "chains":\n            continue', "die", GUARDS, ()),
    ("Y5 points-target references dropped", FV, _REF,
     '        if ref.uuid or ref.target == "points":\n            continue', "die", GUARDS, ()),
    ("Y5b entity-target references dropped", FV, _REF,
     '        if ref.uuid or ref.target == "entities":\n            continue', "die", GUARDS, ()),
    ("Y6 folder rows not created", FV,
     "    if rows:\n        table = ", "    if False:\n        table = ", "die", GUARDS, ()),
    ("Y6b existing folder row overwritten", FV,
     "            section_table.setdefault(prefix, folder_uuid)",
     "            section_table[prefix] = folder_uuid", "die", GUARDS, ()),
    ("Y7 unnamed record not named", FV,
     "        _mint_unnamed(data, section, ctx.path)", "        pass", "die", GUARDS, ()),
    ("Y7b minted number off by one", FV,
     'base = f"{singular}_{i + 1:03d}"', 'base = f"{singular}_{i:03d}"', "die", GUARDS, ()),
    ("Y7c taken name not suffixed", FV,
     "        while name in used:", "        while False:", "die", GUARDS, ()),
    ("Y7d mint WARNING downgraded", FV,
     "        logger.warning(_(\n            \"format 2->3:", "        logger.debug(_(\n            \"format 2->3:",
     "die", GUARDS, ()),
    ("Y8 node without kind let through", FV,
     '            if (isinstance(node, dict) and node.get("kind") is None',
     '            if (False and node.get("kind") is None', "die", GUARDS, ()),
    ("Y9 step not registered", FV, "    2: _step_2_to_3,\n", "", "die", GUARDS, ()),
    ("Y10 deep copy dropped", FV,
     "    data = copy.deepcopy(data)\n\n    # Р-У3.3", "    pass\n\n    # Р-У3.3", "die", GUARDS, ()),
    ("Y11 seed separator drifts", UU,
     'uuid5(NS_MIGRATION, f"{section}|{full_name}")', 'uuid5(NS_MIGRATION, f"{section}:{full_name}")',
     "die", GUARDS, ()),
    ("Y12 folder: marker dropped", UU,
     'f"{section}|folder:{path}"', 'f"{section}|{path}"', "die", GUARDS, ()),
    ("Y13 seed namespace swapped", UU,
     'uuid5(NS_MIGRATION, f"{section}|{full_name}")', 'uuid5(NS_DERIVED, f"{section}|{full_name}")',
     "die", GUARDS, ()),
    ("Z1 stub det_uuid on its own seed", ST,
     "        return migration_uuid(section, name)\n",
     "        import uuid as _u\n"
     "        return str(_u.uuid5(_u.UUID(\"00000000-0000-0000-0000-0000000000ab\"), str(n)))\n",
     "die", STUB_GUARDS, ()),
    ("Z2 stub mints no folder rows", ST,
     "        if not rows:\n            continue", "        if True:\n            continue",
     "die", STUB_GUARDS, ()),
    ("Z2b stub skips list-section folder rows", ST,
     '_folder_prefixes(rec.get("name") or "")', '_folder_prefixes("")',
     "die", STUB_GUARDS, ()),
    ("Z2c stub clobbers a folder row passed in", ST,
     "                section_table.setdefault(prefix, folder_uuid)",
     "                section_table[prefix] = folder_uuid",
     "die", STUB_GUARDS, ()),
    ("Z5 stub table drops a section", ST,
     '                  "coordinate_placements", "net_traces", "entities", "imprints")',
     '                  "coordinate_placements", "net_traces", "entities")', "die", STUB_GUARDS, ()),
    ("B1 config .bak failure logged and skipped", UD,
     "            backup = backup_file(path)\n        except OSError as e:\n            raise ValidationError(",
     "            backup = backup_file(path)\n        except OSError as e:\n            continue\n            raise ValidationError(",
     "die", BAK_GUARDS, ()),
    ("B2 registry .bak failure logged and skipped", RU,
     "            backup = backup_file(path)\n        except OSError as e:\n            raise ValueError(",
     "            backup = backup_file(path)\n        except OSError as e:\n            continue\n            raise ValueError(",
     "die", BAK_GUARDS, ()),
    ("B3 write failure after a good .bak is fatal", UD,
     "            write_config_file(path, content, backup=False, serialized_text=text)\n        except (OSError, ValidationError) as e:",
     "            write_config_file(path, content, backup=False, serialized_text=text)\n        except ():",
     "die", BAK_GUARDS, ()),
    ("T1 load_config does not lift the graph", LD,
     "    upgrade_graph_on_disk(path)\n    result =", "    pass\n    result =", "die", THROUGH, ()),
    ("T2 load_config does not lift the registries", LD,
     "    upgrade_registries_on_disk(path, result[0])", "    pass", "die", THROUGH, ()),
    ("T3 every open lifts every file again", UD,
     "        if read_version(path) < current_format():", "        if True:", "die", THROUGH, ()),
    ("T3r every open lifts the registry again", RU,
     "        if schemas[path] != REGISTRY_SCHEMA_VERSION:\n            # None",
     "        if schemas[path] is None:\n            # None", "die", THROUGH, ()),
    ("T4 registry record part is the name", RG,
     "        return uuid\n", "        return name\n", "die", THROUGH, ()),
    ("T5 2->3 step not registered", FV, "    2: _step_2_to_3,\n", "", "die", THROUGH, ()),
    ("T6 net_traces out of the step record walk", F3,
     '                     "coordinate_placements", "net_traces", "entities",\n',
     '                     "coordinate_placements", "entities",\n', "die", THROUGH, ()),
    ("T7 step loses sheet_templates", FV, _STEP_END,
     '    data.pop("sheet_templates", None)\n' + _STEP_END, "die", THROUGH, ()),
    ("T8 step loses tree_instances", FV, _STEP_END,
     '    data.pop("tree_instances", None)\n' + _STEP_END, "die", THROUGH, ()),
    ("C1 cosmetic comment (control)", UU,
     "def migration_uuid(section: str, full_name: str) -> str:",
     "def migration_uuid(section: str, full_name: str) -> str:  # control", "survive", GUARDS, ()),
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
        # -n auto (pytest-xdist, 2026-10-03): the guard set 16 s -> 6 s per row.
        r = subprocess.run([PY_BIN, "-m", "pytest", *paths, *extra, "-q", "--no-header",
                            "-p", "no:cacheprovider", "-n", "auto"],
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
