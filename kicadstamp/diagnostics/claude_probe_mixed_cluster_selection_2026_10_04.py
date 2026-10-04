# kicadstamp/diagnostics/claude_probe_mixed_cluster_selection_2026_10_04.py
"""READ-ONLY probe: can "Update from selection" narrow a MIXED selection to ONE
cluster? Claude, 2026-10-04 (Denis: DAC_BUF cannot be selected without the PIF
clusters around it; Refresh then refuses on the foreign roles).

Footprints are easy to narrow (Role / Cluster fields). Copper is the question:
a via or track carries no cluster. For every selected via/track this probe
prints the three candidate signals side by side, so a rule is chosen from data:

  * REGISTRY — is its uuid recorded under some registry key (copper KiCadStamp
    drew for a known record), and which anchor part owns it;
  * NET — which selected clusters have a pad on the item's net;
  * NEAREST — the cluster of the nearest selected pad (a track: the nearer of
    its two ends) and the distance in mm.

It also lists which cells of the config have EXACTLY the role set of each
selected (Cluster, sheet) group — the candidates an automatic pick would have.

Reads the board (its own kipy socket, closed at the end) and a COPY of the
profile (claude.md p.28 — loading may lift the copy's format on disk; never the
original). Writes nothing else.

    .venv/bin/python kicadstamp/diagnostics/claude_probe_mixed_cluster_selection_2026_10_04.py \
        profiles/<profile>-probecopy/config.sexp
"""
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.config.loader import load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint, Track, Via
from kicadstamp.utils.paths import registry_paths_for_config

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NM = 1_000_000


def _registry_owner(paths) -> dict:
    """item uuid -> registry key, over both registries (any value shape)."""
    owner = {}
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        for key, value in raw.items():
            if key == "schema_version":
                continue
            for u in _UUID_RE.findall(json.dumps(value)):
                owner[u] = key
    return owner


def _dist_mm(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y) / NM


def main(config_path: str) -> None:
    cfg, ctx = load_config(config_path)
    sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
    owner = _registry_owner(registry_paths_for_config(
        config_path, getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None)))
    adapter = create_board_adapter(config_path=config_path)
    adapter.refresh_board()
    try:
        items = adapter.get_selected_items()
        fps = [i for i in items if isinstance(i, Footprint)]
        vias = [i for i in items if isinstance(i, Via)]
        tracks = [i for i in items if isinstance(i, Track)]
        fp_info = {}
        pads = []  # (pad, group)
        for fp in fps:
            role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
            cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
            # The LAST path element is the symbol's own uuid, not a sheet: the
            # group is the Cluster tag alone (one sheet instance per board here).
            sheet = None
            group = str(cluster)
            fp_info[fp.ref] = (role, cluster, sheet, group)
            for pad in adapter.get_footprint_pads(fp):
                pads.append((pad, group, fp.ref))
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            close()

    print(f"profile copy: {config_path}")
    print(f"selected: {len(fps)} footprints, {len(vias)} vias, {len(tracks)} tracks")
    groups = defaultdict(list)
    for ref, (role, cluster, sheet, group) in sorted(fp_info.items()):
        groups[group].append((ref, role))
    print("\n== groups (Cluster@sheet): refs/roles")
    for group, members in sorted(groups.items()):
        roles = sorted(r for _ref, r in members if r)
        print(f"  {group}: {len(members)} fp — " +
              ", ".join(f"{ref}={role}" for ref, role in members))
        exact = [name for name, cell in cfg.cells.items()
                 if sorted(c.role for c in cell.components) == roles]
        print(f"    cells with EXACTLY this role set: {exact or '-'}")

    net_groups = defaultdict(set)
    for pad, group, _ref in pads:
        if pad.net_name:
            net_groups[pad.net_name].add(group)

    def nearest(points):
        best = None
        for pad, group, ref in pads:
            d = min(_dist_mm(pt, pad.position) for pt in points)
            if best is None or d < best[0]:
                best = (d, group, ref)
        return best

    rows = []
    for v in vias:
        rows.append(("via", v.uuid, v.net_name, "-", [v.position]))
    for t in tracks:
        layer = getattr(t.layer, "name", str(t.layer))
        rows.append(("track", t.uuid, t.net_name, layer, [t.start, t.end]))

    print("\n== copper: kind | net | layer | registry owner (anchor part) | "
          "groups with a pad on the net | nearest pad group (mm)")
    tally = Counter()
    for kind, uid, net, layer, pts in rows:
        key = owner.get(uid)
        reg = key.split("|", 1)[0] if key else "-"
        ng = sorted(net_groups.get(net, ()))
        near = nearest(pts) if pads else None
        near_s = f"{near[1]} via {near[2]} {near[0]:.2f}" if near else "-"
        print(f"  {kind:<5} {str(net)[:22]:<22} {str(layer)[:8]:<8} {reg[:34]:<34} "
              f"{'/'.join(ng) or '-':<30} {near_s}")
        net_one = ng[0] if len(ng) == 1 else ("MANY" if ng else "NONE")
        tally[(kind, reg, net_one, near[1] if near else "-")] += 1

    print("\n== tally: (kind, registry owner anchor, net-signal group, nearest group) -> count")
    for k, n in sorted(tally.items()):
        print(f"  {k}: {n}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: claude_probe_mixed_cluster_selection_2026_10_04.py "
                         "<config.sexp of a COPY>")
    main(sys.argv[1])
