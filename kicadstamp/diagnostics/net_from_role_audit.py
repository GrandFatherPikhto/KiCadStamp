#!/usr/bin/env python3
"""
net_from_role_audit.py — audits the net_from_role algorithm on real cells.

What it checks (see techdocs/handoff/deepseek/handoff_2026_08_11_net_resolution_
by_role_math_analysis.md, sections 6/8a):

For every via/track of every cell in --templates, classify its net into one of:
  - rule     : net is a rule net (GND by default) -> net: null already works
  - lemma2   : there is a role whose non-rule nets are exactly {this net}
               -> net_from_role: <role> (no pad needed)
  - pad      : the only roles on this net are multi-net roles (LDO IN/OUT, FB)
               -> net_from_role: <role>, pad: <N> required
  - fallback : M(n) is empty (no role pad is on this net) -> literal/manual

CRITICAL: a Role is NOT unique on the board — the same role (C_OUT_BULK) is
reused by many clusters on different nets. So the audit is scoped per CLUSTER:
the role->net graph is built only from components whose Cluster field matches
the cell's cluster (taken from clone_placements in --profile). Without that
scope, multi-cluster roles look falsely "multi-net" and pad is over-reported.

Then audits the autoweight heuristic (w(n) = degree of net in the role-net
bipartite graph) per cluster: for every multi-net role it reports how many
via/tracks sit on the "main" (max-degree) net vs on non-main nets. Non-main
nets PROVE that autoweight cannot replace the pad (section 6 of the analysis).

The role/net graph (cluster_role_nets) can come from either --netlist (a
KiCad .net export -- offline, works without KiCad running) or --board (kipy
IPC straight off the live board -- no manual Export Netlist step; requires
the board to be up to date, i.e. Update PCB from Schematic already run). If
both are given, --netlist wins. --board alone is normally enough once the
board is live and up to date -- that is the point: on a section that's
already routed, nets are already derivable from Role+Cluster, no hand-typed
net list needed.

Inputs:
  --templates <dir>       cell YAML files (profiles/*/templates/*.yaml)
  --netlist <file>        optional: KiCad .net file (full graph, needs Cluster)
  --board                 optional: build the graph live via kipy instead of
                          --netlist (also runs the geometry fake-run, see below)
  --profile <file>        optional: profile YAML with clone_placements
                          (template -> params + anchor_cluster)
  --rule-nets GND,...     rule nets (default: GND)
  --json <file>           optional: write machine-readable report

Run (offline, from a .net export):
    python -m kicadstamp.diagnostics.net_from_role_audit \
        --templates <dir> --netlist <proj>.net --profile power.yaml

Run (live, no .net export needed):
    python -m kicadstamp.diagnostics.net_from_role_audit \
        --templates <dir> --profile power.yaml --board
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml  # noqa: E402

from kicadstamp.cloner.sexp import child, children, atom, load_file, sval  # noqa: E402
import sexpdata  # noqa: E402

try:  # live-board mode (--board) needs kipy + the adapter; keep it optional
    from kipy.geometry import Vector2  # noqa: E402
    from kicadstamp.kicad.adapter import KiCadBoardAdapter  # noqa: E402
    from kicadstamp.utils.units import MM  # noqa: E402
    from kicadstamp.geometry.spoke_layout import local_to_absolute  # noqa: E402
    from kicadstamp.geometry.clone_geometry import _mirror_x  # noqa: E402
    from kicadstamp.placement.services.component_pool import cluster_prefix_match  # noqa: E402
    _LIVE_AVAILABLE = True
except Exception:  # pragma: no cover - import failure only on odd environments
    _LIVE_AVAILABLE = False

ROLE_FIELD_NAME = "Role"
CLUSTER_FIELD_NAME = "Cluster"
RULE_NETS_DEFAULT = {"GND"}


# --------------------------------------------------------------------------
# .net graph
# --------------------------------------------------------------------------

def _field_value(field_node) -> str:
    """Value of a (field (name "X") VALUE) node — the last atom.

    sexpdata.Symbol subclasses str, so `isinstance(x, str)` is True even for
    the node's own leading symbol ('field'). An EMPTY field
    `(field (name "Cluster"))` would otherwise be mis-read as value 'field'.
    We take the last atom that is not the leading symbol; nested lists
    ((name ...) child) are excluded.
    """
    atoms = [item for item in field_node[1:]
             if isinstance(item, str) or isinstance(item, sexpdata.Symbol)]
    return str(sval(atoms[-1])) if atoms else ""


def _comp_field(c, name: str) -> str:
    for f in children(child(c, "fields") or [], "field"):
        if atom(f, "name") == name:
            return _field_value(f)
    return ""


def build_netlist_graph(net_path: str):
    """Parse a KiCad .net file into per-cluster, per-role graphs.

    Returns:
        cluster_role_nets : dict[cluster, dict[role, dict[pad, set[net]]]]
        cluster_roles     : dict[cluster, set[role]]
    """
    root = load_file(net_path)

    # ref -> (role, cluster)
    ref_meta: dict[str, tuple[str, str]] = {}
    for c in children(child(root, "components") or [], "comp"):
        ref = atom(c, "ref")
        role = _comp_field(c, ROLE_FIELD_NAME)
        cluster = _comp_field(c, CLUSTER_FIELD_NAME)
        if ref and role:
            ref_meta[ref] = (role, cluster)

    # ref -> {pad: {nets}}
    ref_pad_nets: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for n in children(child(root, "nets") or [], "net"):
        net = atom(n, "name")
        if not net:
            continue
        for node in children(n, "node"):
            ref = atom(node, "ref")
            pin = atom(node, "pin")
            if ref and pin:
                ref_pad_nets[ref][pin].add(net)

    cluster_role_nets: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set)))
    cluster_roles: dict[str, set[str]] = defaultdict(set)
    for ref, (role, cluster) in ref_meta.items():
        for pad, nets in ref_pad_nets.get(ref, {}).items():
            cluster_role_nets[cluster][role][pad] |= nets
            cluster_roles[cluster].add(role)

    return cluster_role_nets, cluster_roles


def build_netlist_graph_live(adapter):
    """Same shape as build_netlist_graph(), read straight off the live board
    via kipy instead of a KiCad .net export.

    This is what makes --board self-sufficient: on a board that's already
    been updated from schematic, Role/Cluster/net are all live IPC data --
    there is no reason to make the user do File > Export Netlist by hand
    first. --netlist stays for offline analysis (archived .net, KiCad not
    running); when both are given, --netlist wins (see main()).
    """
    cluster_role_nets: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set)))
    cluster_roles: dict[str, set[str]] = defaultdict(set)
    for fp in adapter.get_footprints():
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if not role:
            continue
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
        for p in adapter.get_footprint_pads(fp):
            # kipy gives unconnected pads a truthy Net object with an EMPTY
            # name, not None -- "if not p.net" alone lets a bogus "" net in.
            if not p.net or not p.net.name:
                continue
            cluster_role_nets[cluster][role][str(p.number)].add(p.net.name)
            cluster_roles[cluster].add(role)
    return cluster_role_nets, cluster_roles


# --------------------------------------------------------------------------
# Cell / profile loading
# --------------------------------------------------------------------------

def load_cells(templates_dir: str) -> dict[str, dict]:
    """Handles both the current layout (cells nested under a top-level
    `cells:` key, since the 2026-08-02 "cells include unification") and the
    legacy flat layout (cell_name: {...} directly at the file's top level,
    still found in old worktrees/snapshots) — a file can use either."""
    cells: dict[str, dict] = {}
    for path in sorted(Path(templates_dir).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        candidates = list((data.get("cells") or {}).items()) + list(data.items())
        for name, cell in candidates:
            if isinstance(cell, dict) and ("components" in cell or "vias" in cell):
                cells[name] = cell
    return cells


def load_placements_by_template(profile_path: str | None) -> dict[str, list[dict]]:
    """Extract {cell_name: [{params, cluster}, ...]} from a profile's
    extract_profiles: section — that's where params/net_template_role
    actually live in the current schema, keyed by profile key with an
    explicit `name:` (the cell name), NOT on clone_placements: entries
    (those resolve nets live via anchor_cluster at apply time and mostly
    carry no params: at all in this project)."""
    if not profile_path:
        return {}
    data = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8")) or {}
    out: dict[str, list[dict]] = defaultdict(list)
    for key, entry in (data.get("extract_profiles") or {}).items():
        if not isinstance(entry, dict):
            continue
        cell_name = entry.get("name") or key
        out[cell_name].append({
            "params": entry.get("params") or {},
            "cluster": "",
        })
    # anchor_cluster (the actual cluster a cell got placed at) lives on
    # clone_placements:, keyed by which cell: they reference — attach it to
    # the matching extract_profiles entry/entries for that cell so
    # matching_clusters()/classify_cell() still get a real cluster to scope by.
    # Also carry the clone_placement's OWN params ({PWR_IN}/{PWR_OUT}->+3V3 etc.)
    # — they are what actually resolve the cell's placeholders per instance.
    for cp in data.get("clone_placements", []) or []:
        cell_name = cp.get("cell")
        cluster = cp.get("anchor_cluster") or cp.get("cluster") or ""
        if cell_name in out and cluster:
            out[cell_name].append({"params": cp.get("params") or {}, "cluster": cluster})
    return dict(out)


def _clone_placements_from_profile(profile_path: str | None) -> list[dict]:
    """Raw clone_placements: list (for --board fake-run, which needs the
    anchor_role/anchor_pad/rotation of each placement to resolve the origin)."""
    if not profile_path:
        return []
    data = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8")) or {}
    return list(data.get("clone_placements", []) or [])


def resolve(template: str | None, params: dict) -> str | None:
    if template is None:
        return None
    try:
        return template.format(**params)
    except KeyError:
        return template


# --------------------------------------------------------------------------
# Classification (cluster-scoped)
# --------------------------------------------------------------------------

# Role-name synonyms across the profile/schematic boundary. The schematic and
# the hand-written cells sometimes name the same part differently (only the
# ferrite bead, so far): FB_PI_FLT (cell) vs PI_FB (schematic). Canonicalise
# both sides to the same token before matching.
_ROLE_SYNONYMS = {
    "fb_pi_flt": "pi_fb",
}


def _canonical_role(role: str) -> str:
    """casefold + synonym-normalise a role name for matching."""
    key = role.casefold()
    return _ROLE_SYNONYMS.get(key, key)


def _casefold_index(role_nets: dict) -> dict[str, str]:
    """{canonical role -> actual role in cluster} for a cluster's role->nets map.

    Profile role names may differ from the schematic's in case
    (C_In_Bulk in the cell vs C_IN_BULK in the .net) or by synonym
    (FB_PI_FLT vs PI_FB) — canonicalise before matching.
    """
    return {_canonical_role(r): r for r in role_nets}

def _role_net_sets(role_nets: dict[str, dict[str, set[str]]], role: str):
    """(all_nets, {pad: net}) for one role in one cluster."""
    pads = role_nets.get(role, {})
    all_nets = set()
    for ns in pads.values():
        all_nets |= ns
    return all_nets, pads


def _local_points(kind: str, entry: dict) -> list[tuple[float, float]]:
    """Local (along, across) points of a via (1) or track (2 endpoints).

    Cell coordinates are rotation-invariant among themselves: distance between
    two local points does not change under the cell's rotation, so choosing the
    nearest component by local distance is valid without the absolute transform.
    """
    if kind == "via":
        return [(float(entry.get("offset_along_mm", 0.0)),
                 float(entry.get("offset_across_mm", 0.0)))]
    return [
        (float(entry.get("start_along_mm", 0.0)),
         float(entry.get("start_across_mm", 0.0))),
        (float(entry.get("end_along_mm", 0.0)),
         float(entry.get("end_across_mm", 0.0))),
    ]


def _dist_to_component(points: list[tuple[float, float]], comp: dict) -> float:
    """Min Euclidean distance (mm) from a via/track's points to a component's
    local centre. Uses only local cell coordinates (rotation-invariant)."""
    cx, cy = float(comp.get("offset_along_mm", 0.0)), float(comp.get("offset_across_mm", 0.0))
    best = float("inf")
    for px, py in points:
        d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        if d < best:
            best = d
    return best


def _comp_by_canonical(comps: list[dict], role: str) -> dict:
    """Find a cell component slot whose role canonicalises to `role`.

    The schematic role (e.g. 'C_IN_BULK') may differ in case from the cell's
    ('C_In_Bulk') or by synonym ('PI_FB' vs 'FB_PI_FLT') — match canonically.
    """
    want = _canonical_role(role)
    for c in comps:
        if _canonical_role(str(c.get("role") or "")) == want:
            return c
    return {"offset_along_mm": 0.0, "offset_across_mm": 0.0}


def classify_cell(cell_name: str, cell: dict, params: dict, cluster: str,
                  cluster_role_nets: dict, rule_nets: set[str],
                  use_geometry: bool = True) -> dict:
    items = []
    for v in cell.get("vias", []) or []:
        items.append(("via", v.get("net"), v))
    for t in cell.get("tracks", []) or []:
        items.append(("track", t.get("net"), t))

    cell_roles = {c.get("role") for c in cell.get("components", []) or []}
    role_nets = cluster_role_nets.get(cluster, {})
    # Case-insensitive role match (profile may write C_In_Bulk, schematic C_IN_BULK).
    role_index = _casefold_index(role_nets)
    comps = cell.get("components", []) or []

    stats = defaultdict(int)
    details = []
    for kind, net_tpl, entry in items:
        net = resolve(net_tpl, params)
        if net is None:
            cat, note = "rule", "net: null (rule net)"
        elif net in rule_nets:
            cat, note = "rule", f"net {net!r} is a rule net -> net: null"
        elif not role_nets:
            cat, note = "unresolved", (
                f"cluster {cluster!r} absent from netlist graph (no live data)")
        else:
            candidates = [role_index[_canonical_role(r)] for r in cell_roles
                          if _canonical_role(r) in role_index
                          and net in _role_net_sets(role_nets, role_index[_canonical_role(r)])[0]]
            if not candidates:
                cat, note = "fallback", f"no role pad on {net!r} (M(n) empty)"
            else:
                # Prefer a role whose non-rule nets == {net} (lemma 2).
                picked = None
                lemma2_roles = []
                for r in sorted(candidates):
                    all_nets, _ = _role_net_sets(role_nets, r)
                    non_rule = {n for n in all_nets if n not in rule_nets}
                    if non_rule == {net}:
                        lemma2_roles.append(r)
                if len(lemma2_roles) == 1:
                    picked = ("lemma2", f"net_from_role: {lemma2_roles[0]} (single non-rule net)")
                elif len(lemma2_roles) > 1:
                    # Multiple single-net roles on the same net — geometry
                    # picks the physically nearest one (|R(n)| > 1, common bus).
                    if use_geometry:
                        pts = _local_points(kind, entry)
                        r = min(lemma2_roles,
                                key=lambda rr: _dist_to_component(pts, _comp_by_canonical(comps, rr)))
                        picked = ("lemma2", f"net_from_role: {r} (nearest of {sorted(lemma2_roles)})")
                    else:
                        r = lemma2_roles[0]
                        picked = ("lemma2", f"net_from_role: {r} (single non-rule net)")
                else:
                    # All candidates are multi-net roles -> pad required; pick
                    # the nearest one by geometry when ambiguous.
                    if use_geometry and len(candidates) > 1:
                        pts = _local_points(kind, entry)
                        r = min(candidates,
                                key=lambda rr: _dist_to_component(pts, _comp_by_canonical(comps, rr)))
                    else:
                        r = sorted(candidates)[0]
                    all_nets, pads = _role_net_sets(role_nets, r)
                    pad_txt = sorted(p for p, ns in pads.items() if net in ns)[0]
                    picked = ("pad", f"net_from_role: {r}, pad: {pad_txt} (multi-net role)")
                cat, note = picked
        stats[cat] += 1
        # Fake-run geometry info for ambiguous |R(n)|>1 cases: candidates with
        # local distances, so the report shows WHERE the via/track would land.
        geo = None
        if cat in ("lemma2", "pad") and len(candidates) > 1:
            pts = _local_points(kind, entry)
            geo = [{"role": r,
                    "dist_mm": round(_dist_to_component(pts, _comp_by_canonical(comps, r)), 3)}
                   for r in sorted(candidates, key=lambda rr: _dist_to_component(
                       pts, _comp_by_canonical(comps, rr)))]
        details.append({
            "kind": kind, "net": net, "category": cat, "note": note,
            "geometry": geo,
            "pos": {k: entry.get(k) for k in ("offset_along_mm", "offset_across_mm",
                                              "start_along_mm", "end_along_mm")},
        })

    return {"cell": cell_name, "cluster": cluster, "stats": dict(stats), "details": details}


def _params_for_cluster(uses: list[dict], cluster: str,
                        cell: dict | None = None, role_nets: dict | None = None) -> dict:
    """Pick the profile placement whose params actually belong to `cluster`.

    Primary signal: cluster-name token overlap. Profile names use
    'Pi_Filter_1V2_VCCINT', schematic (.net) names use 'PIF_1V2_VCCINT' —
    same section, different prefix, tokens still overlap ('1v2', 'vccint').

    That signal is ABSENT for cells whose clone_placements' anchor_cluster
    tags the ANCHOR's own cluster, not this cell's (e.g. ldo_adj_pm2v5:
    anchor_role=Conn_pm5v, anchor_cluster is None or 'Conn_pm5v' for BOTH
    the P2V5 and N2V5 instances — neither shares a token with
    'LDO_ADJ_P2V5'/'LDO_ADJ_N2V5', found 2026-08-11 comparing those two
    clusters). Falls back to a stronger signal there: resolve every
    via/track net template with each candidate's params and count how many
    of the results actually appear in the target cluster's live role->net
    graph. Wrong params (N2V5 classified with P2V5's PWR_OUT=+2V5) resolve
    to nets absent from that cluster -- zero hits -- while the right params
    line up with the real board.
    """
    if not uses:
        return {}
    if len(uses) == 1:
        return uses[0]["params"]

    tokens = set(cluster.lower().split("_"))
    scored = [(len(tokens & set((u.get("cluster") or "").lower().split("_"))), u) for u in uses]
    best_score = max(s for s, _ in scored)
    if best_score > 0:
        return max(scored, key=lambda t: t[0])[1]["params"]

    if not cell or not role_nets:
        return uses[0]["params"]

    all_cluster_nets = {n for pads in role_nets.values() for ns in pads.values() for n in ns}
    templates = [v.get("net") for v in cell.get("vias", []) or []]
    templates += [t.get("net") for t in cell.get("tracks", []) or []]

    def _hits(params: dict) -> int:
        return sum(1 for tpl in templates if resolve(tpl, params) in all_cluster_nets)

    return max(uses, key=lambda u: _hits(u["params"]))["params"]


def _clones_for_cluster(clone_placements: list[dict], wanted_cluster: str, cell: dict,
                        cluster_role_nets: dict, rule_nets: set[str]) -> list[dict]:
    """clone_placements (for one cell) whose OWN params actually belong to
    wanted_cluster, verified against the live role->net graph.

    anchor_cluster CANNOT be used for this: it tags the ANCHOR's own cluster
    (e.g. 'Conn_pm5v', to narrow which Conn_pm5v to anchor from), not this
    cell's placement cluster -- for ldo_adj_pm2v5 it's None or 'Conn_pm5v'
    for BOTH the P2V5 and N2V5 instances, matching neither. Matching by
    anchor_cluster TOKENS instead (tried first) also produces false
    positives: 'Pi_Filter_1V2_VCCA_P2V5' and 'LDO_ADJ_P2V5' share the
    'p2v5' token despite being unrelated clusters (found 2026-08-11 -- the
    PIF fake-run wrongly appeared instead of LDO_ADJ's own, which vanished
    entirely for the same anchor_cluster reason).

    Instead: resolve every via/track net template with each candidate's OWN
    params, and require ALL of the resulting non-rule nets (GND is
    excluded -- present in every cluster, so uninformative) to actually
    appear in wanted_cluster's live net set. A wrong cluster's params
    resolve to nets absent from wanted_cluster -- the check fails cleanly.
    """
    role_nets = cluster_role_nets.get(wanted_cluster, {})
    if not role_nets:
        return []
    all_nets = {n for pads in role_nets.values() for ns in pads.values() for n in ns}
    templates = [v.get("net") for v in cell.get("vias", []) or []]
    templates += [t.get("net") for t in cell.get("tracks", []) or []]

    matched = []
    for cp in clone_placements:
        params = cp.get("params") or {}
        resolved = {resolve(tpl, params) for tpl in templates if tpl is not None}
        non_rule = {n for n in resolved if n not in rule_nets}
        if non_rule and non_rule <= all_nets:
            matched.append(cp)
    return matched


def matching_clusters(cell_roles: set[str], cluster_role_nets: dict,
                      cluster_roles: dict) -> list[str]:
    """Clusters best matching the cell's role set, in priority order.

    Full coverage (every cell role present) is ideal; when the netlist uses
    slightly different role names than the template (live drift, e.g.
    'PI_FILTER_FB' vs 'PI_FB') or Cluster is empty in the .net, fall back to
    clusters with the LARGEST role intersection, so the audit still runs on the
    closest real cluster instead of silently skipping.
    """
    wanted = {_canonical_role(r) for r in cell_roles if r}
    if not wanted:
        return []
    scored = sorted(
        ((len(wanted & {_canonical_role(r) for r in roles}), cluster)
         for cluster, roles in cluster_roles.items()),
        key=lambda t: (-t[0], t[1]))
    best = scored[0][0] if scored else 0
    return [c for score, c in scored if score == best and score > 0]


# --------------------------------------------------------------------------
# Autoweight audit (cluster-scoped)
# --------------------------------------------------------------------------

def audit_autoweight(cell: dict, params: dict, cluster: str,
                     cluster_role_nets: dict, cluster_roles: dict,
                     rule_nets: set[str]) -> list[dict]:
    role_nets = cluster_role_nets.get(cluster, {})
    roles_in_cluster = cluster_roles.get(cluster, set())

    items = []
    for v in cell.get("vias", []) or []:
        items.append(("via", v.get("net")))
    for t in cell.get("tracks", []) or []:
        items.append(("track", t.get("net")))

    rows = []
    for c in cell.get("components", []) or []:
        role = c.get("role")
        if not role or role not in role_nets:
            continue
        all_nets, _ = _role_net_sets(role_nets, role)
        non_rule = {n for n in all_nets if n not in rule_nets}
        if len(non_rule) < 2:
            continue  # lemma-2 role, autoweight not needed

        degree = {n: sum(1 for r in roles_in_cluster
                         if n in _role_net_sets(role_nets, r)[0])
                  for n in non_rule}
        main_net = max(non_rule, key=lambda n: (degree[n], n))

        covered = main_fail = 0
        for _kind, net_tpl in items:
            net = resolve(net_tpl, params)
            if net in non_rule:
                if net == main_net:
                    covered += 1
                else:
                    main_fail += 1

        rows.append({
            "role": role,
            "non_rule_nets": sorted(non_rule),
            "degree": {n: degree[n] for n in sorted(non_rule)},
            "main_net": main_net,
            "via_tracks_on_main": covered,
            "via_tracks_on_other": main_fail,
            "autoweight_covers_all": main_fail == 0,
        })
    return rows


# --------------------------------------------------------------------------
# Live board fake-run (--board, IPC via kipy)
# --------------------------------------------------------------------------

def load_live_board():
    """Connect to the live KiCad board; return (adapter, pads_by_ref).

    pads_by_ref[ref] = [(x_mm, y_mm, pad_number, net), ...] — real pad
    coordinates from the board, so the fake-run can measure distance to the
    actual PAD (not just the component centre).
    """
    if not _LIVE_AVAILABLE:
        raise RuntimeError("live-board mode requires kipy/kicadstamp.kicad.adapter")
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    pads_by_ref = {}
    for fp in adapter.get_footprints():
        ref = fp.reference_field.text.value
        pads = []
        for p in adapter.get_footprint_pads(fp):
            # Same empty-name-net quirk as build_netlist_graph_live() --
            # exclude it here too, otherwise "nearest pad" can land on a
            # pad that isn't actually connected to anything.
            if not p.net or not p.net.name:
                continue
            pads.append((int(p.position.x), int(p.position.y), p.number, p.net.name))
        if pads:
            pads_by_ref[ref] = pads
    return adapter, pads_by_ref


def _anchor_origin(adapter, clone: dict, pads_by_ref: dict):
    """Resolve the cell's absolute origin from a clone_placement's anchor.

    Returns (Vector2 origin, rotation_deg, mirror) or None if the anchor
    cannot be resolved (no live board, anchor_role missing, or ambiguous).
    Vector2 units are nanometres (int), matching local_to_absolute.

    anchor_role is NOT unique on the board (same caveat as the netlist-graph
    side of this script, see module docstring) — narrow by anchor_cluster,
    same rule the real resolver uses (resolve_footprint_by_role /
    cluster_prefix_match). Unlike the real resolver this does not also narrow
    by anchor_sheet or the live board selection (no sheet_names/selection
    state available here) — if candidates remain ambiguous after
    anchor_cluster, this is reported and skipped rather than guessed.
    """
    role = clone.get("anchor_role")
    if not role:
        return None
    want_pad = str(clone.get("anchor_pad") or "1")
    rot = float(clone.get("rotation_deg") or 0.0)
    mirror = bool(clone.get("mirror"))
    anchor_cluster = clone.get("anchor_cluster")

    candidates = [fp for fp in adapter.get_footprints()
                  if _canonical_role(adapter.get_field_value(fp, ROLE_FIELD_NAME) or "")
                  == _canonical_role(role)]
    if anchor_cluster and len(candidates) > 1:
        narrowed = [fp for fp in candidates if cluster_prefix_match(
            adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or "", anchor_cluster)]
        if narrowed:
            candidates = narrowed
    if len(candidates) != 1:
        refs = sorted(fp.reference_field.text.value for fp in candidates)
        print(f"  [--board] {clone.get('cell')!r}: anchor_role {role!r} "
              f"{'ambiguous' if refs else 'not found'} on live board "
              f"({len(refs)} candidates: {refs}) -- skipping")
        return None

    fp = candidates[0]
    for p in adapter.get_footprint_pads(fp):
        # kipy pad.number can be int/float/str — normalise before compare.
        if str(p.number) == want_pad:
            # origin = anchor pad + xy shift (clone_geometry.py:115-119).
            # The shift is rotated by parent_rotation_deg, NOT clone.rotation_deg
            # -- and parent_rotation_deg is always 0.0 here: this script only
            # walks top-level clone_placements (clone_position_calculator.py:374
            # calls _resolve_one_level with parent_rotation_deg=0.0), never a
            # nested CellPlacement. clone.rotation_deg only rotates the cell's
            # CONTENTS (applied below in fake_run_live's local_to_absolute call).
            x_nm, y_nm = int(p.position.x), int(p.position.y)
            xy = clone.get("xy") or [0.0, 0.0]
            s = local_to_absolute(Vector2.from_xy(0, 0), float(xy[0]), float(xy[1]), 0.0)
            return Vector2.from_xy(x_nm + int(s.x), y_nm + int(s.y)), rot, mirror
    return None


def fake_run_live(cell: dict, params: dict, clone: dict, adapter, pads_by_ref: dict):
    """Compute absolute via/track positions (origin from anchor pad) and find
    the nearest real pad on the board — i.e. WHERE each via/track would land.

    All arithmetic is in nanometres (local_to_absolute + Vector2); the report
    converts distances to mm. pads_by_ref stores nanometres as ints.
    """
    origin_rot = _anchor_origin(adapter, clone, pads_by_ref)
    if origin_rot is None:
        return {"ok": False, "reason": "no resolvable anchor_role/anchor_pad on live board"}
    origin, rotation_deg, mirror = origin_rot

    rows = []
    items = []
    for v in cell.get("vias", []) or []:
        items.append(("via", v.get("net"), v))
    for t in cell.get("tracks", []) or []:
        items.append(("track", t.get("net"), t))

    def _place(along_mm: float, across_mm: float):
        p = local_to_absolute(origin, along_mm, across_mm, rotation_deg)
        return _mirror_x(origin, p) if mirror else p

    for kind, net_tpl, entry in items:
        if kind == "via":
            pts = [_place(float(entry.get("offset_along_mm", 0.0)),
                          float(entry.get("offset_across_mm", 0.0)))]
        else:
            pts = [
                _place(float(entry.get("start_along_mm", 0.0)),
                      float(entry.get("start_across_mm", 0.0))),
                _place(float(entry.get("end_along_mm", 0.0)),
                      float(entry.get("end_across_mm", 0.0))),
            ]
        best = None
        for pt in pts:
            for ref, pads in pads_by_ref.items():
                for (px_nm, py_nm, pnum, pnet) in pads:
                    d = ((pt.x - px_nm) ** 2 + (pt.y - py_nm) ** 2) ** 0.5
                    if best is None or d < best[0]:
                        best = (d, ref, pnum, pnet)
        rows.append({
            "kind": kind,
            "net": resolve(net_tpl, params),
            "nearest_pad_ref": best[1] if best else None,
            "nearest_pad_num": best[2] if best else None,
            "nearest_pad_net": best[3] if best else None,
            "dist_mm": round(best[0] / MM, 3) if best else None,
        })
    return {"ok": True, "rows": rows}

# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def summarize(stats_list: list[dict]) -> dict:
    total = defaultdict(int)
    for s in stats_list:
        for k, v in s.items():
            total[k] += v
    return dict(total)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--templates", required=True,
                    help="directory with cell YAML files (templates/*.yaml)")
    ap.add_argument("--netlist", default=None,
                    help="optional: KiCad .net file (offline role/net graph; "
                         "if omitted and --board is given, the graph is built "
                         "live from the board instead)")
    ap.add_argument("--profile", default=None,
                    help="optional: profile YAML (clone_placements -> params+cluster)")
    ap.add_argument("--rule-nets", default="GND", help="comma-separated rule nets")
    ap.add_argument("--no-geometry", action="store_true",
                    help="disable geometry tiebreak (pick nearest component by "
                         "local distance) for |R(n)|>1 ambiguous candidates")
    ap.add_argument("--board", action="store_true",
                    help="connect via kipy: (1) if --netlist is not given, build "
                         "the role/net graph live from the board (no manual "
                         "Export Netlist step); (2) fake-run each cell's anchor "
                         "pad as origin and report the nearest REAL pad each "
                         "via/track would land on")
    ap.add_argument("--cluster", default=None, action="append",
                    help="only audit clusters matching this (sub)string; "
                         "repeatable. E.g. --cluster 3V3_VCCIO --cluster 1V2_VCCD_PLL")
    ap.add_argument("--net", default=None, action="append",
                    help="only report via/track rows whose resolved net matches "
                         "this (sub)string; repeatable. E.g. --net +3V3_VCCIO")
    ap.add_argument("--json", default=None, help="optional: JSON report path")
    args = ap.parse_args()

    rule_nets = {s for s in args.rule_nets.split(",") if s}
    cells = load_cells(args.templates)
    placements = load_placements_by_template(args.profile)

    cluster_role_nets = cluster_roles = None
    adapter = pads_by_ref = None

    if args.netlist:
        cluster_role_nets, cluster_roles = build_netlist_graph(args.netlist)
        n_clusters = len(cluster_role_nets)
        n_edges = sum(len(v) for v in cluster_role_nets.values())
        print(f"netlist {args.netlist}: {n_clusters} clusters, {n_edges} cluster-role entries")

    if args.board:
        try:
            adapter, pads_by_ref = load_live_board()
            print(f"live board: {len(pads_by_ref)} refs with pads")
            if cluster_role_nets is None:
                # No --netlist given -- build the same role/net graph straight
                # from the live board, no manual Export Netlist step needed.
                cluster_role_nets, cluster_roles = build_netlist_graph_live(adapter)
                n_clusters = len(cluster_role_nets)
                n_edges = sum(len(v) for v in cluster_role_nets.values())
                print(f"live role/net graph: {n_clusters} clusters, {n_edges} "
                      f"cluster-role entries (from the board, no --netlist)")
        except Exception as exc:  # live board unavailable
            print(f"\n[--board] live board unavailable: {exc}")

    per_cell = []
    autoweight_rows = []
    skipped = []
    for name, cell in sorted(cells.items()):
        uses = placements.get(name)
        if not uses:
            skipped.append(name)
            continue
        cell_roles = {c.get("role") for c in cell.get("components", []) or []}
        # Profile clusters first (they are the ground truth of where the cell
        # is actually placed), then graph best-matches (robust to clusters that
        # the user filled with typos / empty Cluster in the .net).
        clusters = [u["cluster"] for u in uses] or [""]
        if cluster_role_nets:
            clusters += matching_clusters(cell_roles, cluster_role_nets, cluster_roles)
        # One classification per (cell, cluster); params come from the matching
        # profile placement of THAT cluster (each clone_placement carries its
        # own GND/PWR_IN/PWR_OUT mapping), falling back to the first use.
        if args.cluster:
            clusters = [c for c in clusters if any(k in c for k in args.cluster)]
        seen = set()
        for cluster in clusters:
            if cluster in seen:
                continue
            seen.add(cluster)
            params = _params_for_cluster(uses, cluster, cell,
                                         (cluster_role_nets or {}).get(cluster, {}))
            result = classify_cell(name, cell, params, cluster,
                                   cluster_role_nets or {}, rule_nets,
                                   use_geometry=not args.no_geometry)
            if args.net:
                result["details"] = [d for d in result["details"]
                                     if d.get("net") and any(k in d["net"] for k in args.net)]
            per_cell.append(result)
            if cluster_role_nets:
                autoweight_rows.extend(
                    audit_autoweight(cell, params, cluster,
                                     cluster_role_nets, cluster_roles, rule_nets))

    if skipped:
        print(f"\n(skipped cells without profile placement: {', '.join(sorted(skipped))})")

    total = summarize([r["stats"] for r in per_cell])
    n_total = sum(total.values())

    print("\n=== Per-cell classification (cell / cluster) ===")
    for r in per_cell:
        s = r["stats"]
        print(f"  {r['cell']:20s} [{r['cluster']:24s}] "
              + "  ".join(f"{k}={v}" for k, v in sorted(s.items())))

    geo_rows = [d for r in per_cell if r.get("details") for d in r["details"] if d.get("geometry")]
    if geo_rows:
        print("\n=== Fake-run geometry: where via/track would land (|R(n)|>1) ===")
        for d in geo_rows:
            ranked = ", ".join(f"{g['role']}={g['dist_mm']}mm" for g in d["geometry"])
            print(f"  {d['kind']:5s} net={d['net']!r:22s} [{d['category']:6s}] "
                  f"candidates: {ranked} -> {d['note']}")
    print("\n=== Total ===")
    if n_total:
        for k in sorted(total):
            print(f"  {k:10s} {total[k]:4d}  {100.0 * total[k] / n_total:5.1f}%")

    if autoweight_rows:
        print("\n=== Autoweight audit (multi-net roles, per cluster) ===")
        for row in autoweight_rows:
            flag = "OK" if row["autoweight_covers_all"] else "FAIL"
            print(f"  [{flag}] {row['role']}: main={row['main_net']} "
                  f"via_on_main={row['via_tracks_on_main']} "
                  f"via_on_other={row['via_tracks_on_other']} "
                  f"degree={row['degree']}")
        n_fail = sum(1 for r in autoweight_rows if not r["autoweight_covers_all"])
        print(f"\n  autoweight fails on {n_fail}/{len(autoweight_rows)} multi-net roles "
              f"-> pad cannot be replaced by autoweight")

    fake_rows = []
    if args.board and adapter is not None:
        all_clones = _clone_placements_from_profile(args.profile)
        for name, cell in sorted(cells.items()):
            uses = placements.get(name)
            if not uses:
                continue
            cell_clones = [cp for cp in all_clones if cp.get("cell") == name]
            if args.cluster:
                # Net-presence match (see _clones_for_cluster docstring) --
                # anchor_cluster is not a reliable cluster tag for this cell.
                picked = []
                for w in args.cluster:
                    for cp in _clones_for_cluster(cell_clones, w, cell,
                                                  cluster_role_nets or {}, rule_nets):
                        if cp not in picked:
                            picked.append(cp)
                cell_clones = picked
            for cp in cell_clones:
                # Prefer this clone_placement's OWN params (unambiguous, no
                # cluster-name lookup needed at all) -- each pif_fpga/
                # ldo_adj_pm2v5 instance has different PWR_IN/PWR_OUT (found
                # 2026-08-11: reusing uses[0]['params'] for every instance
                # mislabelled the resolved net in the printed report). Only
                # fall back to the cluster-name/net-presence lookup for older
                # clone_placements entries with no params: of their own.
                cluster = cp.get("anchor_cluster") or cp.get("cluster") or ""
                params = cp.get("params") or (
                    _params_for_cluster(uses, cluster, cell,
                                        (cluster_role_nets or {}).get(cluster, {}))
                    if cluster else uses[0]["params"])
                result = fake_run_live(cell, params, cp, adapter, pads_by_ref)
                if result.get("ok"):
                    print(f"\n--- {name} (anchor_role={cp.get('anchor_role')}, "
                          f"pad={cp.get('anchor_pad')}) ---")
                    for row in result["rows"]:
                        print(f"  {row['kind']:5s} net={row['net']!r:22s} "
                              f"-> nearest {row['nearest_pad_ref']}.{row['nearest_pad_num']} "
                              f"(net {row['nearest_pad_net']!r}, dist={row['dist_mm']}mm)")
                    fake_rows.append({"cell": name, "rows": result["rows"]})

    if args.json:
        report = {
            "rule_nets": sorted(rule_nets),
            "cells": per_cell,
            "total": total,
            "autoweight_audit": autoweight_rows,
            "fake_run_live": fake_rows,
        }
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON report -> {args.json}")


if __name__ == "__main__":
    main()
