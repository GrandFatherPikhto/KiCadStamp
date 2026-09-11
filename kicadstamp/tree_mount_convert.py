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

Two STAGES, one command (the user does not care which stage moves what):

  1. own_anchor node -> kind "mount" node (above);
  2. the tree's INNER POINT: pivot-xy / pivot-polar / pivot-ref lift off the
     module NODE onto the REFERENCED tree (2026-09-11, plan_2026_09_11_tree_
     inner_point_and_rotation §V.4) — the inner point is one per tree, never
     per embedding (design Р3). Numbers are never touched. If the move cannot
     be a pure relocation the converter REFUSES (see _move_pivots) and writes
     nothing at all.

A module node's `ref` may ALSO name a `tree_instances:` instance, not a trees:
entry (2026-09-11, plan_2026_09_11_tree_instances_and_converter_safety §В.2).
The old lookup was built from trees: only, so such a node's pivot was silently
left in place and the serializer then refused the result AFTER the file had
already been truncated (a real profile was destroyed this way — see В.1). The
instance now resolves to its TEMPLATE: a default (zero) pivot is dropped, a
non-zero one that differs from the template's is a STOP.

What it does NOT touch: a tree's own (anchor ...) (incl. the self anchor),
the tree's own `rotation`, and every non-trees section (those are stage Б3 /
later).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _
from kicadstamp.utils.file_cache import invalidate_path
from kicadstamp.utils.safe_write import backup_file, write_text_atomic

logger = logging.getLogger(__name__)

# The role-anchor shape (plan §Y.1.2) — the only keys a mount anchor carries.
_ANCHOR_KEYS = ("role", "sheet", "cluster", "pad")


def _new_report() -> dict:
    return {"trees_touched": 0, "mounts_created": 0, "nodes_wrapped": 0,
            "grouped": 0, "renames": [], "trees": {}, "pivots_moved": 0,
            "instance_pivots_dropped": 0}


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
    # Stage Б2 (2026-09-11, plan_2026_09_11_tree_inner_point_and_rotation §V.4):
    # the tree's inner point moved from the module NODE to the TREE. The
    # tree_instances: declarations are passed too (Б3.2 §В.2): a module node may
    # reference a generated instance, which is not a trees: entry.
    _move_pivots(out["trees"], data.get("tree_instances"), report)
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


# ── stage Б2: the tree's inner point (plan §V.4) ───────────────────────────
#
# Before 2026-09-11 a MODULE NODE carried pivot-xy / pivot-polar / pivot-ref —
# the point of the EMBEDDED tree that must land on the node's marker. That
# point belongs to the TREE (one per tree, design Р3), so this pass lifts it
# onto the REFERENCED tree, numbers untouched, and drops it from the node.

_PIVOT_KEYS = ("pivot_xy", "pivot_polar", "pivot_ref")


def _node_pivot(node: dict) -> dict:
    """The pivot descriptor a raw dict carries ({key: value} for the one pivot
    key present, {} when none). Used for both a node dict and a tree dict — the
    keys are the same."""
    return {k: node[k] for k in _PIVOT_KEYS if node.get(k) is not None}


def _pivot_descriptor(pivot: dict) -> tuple:
    """A hashable/comparable form of a pivot descriptor (the raw dicts carry
    lists, which neither compare nor hash the way we need)."""
    return tuple(sorted(
        (k, tuple(v) if isinstance(v, list) else v) for k, v in pivot.items()))


def _is_zero_pivot(pivot: dict) -> bool:
    """True for a pivot that is a NO-OP today: (0,0) or r=0. Relocating it
    changes nothing, so it needs no human decision."""
    if "pivot_ref" in pivot:
        return False
    for key in ("pivot_xy", "pivot_polar"):
        if key in pivot:
            return all(float(v) == 0.0 for v in pivot[key])
    return True


