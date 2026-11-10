# gui/docks/trees_accept.py
"""Accepting an edited node/anchor FORM — on the one Apply button and on every
way out of it (plan_2026_10_23_node_form_without_apply.md, Ч1).

WHY THIS MODULE EXISTS: the master-detail forms (`NodeFormWidget`,
`AnchorFormWidget`) own `apply()`, but the DECISION "there is an edit in
flight — accept it before this action" is asked by seven different doors (the
Apply button, a selection change, a panel reload, a tab rebuild, the dock's
close, Save, the window's exit). One body here is what keeps those doors from
drifting apart, and `gui/docks/trees_dock.py` is a giant (rule 45) so the
bodies live here rather than in it.

TWO POLICIES, and the difference is the whole point:

* ``REFUSE`` — the action that would LEAVE the form is cancelled when the edit
  does not validate: the selection goes BACK to the node the form edits (by
  ref), the panel keeps the draft, and a red Log line names the node and the
  reason. Used by: a selection change, Save.
* ``PROCEED`` — the action goes on regardless; a failed accept is reported the
  same way (red line) and the draft is lost. Used by: a panel reload, a tab
  rebuild, the dock's close, the window's exit — the alternative would be an
  action the user asked for that silently does not happen.

The SIGNAL of "changed" is `form._touched` (design §9.4) and nothing else: it
is set by `_mark_touched` from every user-facing field and cleared by a
successful `apply()` and by the form's own prefill, so a mere walk through the
nodes never marks the config dirty.

Discard/Revert is the ONE place that does NOT accept: the user said "throw the
unsaved changes away", so the draft goes with the working set and one info line
names it (`discarding`).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItemIterator

from kicadstamp.i18n import _
from kicadstamp.trees import TreeNode

from ..connection import ui_thread_board_read, worker_timeout_ms
from ..worker import start_long_op
from ._common import ERROR_STYLE, show_message
from .cascade import (run_curated_forest_redraw_worker,
                      run_curated_tree_redraw_worker,
                      run_single_node_redraw_worker)
from .trees_embedding import refuse_embedded_tree
from .trees_selection import select_only

logger = logging.getLogger(__name__)

__all__ = [
    "PROCEED",
    "REFUSE",
    "SUBTREE_KINDS",
    "accept_form",
    "accept_forms",
    "accept_before_switch",
    "apply_and_redraw",
    "discarding",
    "redraw_form",
    "redraw_node",
    "redraw_subtree",
    "subtree_refs",
    "touched_names",
]

PROCEED = "proceed"
REFUSE = "refuse"

#: Kinds that name NO record of their own: their edit shows on their CHILDREN
#: only, so their redraw is the curated redraw of their own subtree.
SUBTREE_KINDS = frozenset({"mount", "copper", "point", "external"})

#: Kinds with neither a record nor (possibly) children — an Apply on one of
#: these only accepts, and says so.
_CHILDLESS_KINDS = frozenset({"point", "external"})


# ── what the form is, in words ────────────────────────────────────────────

def form_name(form) -> str:
    """The name a Log line can use for `form`: the node's ref (the NEW one —
    a rename is accepted under the name the user typed), else the tree's name
    for the anchor form, else a generic word. Never raises on a stub form."""
    node = getattr(form, "_existing", None)
    ref = getattr(node, "ref", None)
    if ref:
        return str(ref)
    tree = getattr(form, "_tree", None)
    name = getattr(tree, "name", None)
    if name:
        return str(name)
    return _("the open form")


def refusal_line(form) -> str:
    """The red Log line of a REFUSED accept: the node and the form's own reason.

    The reason is `apply_status_label` — `apply()` already writes the exact
    message there ("Ref 'X' is already used", the bad offset, …), so this
    module never invents a second wording of the same rule."""
    label = getattr(form, "apply_status_label", None)
    reason = label.text().strip() if label is not None else ""
    if not reason:
        reason = _("the form did not validate")
    return _("Node {ref}: the edit was not applied ({reason}) — nothing was "
             "written and the form is unchanged.").format(
                 ref=form_name(form), reason=reason)


def is_touched(form) -> bool:
    """True when `form` carries USER edits that have not been accepted."""
    return bool(form is not None and getattr(form, "_touched", False))


# ── the ONE acceptance body ───────────────────────────────────────────────

def accept_form(dock, form, *, policy: str = PROCEED) -> bool:
    """Accept `form` IF it is touched. True = the caller may go on.

    * not touched → True, nothing is written (a walk through the nodes must
      not dirty the config);
    * touched and `apply()` succeeds → True, `_touched` is cleared by apply();
    * touched and `apply()` fails → the red Log line + the status line, and
      the ANSWER is the policy: REFUSE → False (the caller cancels the leaving
      action), PROCEED → True (the caller goes on, the draft is lost);
    * inside `discarding(...)` → True and silent (the caller is throwing the
      whole working set away and has already said so in one line).
    """
    if form is None:
        return True
    if getattr(dock, "_discarding_working_set", False):
        return True
    if not is_touched(form):
        return True
    if form.apply():
        return True
    line = refusal_line(form)
    logger.error(line)
    if dock is not None:
        dock._show_status(line)
    return policy == PROCEED


def panel_forms(dock) -> list:
    """EVERY form currently alive on the dock's pages, active page first?

    No — page order, because a rebuild destroys all of them and the ORDER of
    acceptance must not depend on which tab happens to be in front. The lazy
    panel only ever hosts one form per page, so this is at most
    `tree_tabs.count()` widgets."""
    forms = []
    for index in range(dock.tree_tabs.count()):
        panel = dock._form_panel_of_page(dock.tree_tabs.widget(index))
        if panel is None:
            continue
        form = dock._embedded_form_of(dock._panel_page(panel))
        if form is not None:
            forms.append(form)
    return forms


def accept_forms(dock, *, policy: str = PROCEED) -> list[str]:
    """Accept every live form; returns the names of the forms that REFUSED.

    The caller decides what a refusal means — Save cancels (REFUSE, non-empty
    answer = say no), a rebuild goes on (PROCEED, the line is already in the
    Log)."""
    refused: list[str] = []
    for form in panel_forms(dock):
        if not accept_form(dock, form, policy=policy):
            refused.append(form_name(form))
    return refused


def touched_names(dock) -> list[str]:
    """Names of the forms that currently hold an unapplied edit — what the
    Discard flow names in its one info line BEFORE it lets them go."""
    return [form_name(form) for form in panel_forms(dock) if is_touched(form)]


@contextmanager
def discarding(dock):
    """Discard/Revert the working set: the open form is NOT accepted.

    The flag is read by `accept_form`, so the rebuild the discard triggers
    cannot quietly accept a draft the user just asked to throw away. A `None`
    dock (a window with no Trees dock) is a no-op."""
    if dock is None:
        yield
        return
    previous = getattr(dock, "_discarding_working_set", False)
    dock._discarding_working_set = True
    try:
        yield
    finally:
        dock._discarding_working_set = previous


# ── the selection change: REFUSE puts the selection back ─────────────────

def _item_of_form(dock, form):
    """The QTreeWidgetItem of the NODE the form edits, on the ACTIVE page.

    Two answers, in this order: the item whose UserRole data IS the node
    object (identity — survives a rename, because the row was moved, not
    rebuilt), then the first row whose node has the same ref. None when the
    node is gone (a structural edit removed it): then there is nothing to
    restore and the caller simply leaves the selection where it is."""
    node = getattr(form, "_existing", None)
    if not isinstance(node, TreeNode):
        return None
    tree = getattr(dock, "_current_tree", lambda: None)()
    widget = dock._tree_widget_for(tree) if tree is not None else None
    if widget is None:
        return None
    by_ref = None
    it = QTreeWidgetItemIterator(widget)
    while it.value() is not None:
        item = it.value()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, TreeNode):
            if data is node:
                return item
            if by_ref is None and data.ref == node.ref:
                by_ref = item
        it += 1
    return by_ref


def restore_selection(dock, form) -> bool:
    """Put the selection BACK on the node `form` edits. True when it moved.

    `_restoring_selection` is raised for the duration of `select_only`: the
    resulting `itemSelectionChanged` is delivered SYNCHRONOUSLY, and
    `on_selection_changed` returns early on that flag — otherwise the panel
    would re-fill for the very node we are leaving and the draft would go
    down with the form we are trying to keep."""
    item = _item_of_form(dock, form)
    if item is None:
        return False
    tree = dock._current_tree()
    widget = dock._tree_widget_for(tree) if tree is not None else None
    dock._restoring_selection = True
    try:
        select_only(widget, item)
    finally:
        dock._restoring_selection = False
    return True


def accept_before_switch(dock, form) -> bool:
    """Selection change: True = swap the panel to the new node, False = REFUSED.

    A refused switch puts the selection back and raises `_switch_refused`, so
    `on_selection_changed` can return before it overwrites the red line with
    the new row's preview."""
    dock._switch_refused = False
    if accept_form(dock, form, policy=REFUSE):
        return True
    restore_selection(dock, form)
    dock._switch_refused = True
    return False


