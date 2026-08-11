#!/usr/bin/env python3
"""round_trip_net_from_role.py — live end-to-end check of the net_from_role
core (plan steps 1-4) on the 3CH-AWG-TIA board.

Two halves, both read-only (nothing is placed or written to the board):

  1. EXTRACT from LDO_ADJ_P2V5: build the selection items straight off the
     live board (the cluster's footprints by Cluster field, plus vias/tracks
     whose net is in the cluster's role-net set and whose geometry lands in
     the cluster bbox), run the real extractor, and show that via/track nets
     come out as net_from_role / net_from_role_pad instead of literals.

  2. APPLY-simulate to LDO_ADJ_N2V5: build a cluster-scoped role_to_ref
     (Role + Cluster == LDO_ADJ_N2V5 — the same signal the real resolver's
     cluster-narrowing / resolve_by_cluster_tag uses), then run
     resolve_net_from_role for every net_from_role in the extracted cell and
     show the nets resolve live to the N2V5 rail nets (-2V5 / -2V5_DIRTY /
     /Power/-2V5_ADJ / /Power/-2V5_LED), NOT the P2V5 ones (+2V5 / ...).
     This exercises exactly the apply path the calculator uses
     (_resolve_role_nets before geometry), without touching the board.

Requires KiCad running with the 3CH-AWG-TIA board open and up to date
(Update PCB from Schematic already run — same precondition as --board in
net_from_role_audit.py).

Run:
    python -m kicadstamp.diagnostics.round_trip_net_from_role
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.template_extraction import extract_template_from_selection
from kicadstamp.net_resolution import resolve_net_from_role

CLUSTER_FIELD = "Cluster"
ROLE_FIELD = "Role"
P2V5 = "LDO_ADJ_P2V5"
N2V5 = "LDO_ADJ_N2V5"
RULE_NETS = {"GND"}


def _cluster_footprints(adapter, cluster: str):
    out = []
    for fp in adapter.get_footprints():
        if adapter.get_field_value(fp, CLUSTER_FIELD) == cluster \
                and adapter.get_field_value(fp, ROLE_FIELD):
            out.append(fp)
    return out


def _cluster_nets(adapter, fps) -> set[str]:
    nets = set()
    for fp in fps:
        for p in adapter.get_footprint_pads(fp):
            if p.net and p.net.name:
                nets.add(p.net.name)
    return nets


def _union_bbox_mm(adapter, fps):
    """Union of footprint bboxes in (min_x, min_y, max_x, max_y) mm."""
    boxes = adapter.get_bounding_boxes(fps)
    xs, ys = [], []
    for b in boxes:
        if b is None:
            continue
        xs.append(b.pos.x / 1e6)
        ys.append(b.pos.y / 1e6)
        xs.append((b.pos.x + b.size.x) / 1e6)
        ys.append((b.pos.y + b.size.y) / 1e6)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _in_bbox_mm(x_mm, y_mm, bbox, pad_mm=3.0):
    if bbox is None:
        return True
    min_x, min_y, max_x, max_y = bbox
    return (min_x - pad_mm <= x_mm <= max_x + pad_mm
            and min_y - pad_mm <= y_mm <= max_y + pad_mm)


def _build_cluster_items(adapter, cluster: str):
    fps = _cluster_footprints(adapter, cluster)
    if not fps:
        raise RuntimeError(f"no footprints tagged {CLUSTER_FIELD}={cluster!r}")
    nets = _cluster_nets(adapter, fps)
    bbox = _union_bbox_mm(adapter, fps)

    vias = [v for v in adapter.get_vias()
            if v.net and v.net.name in nets
            and _in_bbox_mm(v.position.x / 1e6, v.position.y / 1e6, bbox)]
    tracks = [t for t in adapter.get_tracks()
              if t.net and t.net.name in nets
              and (_in_bbox_mm(t.start.x / 1e6, t.start.y / 1e6, bbox)
                   or _in_bbox_mm(t.end.x / 1e6, t.end.y / 1e6, bbox))]
    print(f"  {cluster}: {len(fps)} footprints, {len(vias)} vias, {len(tracks)} tracks "
          f"(bbox={bbox})")
    return fps + vias + tracks


def _cluster_role_to_ref(adapter, cluster: str) -> dict[str, str]:
    """Role -> ref for one cluster (Role + exact Cluster field). Same signal
    the real resolver uses for cluster narrowing (resolve_by_cluster_tag /
    _narrow_ambiguous_candidates by anchor_cluster)."""
    out = {}
    for fp in adapter.get_footprints():
        role = adapter.get_field_value(fp, ROLE_FIELD)
        cl = adapter.get_field_value(fp, CLUSTER_FIELD)
        if role and cl == cluster:
            out[role] = fp.reference_field.text.value
    return out


def _summarize_item(kind, entry):
    role = entry.get("net_from_role")
    pad = entry.get("net_from_role_pad")
    if role is not None:
        pad_txt = f", pad: {pad}" if pad is not None else ""
        return f"{kind} net_from_role: {role}{pad_txt}"
    return f"{kind} net={entry.get('net')!r}"


def main():
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    print("connected to live board")

    # ---- 1. EXTRACT from P2V5 ----
    print(f"\n=== EXTRACT {P2V5} ===")
    items = _build_cluster_items(adapter, P2V5)
    result = extract_template_from_selection(
        adapter, "roundtrip_ldo_adj_p2v5", items=items, rule_nets=RULE_NETS)
    cell = result["roundtrip_ldo_adj_p2v5"]
    print(f"  extracted: {len(cell['vias'])} vias, {len(cell['tracks'])} tracks, "
          f"{len(cell['components'])} components")
    for v in cell["vias"]:
        print("  via  " + _summarize_item("via", v))
    for t in cell["tracks"]:
        print("  trk  " + _summarize_item("track", t))

    n_role = sum(1 for x in cell["vias"] + cell["tracks"]
                 if x.get("net_from_role"))
    n_literal = sum(1 for x in cell["vias"] + cell["tracks"]
                    if x.get("net") is not None)
    print(f"  -> {n_role} via/track with net_from_role, {n_literal} still literal "
          f"(rule nets / fallback)")
    if n_role == 0:
        print("  NOTE: no net_from_role suggested — check the selection geometry / "
              "that vias landed in the cluster bbox")
        return

    # ---- 2. APPLY-simulate to N2V5 ----
    print(f"\n=== APPLY-SIMULATE {N2V5} (cluster-scoped role_to_ref) ===")
    role_to_ref = _cluster_role_to_ref(adapter, N2V5)
    print(f"  role_to_ref: {sorted(role_to_ref.items())}")

    resolved = {}
    for entry in cell["vias"] + cell["tracks"]:
        role = entry.get("net_from_role")
        if role is None:
            continue
        pad = entry.get("net_from_role_pad")
        net = resolve_net_from_role(role, pad, role_to_ref, adapter,
                                    rule_nets=RULE_NETS)
        resolved[(role, pad)] = net
        print(f"  {role!r}{', pad=' + str(pad) if pad else ''} -> live net {net!r}")

    n2v5_nets = {"/Power/-2V5_ADJ", "-2V5_DIRTY", "-5V", "/Power/-2V5_LED", "-2V5"}
    hits = sum(1 for net in resolved.values() if net in n2v5_nets)
    print(f"  -> {hits}/{len(resolved)} resolved nets are N2V5 rail nets "
          f"({sorted(n2v5_nets)})")
    wrong = {net for net in resolved.values() if net not in n2v5_nets}
    if wrong:
        print(f"  WARNING: resolved to non-N2V5 nets: {sorted(wrong)}")


if __name__ == "__main__":
    main()