def _instance_templates(instances: Any) -> dict[str, str]:
    """`name -> template` for every `tree_instances:` declaration (Б3.2 §В.2.2).

    A module node may reference a GENERATED INSTANCE, which is not a `trees:`
    entry — the old lookup was built from `trees:` only, so such a node's pivot
    was silently left in place and `dict_to_sexp` then refused the result AFTER
    the target had already been truncated (a real 252 КБ profile was destroyed
    this way; see В.1). The map lets the converter resolve those refs to the
    TEMPLATE that owns the inner point."""
    out: dict[str, str] = {}
    if not isinstance(instances, list):
        return out
    for inst in instances:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name")
        template = inst.get("template")
        if isinstance(name, str) and isinstance(template, str):
            out[name] = template
    return out


def _move_pivots(trees: list, instances: Any, report: dict) -> None:
    """Lift every module node's pivot onto the tree it references (§V.4.1).

    A module node's `ref` names EITHER a `trees:` entry OR a `tree_instances:`
    instance (Б3.2 §В.2.2); both resolve to the TREE that owns the inner point.
    For a generated instance the inner point IS its template's: a default
    (zero) pivot, or one EQUAL to the template's own, is a no-op and is simply
    dropped from the node; anything else cannot be a pure relocation.

    STOPS — raises ValidationError, so convert_config_file writes NOTHING (a
    partial conversion is never allowed, §V.4.2):

      * one tree embedded with DIFFERENT pivots: the inner point is one per
        tree, so choosing between them is a human decision;
      * the tree already carries its own pivot and an embedding disagrees;
      * a NON-ZERO pivot (or a pivot-ref) on a tree that is ALSO a root tree in
        this config: after the move it would shift that tree's STANDALONE
        placement — the case §V.3 calls out explicitly;
      * an INSTANCE embedded with a pivot that differs from its template's own
        (the point belongs to the TEMPLATE, never to one embedding);
      * a `ref` naming NEITHER a tree NOR an instance — previously a silent
        `continue`, after which the serializer failed with an unrelated message.
    """
    by_name = {t.get("name"): t for t in trees if isinstance(t, dict)}
    instance_of = _instance_templates(instances)
    asked: dict[str, list[tuple[dict, dict]]] = {}
    for tree in trees:
        if not isinstance(tree, dict):
            continue
        pending = list(tree.get("nodes") or [])
        while pending:
            node = pending.pop()
            if not isinstance(node, dict):
                continue
            pending.extend(node.get("children") or [])
            if node.get("kind") != "module":
                continue
            pivot = _node_pivot(node)
            if pivot:
                asked.setdefault(node.get("ref"), []).append((node, pivot))

    problems: list[str] = []
    for ref, entries in sorted(asked.items()):
        is_tree = ref in by_name
        template_name = ref if is_tree else instance_of.get(ref)
        target = by_name.get(template_name) if isinstance(template_name, str) else None
        if target is None:
            problems.append(_(
                "module node {ref!r}: references neither a trees: entry nor a "
                "tree_instances: instance, so its pivot cannot be moved onto a "
                "tree — check the module ref").format(ref=ref))
            continue
        variants = {_pivot_descriptor(p) for _n, p in entries}
        if len(variants) > 1:
            problems.append(_(
                "tree {name!r}: embedded with {count} DIFFERENT pivots — the "
                "inner point is one per tree").format(name=ref, count=len(variants)))
            continue
        pivot = entries[0][1]
        if not is_tree:
            # Generated instance (Б3.2 §В.2.2, plan §В.2.2): the instance
            # inherits its TEMPLATE's inner point. A zero pivot is the default
            # and a pivot equal to the template's own asks for exactly what the
            # instance already gets — either way dropping it from the node is a
            # pure no-op. Anything else is a human decision (set it on the
            # template, or give the instance its own point), never a guess.
            template_pivot = _node_pivot(target)
            if _is_zero_pivot(pivot) or (
                    template_pivot
                    and _pivot_descriptor(template_pivot) == _pivot_descriptor(pivot)):
                for node, _pivot in entries:
                    for key in _PIVOT_KEYS:
                        node.pop(key, None)
                report["instance_pivots_dropped"] += 1
                continue
            problems.append(_(
                "module node {ref!r} (instance of tree {template!r}) is embedded "
                "with a pivot ({pivot}) that differs from the template's own "
                "inner point — a generated instance inherits its template's "
                "point, so set it on the TEMPLATE tree instead").format(
                    ref=ref, template=template_name, pivot=pivot))
            continue
        existing = _node_pivot(target)
        if existing and _pivot_descriptor(existing) != _pivot_descriptor(pivot):
            problems.append(_(
                "tree {name!r}: already carries its own pivot, which differs "
                "from the one its embedding asks for").format(name=ref))
            continue
        if existing:
            continue  # identical — nothing to move
        if not _is_zero_pivot(pivot):
            problems.append(_(
                "tree {name!r}: embedded with a NON-ZERO pivot ({pivot}) while "
                "it is also a root tree — moving it onto the tree would shift "
                "its standalone placement").format(name=ref, pivot=pivot))
            continue
        # Pure relocation: the descriptor goes ONTO the tree and off the node(s).
        target.update(pivot)
        for node, _pivot in entries:
            for key in _PIVOT_KEYS:
                node.pop(key, None)
        report["pivots_moved"] += 1

    if problems:
        raise ValidationError(format_fatal_error(
            _("tree pivot conversion: {n} place(s) need a human decision — "
              "nothing was written").format(n=len(problems)),
            problems))


