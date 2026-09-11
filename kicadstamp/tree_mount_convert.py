# kicadstamp/tree_mount_convert.py
"""Convert the pre-2026-09-11 trees: grammar (per-node own_anchor) into the
mount-node grammar (plan_2026_09_11_tree_mount_nodes, task Y.6).

Before this change a node carried its own live (role ...) anchor as a hidden
attribute: `(node (ref "pif_dvdd_channel_0") (kind placement) (xy ...) (anchor
(role "AD_DAC") (sheet "Channel_0") (cluster "DAC_BUF") (pad "3")))` — the
node was DRAWN under its parent but COMPUTED from that live component, so the
drawn hierarchy lied about the real one. The new grammar makes the anchor an
explicit POINT OF REFERENCE node:

    (node (ref "AD_DAC_pad3") (kind mount)
      (anchor (role "AD_DAC") (sheet "Channel_0") (cluster "DAC_BUF") (pad "3"))
      (node (ref "pif_dvdd_channel_0") (kind placement) (xy -1.5 0.0)))

There is NO runtime compatibility layer (Denis 2026-09-11): the parser now
REJECTS the old grammar with a pointer here, and this converter is the only
way to migrate a config. It is a PURE dict -> dict operation (no board, no
Config), so it runs anywhere; the CLI wrapper writes the result out.

Geometry is preserved EXACTLY: a mount node has no xy/rotation of its own and
the wrapped node keeps its xy/polar/rotation unchanged, so the child's base is
the very same live point it already used.

What it does NOT touch (those are the next stages, not this task): pivot-*,
a tree's own (anchor ...), is_auto, and every non-trees section.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import invalidate_path

logger = logging.getLogger(__name__)

# The role-anchor shape (plan §Y.1.2) — the only keys a mount anchor carries.
_ANCHOR_KEYS = ("role", "sheet", "cluster", "pad")


def _new_report() -> dict:
    return {"trees_touched": 0, "mounts_created": 0, "nodes_wrapped": 0,
            "grouped": 0, "renames": [], "trees": {}}


def _is_old_grammar(node: Any) -> bool:
    """A node dict in the REMOVED grammar: it carries a nested anchor mapping
    and is NOT already a kind "mount" node (mount nodes also carry an anchor
    mapping — that is what makes the converter idempotent)."""
    return (isinstance(node, dict) and isinstance(node.get("anchor"), dict)
            and node.get("kind") != "mount")


def _anchor_key(anchor: dict) -> tuple:
    return tuple(anchor.get(k) for k in _ANCHOR_KEYS)


def _collect_tree_refs(nodes: list) -> set[str]:
    """Every node ref in a tree, any kind, recursively (the mount-name
    collision domain: a new mount ref must not collide with an existing one)."""
    refs: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ref = node.get("ref")
        if isinstance(ref, str):
            refs.add(ref)
        children = node.get("children")
        if isinstance(children, list):
            refs.update(_collect_tree_refs(children))
    return refs


def _mount_name(anchor: dict, used_refs: set[str], report: dict,
                tree_name: str) -> str:
    """A mount node's ref (plan §Y.6.3): `{role}_pad{N}` when a pad is set, else
    `{role}`. On a collision with an existing ref in the same tree, a numeric
    suffix is appended AND the rename is recorded in the report — never silent.
    The chosen name is reserved immediately (two mounts in one tree can never
    take the same name)."""
    base = str(anchor["role"])
    if anchor.get("pad") is not None:
        base = f"{base}_pad{anchor['pad']}"
    name = base
    suffix = 2
    while name in used_refs:
        name = f"{base}_{suffix}"
        suffix += 1
    if name != base:
        report["renames"].append((tree_name, base, name))
    used_refs.add(name)
    return name


def _convert_node(node: dict, used_refs: set[str], report: dict,
                  tree_name: str) -> dict:
    """One node dict, children converted recursively. A node's old-grammar
    anchor mapping is DROPPED here — the caller has already wrapped this node
    in a mount node carrying it; a kind "mount" node keeps its own anchor."""
    out = dict(node)
    if node.get("kind") != "mount":
        out.pop("anchor", None)
    children = node.get("children")
    if isinstance(children, list) and children:
        out["children"] = _convert_siblings(children, used_refs, report, tree_name)
    return out


def _convert_siblings(nodes: list, used_refs: set[str], report: dict,
                      tree_name: str) -> list[dict]:
    """Convert one sibling list. Nodes in the old grammar are grouped by
    IDENTITY of their anchor (role/sheet/cluster/pad) and become children of ONE
    mount node, inserted at the position of the FIRST such node (plan §Y.6.2 —
    same anchor means the same reference point, so one mount node is enough);
    nodes without an anchor keep their place. Grouping is deliberately limited
    to ONE sibling list: merging across different parents would reparent nodes,
    which is not what this migration does."""
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple[str, Any]] = []
    for node in nodes:
        if _is_old_grammar(node):
            key = _anchor_key(node["anchor"])
            if key not in groups:
                groups[key] = []
                order.append(("group", key))
            groups[key].append(node)
        else:
            order.append(("plain", node))

    out: list[dict] = []
    for kind, payload in order:
        if kind == "plain":
            if isinstance(payload, dict):
                out.append(_convert_node(payload, used_refs, report, tree_name))
            continue
        members = groups[payload]
        raw_anchor = members[0]["anchor"]
        anchor = {k: raw_anchor[k] for k in _ANCHOR_KEYS
                  if raw_anchor.get(k) is not None}
        name = _mount_name(anchor, used_refs, report, tree_name)
        report["mounts_created"] += 1
        report["nodes_wrapped"] += len(members)
        if len(members) > 1:
            report["grouped"] += len(members) - 1
        report["trees"][tree_name] = report["trees"].get(tree_name, 0) + 1
        out.append({
            "ref": name,
            "kind": "mount",
            "anchor": anchor,
            "children": [_convert_node(m, used_refs, report, tree_name)
                         for m in members],
        })
    return out


def convert_trees_dict(data: dict) -> tuple[dict, dict]:
    """Pure conversion of a whole config dict's `trees` section (everything
    else is copied verbatim). Returns (new_data, report); running it on an
    already-converted config changes nothing (idempotent)."""
    report = _new_report()
    out = dict(data)
    trees = data.get("trees")
    if not isinstance(trees, list):
        return out, report
    new_trees: list = []
    for tree in trees:
        if not isinstance(tree, dict):
            new_trees.append(tree)
            continue
        nodes = tree.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            new_trees.append(dict(tree))
            continue
        if not any(_is_old_grammar(n) for n in _iter_nodes(nodes)):
            new_trees.append(dict(tree))
            continue
        report["trees_touched"] += 1
        used_refs = _collect_tree_refs(nodes)
        new_tree = dict(tree)
        new_tree["nodes"] = _convert_siblings(nodes, used_refs, report,
                                              tree.get("name", "?"))
        new_trees.append(new_tree)
    out["trees"] = new_trees
    return out, report


def _iter_nodes(nodes: list):
    """Every node dict of a sibling list, recursively (detection helper)."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        children = node.get("children")
        if isinstance(children, list):
            yield from _iter_nodes(children)


