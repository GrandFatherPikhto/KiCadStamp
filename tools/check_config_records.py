"""tools/check_config_records.py — read-only: does every record of the profile's .sexp address parts of ITS cluster?

The config never names a refdes: a record addresses a part by (Role, Cluster, sheet). This check takes
every such address in the loaded config and answers it against the OPEN board, with the product's own
rules (cluster_matching.cluster_prefix_match, sheet_names.sheet_in_path):

  entities     every role of the entity's cell must be on exactly ONE part of the entity's cluster on
               its sheet; a part of that cluster instance whose role the cell does not list is reported
               too, and so is a pinned `refs` entry whose part carries another Role or Cluster;
  anchors      anchor_role / anchor_cluster / anchor_sheet of net_traces, mount nodes, tree anchors,
               points and thermal via arrays must name exactly one part;
  copper ends  a role named in a net_trace's `pads` or in a track's/via's net_from_role must exist on
               the board (a hint, not an address: no cluster is demanded there).

For every address that finds nothing in its cluster the check says where that role DOES stand — a part
of another cluster, or a part with no Cluster field at all. Nothing is written; the profile is loaded
from a temporary copy exactly as tools/check_placement.py does it.

usage: .venv/bin/python tools/check_config_records.py <profile dir | profile .sexp>
exit code: 0 clean, 1 problems found, 2 refused (wrong board open)
"""
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_placement import _config_name, _load_copy                 # noqa: E402
from kicadstamp.adapter_factory import create_board_adapter          # noqa: E402
from kicadstamp.cluster_matching import cluster_prefix_match         # noqa: E402
from kicadstamp.constants import DEFAULT_TIMEOUT_MS                  # noqa: E402
from kicadstamp.sheet_names import resolve_sheet_path_names, sheet_in_path   # noqa: E402


class Part:
    __slots__ = ("ref", "role", "cluster", "path")

    def __init__(self, ref, role, cluster, path):
        self.ref, self.role, self.cluster, self.path = ref, role, cluster, path

    @property
    def where(self) -> str:
        return f"{self.cluster or 'NO Cluster'} @ {'/'.join(p or '?' for p in self.path) or '/'}"


def _read_board(cfg_file, ctx):
    sheet_names = dict((ctx.sheet_names if ctx else {}) or {})
    adapter = create_board_adapter(timeout_ms=DEFAULT_TIMEOUT_MS, config_path=str(cfg_file))
    try:
        adapter.refresh_board()
        board = Path(adapter.get_board_filename() or "").stem
        sheet = Path(getattr(ctx, "root_sheet", None) or "").stem
        if board and sheet and board != sheet:
            print(f"REFUSED: KiCad has {board!r} open, the profile is for {sheet!r} — open that board first")
            return None
        parts = []
        for fp in adapter.get_footprints():
            parts.append(Part(fp.ref,
                              adapter.get_field_value(fp, "Role") or None,
                              adapter.get_field_value(fp, "Cluster") or None,
                              tuple(resolve_sheet_path_names(fp, sheet_names))))
        return parts
    finally:
        adapter.close()


def _on_sheet(part, sheet) -> bool:
    return not sheet or sheet_in_path(part.path, sheet)


def _in_cluster(part, cluster) -> bool:
    return not cluster or cluster_prefix_match(part.cluster or "", cluster)


def _elsewhere(by_role, role, cluster, sheet) -> str:
    """Where `role` stands when its own cluster/sheet has none of it."""
    others = [p for p in by_role.get(role, []) if not (_in_cluster(p, cluster) and _on_sheet(p, sheet))]
    if not others:
        return "the role is on no part of the board"
    shown = ", ".join(f"{p.ref} ({p.where})" for p in others[:6])
    more = f" and {len(others) - 6} more" if len(others) > 6 else ""
    return f"the role stands on {shown}{more}"


def _address(by_role, role, cluster, sheet):
    """(verdict, text) for one (Role, Cluster, sheet) address — verdict None when it names one part."""
    hits = [p for p in by_role.get(role, []) if _in_cluster(p, cluster) and _on_sheet(p, sheet)]
    if len(hits) == 1:
        return None, hits[0].ref
    if not hits and sheet:
        # The product narrows by sheet only when that leaves someone (role_narrowing), so a record
        # whose sheet is wrong still resolves — to the one part of its cluster on ANOTHER sheet.
        off_sheet = [p for p in by_role.get(role, []) if _in_cluster(p, cluster)]
        if len(off_sheet) == 1:
            p = off_sheet[0]
            return "SHEET", (f"resolves to {p.ref} only because sheet narrowing is soft — "
                             f"the record says {sheet!r}, {p.ref} stands on {p.where}")
    if not hits:
        untagged = [p for p in by_role.get(role, []) if not p.cluster and _on_sheet(p, sheet)]
        if cluster and untagged:
            return "UNTAGGED", (f"no part of {cluster} carries it; "
                                f"{', '.join(p.ref for p in untagged)} carries it with NO Cluster field")
        return "ABSENT", _elsewhere(by_role, role, cluster, sheet)
    return "AMBIGUOUS", ", ".join(f"{p.ref} ({p.where})" for p in hits)