# ── the ONE Apply button ─────────────────────────────────────────────────

def apply_and_redraw(dock, form) -> bool:
    """The single Apply button of all three hosts: accept, then place the edit
    when there is something to place.

    A refused accept does NOTHING else — the button is never disabled for a
    kind, so "can I accept this?" and "can this be redrawn?" stay two separate
    questions."""
    if not accept_form(dock, form, policy=REFUSE):
        return False
    redraw_form(dock, form)
    return True


def _say(form, text: str) -> None:
    label = getattr(form, "apply_status_label", None)
    if label is not None:
        label.setText(text)


def redraw_form(dock, form) -> None:
    """Place the ACCEPTED edit (the caller has already accepted it).

    Three honest answers, never a lie about what happened:
    * no dock / no live board — accepted, "no live board Redraw available";
    * the node has neither a record nor children — "nothing to redraw on this
      node" (a `point`/`external` leaf);
    * otherwise the dock's own dispatch (`_redraw_edited_node`) runs, and the
      anchor form's whole-tree redraw for the tree settings."""
    node = getattr(form, "_existing", None)
    if dock is None:
        _say(form, _("Applied — no live board Redraw available."))
        return
    if not isinstance(node, TreeNode):
        # §3 of plan_2026_11_09_node_redraw_from_live_parent: the dock REFUSES a whole-tree
        # redraw of a tree a `module` node places (it would be laid out from the Grid Origin),
        # so the form must not claim one started — the reason is already in the Log.
        if dock._on_redraw_whole_tree():
            _say(form, _("Applied — whole-tree redraw started."))
        else:
            _say(form, _("Applied — the whole-tree redraw was refused (see the Log)."))
        return
    if node.kind in _CHILDLESS_KINDS and not node.children:
        _say(form, _("Applied — nothing to redraw on this node."))
        return
    # §4.2: «handled» and «started» are different answers — a refusal already put its reason in
    # the Log, so the form must not claim a redraw began.
    if dock._redraw_edited_node(node):
        _say(form, _("Applied — Redraw started."))
    else:
        _say(form, _("Applied — the node's redraw was refused (see the Log)."))


