# kicadstamp/diagnostics/deepseek_probe_whole_tree_redraw_2026_10_03.py
"""SH0 probe of plan_2026_10_03_whole_tree_redraw_with_embedded.md: WHY do the
embedded dac_buf_channel_N parts stay put when "Redraw whole tree" is run on the
fpga tree, while the pif-* parts of the same tree DO move?

It repeats the PLANNING half of gui/docks/cascade.run_curated_forest_redraw for
selected_refs = collect_tree_refs(<tree>) -- the same selection "Redraw whole
tree" hands in -- and NOTHING is written to the board: every ApplyPipeline is
built with dry_run=True, exactly the run the real redraw would do minus the
write. Read-only.

For every name the plan emits it prints:
  * WHERE its override would come from (stage2 / rigid / none / skipped by the
    self-anchor drift guard),
  * the override value (the tree layout position when it comes from stage2),
  * the component moves the dry run plans for that name, next to the parts'
    CURRENT live positions read from the board.

The stage-2 loop mirrors cascade line by line (tree_layout_base ->
layout_tree_from_base from the flow root's live anchor), and any exception there
is caught PER FLOW ROOT and printed with its traceback -- the real cascade turns
it into the "stage-2 base unavailable" warning and leaves the module content in
place, which is one of the candidate causes this probe has to confirm or kill.

ASCII only (a cp1251 console must not crash on the table).

Run (KiCad must have the board open; the config default is the COPY of the
profile, see rule 34 of techdocs/me/deepseek.md):
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_whole_tree_redraw_2026_10_03
    .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_whole_tree_redraw_2026_10_03 \
        --config profiles/3ch-awg-tia-v103-copy/config.sexp --tree fpga --out report.txt
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.adapter_factory import create_board_adapter  # noqa: E402
from kicadstamp.apply_pipeline import ApplyPipeline  # noqa: E402
from kicadstamp.config import load_config  # noqa: E402
from kicadstamp.copper_order import copper_node_dependencies  # noqa: E402
from kicadstamp.constants import DEFAULT_TIMEOUT_MS  # noqa: E402
from kicadstamp.link_trees import link_trees  # noqa: E402
from kicadstamp.tree_position import (  # noqa: E402
    apply_rigid_override,
    capture_rigid_state,
    curated_forest_module_content,
    curated_redraw_plan,
    curated_redraw_plan_forest,
    layout_tree_from_base,
    tree_layout_base,
)
from kicadstamp.trees import check_tree_self_anchor_drift  # noqa: E402
from kicadstamp.utils.units import MM  # noqa: E402

# Private helpers of the planner, imported on purpose: this probe must report the
# content_refs decision the way the CODE makes it, not re-derive it (rule 35).
from kicadstamp.tree_position import (  # noqa: E402
    _active_module_entries,
    _module_content_record_refs,
    _module_markers,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "profiles" / "3ch-awg-tia-v103-copy" / "config.sexp"

# nm -> mm comparison; 1e-3 mm is a thousand times the nm grid step, still
# three orders below any real placement difference.
_ZERO_TOL_MM = 1e-3

logger = logging.getLogger("deepseek_probe_whole_tree_redraw")


def _fmt_vec(v) -> str:
    return f"({v.x / MM:+10.4f}, {v.y / MM:+10.4f})"


def _collect_tree_refs(tree) -> list[str]:
    """Local copy of gui/docks/trees_dock.collect_tree_refs (DFS, parent before
    child, module markers included) so the probe does not import a Qt module."""
    refs: list[str] = []

    def walk(nodes) -> None:
        for node in nodes:
            refs.append(node.ref)
            walk(node.children)

    walk(tree.nodes)
    return refs


def _self_anchor_skips(cfg, linked, selected_refs: set) -> tuple[dict, dict]:
    """Mirror of gui/docks/cascade._self_anchor_drift_skips (no Qt import):
    a tree the self-anchor drift guard refuses has its planned records dropped
    from the run and reported as skipped instead of applied inertly."""
    skips: dict = {}
    reasons: dict = {}
    by_name = {t.name: t for t in (getattr(cfg, "trees", []) or [])}
    for tree in linked:
        plain = by_name.get(tree.name)
        reason = (check_tree_self_anchor_drift(cfg, plain)
                  if plain is not None else None)
        if reason is None:
            continue
        reasons[tree.name] = reason
        tree_names, _w = curated_redraw_plan(tree, selected_refs)
        for name in tree_names:
            skips[name] = reason
    return skips, reasons


def _is_required(name: str) -> bool:
    """The rows plan SH0 names explicitly: the three dac_buf channels, every
    channel pif, and the ordinary fpga node."""
    return (name.startswith("dac_buf_channel_")
            or name == "fpga_fpga"
            or (name.startswith("pif_") and "_channel_" in name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG),
                        help="profile config.sexp (default: the -copy of the "
                             "working profile)")
    parser.add_argument("--tree", default="fpga",
                        help='the tree "Redraw whole tree" is pressed on')
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--out", default=None,
                        help="also write the whole report to this file")
    args = parser.parse_args(argv)

    out_lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        out_lines.append(text)

    config_path = str(args.config)
    emit("=== SH0 whole-tree redraw probe (read-only, dry run) ===")
    emit(f"platform     : {sys.platform}")
    emit(f"cwd          : {Path.cwd()}")
    emit(f"config       : {config_path}")
    emit(f"tree         : {args.tree}")
    if not Path(config_path).exists():
        emit(f"FATAL: config not found: {config_path}")
        return 2

    cfg, ctx = load_config(config_path)
    sheet_names = dict((ctx.sheet_names if ctx else {}) or {})

    target = next((t for t in cfg.trees if t.name == args.tree), None)
    if target is None:
        emit(f"FATAL: tree {args.tree!r} not in {[t.name for t in cfg.trees]}")
        return 2
    emit(f"trees        : {[t.name for t in cfg.trees]}")

    # --- the one thing that must be running: KiCad with the board ------------
    adapter = create_board_adapter(timeout_ms=args.timeout_ms,
                                   config_path=config_path)
    try:
        adapter.refresh_board()
    except Exception as exc:  # noqa: BLE001 — no board, no measurement
        emit("FATAL: cannot read the live board "
             f"({type(exc).__name__}: {exc}).")
        emit("KiCad must be RUNNING with the board open; without it SH0 is not "
             "done and must not be substituted with a fake board.")
        return 3

    try:
        emit(f"live board   : {adapter.get_board_filename()!r}")
    except Exception:  # noqa: BLE001 — identity is a nicety, not the measurement
        pass

    try:
        linked = link_trees(cfg, cfg.trees)
        by_tree = {t.name: t for t in cfg.trees}
        selected_refs = set(_collect_tree_refs(target))
        emit(f"selected_refs: {len(selected_refs)} refs from tree {args.tree!r}")

        # --- the plan the real run makes -------------------------------------
        copper_deps: dict = {}
        try:
            copper_deps = copper_node_dependencies(cfg)
        except Exception as exc:  # noqa: BLE001
            emit(f"note: copper order edges unavailable ({exc})")

        names, warnings = curated_redraw_plan_forest(
            linked, selected_refs, copper_deps=copper_deps)
        for w in warnings:
            emit(f"plan warning : {w}")

        skips, reasons = _self_anchor_skips(cfg, linked, selected_refs)
        for tree_name, reason in reasons.items():
            emit(f"guard refusal: tree {tree_name!r}: {reason}")

        content_refs, flow_roots = curated_forest_module_content(
            linked, selected_refs)
        emit("")
        emit(f"plan names   : {len(names)}")
        emit(f"content_refs : {len(content_refs)} -> {sorted(content_refs)}")
        emit(f"flow_roots   : {flow_roots}")
        emit(f"guard skips  : {sorted(skips)}")

        # --- module markers of the target tree (is content even reached?) ----
        emit("")
        emit("--- module markers of the target tree ---")
        markers = _module_markers(linked)
        active = {id(m): (m, owner) for m, owner in
                  _active_module_entries(markers, selected_refs)}
        for owner, m, parent_ref in markers:
            if owner != target.name:
                continue
            child = m.module_linked.name if m.module_linked is not None else None
            is_active = id(m) in active
            n_content = (len(_module_content_record_refs(m))
                         if m.module_linked is not None else 0)
            emit(f"  marker {m.node.ref!r:20s} kind=module parent={parent_ref!r:14s} "
                 f"module_linked={child!r:16s} active={is_active} "
                 f"content_records={n_content}")

        # --- stage 2, exactly as cascade builds it ---------------------------
        emit("")
        emit("--- stage 2 (layout from each flow root's live anchor) ---")
        stage2: dict = {}
        for root_name in flow_roots:
            root = by_tree.get(root_name)
            if root is None:
                emit(f"  root {root_name!r}: NOT in by_tree "
                     f"(keys={sorted(by_tree)}) -- its content gets no stage-2 value")
                continue
            try:
                base_pos, base_rot = tree_layout_base(
                    adapter, cfg, root, sheet_names, by_tree)
                laid = layout_tree_from_base(
                    root, base_pos, base_rot, by_tree,
                    adapter=adapter, cfg=cfg, sheet_names=sheet_names)
                stage2.update(laid)
                emit(f"  root {root_name!r}: base={_fmt_vec(base_pos)} "
                     f"rot={base_rot:8.3f}  laid_records={len(laid)}")
            except Exception as exc:  # noqa: BLE001 — this is the smoke itself
                emit(f"  root {root_name!r}: STAGE-2 FAILED -> "
                     f"{type(exc).__name__}: {exc}")
                emit("    (the real cascade swallows this into the warning "
                     "'stage-2 base unavailable' and leaves the module content "
                     "in place)")
                for line in traceback.format_exc().rstrip().splitlines():
                    emit("    " + line)
        emit(f"  stage2 records total: {len(stage2)}")
        missing_stage2 = sorted(content_refs - set(stage2))
        emit(f"  content_refs WITHOUT a stage-2 value: {missing_stage2}")

        # --- rigid captures (the other override source) ----------------------
        captures: dict = {}
        parent_map: dict = {}
        for tree in linked:
            tree_captures, tree_parent_map = capture_rigid_state(
                adapter, cfg, tree, names, sheet_names)
            captures.update(tree_captures)
            parent_map.update(tree_parent_map)

        # --- live board positions -------------------------------------------
        live: dict = {}
        for fp in adapter.get_footprints():
            live[fp.ref] = (fp.position.x / MM, fp.position.y / MM, fp.angle_deg)

        # --- per-name: source of the override + dry-run plan ------------------
        emit("")
        emit("=== per-name rows ===")
        rows: list[dict] = []
        for name in names:
            row: dict = {"name": name, "required": _is_required(name),
                         "override": None, "source": None, "planned": [],
                         "vias": [], "tracks": [], "error": None}
            if name in skips:
                row["source"] = "skip(guard)"
                rows.append(row)
                continue

            override = None
            if name in content_refs:
                if name in stage2:
                    pos, rot = stage2[name]
                    override = _position_override(pos, rot)
                    row["source"] = "stage2"
                else:
                    row["source"] = "stage2-MISSING (own record)"
            else:
                cap = captures.get(name)
                if cap is not None:
                    parent_ref, parent_record, _is = parent_map[name]
                    try:
                        override = apply_rigid_override(
                            adapter, cfg, parent_ref, parent_record, cap,
                            sheet_names)
                        row["source"] = "rigid"
                    except Exception as exc:  # noqa: BLE001
                        row["source"] = "rigid-FAILED (own record)"
                        row["error"] = f"rigid: {type(exc).__name__}: {exc}"
                else:
                    row["source"] = "none (own record)"
            row["override"] = override

            try:
                planned, vias, tracks, _report = _dry_run_name(
                    config_path, cfg, ctx, name, override, args.timeout_ms)
                row["planned"], row["vias"], row["tracks"] = planned, vias, tracks
            except Exception as exc:  # noqa: BLE001 — a dry run must not stop SH0
                row["error"] = f"dry-run: {type(exc).__name__}: {exc}"
            rows.append(row)

        for row in rows:
            _print_row(emit, row, live)

        # --- the compact verdict table ---------------------------------------
        emit("")
        emit("=== summary ===")
        emit(f"{'':2} {'name':28s} {'source':26s} {'live->planned (mm)':22s} verdict")
        for row in rows:
            mark = "*" if row["required"] else " "
            verdict, delta_text = _verdict(row, live)
            emit(f"{mark} {row['name']:28s} {str(row['source']):26s} "
                 f"{delta_text:22s} {verdict}")

        emit("")
        emit("Legend: '*' = row the plan names explicitly. 'live->planned' is the "
             "largest per-part shift between the board NOW and the dry-run target.")
        emit("Read: a content row whose source is 'stage2-MISSING' or whose "
             "planned target equals the live position IS the answer to why it "
             "does not move; a 'skip(guard)' row means the guard drops it.")

        if args.out:
            Path(args.out).write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            emit(f"\nreport written: {args.out}")
        return 0
    finally:
        adapter.close()


def _position_override(pos, rot):
    from kicadstamp.tree_position import PositionOverride
    return PositionOverride(position=pos, rotation_deg=rot)


def _dry_run_name(config_path, cfg, ctx, name, override, timeout_ms):
    """ONE ApplyPipeline run for `name` with its override, dry_run=True -- the
    real redraw's per-name run minus the write. Returns (components, vias,
    tracks, report_lines); the copper halves are read from the planner's own
    accumulators, so a net_trace row (whose plan is vias/tracks, NOT component
    moves) reports what it would actually place."""
    with ApplyPipeline(config_path=config_path, preloaded_cfg=cfg,
                       preloaded_ctx=ctx, timeout_ms=timeout_ms, only=[name],
                       dry_run=True,
                       position_overrides={name: override} if override else None
                       ) as pipeline:
        report = pipeline.run()
        planner = getattr(pipeline, "planner", None)
        planned = [(c.ref, c.dest.x / MM, c.dest.y / MM)
                   for c in (getattr(planner, "_planned", None) or [])]
        # plan_vias()/plan_tracks() RETURN the net_trace copper but cache it in
        # _net_trace_vias/_net_trace_tracks instead of _planned_vias/_planned_
        # tracks -- reading only the latter reports a net_trace row as "nothing
        # planned", which is FALSE. Union both halves.
        all_vias = ((getattr(planner, "_planned_vias", None) or [])
                    + (getattr(planner, "_net_trace_vias", None) or []))
        all_tracks = ((getattr(planner, "_planned_tracks", None) or [])
                      + (getattr(planner, "_net_trace_tracks", None) or []))
        vias = [(v.owner_ref, v.position.x / MM, v.position.y / MM, v.net_name)
                for v in all_vias]
        tracks = [(t.owner_ref, t.start.x / MM, t.start.y / MM,
                   t.end.x / MM, t.end.y / MM, t.net_name)
                  for t in all_tracks]
    return planned, vias, tracks, report


def _print_row(emit, row: dict, live: dict) -> None:
    mark = "*" if row["required"] else " "
    emit("")
    emit(f"{mark} {row['name']}  [{row['source']}]")
    ov = row["override"]
    if ov is not None:
        emit(f"    override (tree layout value): {_fmt_vec(ov.position)} "
             f"rot={ov.rotation_deg:8.3f}")
    else:
        emit("    override: NONE (the record is applied from its own fields)")
    if row["error"]:
        emit(f"    ERROR: {row['error']}")
    if not row["planned"] and not row["vias"] and not row["tracks"]:
        emit("    dry run: no moves planned")
    for ref, x, y in row["planned"]:
        live_entry = live.get(ref)
        if live_entry is None:
            emit(f"    planned  {ref:12s} ({x:+10.4f}, {y:+10.4f}) mm   "
                 f"live=NOT FOUND on board")
            continue
        lx, ly, la = live_entry
        emit(f"    planned  {ref:12s} ({x:+10.4f}, {y:+10.4f}) mm   "
             f"live=({lx:+10.4f}, {ly:+10.4f}) a={la:6.1f}   "
             f"shift=({x - lx:+8.4f}, {y - ly:+8.4f})")
    for owner, x, y, net in row["vias"]:
        emit(f"    planned  via/{owner:20s} ({x:+10.4f}, {y:+10.4f}) mm   net={net}")
    for owner, sx, sy, ex, ey, net in row["tracks"]:
        emit(f"    planned  track/{owner:18s} ({sx:+10.4f}, {sy:+10.4f}) -> "
             f"({ex:+10.4f}, {ey:+10.4f}) mm   net={net}")


def _verdict(row: dict, live: dict) -> tuple[str, str]:
    """(verdict text, largest-shift text) for the compact table."""
    if row["error"]:
        return (row["error"][:40], "-")
    if row["source"] == "skip(guard)":
        return ("SKIPPED by guard", "-")
    if row["source"] and row["source"].startswith("stage2-MISSING"):
        return ("NO TREE VALUE -> own record", "-")
    copper = len(row["vias"]) + len(row["tracks"])
    if not row["planned"]:
        if copper:
            return (f"copper: {len(row['vias'])}v/{len(row['tracks'])}t planned", "-")
        return ("no moves planned", "-")
    biggest = 0.0
    for ref, x, y in row["planned"]:
        entry = live.get(ref)
        if entry is None:
            continue
        d = max(abs(x - entry[0]), abs(y - entry[1]))
        biggest = max(biggest, d)
    if biggest <= _ZERO_TOL_MM:
        return ("NO MOVE (planned == live)", f"{biggest:+.4f}")
    return ("WOULD MOVE", f"{biggest:+.4f}")


if __name__ == "__main__":
    raise SystemExit(main())
