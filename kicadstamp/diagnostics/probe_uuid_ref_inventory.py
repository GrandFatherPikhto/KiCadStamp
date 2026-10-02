#!/usr/bin/env python3
# kicadstamp/diagnostics/probe_uuid_ref_inventory.py
"""probe_uuid_ref_inventory.py — У0.1 inventory of name-based references.

Read-only. Runs over the COPIES ``profiles/*-copy/`` (never the Syncthing
originals) and reports, for every config file, which SECTION references which
SECTION, through which FIELD, and how many times — plus whether the target name
actually exists (a dangling name is a defect the У1 fatal must cover).

The reference-field map is taken by hand from kicadstamp/config/entries.py and
kicadstamp/trees.py (the loaders ARE the authoritative list — the probe does not
guess). Physical fields (anchor_ref/anchor_role/anchor_sheet/anchor_cluster/
anchor_pad, cluster, sheet, role, refdes) are NOT record references and are
deliberately out of scope here; they are the "physics" half the У0.3 split names.

Usage:  .venv/bin/python kicadstamp/diagnostics/probe_uuid_ref_inventory.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from kicadstamp.config.sexp_format import sexp_to_dict  # noqa: E402


def _eff(d: dict, section: str) -> str | None:
    """The effective --only name of one raw record dict, mirroring the
    *_effective_name helpers in config/models.py (they take dataclasses; the
    probe works on the raw dicts sexp_to_dict returns)."""
    name = d.get("name")
    if name:
        return name
    if section == "chains":
        return d.get("net")
    if section == "net_traces":
        return d.get("net")
    if section == "clone_placements":
        return d.get("cluster")
    if section == "coordinate_placements":
        cluster, role = d.get("cluster"), d.get("role")
        return f"{cluster}/{role}" if cluster and role else None
    return None


# Node kind -> the config section its `ref` names. Kinds absent here are the
# LOCAL-NAME kinds (module/mount/copper/component) and "external" (a live
# refdes, no record) — see trees.KINDS / _LOCAL_REF_KINDS.
_KIND_TARGET = {
    "placement": "entities",
    "clone": "clone_placements",
    "chain": "chains",
    "rule": "chains",
    "coordinate": "coordinate_placements",
    "net_trace": "net_traces",
    "point": "points",
    "module": "trees",
}


def _names_of(data: dict) -> dict[str, set[str]]:
    """Every record NAME available in one parsed config dict, per section."""
    out: dict[str, set[str]] = {
        "cells": set((data.get("cells") or {}).keys()),
        "points": set((data.get("points") or {}).keys()),
        "imprints": {_eff(x, "imprints") for x in data.get("imprints") or []},
        "entities": {_eff(x, "entities") for x in data.get("entities") or []},
        "chains": {_eff(x, "chains") for x in data.get("chains") or []},
        "clone_placements": {_eff(x, "clone_placements")
                             for x in data.get("clone_placements") or []},
        "coordinate_placements": {_eff(x, "coordinate_placements")
                                  for x in data.get("coordinate_placements") or []},
        "thermal_via_arrays": {x.get("name") for x in data.get("thermal_via_arrays") or []},
        "net_traces": {_eff(x, "net_traces") for x in data.get("net_traces") or []},
        "trees": {x.get("name") for x in data.get("trees") or []},
        "tree_instances": {x.get("name") for x in data.get("tree_instances") or []},
        "sheet_templates": set((data.get("sheet_templates") or {}).keys()),
        "extract_profiles": set((data.get("extract_profiles") or {}).keys()),
        "clone_profiles": set((data.get("clone_profiles") or {}).keys()),
    }
    return out


def _walk_nodes(nodes, out: list):
    for n in nodes or []:
        out.append(n)
        _walk_nodes(n.get("children"), out)


def _walk_refs(data: dict) -> list[tuple[str, str, str, str]]:
    """(source_section, field, target_section, target_name) for one config."""
    refs: list[tuple[str, str, str, str]] = []

    for e in data.get("entities") or []:
        if e.get("cell") is not None:
            refs.append(("entities", "cell", "cells", e["cell"]))
        if e.get("imprint") is not None:
            refs.append(("entities", "imprint", "imprints", e["imprint"]))

    for cp in data.get("clone_placements") or []:
        if cp.get("cell") is not None:
            refs.append(("clone_placements", "cell", "cells", cp["cell"]))
        if cp.get("anchor_point") is not None:
            refs.append(("clone_placements", "anchor_point", "points", cp["anchor_point"]))

    for c in data.get("chains") or []:
        if c.get("anchor_point") is not None:
            refs.append(("chains", "anchor_point", "points", c["anchor_point"]))
        for sp in c.get("spokes") or []:
            if sp.get("cell") is not None:
                refs.append(("chains[*].spokes", "cell", "cells", sp["cell"]))

    for name, p in (data.get("points") or {}).items():
        if p.get("anchor_point") is not None:
            refs.append(("points", "anchor_point", "points", p["anchor_point"]))

    for cp in data.get("coordinate_placements") or []:
        if cp.get("anchor_point") is not None:
            refs.append(("coordinate_placements", "anchor_point", "points", cp["anchor_point"]))

    for t in data.get("thermal_via_arrays") or []:
        if t.get("anchor_point") is not None:
            refs.append(("thermal_via_arrays", "anchor_point", "points", t["anchor_point"]))

    for cell in (data.get("cells") or {}).values():
        for ncp in cell.get("clone_placements") or []:
            if ncp.get("cell") is not None:
                refs.append(("cells[*].clone_placements", "cell", "cells", ncp["cell"]))

    for tree in data.get("trees") or []:
        anchor = tree.get("anchor") or {}
        if anchor.get("point") is not None:
            refs.append(("trees[*].anchor", "point", "points", anchor["point"]))
        if anchor.get("ref") is not None and not anchor.get("external"):
            refs.append(("trees[*].anchor", "ref", "entities-or-live", anchor["ref"]))
        if tree.get("pivot_ref") is not None:
            refs.append(("trees[*]", "pivot_ref", "trees[*].nodes", tree["pivot_ref"]))
        nodes: list = []
        _walk_nodes(tree.get("nodes"), nodes)
        for n in nodes:
            kind = n.get("kind")
            target = _KIND_TARGET.get(kind, "legacy-refdes" if kind is None else "local/live")
            refs.append((f"trees[*].nodes(kind={kind})", "ref", target, n.get("ref")))
            na = n.get("anchor") or {}
            if na.get("point") is not None:
                refs.append(("trees[*].nodes[kind=mount/component]", "anchor.point", "points",
                             na["point"]))
            if na.get("ref") is not None:
                refs.append(("trees[*].nodes[kind=component]", "anchor.ref",
                             "live-refdes", na["ref"]))
        self_anchor = anchor.get("self") if isinstance(anchor, dict) else None
        if isinstance(self_anchor, dict) and self_anchor.get("ref") is not None:
            refs.append(("trees[*].anchor[self]", "self.ref", "trees[*].nodes",
                         self_anchor["ref"]))

    for ti in data.get("tree_instances") or []:
        if ti.get("template") is not None:
            refs.append(("tree_instances", "template", "trees", ti["template"]))

    return refs


def _sexp_files() -> list[Path]:
    files = sorted(p for p in _REPO.glob("profiles/*-copy/**/*.sexp")
                   if ".bak." not in p.name and p.is_file())
    return files


def main() -> int:
    files = _sexp_files()
    print(f"# У0.1 name-reference inventory — {len(files)} working files (copies)\n")

    # section -> names, merged over all files (completeness by sets, per plan)
    all_names: dict[str, set[str]] = {}
    per_file: dict[str, dict] = {}
    for f in files:
        try:
            data = sexp_to_dict(f.read_text(encoding="utf-8"), path=str(f),
                                upgrade=False) or {}
        except Exception as exc:  # noqa: BLE001 - inventory probe, report and go on
            print(f"!! cannot parse {f.relative_to(_REPO)}: {type(exc).__name__}: {exc}")
            continue
        names = _names_of(data)
        per_file[str(f.relative_to(_REPO))] = {"names": names, "refs": _walk_refs(data)}
        for section, s in names.items():
            all_names.setdefault(section, set()).update(x for x in s if x is not None)

    # Counts of record names per section (over all files, per-file summed)
    print("## Records per section (summed over the 9 files)\n")
    totals: Counter = Counter()
    for meta in per_file.values():
        for section, s in meta["names"].items():
            totals[section] += sum(1 for x in s if x is not None)
    for section in sorted(totals):
        if totals[section]:
            print(f"- {section}: {totals[section]}")
    print()

    # (source_section, field, target_section) -> count, with dangling targets
    print("## References: source section -> (field) -> target section\n")
    edge: Counter = Counter()
    dangling: Counter = Counter()
    dangling_examples: dict = {}
    for rel, meta in per_file.items():
        for src, field, target, name in meta["refs"]:
            edge[(src, field, target)] += 1
            known = all_names.get(target)
            if target == "trees":
                # A module node's ref may name a tree MATERIALIZED from a
                # tree_instances: declaration (name = the generated tree's name),
                # not a hand-written trees: entry — load expands it before linking.
                known = set(known or set()) | set(all_names.get("tree_instances") or set())
            if known is not None and name is not None and name not in known:
                dangling[(src, field, target)] += 1
                dangling_examples.setdefault((src, field, target), (rel, name))
    print("| source section | field | target section | refs | dangling |")
    print("|---|---|---|---:|---:|")
    for (src, field, target), count in sorted(edge.items()):
        print(f"| `{src}` | `{field}` | `{target}` | {count} | {dangling.get((src, field, target), 0)} |")
    print()

    if dangling:
        print("## Dangling references (target name not found) — sample\n")
        for key, (rel, name) in sorted(dangling_examples.items()):
            print(f"- {key[0]} -> {key[1]} -> {key[2]}: `{name}` in `{rel}` "
                  f"({dangling[key]} refs)")
        print()

    print("## Per-file counts\n")
    print("| file | records | refs |")
    print("|---|---:|---:|")
    for rel in sorted(per_file):
        meta = per_file[rel]
        n = sum(1 for s in meta["names"].values() for x in s if x is not None)
        print(f"| `{rel}` | {n} | {len(meta['refs'])} |")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
