# gui/docks/trees_node_row.py
"""The ONE post-Apply update of a tree node's ROW, the node-copy routine it
shares with the dock, and the Apply-time rules of a node edit.

`NodeFormWidget.apply()` mutates the edited TreeNode IN PLACE. The tree's
QTreeWidget ROW, however, is a separate object registered in
``TreesDock._node_items`` UNDER THE NODE'S REF — so a Ref edit used to leave the
row keyed by the OLD ref, its text stale until the whole GUI was restarted
(Denis, 08.10.2026; plan techdocs/handoff/deepseek/plan/2026_10_08_tree_node_ref_
apply.md). This module carries the fix: after a successful Apply the SAME
QTreeWidgetItem is re-registered under the new ref and repainted through the
render's OWN routine (``TreesDock._refresh_tree_marks``, which calls
``_node_item_text`` / ``_apply_node_marks``) — never a second computation, and
never a full ``_rebuild_tabs()`` (that would tear the open form down).

The SAME session added the second half of that defect: under format 3 a node's
`ref` is a NAME HINT beside an authoritative `ref_uuid`, so a Ref edit had to
move the uuid too (else the writer stamp / the loader's normalization put the OLD
name back) — and a node's Kind may change the SECTION the ref resolves in, or
leave it with no record at all. Both are handled by ONE rule here, reading the
config side's own owners (`form_identity.node_ref_uuid`,
`trees.tree_from_dict`), never a second copy of either:

  * `plan_node_edit` — everything that can REFUSE an edit, checked before a
    single field is touched: the ref_uuid resolution and the loader's own rules
    run over the whole FOREST as it would be (the node replaced, re-hung, the
    tree-level refs cascaded). Returns the refusal text or None;
  * `commit_node_edit` — applies the prepared edit to the real node and cascades
    the tree's pivot-ref / self anchor, returning the ONE Log line for them.

It lives OUTSIDE trees_dock.py on purpose: that file is a giant (7955 lines) and
may only SHRINK. `copy_node_onto` moved here together with the fix, so the dock
loses lines rather than gaining them.
"""
from __future__ import annotations

import copy
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTreeWidgetItem

from kicadstamp.config.form_identity import node_ref_uuid
from kicadstamp.trees_local_refs import NAME_ONLY_KINDS
from kicadstamp.exceptions import ValidationError
from kicadstamp.i18n import _
from kicadstamp.trees import TreeNode, tree_from_dict, tree_to_dict

from ._common import ERROR_STYLE as _ERROR_STYLE, show_message

logger = logging.getLogger(__name__)


def copy_node_onto(target: TreeNode, built: TreeNode) -> None:
    """Copy every editable field of a BUILT node onto an EXISTING node in place
    (mutate, don't swap identity — other structures may hold a reference, e.g.
    _node_items). The single copy routine shared by every node-edit apply path
    (the master-detail Node tab's apply()/redraw() and the dialog forms), so
    they can never drift.

    Moved verbatim from trees_dock._copy_node_onto (2026-10-08): the row update
    below belongs to this copy, so both live together and the giant shrinks."""
    target.ref = built.ref
    # The node's IDENTITY beside its name hint (format 3, §п.3): `ref_uuid` MUST
    # move with `ref`, or the writer stamp / the loader's normalization rule by
    # the (stale) uuid and put the OLD name back — the rename would be silently
    # rolled back. `plan_node_edit` has already resolved it (or cleared it for a
    # kind that references no record).
    target.ref_uuid = built.ref_uuid
    target.kind = built.kind
    target.xy = built.xy
    target.polar = built.polar
    target.rotation = built.rotation
    target.name = built.name
    target.group = built.group
    # NOTE (2026-09-11, plan_2026_09_11_tree_inner_point_and_rotation §V.3): the
    # pivot_xy/pivot_polar copy that used to sit here is GONE — a node carries no
    # pivot any more (the inner point belongs to the TREE). The tree-level
    # editor arrives in stage Б2.1.
    target.anchor = built.anchor