# ── the node's own Redraw ─────────────────────────────────────────────────

def redraw_node(dock, node: TreeNode) -> bool:
    """The node editor dialog's **Redraw** button (Phase B): place the REAL
    record on the live board at its (edited) CONFIG position — ONE
    ApplyPipeline --only run for node.ref over the in-memory cfg/trees (the
    dialog just applied to the same Tree/TreeNode objects), in a background
    worker (start_long_op, never blocks the UI). NOT the rigid curated
    redraw: after an offset edit the component must move to the new offset.
    Inherits the documented ApplyPipeline boundary (design §0) — a node that
    can't be resolved by the pipeline fails exactly as it would on a full
    Apply, this phase does not soften it.

    "module" is special-cased (2026-09-07, Denis: Redraw on a module node
    should activate its content, not just be blocked): node.ref names an
    EMBEDDED TREE, which --only can never resolve, so this routes through
    the SAME forest-content-activation machinery the "Full redraw" menu
    action uses (run_curated_forest_redraw_worker), scoped to just this
    one marker via selected_refs={node.ref} — its content is placed from
    the owning tree's LIVE anchor, nothing else in that tree moves.

    The BASE IS LIVE (plan_2026_11_09_node_redraw_from_live_parent): the worker
    resolves the parent on the BOARD and places the node at `parent + its own
    xy/rotation` — the inverse of "Read current position" — instead of letting the
    pipeline lay the node out from the tree's anchor and the Grid Origin. Two
    refusals live HERE because only the dock knows the trees: a node that is in
    no loaded tree, and the TOP node of a tree that a `module` node embeds (that
    one is placed through its module, never from this tab).

    Returns whether a WORKER WAS STARTED (§4.2). False is the honest answer for
    every refusal and for "nothing to place" — the reason is already in the Log,
    and the form says «refused» instead of «started».

    No guard widget goes to start_long_op (Э2, plan_2026_09_12_busy_
    indicator): the trigger is whichever HOST's "Redraw" button called
    NodeFormWidget.redraw() — the form owns no button of its own (the
    master-detail panel's _form_action_row and _NodeDialog both build the
    button they connect), so from here there is nothing stable to disable."""
    # Ч1 of plan_2026_10_23_node_form_without_apply.md: a mount/copper/
    # point/external node names no record of its own, so its edit shows on
    # its CHILDREN — that redraw is the curated redraw of its own subtree,
    # handled (and started) here; nothing below may run for it.
    handled = redraw_subtree(dock, node)
    if handled is not None:
        return handled
    if node is None or not node.ref or dock._cfg is None or dock._ctx is None:
        return False
    if node.kind == "module":
        payload = {
            "config_path": str(dock._root_path) if dock._root_path else "",
            "cfg": dock._cfg,
            "ctx": dock._ctx,
            "trees": dock._trees,
            "selected_refs": {node.ref},
            "timeout_ms": worker_timeout_ms(dock._main_window.connection),
        }
        dock._active_op = start_long_op(
            dock._main_window.connection, (),
            run_curated_forest_redraw_worker, dock._finish_redraw,
            dock._on_redraw_failed, payload,
            busy_text=_("placing"))
        return True
    # Everything below needs the node's TREE: the frame its xy/rotation are
    # stored against now comes from the board (plan_2026_11_09_node_redraw_from_
    # live_parent), so a node nobody can locate in a tree cannot be placed at
    # all — and guessing the absolute origin is exactly the fallback this plan
    # removes.
    tree = dock._tree_of_node(node)
    if tree is None:
        show_message(
            _("Redraw of node {node!r}: it is in no loaded tree — nothing to "
              "measure it against.").format(node=node.ref), ERROR_STYLE, logger)
        return False
    parent_node = dock._find_parent(tree, node)
    if parent_node is None:
        # A MODULE-placed tree's top node is the module's business: from this tab the base
        # could only be the tree's own anchor, and that is what moved IC1 into the corner.
        if refuse_embedded_tree(dock._trees, tree, node_ref=node.ref):
            return False
    # The dock's OWN live adapter, borrowed the way the node-reread worker
    # borrows it (start_long_op holds the shared socket for the whole call): the
    # base READ still happens on the worker (Э1) — this half only hands it over.
    with ui_thread_board_read(
            reason="hand the shared adapter to the node-redraw worker"):
        adapter = dock._live_adapter()
    payload = {
        "config_path": str(dock._root_path) if dock._root_path else "",
        "cfg": dock._cfg,
        "ctx": dock._ctx,
        "ref": node.ref,
        # The frame the worker resolves LIVE: the parent node (None = this tree's
        # own anchor), the tree it belongs to, and the node's stored numbers.
        "tree": tree,
        "node": node,
        "parent_node": parent_node,
        # A MOUNT node's xy/polar are defined relative to its OWN anchor, not the
        # parent (plan_2026_09_11_tree_mount_nodes) — the same base the Read uses,
        # so a Read followed by a Redraw is a no-op.
        "base_anchor": node.anchor if node.kind == "mount" else None,
        "adapter": adapter,
        # A COPY: no live mapping crosses the thread boundary.
        "sheet_names": dict(dock._ctx.sheet_names if dock._ctx is not None else {}),
        "timeout_ms": worker_timeout_ms(dock._main_window.connection),
    }
    dock._active_op = start_long_op(
        dock._main_window.connection, (),
        run_single_node_redraw_worker, dock._finish_redraw,
        dock._on_redraw_failed, payload,
        busy_text=_("placing"))
    return True


