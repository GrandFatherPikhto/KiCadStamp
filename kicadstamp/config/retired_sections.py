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

Two entries of that one point:

- `refuse_retired_section_node(key, node, path)` — a RAW root-level node, called
  from the parse loop BEFORE the aliases and before a section key is recognized,
  so a retired section reads as "retired", never as an unknown/free-form field.
  An empty section is dropped in silence (it must not survive into the dict, or
  the next write would emit it again).
- `refuse_retired_node_kinds(trees, path)` — the parsed `trees:` section.

`refuse_retired_sections(data, path)` is the same refusal for a RAW PARSED DICT,
which the s-expr path never produces: it is the `.json` reader's entry
(`includes._load_config_file`), the one other raw reader of the same sections.

Empty sections/records are NOT a refusal: an empty `(chains)` is accepted and
simply does not survive into the dict (the next write drops it).
"""
from __future__ import annotations

from ..cloner.sexp import sval
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


def _refuse_section(label: str, names: list[str], path: str) -> None:
    """The ONE section-refusal message — shared by the raw-node and the
    raw-dict entry, so both spell it (and translate it) identically."""
    raise ValidationError(format_fatal_error(
        _("config {path}: the {label} section is no longer supported "
          "({count} entry/entries: {names})")
        .format(path=path, label=label, count=len(names),
                names=", ".join(names)),
        [_HINT]))


def _record_names(records) -> list[str]:
    """Identity of every record in a retired section, for the message: `name`
    when set, else `net` (a chains record's own fallback identity)."""
    out: list[str] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        out.append(str(rec.get("name") or rec.get("net") or "?"))
    return out


def _raw_record_names(node) -> list[str]:
    """The same identity read off a RAW s-expr section node: every record
    child's `(name "…")` field when set, else its `(net "…")`. A format-3
    `(folders …)` child is the section's folder table, not a record."""
    out: list[str] = []
    for rec in node[1:]:
        if not isinstance(rec, list) or not rec or sval(rec[0]) == "folders":
            continue
        name = net = None
        for field_node in rec[1:]:
            if not isinstance(field_node, list) or len(field_node) < 2:
                continue
            field_key = sval(field_node[0])
            if field_key == "name":
                name = field_node[1]
            elif field_key == "net":
                net = field_node[1]
        out.append(str(name or net or "?"))
    return out


def _section_node_is_empty(node) -> bool:
    """True when a raw section node carries no RECORD child — only the section
    tag, and possibly the format-3 `(folders …)` table (a section with a folder
    table but no entries is a legal EMPTY section, design §3.8г)."""
    for item in node[1:]:
        if isinstance(item, list) and item and sval(item[0]) == "folders":
            continue
        return False
    return True


def refuse_retired_section_node(key: str, node, path: str = "<config>") -> bool:
    """Refuse ONE raw root-level s-expr node whose name is a retired SECTION.

    True means the caller must DROP the node: `sexp_format.sexp_to_dict` does,
    so the section never reaches the dict. An empty section is dropped in
    silence; a non-empty one is a FATAL naming the file and every record in it.

    The key is the RAW section name — this runs before the aliases and before
    the section-key dispatch, so a retired section reads as "retired", never as
    an unknown section or a free-form field."""
    label = RETIRED_SECTIONS.get(key)
    if label is None:
        return False
    if not _section_node_is_empty(node):
        _refuse_section(label, _raw_record_names(node), path)
    return True


def refuse_retired_sections(data: dict, path: str = "<config>") -> None:
    """Refuse a raw parsed config dict that still carries a retired SECTION —
    the `.json` reader's entry (the s-expr reader goes through
    `refuse_retired_section_node`).

    A non-empty `chains:` / `rules:` is a FATAL naming the file and every
    record in it; an empty one is silently dropped from `data` (it must not
    survive into the dict, or the next write would emit it again)."""
    for key, label in RETIRED_SECTIONS.items():
        if key not in data:
            continue
        value = data[key]
        if not value:
            data.pop(key, None)
            continue
        _refuse_section(label, _record_names(value), path)


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
