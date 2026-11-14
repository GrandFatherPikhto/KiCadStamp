# gui/docks/trees_node_form.py
"""Support for the node form's KIND and REF widgets (Ч3, 2026-10-13).

Three pieces, all of them read off the ONE kind table (kicadstamp.trees.
NODE_KIND_TABLE) so the menu, the rows and the refusal can never drift apart:

  * `kind_menu_rows` — the Kind combo's rows in ORDER (the four MENU kinds, plus
    an EXISTING node's own kind marked "(retired)");
  * `ref_combo_rows` — the Ref combo's items and placeholder for a kind, chosen
    by the table's `ref_source` column;
  * the "already used" surface: `used_ref_index` (WHERE each used record's node
    stands — the ONE owner), `RecordRefs` (which RECORD a combo row names),
    `used_ref_text` (the ONE wording of that place, shared by the combo tooltip
    and the refusal), `mark_used_items` (grey the used rows) and
    `used_ref_refusal`. Before Ч3 the refusal said only "already has a node in
    this file" — Denis: "непонятно, где".

2026-11-14 (§"used refs by uuid" in plan_2026_11_14_used_refs_by_uuid.md) — two
defects of that surface, both fixed here:

  * the module kept its OWN copy of the local-kind list and it was missing
    "mount", so a MOUNT's tree-local name greyed out an ENTITY of the same name
    (Denis's `fpga_vccint_5`). The copy is gone: the list is
    `kicadstamp.trees.LOCAL_REF_KINDS`, the very tuple the loader and the two
    node parsers read;
  * occupancy was compared by BARE NAME, so an ENTITY and a CELL of one name
    "occupied" each other although they are different records. It is compared by
    the record's UUID now — `ref_uuid` on the node, `Record.obj.uuid` on the
    record — and by (section, name) ONLY for an OLD node that carries no
    `ref_uuid`, inside the section the loader resolves such a node in.

They live HERE, not in gui/docks/trees_dock.py: that file is a giant and may only
shrink, so every new branch of the node form goes to a helper module instead.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt

from kicadstamp.i18n import _
from kicadstamp.trees import (MENU_KINDS, TreeNode, node_kind_label,
                              node_kind_ref_source)
from kicadstamp.trees import LOCAL_REF_KINDS as _LOADER_LOCAL_REF_KINDS
#: THE list of tree-local kinds — the LOADER's own, imported and re-bound, never
#: copied. This module USED to keep a copy and the copy had drifted ("mount" was
#: missing), which greyed out an ENTITY named like a mount. The mutation rig of
#: plan_2026_11_14_used_refs_by_uuid puts a literal here (its row M2) and cells
#: а/д/ж go red, so the copy cannot come back unnoticed.
LOCAL_REF_KINDS = _LOADER_LOCAL_REF_KINDS

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


def _kind_targets() -> dict:
    """kind -> the config section that kind's node REF resolves in: the loader's
    OWN table (`format3._F3_NODE_KIND_TARGET`, walked by the loader's
    normalization, by the writer stamp AND by `form_identity.node_ref_uuid`).
    Imported lazily for the same reason those two do it: `kicadstamp.config` is
    a heavy package and this module is imported by the GUI."""
    from kicadstamp.config.format3 import _F3_NODE_KIND_TARGET
    return _F3_NODE_KIND_TARGET


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

    A row carries NO uuid on purpose (2026-11-14): `(kind, name)` is exactly the
    pair the LOADER resolves a node by, and `RecordRefs.resolve` turns it into
    the same (section, uuid) the loader would reach — so a third item-data
    element would buy nothing and would silently break every two-value unpacking
    (`NodeFormWidget._on_ref_selected`, `AnchorFormWidget._on_ref_selected`, and
    the two tests that read a row's data).
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


class UsedRefs:
    """WHERE every already-used RECORD stands — keyed the way the LOADER keys a
    node, which is the whole point of 2026-11-14.

    * `by_uuid` — `ref_uuid` -> (tree, node label): a node that carries the
      record's uuid occupies THAT record, whatever its ref says;
    * `by_section` — (section, ref) -> place, for an OLD node WITHOUT a uuid
      only, and only in the section the loader would resolve that node in
      (`_F3_NODE_KIND_TARGET`; a node with no kind at all searches every section,
      so it holds its name in all of them);
    * `by_name` — ref -> place for EVERY non-local node: the grammar's rule 2
      (`trees._parse_node`: "a ref appears in at most one node across the whole
      file") is stated about the REF, and it is what must still refuse a typo or
      an external refdes another node already carries. Asked ONLY for a ref that
      names no record at all;
    * `names` — those same refs as a set, for the auto-numbering
      (`TreesDock._unique_ref`), which compares NAMES by nature.

    Local kinds are absent from all four: a mount's name is a name local to one
    tree, so it never occupies anything.
    """

    def __init__(self, by_uuid: Optional[dict] = None,
                 by_section: Optional[dict] = None,
                 by_name: Optional[dict] = None) -> None:
        self.by_uuid = dict(by_uuid or {})
        self.by_section = dict(by_section or {})
        self.by_name = dict(by_name or {})
        self.names = frozenset(self.by_name)

    def place(self, record: Optional[tuple], name: str, *,
              own_uuid: Optional[str] = None,
              own_name: Optional[str] = None) -> Optional[tuple[str, str]]:
        """(tree, node label) of the node that HOLDS the ref `name`, or None when
        it is free.

        `record` is `RecordRefs.resolve`'s answer — (section, uuid) when the ref
        names a record, None when it names none (a tree name, a mount name, an
        external refdes, a typo). A uuid hit decides ALONE: a node carrying the
        same uuid IS that record, however many other records share its name (the
        defect this fixes: an entity and a cell of one name used to take each
        other). A record WITHOUT a uuid (an old file) falls back to (section,
        name), so a shared name cannot cross sections. A ref naming NO record
        falls back to the plain REF — the loader's own rule 2.

        `own_uuid`/`own_name` (the node being edited) are never a place: a record
        is not "taken" by the very node that holds it.
        """
        uuid = record[1] if record is not None else None
        if uuid is not None and uuid != own_uuid:
            place = self.by_uuid.get(uuid)
            if place is not None:
                return place
        if not name:
            return None
        if record is not None:
            place = self.by_section.get((record[0], name))
        else:
            place = self.by_name.get(name)
        if place is not None and own_uuid is None and name == own_name:
            return None
        return place


def used_ref_index(trees) -> UsedRefs:
    """The ONE owner of the "already used" index over the WHOLE forest (scope =
    the whole file, which is what the loader's "a record's node appears at most
    once" rule measures).

    LOCAL kinds (kicadstamp.trees.LOCAL_REF_KINDS — module/mount/copper/
    component) are not records at all and hold NOTHING: a mount's name is unique
    inside its own tree only, so the same name may stand in two trees or beside an
    ENTITY of that name. That list is the loader's, imported — this module keeps
    no copy of it (it USED to, and the copy had drifted: it was missing "mount").
    """
    by_uuid: dict[str, tuple[str, str]] = {}
    by_section: dict[tuple[str, str], tuple[str, str]] = {}
    by_name: dict[str, tuple[str, str]] = {}
    targets = _kind_targets()
    every_section = tuple(dict.fromkeys(targets.values()))

    def walk(nodes, tree_name: str) -> None:
        for node in nodes:
            if node.kind not in LOCAL_REF_KINDS:
                place = (tree_name, _node_label(node))
                by_name.setdefault(node.ref, place)
                if node.ref_uuid is not None:
                    by_uuid[node.ref_uuid] = place
                elif node.kind is None:
                    # An OLD node with no kind: the loader searches EVERY
                    # section for its name, so it holds that name in all of them
                    # — it may be any one of those records.
                    for section in every_section:
                        by_section[(section, node.ref)] = place
                else:
                    section = targets.get(node.kind)
                    if section is not None:
                        by_section[(section, node.ref)] = place
            walk(node.children, tree_name)

    for tree in trees:
        walk(tree.nodes, tree.name)
    return UsedRefs(by_uuid, by_section, by_name)


class RecordRefs:
    """Which RECORD a ref-combo row names: `resolve(kind, name)` ->
    (section, uuid), or None when the ref names NO record at all.

    The section comes from the loader's own table, so a row is looked up exactly
    where the loader resolves the node built from it. `kind` None (an "auto" row)
    searches every section and answers only when the name is UNIQUE among them —
    `ref_combo_rows` prefixes a collision with `{kind}:` anyway, so a plain row is
    never ambiguous. A record with no uuid (a format-2 graph, a test fake) answers
    (section, None): the name resolves, there is simply no uuid to compare.
    """

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._sections: dict[str, dict] = {}
        self._targets = _kind_targets()

    def names_of(self, section: str) -> dict:
        """name -> uuid for ONE section, built on first use (a form that never
        greys a row never pays for these maps)."""
        try:
            return self._sections[section]
        except KeyError:
            from kicadstamp.config.form_identity import section_uuids
            self._sections[section] = section_uuids(self._cfg, section)
            return self._sections[section]

    def resolve(self, kind: Optional[str], name: str
                ) -> Optional[tuple[str, Optional[str]]]:
        if not name:
            return None
        section = self._targets.get(kind)
        sections = ((section,) if section is not None
                    else tuple(dict.fromkeys(self._targets.values())))
        hits = [(s, self.names_of(s)[name]) for s in sections
                if name in self.names_of(s)]
        if len(hits) != 1:
            return None
        return hits[0][0], (hits[0][1] or None)

    #: The same question as a callable, so a caller may hand this object itself
    #: to `mark_used_items`/`used_ref_place` (they take `resolve(kind, name)`).
    __call__ = resolve


def used_ref_text(tree_name: str, node_label: str) -> str:
    """The place of an already-used record, phrased once — shared by the combo's
    greyed-row tooltip (mark_used_items) and by the refusal (used_ref_refusal)."""
    return _("already stands in tree {tree!r} (node {node})").format(
        tree=tree_name, node=node_label)


def mark_used_items(combo, index: UsedRefs, own, resolve,
                    kind: Optional[str] = None) -> None:
    """Disable (grey) the combo's rows of already-used records and give each the
    place as its tooltip.

    `index` is the forest's `UsedRefs`; `resolve(kind, name)` (RecordRefs) says
    which record a ROW names, and `index.place` then says whether a node holds
    it — the very pair `used_ref_place` asks for the refusal, so a greyed row is
    EXACTLY a refusal. `kind` is the form's CURRENT kind: a local kind's rows are
    tree-local names, never records, and nothing of theirs is ever greyed.

    `own` (the node being edited) is NEVER marked: a node's own record is not
    "taken from itself", and disabling its row would fight the form pre-filling
    it."""
    if index is None or kind in LOCAL_REF_KINDS:
        return
    own_uuid = getattr(own, "ref_uuid", None)
    own_ref = getattr(own, "ref", None)
    for row in range(combo.count()):
        data = combo.itemData(row)
        if not isinstance(data, tuple) or len(data) < 2 or not data[1]:
            continue
        place = index.place(resolve(data[0], data[1]), data[1],
                            own_uuid=own_uuid, own_name=own_ref)
        if place is None:
            continue
        item = combo.model().item(row)
        if item is not None:
            item.setEnabled(False)
        combo.setItemData(row, used_ref_text(*place), Qt.ItemDataRole.ToolTipRole)


def used_ref_place(index: Optional[UsedRefs], resolve,
                   kind: Optional[str], ref: str, own=None
                   ) -> Optional[tuple[str, str]]:
    """Where the record `ref` of `kind` already stands, or None — the refusal's
    ONE question (`NodeFormWidget.build_node`), asked of the SAME index and the
    SAME resolver that grey the rows.

    A local kind names no record (`kicadstamp.trees.LOCAL_REF_KINDS`): a mount may
    repeat an entity's name, so it is never a repeat of anything. A form without
    an index (a standalone form handed nothing) greys and refuses nothing."""
    if index is None or kind in LOCAL_REF_KINDS:
        return None
    return index.place(resolve(kind, ref), ref,
                       own_uuid=getattr(own, "ref_uuid", None),
                       own_name=getattr(own, "ref", None))


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