def _format_report(report: dict) -> list[str]:
    lines = [
        _("Tree conversion: {trees} tree(s) touched, {mounts} mount "
          "node(s) created, {wrapped} node(s) wrapped, {pivots} inner point(s) "
          "moved onto a tree")
        .format(trees=report["trees_touched"], mounts=report["mounts_created"],
                wrapped=report["nodes_wrapped"],
                pivots=report.get("pivots_moved", 0)),
    ]
    if report["grouped"]:
        lines.append(_("  grouped {n} node(s) under a shared mount node")
                     .format(n=report["grouped"]))
    if report.get("instance_pivots_dropped"):
        lines.append(_("  dropped {n} instance pivot(s) — a generated instance "
                       "inherits its template's inner point")
                     .format(n=report["instance_pivots_dropped"]))
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
    place (a timestamped `.bak` of the old content is made first, task В.1),
    an explicit path writes a NEW file and leaves the root untouched (no
    backup needed — the original is not modified). `dry_run` returns the
    report without writing.

    Writing is DATA-SAFE (task В.1, plan §В.1.2): the converted dict is
    serialized to a STRING and re-parsed with the NORMAL reader BEFORE the
    target is touched, then written via a temp file + os.replace. The old
    `with open(target, "w")` truncated the target FIRST, so a serialization
    error left a 0-byte config (leftover pivot_xy on a node made dict_to_sexp
    refuse only AFTER the truncation — found live on a real profile, in-place
    mode, reproduced twice). Any failure now leaves the target byte-for-byte
    intact.
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

    # §В.1.2 steps 1-2: serialize to a string and self-verify with the NORMAL
    # reader (never raw_trees=True) BEFORE the target is opened for writing. A
    # converter output the standard loader refuses must fail HERE, leaving the
    # user's file untouched.
    new_text = dict_to_sexp(converted)
    sexp_to_dict(new_text)

    # §В.1.2 step 3: in-place overwrite snapshots the old content first
    # (timestamped, never clobbers an earlier backup). With --output the root
    # is not modified, so no backup is made.
    backup_path = backup_file(target) if not output else None

    # §В.1.2 step 4: atomic replace — a temp file in the SAME directory, then
    # os.replace, so a crash/full disk can never leave a half-written config.
    write_text_atomic(target, new_text)
    invalidate_path(target)
    report_lines.append(_("Written to: {path}").format(path=target))
    if backup_path is not None:
        report_lines.append(_("Backup: {path}").format(path=backup_path))
    logger.info(_("convert-trees: wrote {path}").format(path=target))
    return report_lines