def is_handle_node(pivot_ref, node: TreeNode) -> bool:
    """True when `node` is the RECORD a tree's `pivot-ref` names — the row that
    carries the "handle" (inner point) mark. A local NAME may equal that ref since
    16.11.2026 (plan_2026_11_14_mount_and_record_may_share_a_name §1), and the
    mark on a mount would point at a node the layout can never use."""
    return (pivot_ref is not None and node.ref == pivot_ref
            and node.kind not in NAME_ONLY_KINDS)


def node_path(tree, node: TreeNode):
    """Where `node` sits in `tree`: child INDICES from the root, or None when it
    is not there. THE identity a `deepcopy` preserves and a NAME no longer is —
    a mount and a record may share one (plan_2026_11_14_mount_and_record_may_
    share_a_name §1: a probe built by name put a child's edit onto the mount)."""
    def walk(nodes, prefix: tuple) -> Optional[tuple]:
        for index, current in enumerate(nodes or ()):
            here = prefix + (index,)
            if current is node:
                return here
            hit = walk(getattr(current, "children", None), here)
            if hit is not None:
                return hit
        return None

    return walk(getattr(tree, "nodes", None), ())


def node_at_path(tree, path: Optional[tuple]) -> Optional[TreeNode]:
    """The node at `node_path`'s indices, or None when the path no longer fits
    (a copy with a different shape, a tree that changed)."""
    if path is None:
        return None
    nodes = getattr(tree, "nodes", None)
    node = None
    for index in path:
        if not nodes or index >= len(nodes):
            return None
        node = nodes[index]
        nodes = getattr(node, "children", None)
    return node


def _node_by_ref(tree, ref: str):
    """The node of `tree` whose ref is `ref`, or None. Refs are unique within a
    tree (the loader's rule 2 makes them unique across the whole file), so this
    is unambiguous — and it is how a COPY's counterpart of a live node is found
    (a deepcopy shares no identity with the original)."""
    stack = list(tree.nodes)
    while stack:
        node = stack.pop()
        if node.ref == ref:
            return node
        stack.extend(node.children)
    return None


def cascade_tree_refs(tree, old_ref: str, new_ref: str) -> list[str]:
    """Point the tree's OWN references to a renamed NODE at its new ref.

    `pivot_ref` and `anchor.self_ref` name a NODE of this tree — and the node is
    the SAME one after a rename, so both must follow it (plan §п.3.2, the
    "Решение по п.3"): refusing here would punish an ordinary rename. Returns the
    parts that followed ("pivot" and/or "self", in that order) — the ONE Log line
    the Apply reports is built from it.

    `_stale_net_traces` is deliberately NOT touched: "no copper" is a property of
    the RECORD the node used to name, not of the node."""
    followed: list[str] = []
    if tree is None:
        return followed          # a standalone form: no tree to cascade into
    if tree.pivot_ref is not None and tree.pivot_ref == old_ref:
        tree.pivot_ref = new_ref
        followed.append("pivot")
    anchor = tree.anchor
    if anchor is not None and anchor.self_ref == old_ref:
        anchor.self_ref = new_ref
        followed.append("self")
    return followed


def _probe_tree(dock, tree, node: TreeNode, built: TreeNode,
                selected_parent, current_parent):
    """A COPY of `tree` as it would be after the edit: the edited node replaced
    by `built`, re-hung under `selected_parent` (None = top level) when that
    differs from its current parent, and the tree-level refs cascaded.

    Structural only — the dock, the real tree and the real node are untouched, so
    the loader's rules can be run on the OUTCOME before anything is committed.
    The re-hang goes through the dock's own removal point (`_detach_node`, the
    one place a node leaves a sibling list), never a second copy of it."""
    probe = copy.deepcopy(tree)
    # ПО ПУТИ, not by name: a mount and a record may carry one name in this tree
    # (§1), and `_node_by_ref` would land the edit on whichever came first.
    probe_node = node_at_path(probe, node_path(tree, node))
    if probe_node is None:
        return None                  # the node is not in this tree: nothing to probe
    copy_node_onto(probe_node, built)
    if selected_parent is not current_parent:
        # The NEW parent is resolved on the copy BEFORE anything leaves it: a
        # node's departure shifts the indices of every later sibling.
        target = (None if selected_parent is None
                  else node_at_path(probe, node_path(tree, selected_parent)))
        dock._detach_node(probe, probe_node)
        if selected_parent is None:
            probe.nodes.append(probe_node)
        elif target is not None:
            target.children.append(probe_node)
        else:
            return None              # the chosen parent is not in this tree
    cascade_tree_refs(probe, node.ref, built.ref)
    return probe