def _label(cluster, sheet):
    return f"{cluster or '(any cluster)'} @ {sheet or '(any sheet)'}"


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    target = Path(argv[0]).resolve()
    profile, cfg_name = (target.parent, target.name) if target.is_file() else (target, _config_name(target))
    tmp, cfg_file, cfg, ctx = _load_copy(profile, cfg_name)
    try:
        parts = _read_board(cfg_file, ctx)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if parts is None:
        return 2
    by_role = defaultdict(list)
    for p in parts:
        if p.role:
            by_role[p.role].append(p)

    problems = 0
    entities = [e for e in cfg.entities if not getattr(e, "retired", False)]
    print(f"board footprints: {len(parts)}; entities: {len(entities)}; net_traces: {len(cfg.net_traces)}")

    # ---- entities: the cell's roles inside the entity's cluster instance ----
    print("\n--- entities: cell roles → parts of the entity's cluster ---")
    covered = defaultdict(set)          # (cluster, sheet) -> roles listed by the cells of its entities
    no_cluster = []
    for e in sorted(entities, key=lambda x: x.name):
        cell = cfg.cells.get(e.cell)
        if cell is None:
            print(f"[{e.name}] cell {e.cell!r} is not in cells:")
            problems += 1
            continue
        if not e.cluster:
            no_cluster.append(e.name)
            continue
        roles = [c.role for c in cell.components]
        covered[(e.cluster, e.sheet)].update(roles)
        lines = []
        for role in roles:
            verdict, text = _address(by_role, role, e.cluster, e.sheet)
            if verdict:
                lines.append(f"   {verdict:9} role {role}: {text}")
        for role, ref in sorted((getattr(e, "refs", None) or {}).items()):
            pinned = next((p for p in parts if p.ref == ref), None)
            if pinned is None:
                lines.append(f"   PINNED    refs {role} → {ref}: no such part on the board")
            elif pinned.role != role or not _in_cluster(pinned, e.cluster) or not _on_sheet(pinned, e.sheet):
                lines.append(f"   PINNED    refs {role} → {ref}: that part is Role {pinned.role} of {pinned.where}")
        if lines:
            print(f"[{e.name}] cell {e.cell!r}, {_label(e.cluster, e.sheet)}")
            print("\n".join(lines))
            problems += len(lines)

    # parts of a recorded cluster instance whose role no cell of that instance lists
    print("\n--- parts of a recorded cluster that no cell lists ---")
    extra = 0
    for (cluster, sheet), roles in sorted(covered.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        for p in parts:
            if p.role and p.cluster and _in_cluster(p, cluster) and _on_sheet(p, sheet) and p.role not in roles:
                print(f"   UNLISTED  {p.ref} (Role {p.role} of {p.where}): no cell of {_label(cluster, sheet)} has it")
                extra += 1
    if not extra:
        print("   none")
    problems += extra

    # ---- anchors ----
    print("\n--- anchors (net_traces, mount nodes, tree anchors, points, thermal vias) ---")
    anchors = []
    for nt in cfg.net_traces:
        if not getattr(nt, "retired", False) and nt.anchor_role:
            anchors.append((f"net_trace {nt.name or nt.net!r}", nt.anchor_role, nt.anchor_cluster, nt.anchor_sheet))

    def walk(tree, nodes):
        for n in nodes:
            a = getattr(n, "anchor", None)
            if a is not None and getattr(a, "role", None):
                anchors.append((f"tree {tree} node {n.ref!r}", a.role, a.anchor_cluster, a.anchor_sheet))
            walk(tree, getattr(n, "children", None) or [])
    for t in cfg.trees:
        if t.anchor is not None and t.anchor.role:
            anchors.append((f"tree {t.name!r} anchor", t.anchor.role, t.anchor.anchor_cluster, t.anchor.anchor_sheet))
        walk(t.name, t.nodes)
    for name, pt in (cfg.points or {}).items():
        if pt.anchor_role:
            anchors.append((f"point {name!r}", pt.anchor_role, pt.anchor_cluster, pt.anchor_sheet))
    for tva in cfg.thermal_via_arrays:
        if tva.anchor_role:
            anchors.append((f"thermal via array {tva.name!r}", tva.anchor_role, tva.anchor_cluster, tva.anchor_sheet))
    bad = 0
    for what, role, cluster, sheet in anchors:
        verdict, text = _address(by_role, role, cluster, sheet)
        if verdict:
            print(f"   {verdict:9} {what}: anchor {role} of {_label(cluster, sheet)} — {text}")
            bad += 1
    print(f"   {len(anchors)} anchor(s) checked" + ("" if bad else ", all name exactly one part"))
    problems += bad

    # ---- copper ends: roles a net_trace's copper reaches (hints, not addresses) ----
    print("\n--- copper ends (net_traces pads / net_from_role) ---")
    gone = 0
    for nt in cfg.net_traces:
        if getattr(nt, "retired", False):
            continue
        roles = {p.split(".", 1)[0] for p in (nt.pads or [])}
        for item in list(nt.tracks or []) + list(getattr(nt, "vias", None) or []):
            if getattr(item, "net_from_role", None):
                roles.add(item.net_from_role)
        for role in sorted(r for r in roles if r not in by_role):
            print(f"   ABSENT    net_trace {nt.name or nt.net!r}: role {role} is on no part of the board")
            gone += 1
    if not gone:
        print("   every role the copper names exists on the board")
    problems += gone

    if no_cluster:
        print(f"\n(entities without a cluster, addressed by refs/sheet only — not checked: {', '.join(no_cluster)})")
    print(f"\nresult: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
