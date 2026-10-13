# gui/docks/trees_node_form.py
"""Support for the node form's KIND and REF widgets (Ч3, 2026-10-13).

Three pieces, all of them read off the ONE kind table (kicadstamp.trees.
NODE_KIND_TABLE) so the menu, the rows and the refusal can never drift apart:

  * `kind_menu_rows` — the Kind combo's rows in ORDER (the four MENU kinds, plus
    an EXISTING node's own kind marked "(retired)");
  * `ref_combo_rows` — the Ref combo's items and placeholder for a kind, chosen
    by the table's `ref_source` column;
  * the "already used" surface: `used_ref_locations` (WHERE each used record's
    node stands), `used_ref_text` (the ONE wording of that place, shared by the
    combo tooltip and the refusal), `mark_used_items` (grey the used rows) and
    `used_ref_refusal`. Before Ч3 the refusal said only "already has a node in
    this file" — Denis: "непонятно, где".

They live HERE, not in gui/docks/trees_dock.py: that file is a giant and may only
shrink, so every new branch of the node form goes to a helper module instead.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt

from kicadstamp.i18n import _
from kicadstamp.trees import MENU_KINDS, TreeNode, node_kind_label, node_kind_ref_source

#: Node kinds whose ref is a NAME LOCAL to one tree (a mount name, a copper
#: container name, a component node name) — never a config RECORD, so two trees
#: may legitimately reuse one. Excluded from the "used" walk for the same reason
#: `TreesDock._used_refs` excludes them.
_LOCAL_REF_KINDS = ("module", "copper", "component")

#: The Ref field's hint for a kind whose ref is a NAME LOCAL to the tree (source
#: "local" in NODE_KIND_TABLE): the wording names WHAT is being asked for, so a
#: mount node, a copper container and a component node do not all say the same
#: indistinguishable thing. Not part of that table — the table is about kinds,
#: these are three sentences.
LOCAL_REF_PLACEHOLDERS = {
    "mount": _("mount node name (unique in tree)"),
    "copper": _("copper container name (unique in tree)"),
    "component": _("component node name (unique in tree)"),
}


def kind_menu_rows(has_existing: bool,
                   existing_kind: Optional[str]) -> list[tuple[str, Optional[str]]]:
    """(label, kind value) rows for the Kind combo, in order.

    The four MENU kinds always come first (Entity is the default). In EDIT mode
    an existing node whose kind the menu no longer offers gets its OWN row,
    marked "(retired)" — its kind must survive an edit (Ч3: "убрать из меню" ≠
    "удалить из грамматики"); a node with NO kind at all gets "auto (retired)".
    """
    rows = [(node_kind_label(k), k) for k in MENU_KINDS]
    if not has_existing:
        return rows
    if existing_kind is None:
        rows.append((_("auto (retired)"), None))
    elif existing_kind not in MENU_KINDS:
        rows.append((_("{label} (retired)").format(
            label=node_kind_label(existing_kind)), existing_kind))
    return rows


def ref_combo_rows(kind: Optional[str], *, ref_candidates,
                   module_candidates, net_trace_candidates
                   ) -> tuple[list[tuple], str]:
    """(items, placeholder) for the Ref combo of `kind`.

    `items` are (display_text, kind, name) triples for `mark_used_items`/
    `_set_ref_items`; an EMPTY list means "free text / no candidates" and the
    caller just clears the combo. WHERE the list comes from is the table's
    `ref_source` column (kicadstamp.trees.NODE_KIND_TABLE): rewording what a kind
    means cannot leave the combo offering the wrong list.
    """
    source = node_kind_ref_source(kind)
    if source == "trees":
        # Ref = a child TREE NAME (not a record) — the form's separate tree-name
        # candidate list, minus self/dups/cycle risks.
        return ([(name, None, name) for name in module_candidates],
                _("child tree name"))
    if source == "local":
        # A local NAME (unique within the tree), NOT a config record — free text
        # (mount: §Y.1.3; copper container: P.2.4; component node: Э3/Т3.2 — its
        # ref names the NODE, never the component it places).
        return ([], LOCAL_REF_PLACEHOLDERS.get(kind,
                                               _("local name (unique in tree)")))
    if source == "free":
        return ([], _("external refdes (live board)"))
    if source == "net_traces":
        # Inter-node copper: the Ref IS a net_traces: record name, resolved BY
        # KIND (never auto-searched — its ref is a net NAME that may collide).
        return ([(name, kind, name) for name in net_trace_candidates],
                _("net_traces record name"))
    if kind is None:
        # auto: ALL placeable names — a name unique to a section shown plain, one
        # shared by 2+ sections shown once per section as {kind}:{name}.
        section_count: dict[str, int] = {}
        for _k, name in ref_candidates:
            section_count[name] = section_count.get(name, 0) + 1
        items = []
        for k, name in ref_candidates:
            if section_count[name] > 1:
                items.append((f"{k}:{name}", k, name))
            else:
                items.append((name, None, name))
        return (items, _("record name (from config)"))
    return ([(name, kind, name) for k, name in ref_candidates if k == kind],
            _("record name (from config)"))


def _node_label(node: TreeNode) -> str:
    """How the tooltip/refusal names ONE node: its ref, plus the kind's label
    from the shared table (kicadstamp.trees.NODE_KIND_TABLE) — the same labels
    the tree rows carry."""
    if node.kind is None:
        return node.ref
    return f"{node.ref} ({node_kind_label(node.kind)})"


def used_ref_locations(trees) -> dict[str, tuple[str, str]]:
    """ref -> (tree name, node label) for every already-used RECORD ref in the
    FOREST (scope = the whole file, which is what the loader's "a ref appears in
    at most one node" rule measures)."""
    out: dict[str, tuple[str, str]] = {}

    def walk(nodes, tree_name: str) -> None:
        for node in nodes:
            if node.kind not in _LOCAL_REF_KINDS:
                out[node.ref] = (tree_name, _node_label(node))
            walk(node.children, tree_name)

    for tree in trees:
        walk(tree.nodes, tree.name)
    return out


def used_ref_text(tree_name: str, node_label: str) -> str:
    """The place of an already-used record, phrased once — shared by the combo's
    greyed-row tooltip (mark_used_items) and by the refusal (used_ref_refusal)."""
    return _("already stands in tree {tree!r} (node {node})").format(
        tree=tree_name, node=node_label)


def mark_used_items(combo, places: dict[str, tuple[str, str]],
                    own_ref: Optional[str]) -> None:
    """Disable (grey) the combo's rows of already-used records and give each the
    place as its tooltip.

    `own_ref` (Edit mode) is NEVER marked: a node's own ref is not "taken from
    itself", and disabling its row would fight the form pre-filling it."""
    for row in range(combo.count()):
        data = combo.itemData(row)
        name = data[1] if isinstance(data, tuple) else None
        if name is None or name == own_ref:
            continue
        place = places.get(name)
        if place is None:
            continue
        item = combo.model().item(row)
        if item is not None:
            item.setEnabled(False)
        combo.setItemData(row, used_ref_text(*place), Qt.ItemDataRole.ToolTipRole)


def used_ref_refusal(ref: str, place: Optional[tuple[str, str]]) -> str:
    """The refusal text for a ref that already has a node elsewhere.

    With a known place the refusal NAMES it (Ч3 «Дополнение»: "where?"), and
    tells the two honest ways out; without one (a form handed only a set) it
    falls back to the historic wording."""
    if place is None:
        return _("Record {ref!r} already has a node in this file — a record's "
                 "position source must be exactly one.").format(ref=ref)
    return _("Record {ref!r} {where} — a record has exactly one position source; "
             "move the node, or embed tree {tree!r} as a Tree node.").format(
                 ref=ref, where=used_ref_text(*place), tree=place[0])