def plan_node_edit(dock, tree, node: TreeNode, built: TreeNode, *,
                   cfg, selected_parent, current_parent):
    """Everything that can REFUSE the edit, before a single field is touched.

    Returns the refusal TEXT (a red line for the form and the Log) or None when
    the edit may be committed. Two checks, both through the owners of the rules
    themselves — never a second copy:

    1. `ref_uuid` (`form_identity.node_ref_uuid`): a kind WITH a record section
       takes the uuid of the record the NEW ref names, and a ref naming no record
       refuses the Apply — under format 3 the writer stamp would otherwise accept
       it and the next load would roll the name back. Any other kind carries no
       uuid at all, and `built` simply has none (a STALE one must not survive:
       the loader fatals on a `ref_uuid` beside a local kind). `cfg is None` (no
       loaded graph) leaves the uuid unset, which is the safe answer: the writer
       resolves the reference by name, or refuses at write time.
    2. the LOADER's own rules (`trees.tree_from_dict`) over the WHOLE FOREST —
       every tree of the dock, the edited one replaced by the probe and ONE
       SHARED `seen_refs` across the walk (a ref already carried by a node of
       ANOTHER tree is only visible with a shared set — the ref combo offers
       free-typed text, so "(used)" is a hint, not a guard). A tree that the
       loader would refuse is a config the next open cannot read, whatever the
       form shows — so the Apply refuses with the loader's own text.

    One declared gap: a tree that ALREADY failed the loader's rules is refused
    here too, even for an unrelated edit. In the running app that cannot happen
    (the dock's trees come from `load_config`); a tree built by the create-tree
    path may, and then Apply refuses until the tree is fixed — which is the
    honest outcome, never a silent write of an unreadable config."""
    if cfg is not None:
        try:
            built.ref_uuid = node_ref_uuid(cfg, built.kind, built.ref)
        except ValidationError as exc:
            return _refusal_text(exc, node)
    forest = getattr(dock, "_trees", None)
    if not forest:
        # A standalone form (no dock, no forest): nothing to check against.
        return None
    seen: set[str] = set()
    try:
        for candidate in list(forest):
            if candidate is tree:
                probe = _probe_tree(dock, candidate, node, built,
                                    selected_parent, current_parent)
                if probe is None:
                    # The node (or the chosen parent) is not in this tree — the
                    # create-tree flow, never an edit of a real node. Nothing to
                    # probe; the OTHER trees are still checked.
                    continue
                data = tree_to_dict(probe)
            else:
                data = tree_to_dict(candidate)
            tree_from_dict(data, seen)
    except ValidationError as exc:
        return _refusal_text(exc, node)
    return None


def _refusal_text(exc, node: TreeNode) -> str:
    """The ONE wording of every Apply refusal: the rule's own message plus the
    fact the user needs — the node (and the tree) were left as they were."""
    return _("{error} — the node keeps {ref!r}").format(error=exc, ref=node.ref)