def _format_report(report: dict) -> list[str]:
    lines = [
        _("Tree mount conversion: {trees} tree(s) touched, {mounts} mount "
          "node(s) created, {wrapped} node(s) wrapped")
        .format(trees=report["trees_touched"], mounts=report["mounts_created"],
                wrapped=report["nodes_wrapped"]),
    ]
    if report["grouped"]:
        lines.append(_("  grouped {n} node(s) under a shared mount node")
                     .format(n=report["grouped"]))
    for tree_name, count in sorted(report["trees"].items()):
        lines.append(_("  tree {name!r}: {n} mount node(s)")
                     .format(name=tree_name, n=count))
    for tree_name, old, new in report["renames"]:
        lines.append(_("  tree {name!r}: mount name {old!r} was taken — "
                       "using {new!r}")
                     .format(name=tree_name, old=old, new=new))
    if not report["trees_touched"]:
        lines.append(_("Nothing to do: no own_anchor node found (already "
                       "converted)"))
    return lines


def convert_config_file(root: str, output: Optional[str] = None,
                        dry_run: bool = False) -> list[str]:
    """Convert the trees: section of the config at `root`.

    `output` — where to write the converted s-expr; None overwrites `root` in
    place (the old content is the user's responsibility — the CLI documents
    that a backup is worth making), an explicit path writes a NEW file and
    leaves the root untouched. `dry_run` returns the report without writing.
    """
    root_path = Path(root).resolve()
    # Read RAW (raw_trees=True): the removed own_anchor grammar must be visible
    # to the converter, while a normal reader fatals on it. include: graphs are
    # NOT expanded here — convert each file separately (or run `flatten` after
    # each file is converted); a config whose trees live in an included file
    # gets a warning line below, never a silent half-conversion.
    text = root_path.read_text(encoding="utf-8")
    data = sexp_to_dict(text, raw_trees=True)
    converted, report = convert_trees_dict(data)

    report_lines = _format_report(report)
    if data.get("include"):
        report_lines.append(_("WARNING: this file has include: entries — only "
                              "ITS OWN trees were converted; convert each "
                              "included file separately"))
    target = Path(output).resolve() if output else root_path
    if dry_run:
        report_lines.append(_("Would write to: {path}").format(path=target))
        return report_lines

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(dict_to_sexp(converted))
    invalidate_path(target)
    report_lines.append(_("Written to: {path}").format(path=target))
    logger.info(_("convert-trees: wrote {path}").format(path=target))
    return report_lines
