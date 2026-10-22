# gui/docks/trees_delete.py
"""The DELETE flow of the trees dock: the whole SELECTION, its `net_traces:`
records, and the Delete key.

Why this module exists: `gui/docks/trees_dock.py` is a giant (rule 45) and may
only shrink. The dock keeps the wiring — one line per call, one line per signal
— and this module keeps the bodies, the same split `trees_selection.py`,
`trees_move_flow.py` and `trees_node_row.py` already use.

WHAT THE DELETION IS (Denis, 22.10.2026, plan
`plan_2026_10_22_trees_group_delete_and_no_apply.md`):

  * Delete (the key, with the tree focused) and the node menu's "Delete node" /
    "Delete N nodes" act on the WHOLE selection; a single node is the same path
    with N = 1. Until 22.10 the menu deleted the ONE node it was opened on even
    with a group selected, and the key did nothing at all.
  * A copper node (`kind "net_trace"`) leaves WITH its `net_traces:` record —
    but only when no node of ANY tree in the dock still references that record.
    The template/instance relation does NOT hold the record: an instance's
    copper node is materialized under its OWN identity (`<name>__{instance}`,
    `kicadstamp/config/tree_instances.py`) with its own generated record, and
    those generated records never live in a file (only the template's does), so
    there is nothing to unlink for them. The count is the same one
    `_on_delete_tree` already makes (`deleted_nets - remaining_nets`).
  * The BOARD is never touched: removing copper from the board is a separate
    command, not this one.
  * No confirmation dialog — the same precedent as the old "Delete node"
    (undo is "don't Save"; the config reaches disk only through the working
    set). Refusals are Log/status lines, never modals (rule 43).
  * A generated `tree_instances` tree stays READ-ONLY: nothing is deleted.
  * Only the subtree ROOTS of the selection are removed: a selected child of a
    selected parent rides with its ancestor and is not "deleted twice"
    (`group_roots`, one expression of that rule, already used by the group
    "Move to…").

ORDER MATTERS and is deliberate: the nodes leave the tree FIRST, the records
SECOND. "A record with no node" is a legal config; the reverse ("a node with no
record") is a load-time fatal, and the two staged writes may be flushed in
either order.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from PyQt6.QtCore import QEvent, QObject, Qt

from kicadstamp.i18n import _
from kicadstamp.trees import Tree, TreeNode

from .trees_move_flow import group_roots

logger = logging.getLogger(__name__)

#: What a record-less half of the Log line shows — a visible "none", never an
#: empty gap that reads as a formatting bug.
_NONE = "—"


# ── the pure half: what a deletion removes ───────────────────────────────────

def net_trace_identities(nodes: Sequence[TreeNode]) -> list[str]:
    """The `net_traces:` identities the deletion of `nodes` takes with it, in
    document order, deduplicated.

    `nodes` are the subtree ROOTS being removed (`group_roots`'s answer), so
    every copper node of a removed subtree is collected: deleting a `copper`
    container takes its children with it, and their records must go too
    (Denis, 22.10 — a direct consequence of "a copper node leaves with its
    record").

    A node's ref IS the record identity (`name:`, else a legacy record's
    `net:`) — the same string `link_trees` resolves the node through, so no
    second formula is spelled here. The walk is `trees._walk_nodes`, the
    project's own depth-first reader (not a second copy of it)."""
    from kicadstamp.trees import _walk_nodes

    out: list[str] = []
    for root in nodes:
        for node in _walk_nodes([root]):
            if node.kind == "net_trace" and node.ref and node.ref not in out:
                out.append(node.ref)
    return out


def referenced_identities(trees: Sequence[Tree]) -> set[str]:
    """Every `net_traces:` identity ANY of `trees` still references — the
    "does somebody else still use this record?" side of the rule.

    Asked over EVERY tree the dock holds, including the generated
    `tree_instances` ones (`dock._trees`): a generated tree's copper node
    references its OWN generated record, so it cannot accidentally hold a
    template's record — but the question is asked the same way for all of
    them, exactly like `_on_delete_tree` does."""
    from kicadstamp.internode_capture import tree_net_trace_identities

    out: set[str] = set()
    for tree in trees or ():
        out.update(tree_net_trace_identities(tree))
    return out


def delete_action_text(dock, tree: Tree, node: TreeNode) -> str:
    """The node menu's own label: "Delete node" for one row, "Delete N nodes"
    when the selection is a group (the menu acts on the WHOLE selection, so the
    label must say how many rows that is)."""
    selection = dock._selected_real_nodes(tree)
    count = len(selection) if selection else 1
    if count <= 1:
        return _("Delete node")
    return _("Delete {n} nodes").format(n=count)


# ── the record half: `net_traces:` entries ───────────────────────────────────

def remove_net_trace_records(root_path, identities: Sequence[str]
                             ) -> tuple[list[str], list[str]]:
    """Remove each `net_traces:` record by IDENTITY from the file that OWNS it
    and return `(removed, not_found)`.

    `net_traces:` can live in an include, so the owning file is found with
    `rename.find_list_entry_file` (the same "write an edit back to its own
    file" rule `_stage_net_traces` — this record's WRITE path — already
    follows, and the same identity formula `entry_effective_name`, so the match
    is unambiguous by construction). The write goes through `_common.write_data`
    → `config_writer._write_data`, i.e. into the GUI's working set: nothing
    reaches disk before File > Save.

    `not_found` is a real answer, not an error: a record that no file carries
    (a generated instance's copy, or a config edited underneath us) is named in
    the Log and left alone — never invented, never written."""
    if root_path is None or not identities:
        return [], list(identities or ())

    from ._common import read_data, write_data
    from .rename import entry_effective_name, find_list_entry_file

    removed: list[str] = []
    not_found: list[str] = []
    for identity in identities:
        target = find_list_entry_file(root_path, "net_traces", {"name": identity})
        if target is None:
            not_found.append(identity)
            continue
        data = read_data(target)
        entries = data.get("net_traces") or []
        kept = [entry for entry in entries
                if not (isinstance(entry, dict)
                        and entry_effective_name("net_traces", entry) == identity)]
        if len(kept) == len(entries):
            not_found.append(identity)
            continue
        data["net_traces"] = kept
        write_data(target, data)
        removed.append(identity)
    return removed, not_found


# ── the flow body ────────────────────────────────────────────────────────────

def run_delete_nodes(dock, tree: Tree, clicked: Optional[TreeNode] = None) -> int:
    """Delete the CURRENT selection of `tree` (or `clicked` when nothing is
    selected) and return how many nodes left the tree.

    `clicked` is None for the Delete KEY, which has no row of its own: the
    selection IS the instruction. The menu passes the node it was opened on, and
    the right-click handler has already collapsed a group selection onto that
    row when the click was outside it — so both entries mean the same thing.

    Refusals (a generated instance tree, a single pseudo-row such as the
    "Copper" fold, an empty selection) say so in the Log and the status row and
    change nothing; there is no dialog anywhere on this path."""
    if dock._instance_of(tree) is not None:
        _report(dock, _("A generated instance tree is read-only — nothing was "
                        "deleted."))
        return 0
    selection = dock._selected_real_nodes(tree)
    nodes = list(selection) if selection else (
        [clicked] if isinstance(clicked, TreeNode) else [])
    if not nodes:
        if _only_pseudo_rows_selected(dock, tree):
            _report(dock, _("Nothing to delete: the selected row is not a tree "
                            "node."))
        return 0
    roots = group_roots(tree, nodes)
    if not roots:
        return 0

    identities = net_trace_identities(roots)
    for node in roots:
        # THE one removal point of every structural edit (identity-based) — the
        # deletion used to call list.remove() itself, i.e. a second routine for
        # the same job.
        dock._detach_node(tree, node)
    # Stage the tree BEFORE the records: a record with no node is a legal
    # config at every intermediate step, a node with no record is a fatal.
    dock._mark_dirty()

    remaining = referenced_identities(getattr(dock, "_trees", ()))
    orphaned = [identity for identity in identities if identity not in remaining]
    kept = [identity for identity in identities if identity in remaining]
    removed, not_found = remove_net_trace_records(
        getattr(dock, "_root_path", None), orphaned)

    # The "no copper on the board" mark is a property of the RECORD; the record
    # is gone, so a mark naming it must not survive the deletion.
    stale = getattr(dock, "_stale_net_traces", None)
    if stale is not None:
        stale.difference_update(identities)

    dock._rebuild_tabs()

    logger.info(_("Deleted {n} nodes: {refs}; net_traces records removed: "
                  "{records}").format(
                      n=len(roots), refs=", ".join(node.ref for node in roots),
                      records=", ".join(removed) or _NONE))
    if kept:
        logger.info(_("net_traces records kept (still referenced by another "
                      "node): {records}").format(records=", ".join(kept)))
    if not_found:
        logger.warning(_("net_traces records not found in the config files "
                         "(left in place): {records}").format(
                             records=", ".join(not_found)))
    return len(roots)


def _only_pseudo_rows_selected(dock, tree: Tree) -> bool:
    """True when the selection holds rows but NO real node — the "Copper"
    pseudo-row alone, or a navigation pseudo-row. Asked so the KEY can say why
    it did nothing; in a MIXED selection the pseudo-row is silently skipped and
    the real nodes are deleted (Denis, 22.10), which is what
    `_selected_real_nodes` already gives us."""
    tree_widget = dock._tree_widget_for(tree)
    if tree_widget is None:
        return False
    return bool(tree_widget.selectedItems()) and not dock._selected_real_nodes(tree)


def _report(dock, text: str) -> None:
    """Name a refusal twice: the Log (the durable channel) and the status row
    (the one in front of the user). Never a modal (rule 43)."""
    logger.info(text)
    show = getattr(dock, "_show_status", None)
    if show is not None:
        show(text)


# ── the Delete key ───────────────────────────────────────────────────────────

class DeleteKeyFilter(QObject):
    """`Delete` pressed while a tree widget has focus → the same deletion the
    menu runs.

    A FILTER on the tree WIDGET, deliberately not a dock-wide `QShortcut`: with
    `WidgetWithChildrenShortcut` a Delete typed in a node form's `QLineEdit`
    would delete rows instead of characters, and the panel's fields are part of
    the same dock. Installed per tree widget by the dock (`_rebuild_tabs`), so
    every tab gets its own filter naming its own tree.

    Modifiers are checked: plain Delete (and the keypad's own) is the deletion,
    Ctrl/Shift/Alt+Delete stay available for anything else later.

    OWNED BY THE WIDGET (`parent=widget`): `installEventFilter` never takes
    ownership, and a filter whose last reference died would silently stop
    working — being a child of the tree widget ties its lifetime to the widget's,
    which `_rebuild_tabs` replaces on every render."""

    def __init__(self, dock, tree: Tree, parent=None) -> None:
        super().__init__(parent if parent is not None else dock)
        self._dock = dock
        self._tree = tree

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 — Qt API name
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Delete
                and event.modifiers() in (Qt.KeyboardModifier.NoModifier,
                                          Qt.KeyboardModifier.KeypadModifier)):
            run_delete_nodes(self._dock, self._tree, clicked=None)
            return True
        return False