def commit_node_edit(dock, tree, node: TreeNode, built: TreeNode):
    """Apply the prepared edit to the REAL node and cascade the tree-level refs.

    Returns the ONE Log line when the tree's pivot-ref / self anchor followed the
    rename, else None. Call `plan_node_edit` first: this touches the node and the
    tree, and it is the caller's job to run it only after that returned None."""
    old_ref = node.ref
    copy_node_onto(node, built)
    followed = cascade_tree_refs(tree, old_ref, built.ref)
    if len(followed) == 2:
        note = _("{old!r} → {new!r}: the tree's pivot ref and self anchor "
                 "follow.")
    elif followed == ["pivot"]:
        note = _("{old!r} → {new!r}: the tree's pivot ref follows.")
    elif followed:
        note = _("{old!r} → {new!r}: the tree's self anchor follows.")
    else:
        return None
    return note.format(old=old_ref, new=built.ref)


def report_apply_refusal(form, text: str) -> None:
    """Surface an Apply refusal the ONE way: the red line under the form and the
    same line in the Log. Never a modal (plan 2026-09-11 X.1)."""
    form.apply_status_label.setText(text)
    show_message(text, _ERROR_STYLE, logger)


def apply_node_form(form) -> bool:
    """The BODY of ``NodeFormWidget.apply()`` (Phase B, plan §1.3): validate the
    form and write it onto the edited node in place — explicit, does NOT close
    anything (the caller owns the button row; edits are explicit actions and the
    config reaches disk only through the caller's Save). Returns True when
    applied; resets ``_touched`` (design §9.4) on success.

    It lives here, not in the dock's giant, because the ORDER is the whole point:

      1. `build_node()` — the form's own validation;
      2. `plan_node_edit` — everything the next config LOAD (and the writer stamp)
         would refuse, checked BEFORE the node or the tree is touched: the
         `ref_uuid` the NEW ref names (against the DOCK's current graph — see the
         note at the call), and the loader's own rules over the whole forest as it
         would be, the edited tree INCLUDING the parent the form has selected. A
         refusal is a red line under the form and in the Log, and the node keeps
         its ref (this is the order that makes "nothing is touched" true —
         `_apply_parent_change` re-hangs the node in place and must therefore run
         only AFTER the probe passed);
      3. `_apply_parent_change()` — the Parent combo's re-hang (Э1);
      4. `commit_node_edit` — the copy plus the tree-level cascade (one Log line
         when the tree's pivot-ref / self anchor followed);
      5. `_mark_dirty()` + `refresh_node_row` — the row of the node, in place;
      6. the form's `_used_refs` set is refreshed from the dock: it was captured
         when the form OPENED, and this edit just freed one ref and took another
         — the uniqueness check (and the "(used)" hints) must not judge by the
         old set."""
    if form._existing is None:
        return False
    built = form.build_node()
    if built is None:
        return False  # build_node already warned
    # The graph to resolve against is the DOCK's CURRENT one, not the snapshot the
    # form took at construction: the dock swaps `_cfg` on every `graph_changed`
    # (a Save elsewhere in the app, a Create entity/cell of this very session), and
    # a form opened before that would otherwise refuse a record that exists — the
    # false "names no existing … record" refusal Denis hit (09.10).
    cfg = (getattr(form._dock, "_cfg", None) if form._dock is not None
           else form._cfg)
    refusal = plan_node_edit(
        form._dock, form._tree, form._existing, built, cfg=cfg,
        selected_parent=form._selected_parent_node(),
        current_parent=form._parent_node)
    if refusal is not None:
        report_apply_refusal(form, refusal)
        return False
    if not form._apply_parent_change():
        return False
    old_ref = form._existing.ref
    cascade_note = commit_node_edit(form._dock, form._tree, form._existing, built)
    if form._dock is not None:
        if cascade_note is not None:
            logger.info(cascade_note)
        form._dock._mark_dirty()
        # The row is a SEPARATE object keyed by the node's ref — move its
        # registry key to the new ref and repaint it through the render's own
        # routine (no _rebuild_tabs: it would tear this form down).
        refresh_node_row(form._dock, form._tree, form._existing, old_ref)
        # This form's "used refs" set was captured when it OPENED: the edit just
        # made one ref free and another taken, so the uniqueness check (and the
        # "(used)" hints) must not keep judging by the old set.
        form._used_refs = form._dock._used_refs()
        # Ч3: the PLACES moved with the refs — the greyed rows' hints must not
        # keep naming the old spot either (2026-11-14: the whole INDEX, which is
        # keyed by the record's uuid, not by its ref).
        form._used_ref_index = form._dock._used_ref_index()
    form._touched = False
    form.apply_status_label.setText(_("Applied — keep editing or Redraw."))
    return True