# ── subtree redraw of a node that names no record ─────────────────────────

def subtree_refs(node: TreeNode) -> set[str]:
    """`node.ref` PLUS every descendant's ref — the `selected_refs` of the
    curated redraw of a `mount`/`copper`/`point`/`external` subtree."""
    refs: set[str] = set()

    def walk(current: TreeNode) -> None:
        if current.ref:
            refs.add(current.ref)
        for child in current.children or ():
            walk(child)

    if node is not None:
        walk(node)
    return refs


def _owning_tree(dock, node: TreeNode):
    """The tree whose nodes contain `node` (by identity), or the current one.

    The dialog edits the ACTIVE tree's node, but `_redraw_edited_node` is also
    reached from the context menu — reading the owner off the data instead of
    off "whatever tab is in front" keeps the payload's `tree_name` true. The
    containment itself is the dock's own `_tree_of_node` (one rule, not two)."""
    owner = getattr(dock, "_tree_of_node", None)
    tree = owner(node) if owner is not None else None
    return tree if tree is not None else dock._current_tree()


def redraw_subtree(dock, node: Optional[TreeNode]) -> Optional[bool]:
    """Curated redraw of a `SUBTREE_KINDS` node's own subtree.

    `None` = the caller's dispatch owns this node (it is not a SUBTREE_KINDS node);
    True = handled HERE and a worker WAS STARTED;
    False = handled HERE, nothing started — no children, no owning tree, or a REFUSAL.

    THREE answers because the form says what happened (§4.2 of
    plan_2026_11_09_node_redraw_from_live_parent): "handled" and "started" are different
    things, and a form that claims «Redraw started» on a refusal is a lie.

    A node with NO children starts NOTHING and says nothing: the edit changed a position
    nothing on the board follows, so the honest answer is the caller's status line — not a
    worker that would place its own name (which a `mount`/`copper`/`point`/`external` node
    does not have).

    A node of a tree a `module` PLACES is REFUSED here (§4.1): the curated worker lays the
    subtree out from the tree's own anchor — the Grid Origin. Measured on the live board,
    every `mount` child in `switch_a` drifts by the module's (−32.000, +35.000) mm."""
    if node is None or node.kind not in SUBTREE_KINDS:
        return None
    refs = subtree_refs(node)
    if len(refs) <= 1:
        return False
    tree = _owning_tree(dock, node)
    if tree is None:
        return False
    if refuse_embedded_tree(dock._trees, tree, node_ref=node.ref):
        return False                     # handled here: refused, nothing started
    payload = {
        "config_path": str(dock._root_path) if dock._root_path else "",
        "cfg": dock._cfg,
        "ctx": dock._ctx,
        "trees": dock._trees,
        "tree_name": tree.name,
        "selected_refs": refs,
        "timeout_ms": worker_timeout_ms(dock._main_window.connection),
    }
    dock._active_op = start_long_op(
        dock._main_window.connection, (),
        run_curated_tree_redraw_worker, dock._finish_redraw,
        dock._on_redraw_failed, payload,
        busy_text=_("placing"))
    return True
