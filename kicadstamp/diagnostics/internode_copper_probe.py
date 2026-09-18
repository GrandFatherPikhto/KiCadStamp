"""Inter-node copper probe — why a tree's copper between pads is (or is not) seen.

Input: a root config, a tree name. `--selection` takes the area exactly like
Tools -> Trees -> "Reread inter-node copper" does (the PCB editor's selection
when it is non-empty, else the whole board); without it the area is the whole
board. `--net TEXT` narrows the unit LISTINGS (never the counts) to nets
containing TEXT.

Expected: five read-only sections and a final CHECK block:
  A  node keys vs board components — for every (Cluster, sheet) key of the
     tree's placement nodes: the area components carrying that Cluster, their
     sheet path, and whether they match by the LEAF segment (the shipping
     internode_capture rule) or by ANY segment (the role-narrowing rule,
     role_narrowing._fp_on_sheet).
  B  copper units and their verdicts under four rules:
     {leaf, any segment} x {shipping walk, corrected walk}. The corrected walk
     is a MODEL written here: identical to internode_copper.find_copper_units
     except that a pad terminates a joint and moors copper ONLY when the pad
     carries the copper's own net. The INTERNODE units of each rule are listed.
  C  wrong-net mooring in the shipping walk: units moored to a pad of ANOTHER
     net, and copper joints the shipping walk splits because they sit on a pad
     box — split by a pad of the copper's own net (legitimate) or only by pads
     of other nets (the same defect; the corrected walk merges those).
  D  anchor: for every INTERNODE unit of the corrected rule, the anchor pad's
     component and how its Role narrows over the WHOLE board through the
     resolver's own sheet -> cluster cascade
     (role_narrowing._narrow_by_sheet_cluster_selection), once per sheet-path
     segment — the leaf is what a new record stores today.
  E  the shipping plan_internode_reread over the same area, its Log report as
     is, then CHECK: the pad signatures the shipping code finds vs the
     corrected rule, and whether each new record's (anchor_sheet,
     anchor_cluster) narrows its role to exactly one footprint.

Background (2026-09-15, profiles/3ch-awg-tia-v103, tree ch0_dac_buf): the
re-read reported "unchanged: 0" and nothing else, while the board carries
PIF<->DAC and PIF<->OpAmp copper. Measured causes: the components live on
nested sheets (['Channel_0', 'DAC']) so the leaf rule maps none of them; B.Cu
bypass-cap GND pads under the ICs catch and split F.Cu signal tracks; a new
record's leaf anchor_sheet ('DAC') exists in every channel.

Live KiCad: Yes. Reads only — nothing is written to the board, the config or
the registry (apply_reread_plan is never called).

Run: python -m kicadstamp.diagnostics.internode_copper_probe <config.sexp> <tree> [--selection] [--net TEXT]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import (
    load_config,
    net_trace_effective_name,
    net_trace_pad_signature,
)
from kicadstamp.constants import ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.internode_capture import (
    _area_components,
    _pad_label,
    _tree_node_keys,
    plan_internode_reread,
    reread_report_lines,
)
from kicadstamp.internode_copper import (
    CopperUnit,
    CopperVerdict,
    PadRef,
    _normalize_pad_number,
    classify_unit,
    find_copper_units,
)
from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.placement.services.role_narrowing import _narrow_by_sheet_cluster_selection
from kicadstamp.sheet_names import resolve_sheet_path_names
from kicadstamp.template_selection import _inflated_boxes, _point_in_box, _points_match
from kicadstamp.utils.layers import layer_to_str
from kicadstamp.utils.units import MM

# How many examples a listing prints before it only counts.
_EXAMPLES = 15


def _mm(point) -> str:
    return f"({point.x / MM:.3f}, {point.y / MM:.3f})"


def _pads_str(pads, pad_net, unit_net) -> str:
    """Pad list; a pad of another net than the unit's copper is marked."""
    out = []
    for p in pads:
        mark = "" if pad_net.get(p) == unit_net else f"[net {pad_net.get(p)}]"
        out.append(f"{p.ref}.{p.pad}{mark}")
    return ", ".join(out) or "-"


def _net_wanted(unit, needle) -> bool:
    return not needle or any(needle in n for n in unit.net_names)


def _joint(a_points, b_points):
    for p in a_points:
        for q in b_points:
            if _points_match(p, q):
                return p
    return None


def _area(adapter, use_selection: bool):
    """The same AREA rule as trees_dock.run_internode_reread_worker."""
    if use_selection:
        selected = list(adapter.get_selected_items() or [])
        if selected:
            fps = [i for i in selected if isinstance(i, Footprint)]
            items = [i for i in selected if isinstance(i, (Track, Via))]
            return fps, items, f"selection ({len(selected)} items)"
    fps = list(adapter.get_footprints())
    items = list(adapter.get_tracks()) + list(adapter.get_vias())
    return fps, items, "whole board"