# ── the row registry: ONE row per NODE ────────────────────────────────────────
#
# `dock._node_items` maps `id(node)` to `(node, item)` — see plan_2026_11_14_
# mount_and_record_may_share_a_name Ч1. It used to map the node's REF and span the
# whole dock, so two trees carrying one name (a mount in t1, a record node in t2 —
# legal since that plan) overwrote each other and one row became unreachable.
# The VALUE keeps the node itself: an `id` is reused after a buffer rebuild, so a
# stale key must answer None instead of handing back somebody else's row.
# `TreeNode` cannot be the key — it is a mutable dataclass, therefore unhashable.


def row_of(dock, node: TreeNode) -> Optional[QTreeWidgetItem]:
    """The rendered row of THIS node, or None (no rebuilt/stale row matches)."""
    entry = (getattr(dock, "_node_items", None) or {}).get(id(node))
    if entry is None or entry[0] is not node:
        return None
    return entry[1]


def row_for_record(dock, ref: str) -> Optional[QTreeWidgetItem]:
    """The row of the node that PLACES the record `ref` — record kinds only.

    "The row of that name" is ambiguous BY CONSTRUCTION now: a local NAME may be
    a record's name too (`mount` since 16.11.2026). A caller that means the
    RECORD — the identified-copper highlight — must skip the name-only kinds, and
    this is that one walk."""
    if not ref:
        return None
    for node, item in (getattr(dock, "_node_items", None) or {}).values():
        if node.ref == ref and node.kind not in NAME_ONLY_KINDS:
            return item
    return None


def checked_record_refs(dock) -> set[str]:
    """`selected_refs` of "Redraw selected": the RECORDS of every CHECKED row.

    A ticked node hands in its whole subtree's records (`subtree_refs`) and never
    its own local name: a mount/copper/component places nothing, and since its
    name may equal a record's (16.11.2026) the name alone would emit somebody
    else's record. The tick no longer depends on which row the registry happened
    to keep — every row of every tree is its own entry."""
    from .trees_accept import subtree_refs

    refs: set[str] = set()
    for node, item in (getattr(dock, "_node_items", None) or {}).values():
        if item.checkState(0) == Qt.CheckState.Checked:
            refs.update(subtree_refs(node))
    return refs


def refresh_node_row(dock, tree, node: TreeNode, old_ref: str) -> None:
    """Repaint ONE row after an in-place node edit.

    `node` is the edited TreeNode (already carrying its NEW ref); `old_ref` is its
    ref BEFORE the edit. NOTHING is re-keyed any more: the registry is keyed by the
    NODE's identity and the edit mutates the node IN PLACE (design §9.4), so the
    row is found by the very object that changed — the old `pop(old_ref)`/
    `[node.ref] =` move went with the name key. The row is then repainted by
    `dock._refresh_tree_marks(tree)`, the render's OWN routine. The dock's
    master-detail identity (`_current_node_ref`) follows the node, so re-selecting
    the row keeps the SAME (still open) form instead of tearing it down.

    A no-op when the dock carries no registry at all — a headless or standalone
    form with no built tree widget (the modal dialog's Edit mode in a test, a
    create-tree form), where there is no row to update."""
    if getattr(dock, "_node_items", None) is None:
        return
    if tree is not None:
        # The render's own text/marks routine — the row is repainted from the
        # node the form just committed, never by a second calculation here.
        refresh = getattr(dock, "_refresh_tree_marks", None)
        if refresh is not None:
            refresh(tree)
    if getattr(dock, "_current_node_ref", None) == old_ref:
        dock._current_node_ref = node.ref
