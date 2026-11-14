#!/usr/bin/env python3
"""Ч0 probe for plan_2026_11_14_used_refs_by_uuid.md — how many ENTITIES the
node form's "already used" surface greys out in a real profile, and WHY.

Usage:
    python -m kicadstamp.diagnostics.probe_2026_11_14_used_refs_by_uuid <root config>

READ-ONLY with respect to the trees/config file, but NOT side-effect free:
`load_config` runs `upgrade_graph_on_disk` / `upgrade_registries_on_disk` ON DISK.
Point it at a COPY, never at a live profile (Denis's copies live in `profiles/`
and Syncthing watches that tree). The recommended recipe, from the plan:

    TMP=$(mktemp -d) && cp -a profiles/<name> "$TMP/<name>"
    # relative root_sheet / schematic_files -> absolute in the copy
    python -m kicadstamp.diagnostics.probe_2026_11_14_used_refs_by_uuid "$TMP/<name>/<name>.sexp"
    rm -rf "$TMP"

Two rules are measured side by side, because the FIRST column has to stay
reproducible after the fix (it is the behaviour being replaced):

  * OLD — the GUI's own copy of the local-kind list, missing "mount"
    (`gui/docks/trees_node_form.py:33` up to commit 5d395c5c), occupancy by BARE
    NAME. `GUI_LOCAL_KINDS_COPY` below spells that copy out on purpose: this
    column must NOT follow the product.
  * NEW — `kicadstamp.trees.LOCAL_REF_KINDS` and occupancy by the record's UUID,
    with a NAME fallback only for a node that carries no `ref_uuid` (an old
    file) and only inside the section the loader would resolve that node in
    (`format3._F3_NODE_KIND_TARGET`; a node with no kind at all is resolved in
    every section, hence occupies its name everywhere).

The reason buckets are Denis's: a MOUNT (or another local kind) whose name
happens to be an entity's, ANOTHER RECORD of the same name, or a REAL repeat of
the very same record (which must stay grey).
"""
from __future__ import annotations

import sys

from kicadstamp.config import load_config
from kicadstamp.config.form_identity import section_uuids
from kicadstamp.trees import LOCAL_REF_KINDS, node_kind_label

#: The GUI's copy of the local-kind list as it stood before this fix — "mount"
#: missing, which is cause #1 of the defect. Deliberately NOT `LOCAL_REF_KINDS`.
GUI_LOCAL_KINDS_COPY = ("module", "copper", "component")

#: section -> the node kinds that resolve into it (the loader's own table).
def _kind_targets() -> dict:
    from kicadstamp.config.format3 import _F3_NODE_KIND_TARGET
    return _F3_NODE_KIND_TARGET


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _label(node) -> str:
    return node.ref if node.kind is None else f"{node.ref} ({node_kind_label(node.kind)})"


def _old_places(trees) -> dict:
    """ref -> (tree, label, node) under the OLD rule (bare name, GUI's list)."""
    out: dict = {}
    for tree in trees:
        for node in _walk(tree.nodes):
            if node.kind not in GUI_LOCAL_KINDS_COPY:
                out[node.ref] = (tree.name, _label(node), node)
    return out


def _new_maps(trees) -> tuple:
    """(uuid -> place, (section, name) -> place) under the NEW rule."""
    by_uuid: dict = {}
    by_section: dict = {}
    all_sections = list(dict.fromkeys(_kind_targets().values()))
    for tree in trees:
        for node in _walk(tree.nodes):
            if node.kind in LOCAL_REF_KINDS:
                continue
            place = (tree.name, _label(node))
            if node.ref_uuid:
                by_uuid[node.ref_uuid] = place
                continue
            section = _kind_targets().get(node.kind)
            sections = all_sections if node.kind is None else ([section] if section else [])
            for one in sections:
                by_section[(one, node.ref)] = place
    return by_uuid, by_section


def _old_reason(node, uuid) -> str:
    """WHY the OLD rule greys a record named like `node` (the node's own kind)."""
    if uuid and node.ref_uuid == uuid:
        return "real"
    if node.kind in LOCAL_REF_KINDS or node.kind == "external":
        return "local"
    if _kind_targets().get(node.kind) != "entities":
        return "other-section"
    return "real"          # a same-section node without a uuid: a real repeat


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    cfg, _ctx = load_config(argv[1])
    trees = list(cfg.trees)
    old = _old_places(trees)
    by_uuid, by_section = _new_maps(trees)
    entities = section_uuids(cfg, "entities")

    greyed_old = greyed_new = 0
    reasons: dict = {}
    rows = []
    for name, uuid in entities.items():
        hit_old = old.get(name)
        hit_new = ((by_uuid.get(uuid) if uuid else None)
                   or by_section.get(("entities", name)))
        if hit_old is not None:
            greyed_old += 1
            reason = _old_reason(hit_old[2], uuid)
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            reason = ""
        if hit_new is not None:
            greyed_new += 1
        rows.append((name, uuid, reason, hit_old, hit_new))

    print(f"config: {argv[1]}")
    print(f"trees: {len(trees)}   entities: {len(entities)}")
    print(f"greyed under the OLD rule: {greyed_old}  {reasons}")
    print(f"greyed under the NEW rule: {greyed_new}")
    print()
    print("the three buckets:")
    for reason in ("local", "other-section", "real"):
        print(f"  {reason:14s} {reasons.get(reason, 0)}")
    print()
    print("entities the OLD rule greys:")
    for name, uuid, reason, hit_old, _hit_new in rows:
        if hit_old is None:
            continue
        fixed = "  -> AVAILABLE again" if _hit_new is None else ""
        print(f"  {name:24s} {reason:14s} stands in {hit_old[0]!r} "
              f"(node {hit_old[1]}){fixed}")
    print()
    still = [n for (n, _u, _r, _o, h) in rows if h is not None]
    print(f"still greyed under the NEW rule ({len(still)}): {still}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