def corrected_units(tracks, vias, pad_refs, boxes, pad_net, *,
                    same_net_only: bool = True) -> list[CopperUnit]:
    """MODEL of the corrected walk: find_copper_units with ONE change — a pad
    terminates a joint and moors a copper item only when the pad carries the
    item's own net. Everything else (joint matching, ordering, the per-item
    pad tuples) follows the shipping walk.

    `same_net_only=False` switches the one change off; the model must then
    reproduce the shipping walk unit for unit — main() checks exactly that
    before trusting the CORRECTED column."""
    boxes_by_net: dict[str | None, list[tuple[PadRef, object]]] = {}
    for ref, box in zip(pad_refs, boxes):
        if box is not None:
            net_key = pad_net.get(ref) if same_net_only else None
            boxes_by_net.setdefault(net_key, []).append((ref, box))

    def own_pads_at(point, net):
        return [ref for ref, box in boxes_by_net.get(
                    net if same_net_only else None, ())
                if _point_in_box(point, box)]

    parent = {("t", i): ("t", i) for i in range(len(tracks))}
    parent.update({("v", j): ("v", j) for j in range(len(vias))})

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, t in enumerate(tracks):
        for k in range(i + 1, len(tracks)):
            o = tracks[k]
            p = _joint((t.start, t.end), (o.start, o.end))
            if p is not None and not own_pads_at(p, t.net_name):
                union(("t", i), ("t", k))
        for j, v in enumerate(vias):
            p = _joint((t.start, t.end), (v.position,))
            if p is not None and not own_pads_at(p, t.net_name):
                union(("t", i), ("v", j))

    item_pads: dict = {}
    for i, t in enumerate(tracks):
        item_pads[("t", i)] = set(own_pads_at(t.start, t.net_name)) | \
            set(own_pads_at(t.end, t.net_name))
    for j, v in enumerate(vias):
        item_pads[("v", j)] = set(own_pads_at(v.position, v.net_name))

    order, by_root, unit_pads = [], {}, {}
    keyed = [(("t", i), t) for i, t in enumerate(tracks)] + \
            [(("v", j), v) for j, v in enumerate(vias)]
    for key, item in keyed:
        root = find(key)
        if root not in by_root:
            by_root[root] = CopperUnit()
            order.append(root)
        unit = by_root[root]
        (unit.tracks if key[0] == "t" else unit.vias).append(item)
        (unit.track_pads if key[0] == "t" else unit.via_pads).append(
            tuple(sorted(item_pads[key])))
        unit_pads.setdefault(root, set()).update(item_pads[key])
    units = []
    for root in order:
        by_root[root].pads = tuple(sorted(unit_pads.get(root, ())))
        units.append(by_root[root])
    return units


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config")
    parser.add_argument("tree")
    parser.add_argument("--selection", action="store_true",
                        help="take the area like the menu entry (selection, else board)")
    parser.add_argument("--net", default=None,
                        help="list only units whose net contains this text")
    args = parser.parse_args()

    cfg, ctx = load_config(args.config)
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    tree = next((t for t in cfg.trees if t.name == args.tree), None)
    if tree is None:
        print(f"tree {args.tree!r} not found; trees: {[t.name for t in cfg.trees]}")
        return 2

    adapter = create_board_adapter(config_path=args.config)
    adapter.refresh_board()
    board_fps = list(adapter.get_footprints())
    fps, items, area_label = _area(adapter, args.selection)
    tracks = [i for i in items if isinstance(i, Track)]
    vias = [i for i in items if isinstance(i, Via)]
    print(f"config: {args.config}   tree: {tree.name}")
    print(f"area: {area_label} — {len(fps)} footprints, {len(tracks)} tracks, "
          f"{len(vias)} vias; sheet dictionary: "
          f"{len(set(sheet_names.values()))} names")

    # ── A: node keys vs board components ──────────────────────────────────
    keys = _tree_node_keys(tree, cfg)
    comps = _area_components(adapter, fps, sheet_names)
    paths = {ref: list(resolve_sheet_path_names(c.fp, sheet_names))
             for ref, c in comps.items()}

    def label_leaf(c):
        # Mirrors internode_capture.plan_internode_reread (the shipping rule).
        if c.cluster is None:
            return None
        return keys.get((c.cluster, c.sheet)) or keys.get((c.cluster, None))

    ambiguous: list[str] = []

    def label_any(c):
        if c.cluster is None:
            return None
        labels = {keys[(c.cluster, s)] for s in paths[c.ref]
                  if s and (c.cluster, s) in keys}
        if len(labels) > 1:
            ambiguous.append(c.ref)
            return None
        if labels:
            return labels.pop()
        return keys.get((c.cluster, None))

    by_leaf = {r: lab for r, c in comps.items() if (lab := label_leaf(c))}
    by_any = {r: lab for r, c in comps.items() if (lab := label_any(c))}

    print("\n== A. node keys (cluster, sheet) vs area components ==")
    for (cluster, sheet), label in keys.items():
        members = sorted(r for r, c in comps.items() if c.cluster == cluster)
        print(f"  node {label!r}: key ({cluster!r}, {sheet!r}) — "
              f"{len(members)} component(s) with that Cluster in the area")
        for ref in members:
            c = comps[ref]
            print(f"    {ref:6} path={paths[ref]} leaf={c.sheet!r:12} "
                  f"leaf-match={'yes' if ref in by_leaf else 'NO'}  "
                  f"any-segment-match={'yes' if ref in by_any else 'NO'}")
    print(f"  mapped to a node: leaf rule {len(by_leaf)}, any-segment rule {len(by_any)}"
          + (f"; AMBIGUOUS under any-segment (two keys match): {ambiguous}"
             if ambiguous else ""))

    # ── B: units and verdicts under four rules ───────────────────────────
    pads, pad_refs, pad_layer = [], [], {}
    for fp in fps:
        for pad in adapter.get_footprint_pads(fp):
            ref = PadRef(ref=fp.ref, pad=_normalize_pad_number(pad.number))
            pads.append(pad)
            pad_refs.append(ref)
            layer = getattr(fp, "layer", None)
            pad_layer[ref] = layer_to_str(layer) if layer is not None else "?"
    pad_net = {ref: pad.net_name for ref, pad in zip(pad_refs, pads)}
    boxes = _inflated_boxes(adapter, pads) if pads else []

    units, unit_warnings = find_copper_units(adapter, items, footprints=fps)
    fixed = corrected_units(tracks, vias, pad_refs, boxes, pad_net)

    def shape(us):
        return sorted((tuple(sorted(map(id, u.tracks))), tuple(sorted(map(id, u.vias))),
                       u.pads) for u in us)

    # Self-check of the model: with its one change switched off it must be the
    # shipping walk, or the CORRECTED column below measures the model, not the
    # rule.
    model_ok = shape(corrected_units(tracks, vias, pad_refs, boxes, pad_net,
                                     same_net_only=False)) == shape(units)
    print(f"\n== B. copper units: shipping walk {len(units)} "
          f"({len(unit_warnings)} carry several nets — see C), "
          f"corrected walk {len(fixed)} ==")
    print(f"  model self-check (corrected walk with the net rule OFF == shipping "
          f"walk): {'OK' if model_ok else 'FAILED — do not trust the CORRECTED column'}")
    rules = {
        "leaf sheet + shipping walk (SHIPPING)": (units, by_leaf),
        "any segment + shipping walk": (units, by_any),
        "leaf sheet + corrected walk": (fixed, by_leaf),
        "any segment + corrected walk (CORRECTED)": (fixed, by_any),
    }
    corrected: list = []
    for name, (us, mapping) in rules.items():
        verdicts = Counter(classify_unit(u, mapping).value for u in us)
        internode = [u for u in us
                     if classify_unit(u, mapping) is CopperVerdict.INTERNODE]
        if name.endswith("(CORRECTED)"):
            corrected = internode
        print(f"  {name}: {dict(verdicts)}")
        for u in internode:
            if _net_wanted(u, args.net):
                print(f"      INTERNODE {u.net_name}: "
                      f"{_pads_str(u.pads, pad_net, u.net_name)}"
                      f" — {len(u.tracks)} track(s), {len(u.vias)} via(s)")

    # ── C: wrong-net mooring and split joints (shipping walk) ────────────
    print("\n== C. shipping walk: mooring to pads of another net ==")
    wrong = [u for u in units
             if any(pad_net.get(p) != u.net_name for p in u.pads)]
    print(f"  units moored to at least one pad of another net: "
          f"{len(wrong)} of {len(units)}")
    shown = 0
    for u in wrong:
        if shown >= _EXAMPLES or not _net_wanted(u, args.net):
            continue
        layers = sorted({layer_to_str(t.layer) for t in u.tracks})
        foreign = [f"{p.ref}.{p.pad} (net {pad_net.get(p)}, {pad_layer.get(p)})"
                   for p in u.pads if pad_net.get(p) != u.net_name]
        print(f"    {u.net_name} on {layers or ['via only']}: {', '.join(foreign)}")
        shown += 1
    for w in unit_warnings[:_EXAMPLES]:
        print(f"    several nets: {w}")

    def pads_at(point):
        return [pad_refs[i] for i, b in enumerate(boxes)
                if b is not None and _point_in_box(point, b)]

    split = {"own": 0, "foreign": 0}
    examples: list[str] = []
    for i, t in enumerate(tracks):
        others = [(o.start, o.end) for o in tracks[i + 1:]] + \
                 [(v.position,) for v in vias]
        for points in others:
            p = _joint((t.start, t.end), points)
            if p is None:
                continue
            here = pads_at(p)
            if not here:
                continue
            if any(pad_net.get(r) == t.net_name for r in here):
                split["own"] += 1
            else:
                split["foreign"] += 1
                if len(examples) < _EXAMPLES and (
                        not args.net or args.net in (t.net_name or "")):
                    examples.append(
                        f"{t.net_name} at {_mm(p)} split by "
                        + ", ".join(f"{r.ref}.{r.pad} (net {pad_net.get(r)})"
                                    for r in here))
    print(f"  copper joints split because they sit on a pad: {split['own']} by a "
          f"pad of the copper's own net, {split['foreign']} ONLY by pads of "
          f"another net")
    for e in examples:
        print(f"    {e}")

    # ── D: anchor of the corrected units ─────────────────────────────────
    print("\n== D. anchor: does (sheet, cluster) narrow the anchor Role to one "
          "footprint? ==")
    role_of = {fp.ref: adapter.get_field_value(fp, ROLE_FIELD_NAME)
               for fp in board_fps}

    def narrow(role, sheet, cluster):
        candidates = [fp for fp in board_fps if role_of.get(fp.ref) == role]
        got = _narrow_by_sheet_cluster_selection(
            candidates, adapter, set(), sheet, cluster, sheet_names,
            "internode_copper_probe", role)
        return candidates, got

    for u in corrected:
        if not _net_wanted(u, args.net):
            continue
        anchor = u.pads[0]
        c = comps.get(anchor.ref)
        if c is None or not c.role:
            print(f"  {u.net_name}: anchor pad {anchor.ref}.{anchor.pad} has no "
                  f"Role — cannot anchor")
            continue
        print(f"  {u.net_name}: anchor {anchor.ref}.{anchor.pad} role={c.role!r} "
              f"cluster={c.cluster!r}")
        for seg in dict.fromkeys(s for s in paths[anchor.ref] if s):
            candidates, got = narrow(c.role, seg, c.cluster)
            tag = "  <- the leaf a new record stores today" if seg == c.sheet else ""
            verdict = "ONE" if len(got) == 1 else "AMBIGUOUS"
            print(f"      sheet {seg!r:12} + cluster: {len(candidates)} -> "
                  f"{sorted(f.ref for f in got)} ({verdict}){tag}")

    # ── E: the shipping planner, and the check ───────────────────────────
    print("\n== E. shipping plan_internode_reread over the same area "
          "(nothing written) ==")
    plan = plan_internode_reread(adapter, cfg, tree, area_items=items,
                                 area_footprints=fps, sheet_names=sheet_names)
    for line in reread_report_lines(tree.name, plan):
        print(f"  | {line}")

    shipping = {c.signature for c in plan.added}
    shipping |= {c.signature for c, _counts in plan.updated}
    unchanged = set(plan.unchanged)
    shipping |= {frozenset(net_trace_pad_signature(nt) or ())
                 for nt in cfg.net_traces
                 if net_trace_effective_name(nt) in unchanged}
    wanted = {frozenset(_pad_label(p, comps) for p in u.pads) for u in corrected}
    print("\n== CHECK ==")
    if shipping != wanted:
        verdict = "DIFFER"
    elif not wanted:
        # 0 == 0 is not an acceptance: measured 2026-09-15 with IC2 rotated to
        # 315 deg, KiCad returned every pad box shifted 1.724 mm off its pad, no
        # copper moored at all, and both sides found nothing.
        verdict = ("MATCH ON NOTHING — not a pass: check the anchor footprint's "
                   "angle (pad boxes of a non-orthogonal footprint may be shifted)")
    else:
        verdict = "MATCH"
    print(f"  bridges: shipping finds {len(shipping)}, corrected rule finds "
          f"{len(wanted)} -> {verdict}")
    for sig in sorted(wanted - shipping, key=sorted):
        print(f"    only in corrected rule: {sorted(sig)}")
    for sig in sorted(shipping - wanted, key=sorted):
        print(f"    only in shipping code:  {sorted(sig)}")
    for c in plan.added:
        rec = c.record
        _candidates, got = narrow(rec.anchor_role, rec.anchor_sheet,
                                  rec.anchor_cluster)
        print(f"  new record {c.identity!r}: anchor role={rec.anchor_role!r} "
              f"sheet={rec.anchor_sheet!r} cluster={rec.anchor_cluster!r} -> "
              f"{sorted(f.ref for f in got)} "
              + ("OK" if len(got) == 1 else "AMBIGUOUS"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
