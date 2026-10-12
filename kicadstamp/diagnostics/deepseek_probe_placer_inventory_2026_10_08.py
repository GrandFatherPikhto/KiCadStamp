#!/usr/bin/env python3
# kicadstamp/diagnostics/deepseek_probe_placer_inventory_2026_10_08.py
"""Ч0 inventory probe for plan_2026_10_08_remove_placer.md.

Diagnostics only — reads the tree, mutates nothing. It answers three questions
the plan asks of Ч0:

  1. READERS — every place a loaded config's `clone_placements` is READ, and
     whether that read is (a) the in-memory CARRIER of the pipeline plan (the
     materialized entities live there, see "НЕ УДАЛЯТЬ" п.1 of the plan) or
     (b) the FILE SECTION that is going away. Carrier -> KEEP, section ->
     REMOVE. This is the task's main boundary; it is decided per site here, not
     guessed globally.
  2. PRODUCERS — every place the product WRITES a root `clone_placements:`
     (into a file or into a raw dict that is later written). After П1 the
     loader refuses a non-empty root section, so every producer must go too,
     or it would emit a file the loader rejects.
  3. PER-FILE CLASS — every file that mentions the Placer surface, with the
     class the plan wants: REMOVE / EDIT (mixed file) / KEEP (only "НЕ УДАЛЯТЬ"
     points) / PROBE (a diagnostics rig) / TEST.

The tables below are DATA (curated by hand from the Ч0 grep pass) so a re-run
reproduces the same verdicts, and a reviewer can diff them, rather than a
regex guessing intent. The raw per-line enumeration is printed too, and dumped
as a TSV under the (git-ignored) techdocs tree.

Run: .venv/bin/python kicadstamp/diagnostics/deepseek_probe_placer_inventory_2026_10_08.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Everything that names the Placer surface. `clone_placements` / `ClonePlacement`
# are deliberately included: the file section AND the in-memory model share the
# name, so every hit must be classified, never bulk-deleted.
PATTERNS = (
    "PlacerDock", "placer_dock", "_placer_page", "add_placer_requested",
    "Add placer", "_CoordinatePlacementForm", "coordinate_placements_picked",
    "clone_placements", "ClonePlacement", "clone_placement",
    "clone-plan", "plan_clone_placements", "clone_placements_to_dict",
    "dump_clone_placements", "upsert_clone_placement",
)

# Directories walked for the per-file table. `kicadstamp/diagnostics/` is
# listed so the rigs themselves are inventoried (class PROBE).
ROOTS = ("kicadstamp", "gui", "mcp_server", "tests", "tools",
         "docs", "locales", "techdocs/map")

# ── 1. READERS of a loaded config's `clone_placements` ──────────────────────
# Carrier receiver names: a `Config` object's field. These reads all survive —
# after the entity materialization the pipeline plan still lives in this field.
_CARRIER_RECEIVERS = r"(?:cfg|config|self\.cfg|self\._cfg|out_cfg|base_cfg|derived_cfg)"
# Nested receiver names: a `Cell`'s own `clone_placements` (a list of
# `CellPlacement`, "НЕ УДАЛЯТЬ" п.3) — a different mechanism, also survives.
_NESTED_RECEIVERS = r"(?:cell|nested_cell|cell_obj|tpl|template|sheet_template)"
_CARRIER_RE = re.compile(_CARRIER_RECEIVERS + r"\.clone_placements")
_NESTED_RE = re.compile(_NESTED_RECEIVERS + r"\.clone_placements")
# Raw-dict reads (`data["clone_placements"]`, `sections.get("clone_placements")`):
# these are the FILE SECTION, unless the receiver names a cell record.
_RAWDICT_RE = re.compile(
    r"(?:data|raw|out|sections|profile|payload|record|rec|entry)"
    r"\s*(?:\.get\(\s*)?[\"']clone_placements[\"']")
_CELLDICT_RE = re.compile(
    r"(?:cell|cell_data|rec|record|nested)\s*(?:\.get\(\s*)?[\"']clone_placements[\"']")

# ── 2. PRODUCERS of a root `clone_placements:` ──────────────────────────────
# (file, symbol, verdict, note). The plan named three hidden producers
# (convert_placements, clone-plan, sheet_templates). Anything here beyond those
# is a Ч0 finding the plan did not name — Denis asked for an explicit STOP on a
# fourth, so they are marked UNEXPECTED.
PRODUCERS = (
    ("tools/convert_placements.py", "convert_placements_file",
     "REMOVE (planned)", "one-off clone_placements -> entities migration"),
    ("kicadstamp/cli.py + cli_main.py + cloner/plan.py", "cmd_clone_plan / plan_clone_placements / clone_placements_to_dict",
     "REMOVE (planned)", "the `clone-plan` command writes a clone_placements block"),
    ("kicadstamp/config/sheet_templates.py", "expand_sheet_templates",
     "EDIT (planned)", "instantiates templates into data['clone_placements']"),
    ("kicadstamp/author.py + author_cli.py", "dump_clone_placements",
     "REMOVE (planned)", "write {'clone_placements': [...]} to a file"),
    ("kicadstamp/config_writer.py", "upsert_clone_placement",
     "REMOVE/EDIT", "shared write helper; caller is the Placer dock (Ч2)"),
    ("gui/docks/placer.py", "_upsert_clone_placement",
     "REMOVE (Ч2)", "the Placer dock's own save path"),
    ("tools/generate_10cl006.py", "build_clone_placements -> write_sexp",
     "UNEXPECTED", "writes 10CL006YE144C8G.clone_placements.sexp; ALREADY BROKEN "
     "(imports removed Rule/ManualSpoke)"),
    ("tools/generate_test_profile.py", "\"clone_placements\": clones",
     "UNEXPECTED", "writes a test profile carrying a root clone_placements list"),
    ("tools/generate_config.py", "\"clone_placements\": [ ... ]",
     "UNEXPECTED", "static generator config with a root clone_placements list"),
)

# ── 3. PER-FILE CLASS ──────────────────────────────────────────────────────
# class -> meaning:
#   REMOVE        the whole file (or its one subject) goes
#   EDIT          mixed file: the section/interface branch goes, the rest stays
#   EDIT-comment  code survives; only prose that names the deleted dock is rewritten
#   KEEP          only "НЕ УДАЛЯТЬ" points (carrier / nested CellPlacement / model)
#   PROBE         a diagnostics rig (see its own file)
#   TEST          a test file (remove-only-Placer vs mixed decided in Ч3)
FILE_CLASS: dict[str, tuple[str, str]] = {
    # — product: whole-file removal —
    "gui/docks/placer.py": ("REMOVE", "the Placer dock, 2828 lines"),
    "kicadstamp/author_cli.py": ("REMOVE", "CLI front-end whose only subject is dump_clone_placements"),
    "tools/convert_placements.py": ("REMOVE", "clone_placements migration"),
    # — product: mixed (grammar / write / CLI / interface) —
    "kicadstamp/config/sexp_format.py": ("EDIT", "list-section grammar + sheet_template field type"),
    "kicadstamp/config/includes.py": ("EDIT", "_LIST_SECTIONS"),
    "kicadstamp/config/format3.py": ("EDIT", "_F3_LIST_SECTIONS / _F3_NODE_KIND_TARGET"),
    "kicadstamp/config/format_version.py": ("EDIT", "_SECTION_SINGULAR"),
    "kicadstamp/config/loader.py": ("EDIT", "file-section load; carrier stays"),
    "kicadstamp/config/entries.py": ("EDIT", "load_clone_placement public loader"),
    "kicadstamp/config/sheet_templates.py": ("EDIT", "clone_placements branch; coordinate_placements stays"),
    "kicadstamp/config_writer.py": ("EDIT", "upsert_clone_placement + docstrings"),
    "kicadstamp/config_rename.py": ("EDIT", "top-level section vs nested Cell.clone_placements"),
    "kicadstamp/cloner/plan.py": ("EDIT", "plan_clone_placements/clone_placements_to_dict go; verify_channel_net_mapping stays (channel_copy)"),
    "kicadstamp/cli.py": ("EDIT", "cmd_clone_plan"),
    "kicadstamp/cli_main.py": ("EDIT", "clone-plan parser/dispatch/epilog"),
    "kicadstamp/author.py": ("EDIT", "dump_clone_placements/_prune_defaults go; dump_template/apply_config stay"),
    "kicadstamp/extract_writer.py": ("EDIT-comment", "writes NESTED Cell.clone_placements (kept); prose only"),
    "gui/dock_hub.py": ("EDIT", "Placer page wiring"),
    "gui/docks/config_tree.py": ("EDIT", "Clone placements group + Add placer"),
    "gui/docks/rename.py": ("EDIT", "LIST_SECTIONS / clone_placements helpers"),
    "gui/docks/_common.py": ("EDIT", "re-export of upsert_clone_placement"),
    "kicadstamp/config/models.py": ("EDIT-comment", "ClonePlacement/Config.clone_placements stay; prose only"),
    "kicadstamp/cli_common.py": ("EDIT-comment", "PlacerError name stays; prose only"),
    "kicadstamp/cli_extract.py": ("EDIT-comment", "prose only"),
    "kicadstamp/explode_cli.py": ("KEEP", "reads entities + carrier clone_placements; prose only"),
    "kicadstamp/exceptions.py": ("EDIT-comment", "PlacerError name stays; prose only"),
    "mcp_server/tools.py": ("EDIT-comment", "PlacerError name stays; prose only"),
    "mcp_server/handlers.py": ("EDIT-comment", "prose only"),
    # — product: prose-only neighbours of the deleted dock —
    "gui/config_io.py": ("EDIT-comment", "PlacerDock docstring reference"),
    "gui/main_window.py": ("EDIT-comment", "placer_dock property"),
    "gui/select_cell.py": ("KEEP", "reads entities + carrier; prose only"),
    "gui/docks/tools.py": ("EDIT-comment", "Prose about Placer's tabs"),
    "gui/docks/thermal_via.py": ("EDIT-comment", "prose only"),
    "gui/docks/net_trace.py": ("EDIT-comment", "prose only"),
    "gui/docks/points.py": ("EDIT-comment", "prose only"),
    "gui/docks/_anchor_origin.py": ("EDIT-comment", "prose only"),
    "gui/docks/_cell_identity.py": ("EDIT-comment", "prose only"),
    "gui/docks/cell_editor.py": ("EDIT-comment", "nested Cell.clone_placements kept; prose only"),
    "gui/docks/cascade.py": ("EDIT-comment", "prose only"),
    "gui/docks/entity_delete.py": ("EDIT-comment", "nested Cell.clone_placements kept; prose only"),
    "gui/docks/entity_export.py": ("EDIT-comment", "prose only"),
    "gui/docks/entity_index.py": ("EDIT", "reads the FILE section as a placer source (П5 cells)"),
    "gui/docks/entity_tree.py": ("EDIT-comment", "prose only"),
    "gui/docks/live_position.py": ("EDIT-comment", "prose only"),
    "gui/docks/log_panel.py": ("EDIT-comment", "prose only"),
    "gui/docks/pending.py": ("EDIT-comment", "prose only"),
    "gui/docks/role_cluster_tree.py": ("EDIT-comment", "prose only"),
    "gui/docks/root_metadata.py": ("EDIT-comment", "prose only"),
    "gui/docks/trees_dock.py": ("EDIT-comment", "prose only"),
    "kicadstamp/cell_geometry_refresh.py": ("KEEP", "carrier/nested; prose only"),
    "kicadstamp/anchor_graph.py": ("KEEP", "carrier"),
    "kicadstamp/validation.py": ("KEEP", "carrier"),
    "kicadstamp/apply_pipeline.py": ("KEEP", "carrier: materialization + filters"),
    "kicadstamp/selection_narrowing.py": ("KEEP", "carrier"),
    "kicadstamp/registry_upgrade.py": ("KEEP", "carrier"),
    "kicadstamp/flatten.py": ("EDIT", "list-section concatenation"),
    "kicadstamp/config/profile_copy.py": ("KEEP", "nested Cell.clone_placements"),
    "kicadstamp/config/form_identity.py": ("KEEP", "identity for the carrier"),
    "kicadstamp/config/__init__.py": ("EDIT-comment", "load_clone_placement export + prose"),
    "kicadstamp/placement/planner.py": ("KEEP", "carrier"),
    "kicadstamp/placement/dependency_order.py": ("KEEP", "carrier"),
    "kicadstamp/placement/commands.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/clone_position_calculator.py": ("KEEP", "carrier + nested"),
    "kicadstamp/placement/services/clone_role_resolver.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/component_resolver.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/point_resolver.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/role_narrowing.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/board_items_resolver.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/via_planner.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/entity_placement.py": ("KEEP", "materialization"),
    "kicadstamp/geometry/cell_anchor.py": ("EDIT-comment", "nested; prose only"),
    "kicadstamp/geometry/clone_geometry.py": ("EDIT-comment", "prose only"),
    "kicadstamp/cloner/models.py": ("EDIT-comment", "prose only"),
    "kicadstamp/cloner/pcb.py": ("EDIT-comment", "prose only"),
    "kicadstamp/cloner/extract.py": ("EDIT-comment", "prose only"),
    "kicadstamp/utils/file_cache.py": ("EDIT-comment", "prose only"),
    "kicadstamp/utils/paths.py": ("EDIT-comment", "prose only"),
    "kicadstamp/link_trees.py": ("KEEP", "prose only"),
    "kicadstamp/config/registry_upgrade.py": ("KEEP", "carrier"),
    # — tools (beyond the planned convert_placements) —
    "tools/generate_10cl006.py": ("UNEXPECTED", "producer not named in the plan; imports removed Rule/ManualSpoke (dead)"),
    "tools/generate_test_profile.py": ("UNEXPECTED", "producer not named in the plan"),
    "tools/generate_config.py": ("UNEXPECTED", "producer not named in the plan"),
    "tools/door_lint_baseline.py": ("EDIT", "baseline entry for gui/docks/placer.py"),
    # — remaining curated from the Ч0 grep pass (prose-only unless noted) —
    "gui/__init__.py": ("EDIT-comment", "prose only"),
    "gui/docks/fieldstool_dock.py": ("EDIT-comment", "prose only (placer_dock set_root_path)"),
    "gui/docks/tree_from_selection.py": ("EDIT-comment", "prose only (ClonePlacement.anchor_pad)"),
    "gui/entity/anchor_workers.py": ("KEEP", "carrier + nested Cell.clone_placements"),
    "kicadstamp/adopt_at_current_place.py": ("KEEP", "clone_placement_effective_name (kept model helper)"),
    "kicadstamp/board_dedupe.py": ("EDIT-comment", "prose only"),
    "kicadstamp/cell_frame.py": ("EDIT-comment", "prose only"),
    "kicadstamp/channel_copy.py": ("KEEP", "imports verify_channel_net_mapping (kept); prose only"),
    "kicadstamp/component_address.py": ("EDIT-comment", "prose only"),
    "kicadstamp/config/points.py": ("EDIT-comment", "prose only"),
    "kicadstamp/config/retired_sections.py": ("EDIT", "the ONE refusal table gets the clone_placements line"),
    "kicadstamp/geometry/spoke_layout.py": ("EDIT-comment", "prose only"),
    "kicadstamp/imprint_apply.py": ("EDIT-comment", "prose only"),
    "kicadstamp/kicad/adapter.py": ("EDIT-comment", "prose only"),
    "kicadstamp/net_resolution.py": ("EDIT-comment", "prose only"),
    "kicadstamp/net_trace_extract.py": ("EDIT-comment", "prose only"),
    "kicadstamp/net_trace_planner.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/__init__.py": ("EDIT-comment", "prose only"),
    "kicadstamp/placement/services/coordinate_position_calculator.py": ("EDIT-comment", "CoordinatePlacement kept; prose only"),
    "kicadstamp/registry.py": ("EDIT-comment", "prose only"),
    "kicadstamp/registry_match.py": ("EDIT-comment", "prose only"),
    "kicadstamp/runtime_context.py": ("EDIT-comment", "prose only"),
    "kicadstamp/sheet_names.py": ("EDIT-comment", "prose only"),
    "kicadstamp/template_extraction.py": ("EDIT-comment", "prose only"),
    "kicadstamp/template_selection.py": ("EDIT-comment", "prose only"),
    "kicadstamp/tree_position.py": ("EDIT-comment", "prose only"),
    "kicadstamp/trees.py": ("EDIT", "KINDS / _PLACEMENT_KINDS / kind \"clone\" retirement (П4)"),
    "tools/dedupe_vias_tracks.py": ("EDIT-comment", "prose only"),
    # — diagnostics rigs —
    "kicadstamp/diagnostics/probe_gui_min_sizes.py": ("EDIT", "drop PlacerDock from the size list"),
}


def _iter_files():
    for root in ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts:
                continue
            if p.suffix not in (".py", ".md", ".po"):
                continue
            yield p


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def scan_mentions() -> list[tuple[str, int, str]]:
    """(relpath, lineno, pattern) for every mention, per line."""
    rows: list[tuple[str, int, str]] = []
    for p in _iter_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(p)
        for i, line in enumerate(text.splitlines(), 1):
            for pat in PATTERNS:
                if pat in line:
                    rows.append((rel, i, pat))
    return rows


def reader_table() -> list[tuple[str, int, str, str]]:
    """(relpath, lineno, receiver-kind, verdict) for every `clone_placements`
    READ — carrier vs nested vs file-section."""
    out: list[tuple[str, int, str, str]] = []
    for p in _iter_files():
        if p.suffix != ".py":
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if "clone_placements" not in line:
                continue
            if _CARRIER_RE.search(line):
                out.append((_rel(p), i, "cfg.clone_placements", "KEEP (carrier)"))
            elif _NESTED_RE.search(line) or _CELLDICT_RE.search(line):
                out.append((_rel(p), i, "cell.clone_placements", "KEEP (nested CellPlacement)"))
            elif _RAWDICT_RE.search(line):
                # A raw-dict read is EITHER the root file section (REMOVE) or a
                # `Cell` record's nested list (KEEP) — `data.get(...)` looks the
                # same in both. The probe cannot tell them apart from one line,
                # so it flags them for adjudication instead of guessing.
                out.append((_rel(p), i, "raw-dict (cell vs root?)", "REVIEW (raw-dict)"))
    return out


def per_file_table(mentions: list[tuple[str, int, str]]):
    counts: dict[str, int] = {}
    for rel, _ln, _pat in mentions:
        counts[rel] = counts.get(rel, 0) + 1
    rows = []
    for rel in sorted(counts):
        if rel.startswith("kicadstamp/diagnostics/"):
            cls, note = "PROBE", "diagnostics rig"
        elif rel.startswith("tests/"):
            cls, note = "TEST", "remove-only-Placer vs mixed decided in Ч3"
        elif rel.startswith("techdocs/map/"):
            cls, note = "GENERATED", "regenerated by tools/project_map.py in Ч3"
        elif rel.startswith("docs/") or rel.startswith("locales/"):
            cls, note = "DOC", "EN/RU note in Ч3"
        else:
            cls, note = FILE_CLASS.get(rel, ("REVIEW", "not yet curated"))
        rows.append((cls, rel, counts[rel], note))
    return rows


def main() -> int:
    mentions = scan_mentions()
    print(f"roots: {', '.join(ROOTS)}")
    print(f"files with a mention: {len({r[0] for r in mentions})}")
    print(f"total mentions: {len(mentions)}")
    print()

    print("=" * 100)
    print("TABLE 1 — PRODUCERS of a root `clone_placements:`")
    print("=" * 100)
    for f, sym, verdict, note in PRODUCERS:
        print(f"  [{verdict}] {f} :: {sym}\n      {note}")
    unexpected = [f for f, _s, v, _n in PRODUCERS if v.startswith("UNEXPECTED")]
    print()
    print(f"  UNEXPECTED producers (not named in the plan): {len(unexpected)}")
    for f in unexpected:
        print(f"    - {f}")
    print()

    print("=" * 100)
    print("TABLE 2 — READERS of a loaded config's `clone_placements`")
    print("=" * 100)
    readers = reader_table()
    by_verdict: dict[str, int] = {}
    for rel, ln, kind, verdict in readers:
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    print(f"  {len(readers)} read sites")
    for verdict, n in sorted(by_verdict.items()):
        print(f"    {n:4d}  {verdict}")
    print("  (site list is dumped to the TSV below)")
    print()

    print("=" * 100)
    print("TABLE 3 — PER-FILE CLASS")
    print("=" * 100)
    rows = per_file_table(mentions)
    for cls, rel, n, note in rows:
        print(f"  {cls:12s} {n:4d}  {rel}  — {note}")
    print()

    # dump TSVs under the git-ignored techdocs tree
    out_dir = REPO / "techdocs" / "handoff" / "deepseek" / "done"
    if out_dir.exists():
        raw = out_dir / "placer_inventory_2026_10_12.mentions.tsv"
        raw.write_text("".join(f"{r}\t{l}\t{p}\n" for r, l, p in mentions), encoding="utf-8")
        rd = out_dir / "placer_inventory_2026_10_12.readers.tsv"
        rd.write_text("".join(f"{r}\t{l}\t{k}\t{v}\n" for r, l, k, v in readers), encoding="utf-8")
        print(f"wrote {raw.relative_to(REPO)}")
        print(f"wrote {rd.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
