"""Where will a node's **Redraw** put it, and where does the TREE want to put it?

Plan plan_2026_11_09_node_redraw_from_live_parent. Two questions, one read-only run, no board
write and no config write:

  1. the NEW answer — `gui.docks.trees_base_pose.redraw_target`: the parent's frame read from
     the BOARD plus the node's stored xy/rotation. This is what the Redraw worker now places,
     and it is the exact inverse of "Read current position";
  2. the OLD answer — the tree layout from the tree's OWN anchor (`layout_tree_from_anchor`),
     i.e. what `ApplyPipeline(only=[ref])` used to produce: the Grid Origin, the tree's inner
     point and every module offset of the owning tree, whether or not they are real on the board.

The probe also prints the record's LIVE pose, so `delta(new - live)` says how far the new Redraw
would move the part (it must be ~0 for a node whose stored numbers came from a Read), and — for
a tree embedded by a `module` node — where the OWNING tree's layout puts that same node (what
"Redraw whole tree" from the embedded tree's tab does).

After the single-node part it also measures "Redraw SELECTED" for the refs given on the command
line (`<node_ref> [more refs…]`): the rigid planner's own path (`capture_rigid_state` →
`apply_rigid_override`), and — for a name the capture does not cover, i.e. one the planner would
leave alone — `ApplyPipeline(only=[name], dry_run=True)`, which writes nothing either.

Read-only. Usage:
    .venv/bin/python -m kicadstamp.diagnostics.probe_node_redraw_target <config> <tree> <node_ref> [more refs…]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config
from kicadstamp.explore import Board
from kicadstamp.tree_position import (apply_rigid_override, layout_tree_from_anchor,
                                      tree_layout_base)
from kicadstamp.utils.units import MM

from gui.docks.live_position import read_record_live_pose
from gui.docks.trees_base_pose import redraw_target, resolve_node_base_pose


def _fmt(pos) -> str:
    return f"({pos.x / MM:+10.4f}, {pos.y / MM:+10.4f})"


def _find(tree, ref):
    """(node, parent) of `ref` at any depth — parent None for a top-level node."""
    stack = [(n, None) for n in tree.nodes]
    while stack:
        node, parent = stack.pop()
        if node.ref == ref:
            return node, parent
        stack.extend((c, node) for c in node.children or [])
    raise SystemExit(f"node {ref!r} not found in tree {tree.name!r}")


def _owning_marker(cfg, tree_name):
    """(owner tree, module node) of the module node that embeds `tree_name`, or None."""
    for owner in cfg.trees:
        stack = list(owner.nodes)
        while stack:
            node = stack.pop()
            if node.kind == "module" and node.ref == tree_name:
                return owner, node
            stack.extend(node.children or [])
    return None


def main() -> int:
    config_path, tree_name, want_ref = sys.argv[1], sys.argv[2], sys.argv[3]

    board = Board.connect()
    adapter = board.adapter
    adapter.refresh_board()
    cfg, ctx = load_config(config_path)
    sheet_names = dict(ctx.sheet_names or {})
    forest = {t.name: t for t in cfg.trees}
    tree = forest[tree_name]
    node, parent = _find(tree, want_ref)

    print(f"tree {tree_name!r}  node {want_ref!r}  kind={node.kind!r}")
    print(f"  stored xy={node.xy} polar={node.polar} rotation={node.rotation}")
    print(f"  tree anchor: mode={tree.anchor.mode!r} shift={tree.anchor.shift_xy}")
    print(f"  parent: {parent.ref!r} (kind {parent.kind!r})" if parent else "  parent: the tree anchor")

    # ── 1. the NEW answer: live parent + the node's own numbers ─────────────
    live_base, live_deg, mirrored = resolve_node_base_pose(
        cfg, adapter, sheet_names, tree, parent,
        node.anchor if node.kind == "mount" else None)
    print(f"\nlive base        {_fmt(live_base)} rot={live_deg} mirror={mirrored}")
    target, target_rot = redraw_target(
        cfg, adapter, sheet_names, tree, node, parent,
        base_anchor=node.anchor if node.kind == "mount" else None)
    print(f"REDRAW target    {_fmt(target)} rot={target_rot}")

    # ── 2. the OLD answer: the tree's own layout ───────────────────────────
    tree_base, tree_rot = tree_layout_base(adapter, cfg, tree, sheet_names, forest)
    print(f"\ntree base        {_fmt(tree_base)} rot={tree_rot}  (own anchor + inner point)")
    laid = layout_tree_from_anchor(tree, forest, adapter=adapter, cfg=cfg,
                                   sheet_names=sheet_names)
    if want_ref in laid:
        old_pos, old_rot = laid[want_ref]
        print(f"TREE layout      {_fmt(old_pos)} rot={old_rot}")
        print(f"  delta(redraw - tree) = "
              f"({(target.x - old_pos.x) / MM:+.4f}, {(target.y - old_pos.y) / MM:+.4f}) mm")
    else:
        print("TREE layout      (this node places no record — a module/copper/mount container)")

    # ── where the record actually stands right now ─────────────────────────
    if node.kind == "mount":
        print("\nlive child       (a mount node has no record of its own)")
    else:
        try:
            pose = read_record_live_pose(adapter, cfg, node.ref, _record_of(cfg, node),
                                         sheet_names)
            print(f"\nlive child       {_fmt(pose.position)} rot={pose.rotation_deg} "
                  f"mirror={pose.mirror} from_cluster={pose.from_cluster}")
            print(f"  delta(redraw - live) = "
                  f"({(target.x - pose.position.x) / MM:+.4f}, "
                  f"{(target.y - pose.position.y) / MM:+.4f}) mm")
        except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
            print(f"\nlive child       <unreadable: {exc}>")

    # ── what «Redraw SELECTED» would place, for the refs given ─────────────
    selection = sys.argv[3:]
    if selection:
        try:
            selected_measurement(cfg, adapter, sheet_names, tree, set(selection))
        except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
            print(f"\nRedraw selected measurement failed: {exc}")

    # ── what «Redraw whole tree» from an EMBEDDED tab does ────────────────
    owner = _owning_marker(cfg, tree.name)
    if owner is None:
        print("\nembedding        none — this tree is placed by nobody")
        return 0
    owner_tree, marker = owner
    print(f"\nembedding        module {marker.ref!r} of tree {owner_tree.name!r} "
          f"at xy={marker.xy} rotation={marker.rotation}")
    owner_laid = layout_tree_from_anchor(owner_tree, forest, adapter=adapter, cfg=cfg,
                                         sheet_names=sheet_names)
    root_ref = tree.nodes[0].ref if tree.nodes else None
    # «Redraw whole tree» on the EMBEDDED tab lays the tree out from the tab's OWN anchor
    # (`layout_tree_from_anchor` = the numbers above), while the module in the owner tree is
    # what actually places it. The two must be named next to each other — this is the
    # measurement the plan asks for, NOT a fix.
    if root_ref in laid:
        print(f"  root {root_ref!r} on the EMBEDDED tab  {_fmt(laid[root_ref][0])}"
              f"  (tree layout from its own anchor)")
    if root_ref in owner_laid:
        print(f"  root {root_ref!r} from the OWNER tree  {_fmt(owner_laid[root_ref][0])}"
              f"  (module + the tree's inner point)")
    if want_ref in laid and want_ref in owner_laid:
        mine, theirs = laid[want_ref][0], owner_laid[want_ref][0]
        print(f"  {want_ref!r}: embedded tab {_fmt(mine)} vs owner tree {_fmt(theirs)} -> "
              f"off by ({(mine.x - theirs.x) / MM:+.4f}, "
              f"{(mine.y - theirs.y) / MM:+.4f}) mm")
    return 0


def selected_measurement(cfg, adapter, sheet_names, tree, refs) -> None:
    """What «Redraw selected» would place for `refs` — the rigid planner's own path, no write.

    `run_curated_tree_redraw` captures every selected name ONCE (`capture_rigid_state`) and then
    re-projects each capture onto the parent's CURRENT frame (`apply_rigid_override`). A name the
    capture does not cover (a container, or one the planner refuses to capture) is left to the
    ordinary `ApplyPipeline(only=[name])` — measured here as a DRY RUN."""
    from kicadstamp.link_trees import link_trees
    from kicadstamp.tree_position import capture_rigid_state

    print(f"\n=== Redraw selected: tree {tree.name!r}, refs {sorted(refs)}")
    linked = next(lt for lt in link_trees(cfg, cfg.trees) if lt.name == tree.name)
    captures, parent_map = capture_rigid_state(adapter, cfg, linked, sorted(refs),
                                               sheet_names)
    for ref in sorted(refs):
        cap = captures.get(ref)
        if cap is None:
            print(f"  {ref!r}: no capture — the rigid planner leaves it alone")
            _dry_run(cfg, adapter, sheet_names, ref)
            continue
        parent_ref, parent_record, is_anchor = parent_map[ref]
        print(f"  {ref!r}: captured offset {_fmt(cap.local_offset)} "
              f"rot={cap.relative_rotation} parent={parent_ref!r} "
              f"anchor_parent={cap.anchor_parent}")
        try:
            override = apply_rigid_override(adapter, cfg, parent_ref, parent_record,
                                            cap, sheet_names)
        except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
            print(f"    override FAILED: {exc}")
            continue
        print(f"    rigid override -> {_fmt(override.position)} rot={override.rotation_deg}")
        _delta_to_live(cfg, adapter, sheet_names, ref, override.position)


def _delta_to_live(cfg, adapter, sheet_names, ref, target) -> None:
    """`target` against where that record actually stands — the number a user sees move."""
    node = None
    for tree in cfg.trees:
        stack = list(tree.nodes)
        while stack:
            candidate = stack.pop()
            if candidate.ref == ref:
                node = candidate
                break
            stack.extend(candidate.children or [])
        if node is not None:
            break
    if node is None or node.kind in ("external", "mount", "module", "copper", "component"):
        print("    live child: (not a record — nothing to compare)")
        return
    try:
        pose = read_record_live_pose(adapter, cfg, ref, _record_of(cfg, node), sheet_names)
    except Exception as exc:  # noqa: BLE001
        print(f"    live child: <unreadable: {exc}>")
        return
    print(f"    live child {_fmt(pose.position)} -> delta "
          f"({(target.x - pose.position.x) / MM:+.4f}, "
          f"{(target.y - pose.position.y) / MM:+.4f}) mm")


def _dry_run(cfg, adapter, sheet_names, ref) -> None:
    """`ApplyPipeline(only=[ref], dry_run=True)` — writes nothing; prints what it WOULD move."""
    from kicadstamp.apply_pipeline import ApplyPipeline

    try:
        with ApplyPipeline(preloaded_cfg=cfg, only=[ref], dry_run=True,
                           preloaded_ctx=None, config_path="") as pipeline:
            pipeline.run()
    except Exception as exc:  # noqa: BLE001
        print(f"    dry run FAILED: {exc}")
        return
    moves = getattr(pipeline, "planned_moves", None) or []
    print(f"    dry run: {len(moves)} planned move(s)")
    for move in moves[:6]:
        print(f"      {move}")


def _record_of(cfg, node):
    """The config record a node ref names, by the SAME rule link_trees uses (name index);
    None for a local kind (external/mount/module/copper/component), which has no record."""
    from kicadstamp.anchor_graph import build_records
    from kicadstamp.link_trees import _build_by_key_index, _build_by_name_index, _resolve_node_ref

    if node.kind in ("external", "mount", "module", "copper", "component"):
        return None
    records = build_records(cfg)
    record, _is_external = _resolve_node_ref(
        node, _build_by_key_index(records), _build_by_name_index(records))
    return record


if __name__ == "__main__":
    raise SystemExit(main())
