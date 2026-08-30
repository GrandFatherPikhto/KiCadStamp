#!/usr/bin/env python3
# tools/convert_placements.py
"""One-time migration: legacy `clone_placements:` -> `Entity` + a one-node
placement tree (Entity/Placement split, design_2026_08_30_entity_placement_
grammar.md §9, plan §6.2).

For every former clone_placements: entry:
  1. an `entities:` record — the "what" (cell/nets/params/net_overrides/
     cluster/sheet/flags/layer/mirror/refs/comment), NO position at all;
  2. a `trees:` tree with ONE node (kind "placement", ref = the Entity's
     name): the anchor comes from the old anchor_* fields (point / ref /
     role+sheet+cluster+pad, or (origin) when the clone was absolute) and the
     node carries xy (or polar) + rotation from the old positional fields.

The old clone_placements: list is cleared (migrated). Every other section
(rules/coordinate_placements/thermal_via_arrays/net_traces/points/cells) is
left untouched. Idempotent-friendly: re-running on an already-converted file
finds no clones and only (re)writes the unchanged sections.

Run on a COPY of a live profile:
    tools/convert_placements.py profiles/3ch-awg-tia-v103/3ch-awg-tia.sexp
      # -> converts a COPY of the live profile in place (clone_placements ->
      #    Entity + placement trees)
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path


def _clone_effective_name(clone: Dict[str, Any]) -> str:
    """The legacy save/--only identity — `name`, else the Cluster tag (the
    same rule clone_placement_effective_name documents)."""
    return clone.get("name") or clone.get("cluster") or ""


def _clone_to_entity(clone: Dict[str, Any]) -> Dict[str, Any]:
    """The former clone_placement's electrical + identity fields, 1:1 — with
    NO position (that now lives only in the trees: node)."""
    entity: Dict[str, Any] = {
        "name": _clone_effective_name(clone),
        "cell": clone.get("cell") or "",
    }
    for key in ("nets", "params", "net_overrides", "refs"):
        value = clone.get(key)
        if value:
            entity[key] = value
    for key in ("cluster", "sheet", "layer", "comment"):
        value = clone.get(key)
        if value:
            entity[key] = value
    for flag in ("retired", "skip", "ignore_selection", "by_selection", "mirror"):
        if clone.get(flag):
            entity[flag] = True
    return entity


def _clone_to_tree(clone: Dict[str, Any]) -> Dict[str, Any]:
    """One-node tree (design §9.2): the anchor derives from the old anchor
    fields (or (origin) for a formerly-absolute clone); the node is the
    Entity's PLACEMENT (kind "placement", xy/polar + rotation)."""
    name = _clone_effective_name(clone)
    if clone.get("anchor_point"):
        anchor: Dict[str, Any] = {"point": clone["anchor_point"]}
    elif clone.get("anchor_ref"):
        anchor = {"ref": clone["anchor_ref"]}
    elif clone.get("anchor_role"):
        anchor = {"role": clone["anchor_role"]}
        if clone.get("anchor_sheet"):
            anchor["sheet"] = clone["anchor_sheet"]
        if clone.get("anchor_cluster"):
            anchor["cluster"] = clone["anchor_cluster"]
        if clone.get("anchor_pad"):
            anchor["pad"] = clone["anchor_pad"]
    else:
        anchor = {"origin": True}

    node: Dict[str, Any] = {"ref": name, "kind": "placement"}
    if clone.get("radius_mm") is not None:
        # Polar (absolute under (origin), or a polar OFFSET under an anchor).
        node["polar"] = [clone["radius_mm"], clone.get("angle_deg", 0.0)]
    else:
        # Absolute point under (origin), or a flat shift under an anchor.
        node["xy"] = list(clone.get("xy") or [0.0, 0.0])
    if clone.get("rotation_deg"):
        node["rotation"] = clone["rotation_deg"]
    return {"name": name, "anchor": anchor, "nodes": [node]}


def _collect_node_refs(nodes: Any) -> set:
    """Every node ref in a tree's node list (recursively into children)."""
    refs: set = set()
    for node in nodes or []:
        if isinstance(node, dict):
            refs.add(node.get("ref"))
            refs |= _collect_node_refs(node.get("children"))
    return refs


def convert_clone_placements_to_entities(data: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate a config DICT in place-style: every clone_placements: entry
    becomes an Entity + a one-node tree; the clone list is cleared. Every
    other key is preserved. Returns the new dict (input is not mutated).

    Tolerant of a PARTIALLY-converted profile (one where some refs already
    have trees: nodes, e.g. from a preview run or TreesDock editing): an
    Entity whose name already exists is not duplicated, and a tree is only
    created for a ref that is not ALREADY placed by a trees: node — so the
    result never violates the link_trees "a ref appears in at most one node"
    invariant (found on the live 3ch-awg-tia-v103 copy, 2026-08-30)."""
    out: Dict[str, Any] = dict(data)
    clones: List[Dict[str, Any]] = [
        c for c in (out.get("clone_placements") or []) if isinstance(c, dict)]
    entities: List[Dict[str, Any]] = list(out.get("entities") or [])
    trees: List[Dict[str, Any]] = list(out.get("trees") or [])
    existing_entities: set = {e.get("name") for e in entities if isinstance(e, dict)}
    existing_refs: set = set()
    for tree in trees:
        if isinstance(tree, dict):
            existing_refs |= _collect_node_refs(tree.get("nodes"))
    for clone in clones:
        if not clone.get("name") and not clone.get("cluster"):
            continue  # malformed entry — never converted, never dropped
        if not clone.get("cell"):
            continue
        name = _clone_effective_name(clone)
        if name in existing_entities:
            continue  # already converted in an earlier run
        entities.append(_clone_to_entity(clone))
        existing_entities.add(name)
        if name not in existing_refs:
            trees.append(_clone_to_tree(clone))
            existing_refs.add(name)
    out["entities"] = entities
    out["trees"] = trees
    out["clone_placements"] = []
    return out


def convert_placements_file(path: Path) -> Dict[str, Any]:
    """Read a config file (.sexp/.json), migrate clone_placements -> Entity +
    tree, write back. Returns the new config dict. Raises OSError on a
    non-readable/non-writable file (same contract as config_writer)."""
    from kicadstamp.config_writer import read_data, write_data

    data = read_data(path)
    converted = convert_clone_placements_to_entities(data)
    write_data(path, converted)
    return converted


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Migrate clone_placements: to Entity + placement trees "
                    "(run on a COPY of a live profile)")
    parser.add_argument("path", help="config file to convert (.sexp)")
    args = parser.parse_args(argv)
    path = Path(args.path)
    try:
        converted = convert_placements_file(path)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"converted {path}: "
          f"{len(converted.get('clone_placements') or [])} clone_placements, "
          f"{len(converted.get('entities') or [])} entities, "
          f"{len(converted.get('trees') or [])} trees")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
