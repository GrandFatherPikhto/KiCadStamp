#!/usr/bin/env python3
"""Copper-pad ORDER EDGES on the REAL profile (Э2 measurement).

2026-09-17, plan_2026_09_17_order_pass_and_component_node, Э2/P.5.
Committed 2026-09-16 (plan_2026_09_16_commit_document_and_pending_direction,
Т3.3): the commit message of 7849367 named `diagnostics/plan_copper_edges_on_
profile.py`, which lives in the GITIGNORED `diagnostics/` — the live numbers
behind "give inter-node copper edges to the components its pads connect" were
therefore not reproducible from a clean checkout. This is that script, moved
into the tracked package unchanged apart from the module-path bootstrap.

PURE: the edges come from the config and the trees alone (kicadstamp.
copper_order resolves each record's `pads:` ends with the project's own role
predicate), so this runs off-line — no KiCad, no adapter, nothing in
profiles/ is written.

It prints, per tree that carries inter-node copper:
  * every copper ref and the nodes it now waits for (the Э2 edges);
  * the plan WITHOUT the edges (the pre-Э2 order — the safety net alone);
  * the plan WITH them, and whether the two differ;
  * for every copper, whether any of its owners is applied AFTER it (the
    guarantee the edges exist for), and every end that resolved to no node.

Run:  .venv/bin/python -m kicadstamp.diagnostics.plan_copper_edges_on_profile [profile]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config.loader import load_config                      # noqa: E402
from kicadstamp.copper_order import copper_node_dependencies          # noqa: E402
from kicadstamp.link_trees import link_trees                          # noqa: E402
from kicadstamp.tree_position import curated_redraw_plan_forest       # noqa: E402

DEFAULT_PROFILE = (Path(__file__).resolve().parents[2] / "profiles"
                   / "3ch-awg-tia-v103" / "config.sexp")


def walk(nodes):
    """Every LinkedNode of a tree, depth-first."""
    for node in nodes:
        yield node
        yield from walk(node.children)


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROFILE
    print(f"profile: {path}")
    cfg, _ctx = load_config(str(path))
    linked = link_trees(cfg, cfg.trees)

    deps = copper_node_dependencies(cfg)
    copper_all = {ln.node.ref for lt in linked for ln in walk(lt.nodes)
                  if ln.record is not None and ln.record.kind == "net_trace"}
    print(f"\ncopper nodes in the whole forest: {len(copper_all)}")
    print(f"copper nodes with edges: {len(deps)}")
    owners_seen = set()
    for copper_ref in sorted(deps):
        owners = sorted(deps[copper_ref])
        owners_seen |= set(owners)
        print(f"  {copper_ref}  ->  {len(owners)} owner(s): {', '.join(owners)}")
    print(f"distinct owning nodes over all copper: {len(owners_seen)}")

    for tree in linked:
        refs = [ln.node.ref for ln in walk(tree.nodes)
                if ln.record is not None and ln.record.kind == "net_trace"]
        if not refs:
            continue
        selected = {ln.node.ref for ln in walk(tree.nodes) if ln.record is not None}
        without, _w1 = curated_redraw_plan_forest([tree], selected)
        with_edges, _w2 = curated_redraw_plan_forest(
            [tree], selected, copper_deps=deps)
        print(f"\n=== tree {tree.name!r}: {len(selected)} records, "
              f"{len(refs)} copper ===")
        for index, name in enumerate(with_edges):
            tag = "COPPER   " if name in copper_all else "component"
            extra = ""
            if name in deps:
                extra = "   <- after " + ", ".join(sorted(deps[name]))
            print(f"  {index + 1:2d}. {tag}  {name}{extra}")
        print("order with the Э2 edges == order without them:",
              with_edges == without)
        order_index = {name: i for i, name in enumerate(with_edges)}
        late = [c for c in refs
                if c in order_index and c in deps
                and any(order_index.get(o, -1) > order_index[c]
                        for o in deps[c])]
        print("copper applied AFTER every one of its owners:",
              "yes" if not late else f"NO — {late}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
