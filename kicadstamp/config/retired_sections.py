# kicadstamp/config/retired_sections.py
"""Retired config sections and tree-node kinds — one refusal table, one point.

2026-10-10 (plan_2026_10_08_remove_spokes.md, decision Д1): a few config
"sections" and tree-node "kinds" no longer exist in the product. A file that
still carries them must NOT be read silently — the on-disk lift and every
read-modify-write path (config_writer, the GUI docks, the CLI) rewrite the file,
so an unrefused `chains:` would be dropped without a word on the next write.

So the whole pipeline has ONE point that refuses them: `sexp_format.sexp_to_dict`
(the "file text -> dict" step), through which the loader, the writer, the
on-disk lift, the GUI and the CLI all read. The tables below are DATA, so the
next retirement (the Placer plan's `clone_placements`) adds one line, not a
second function.

Empty sections/records are NOT a refusal: an empty `(chains)` is accepted and
simply does not survive into the dict (the next write drops it).
"""
from __future__ import annotations

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

# Retired SECTION key -> the human name used in the refusal message. `rules` is
# the pre-2026-09-01 alias of `chains`; both are refused identically.
RETIRED_SECTIONS: dict[str, str] = {
    "chains": "chains",
    "rules": "chains (legacy `rules`)",
}

# Retired tree NODE kind -> the human name used in the refusal message. `rule`
# is the pre-2026-09-01 alias of `chain`.
RETIRED_NODE_KINDS: dict[str, str] = {
    "chain": "chain",
    "rule": "chain (legacy `rule`)",
}

# One hint both refusals share: never a silent drop — remove the data, or open
# the file with a build that still knows it.
_HINT = _("remove them or open the profile with an older build")


def _record_names(records) -> list[str]:
    """Identity of every record in a retired section, for the message: `name`
    when set, else `net` (a chains record's own fallback identity)."""
    out: list[str] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        out.append(str(rec.get("name") or rec.get("net") or "?"))
    return out


def refuse_retired_sections(data: dict, path: str = "<config>") -> None:
    """Refuse a raw parsed config dict that still carries a retired SECTION.

    A non-empty `chains:` / `rules:` is a FATAL naming the file and every
    record in it; an empty one is silently dropped from `data` (it must not
    survive into the dict, or the next write would emit it again). Call this
    BEFORE a section key is recognized, so a retired section reads as
    "retired", never as "unknown section"."""
    for key, label in RETIRED_SECTIONS.items():
        if key not in data:
            continue
        value = data[key]
        if not value:
            data.pop(key, None)
            continue
        names = _record_names(value)
        raise ValidationError(format_fatal_error(
            _("config {path}: the {label} section is no longer supported "
              "({count} entry/entries: {names})")
            .format(path=path, label=label, count=len(names),
                    names=", ".join(names)),
            [_HINT]))


def _walk_nodes(nodes):
    """Every node dict in a parsed tree, depth-first."""
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        yield node
        yield from _walk_nodes(node.get("children"))


def refuse_retired_node_kinds(trees, path: str = "<config>") -> None:
    """Refuse a config whose `trees:` carries a node of a retired KIND
    (`chain` / legacy `rule`) — the same refusal as a retired section, with its
    own input (the parsed tree dicts). Names the file, the tree and the node's
    ref."""
    for tree in trees or []:
        if not isinstance(tree, dict):
            continue
        tree_name = tree.get("name")
        for node in _walk_nodes(tree.get("nodes")):
            kind = node.get("kind")
            if kind in RETIRED_NODE_KINDS:
                raise ValidationError(format_fatal_error(
                    _("config {path}: tree {tree!r} has a node {ref!r} of the "
                      "retired kind {kind!r} ({label})")
                    .format(path=path, tree=tree_name, ref=node.get("ref"),
                            kind=kind, label=RETIRED_NODE_KINDS[kind]),
                    [_HINT]))
