# gui/docks/trees_mounts_flow.py
"""Т4/Т4.1 of plan_2026_11_03_add_mounts_table — the READS and the WRITE of the
mounts table, and nothing of its window (that is Т5).

WHAT THIS MODULE ANSWERS, in one place:

  * `resolve_instance` — WHICH instance the table is about (Д2). Given a Ref it
    reads that footprint's Role/Cluster and its sheet (the SAME two owners Д2
    names: the adapter for the field values, `sheet_names.sheet_of_names` for the
    sheet) and then PROVES that the role anchor it is about to write resolves back
    to that very footprint (Т4.1(а)); given a role anchor it resolves through
    `ComponentResolver.resolve_anchor_fp` — the resolver `tree_position.
    mount_node_base` uses, so the pads the table shows are the pads the redraw
    will find. A Ref on a footprint WITHOUT a Role is refused in words, because
    the anchor written to the file is always the role one.
  * `occupied_for_instance` — the instance-aware «уже есть» map (Т2.1(в)) over
    PLAIN TUPLES from `tree_mounts.sitting_anchors`, never over the live Tree.
  * `read_pads` / `run_read_pads_worker` — plain data in, plain data out; the
    adapter is read from the CONNECTION on the worker thread.
  * `start_read_pads` — phase 1 on the WORKER through the door (`start_long_op`,
    the shared kipy socket's only in-flight owner), with `is_connected` and
    `socket_busy` checked first (the same two guards `cell_layers.py` uses).
  * `write_plan` — attaching an approved `MountPlan` exactly where the plan proved
    it (`MountPlan.parent_ref`), through the dock's own staging path.

WHY THE WORKER GETS TUPLES AND NOT THE TREE (Т4.1(б)). `start_long_op` hands a
payload to another thread; the dock keeps living while the read runs (only the
dialog's button is disabled). An object the UI thread may mutate is therefore not
plain data — `tree_mounts.sitting_anchors(tree)` is computed ON THE UI THREAD
(pure tree walking, no board) and the worker only sees tuples it cannot share.

THE ADAPTER IS THE DOCK'S OWN, TAKEN INSIDE THE WORKER (§12 of the Т1 review):
`connection.board.adapter` is read on the worker thread — the door's own rule
(`gui/docks/explode_page.adapter_of`). A FRESH adapter cannot answer the resolve at
all until `refresh_board()` loads the document: `get_selected_items()`, the first
call `resolve_footprint_by_role` makes, dies on it with
`AttributeError: 'NoneType' object has no attribute 'document'` (measured in Т1).

Qt-free at import on purpose: the reading half is plain data in, plain data out, so
its cells need no window. The Qt-touching seams (`start_long_op`, `adapter_of`) are
imported INSIDE the functions that need them.
"""
from __future__ import annotations

import logging
from typing import Optional

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _
from kicadstamp.placement.services.component_resolver import (
    ComponentResolver,
    resolve_footprint_by_ref,
)
from kicadstamp.sheet_names import resolve_sheet_path_names, sheet_of_names
from kicadstamp.tree_mounts import (
    MountPlan,
    pad_number,
    pad_rows,
    sitting_anchors,
)

logger = logging.getLogger(__name__)

__all__ = [
    "occupied_for_instance",
    "read_pads",
    "resolve_instance",
    "run_read_pads_worker",
    "start_read_pads",
    "tree_by_name",
    "write_plan",
]


# ── Д2: which instance ─────────────────────────────────────────────────────

def _resolver(adapter, cfg, sheet_names, snapshot, resolver=None):
    """The resolver this module uses — injected by cells, `ComponentResolver`
    otherwise. THE one place the class is chosen, so the table, the anchors of the
    tree and the redraw can never end up with different resolvers."""
    if resolver is not None:
        return resolver
    return ComponentResolver(adapter, cfg, sheet_names, snapshot=snapshot)


def tree_by_name(dock, name: str):
    """The tree the dock holds NOW under this name, or None — the ONE place a
    window asks the dock for a tree.

    Т5/§23: the mounts window is MODELESS, so it lives longer than one tick — a
    reload between «Read pads» and OK replaces every Tree object. That is why the
    window keeps the NAME and this lookup happens at the moment of use: the write
    then lands in the tree that exists, and never in one the dock has dropped."""
    for tree in getattr(dock, "_trees", ()) or ():
        if getattr(tree, "name", None) == name:
            return tree
    return None


def resolve_instance(adapter, cfg, sheet_names, *, ref: Optional[str] = None,
                     role: Optional[str] = None, cluster: Optional[str] = None,
                     sheet: Optional[str] = None, snapshot=None,
                     resolver=None) -> dict:
    """The instance the table is about: {ref, role, cluster, sheet, footprint}.

    Two ways in (Д2), and they end in the SAME dict:

      * `ref` — a way to FIND the instance: Role/Cluster come from that footprint's
        fields and the sheet from its own hierarchical chain; the ANCHOR WRITTEN to
        the file stays the role one. A footprint without a Role is a refusal in
        words (the grammar has no ref-anchor for a mount, and this задача does not
        change it). A sheet that did not resolve is a refusal too, and so is an
        anchor that lands on ANOTHER footprint — see the block comment below;
      * `role` (+ optional sheet/cluster) — the anchor itself, resolved by
        `ComponentResolver.resolve_anchor_fp`. Its refusals (not found, ambiguous
        «уточни Sheet») are the resolver's own and are re-raised unchanged, so this
        module never invents a second wording for them.

    WHY THE REF PATH RESOLVES ITS OWN ANCHOR (Т4.1(а)). The table shows the pads of
    the footprint found by Ref, while the config receives `(role, sheet, cluster)`
    built from that footprint's fields. Those two are the same footprint only if
    that anchor is unambiguous — and it need not be: two instances of one role and
    cluster on one sheet, or a sheet that did not resolve (`sheet_of_names` →
    None). Without the check the table would happily list pads of IC1 while the
    redraw went looking for IC2 — so the anchor is resolved once, with the SAME
    resolver, and a mismatch is refused in words instead of written."""
    if ref:
        footprint = resolve_footprint_by_ref(adapter, ref, ref)
        found_role = adapter.get_field_value(footprint, ROLE_FIELD_NAME)
        if not found_role:
            raise ValidationError(format_fatal_error(
                _("mounts are addressed by a Role — {ref!r} has none").format(
                    ref=footprint.ref),
                [_("tag the component with a Role (Tools -> tree -> Tag selected) "
                   "or pick another component; a mount anchor is always a role "
                   "anchor, so the grammar has no ref anchor to write")]))
        found_sheet = sheet_of_names(
            resolve_sheet_path_names(footprint, sheet_names or {}))
        if found_sheet is None:
            raise ValidationError(format_fatal_error(
                _("mounts are addressed by a Role — {ref!r} stands on a sheet that "
                  "did not resolve, so its role anchor cannot name it").format(
                    ref=footprint.ref),
                [_("open the profile's schematic (root_sheet / schematic_files) so "
                   "the sheet names resolve, or pick a component whose sheet is "
                   "known; without a sheet the anchor may land on a same-role part "
                   "of another sheet")]))
        found_cluster = adapter.get_field_value(footprint, CLUSTER_FIELD_NAME) or None
        landing = _resolver(adapter, cfg, sheet_names, snapshot, resolver) \
            .resolve_anchor_fp(None, found_role, found_sheet, found_cluster,
                               label=footprint.ref)
        if getattr(landing, "ref", None) != footprint.ref:
            raise ValidationError(format_fatal_error(
                _("{ref!r} is not addressed uniquely by its Role — the anchor the "
                  "mounts would carry (role {role!r}, sheet {sheet!r}, cluster "
                  "{cluster!r}) resolves to {other!r}").format(
                    ref=footprint.ref, role=found_role, sheet=found_sheet,
                    cluster=found_cluster,
                    other=getattr(landing, "ref", "?")),
                [_("narrow the anchor (its Sheet or Cluster), or pick the component "
                   "the redraw actually finds")]))
        return {"footprint": footprint, "ref": footprint.ref, "role": found_role,
                "cluster": found_cluster, "sheet": found_sheet}
    if not role:
        raise ValidationError(_(
            "the mounts table needs an instance: a Ref, or a Role with a sheet "
            "and a cluster"))
    footprint = _resolver(adapter, cfg, sheet_names, snapshot, resolver) \
        .resolve_anchor_fp(None, role, sheet, cluster, label=role)
    return {"footprint": footprint, "ref": footprint.ref, "role": role,
            "cluster": cluster, "sheet": sheet}


# ── Д6: «уже есть» — по ЭКЗЕМПЛЯРУ, не только по написанию якоря ─────────

def occupied_for_instance(adapter, cfg, sheet_names, anchors, *, role: str,
                          instance_ref: str, snapshot=None, resolver=None
                          ) -> dict[str, str]:
    """{pad number: mount ref} for the pads of THIS instance that already carry a
    mount standing on them (Т2.1(в), Т4).

    `anchors` are `tree_mounts.sitting_anchors(tree)` tuples —
    `(ref, role, sheet, cluster, pad)` — so this function reads plain data and the
    UI thread keeps its Tree (Т4.1(б)).

    Why the map exists at all: `tree_mounts.mount_key` is STRUCTURAL on purpose —
    the offline tool has no board — so `(role FPGA)(pad 1)` and
    `(role FPGA)(sheet FPGA)(cluster FPGA)(pad 1)` are two keys while on a board
    with one FPGA both are pad 1 of IC1. Д1а pre-fills Sheet/Cluster, so a NEW
    mount is written in full while Денис's older ones may be shorter; only a
    resolve can tell that they name the same pad.

    Two filters keep the map honest, both of them ОДНО правило из `tree_mounts`:

      * `sitting_anchors` — a mount with an offset or a turn on that pad is a
        different point and does not occupy the row (§16);
      * the mount's anchor must resolve to `instance_ref` — the same footprint the
        table is about. A mount of the SAME role on ANOTHER instance (channel 1 vs
        channel 0) must not grey a row here, and a mount of another role cannot
        name this footprint at all (a footprint has exactly one Role), so those
        are skipped WITHOUT a resolve — that skip is what keeps the cost at "the
        mounts of this tree that share the role".

    An anchor that does not resolve (a stale record, a channel whose sheet no
    longer exists) is SKIPPED, not fatal: this map only greys rows, and the plan's
    own rules still guard the write."""
    resolve_fp = _resolver(adapter, cfg, sheet_names, snapshot, resolver)
    out: dict[str, str] = {}
    for ref, anchor_role, anchor_sheet, anchor_cluster, pad in anchors or ():
        if anchor_role != role:
            continue
        try:
            footprint = resolve_fp.resolve_anchor_fp(
                None, anchor_role, anchor_sheet, anchor_cluster, label=ref)
        except ValidationError:
            continue
        if getattr(footprint, "ref", None) != instance_ref:
            continue
        out.setdefault(pad_number(pad), ref)
    return out


# ── the read itself (worker side) ──────────────────────────────────────────

def read_pads(adapter, cfg, sheet_names, anchors, *, ref=None, role=None,
              cluster=None, sheet=None, snapshot=None, resolver=None) -> dict:
    """Plain data: the instance (without its footprint object) and the table's
    rows + the instance-aware «уже есть» map. Never touches a widget."""
    instance = resolve_instance(adapter, cfg, sheet_names, ref=ref, role=role,
                                cluster=cluster, sheet=sheet, snapshot=snapshot,
                                resolver=resolver)
    rows = pad_rows(adapter.get_footprint_pads(instance["footprint"]))
    occupied = occupied_for_instance(
        adapter, cfg, sheet_names, anchors, role=instance["role"],
        instance_ref=instance["ref"], snapshot=snapshot, resolver=resolver)
    return {"instance": {key: value for key, value in instance.items()
                         if key != "footprint"},
            "rows": rows, "occupied": occupied}


def run_read_pads_worker(payload: dict) -> dict:
    """`start_long_op` worker entry — plain data in, plain data out.

    The CONNECTION travels in the payload and the adapter is read from it HERE, on
    the worker thread (see the module docstring: a fresh adapter would not even
    answer the resolve). `anchors` ride along as TUPLES from
    `sitting_anchors(tree)` — no Tree, no TreeNode crosses threads.

    WHAT THIS COSTS, measured 03.11.2026 (probe_2026_11_03_mount_pads_read,
    HiPiMS-Controller-v099, 175 footprints) and again for Т4:

      * with the dock's warm adapter the whole read is ~1 ms; on a FRESH adapter
        14.7-15.8 ms, almost all of it the first IPC round trip of the resolve;
      * the pads themselves are in memory — 0.8 ms for 145 pads, never per-pad IPC;
      * «уже есть» adds ONE resolve per mount of this tree that shares the role:
        25 such mounts (the v103 FPGA tree) cost 3.1 ms median, a 2-mount tree
        0.4 ms — i.e. ~0.2 ms per mount that actually resolves (the ones that do
        not — roles this board does not carry — are cheaper, ~0.12 ms, because the
        resolver fails fast on an empty candidate list);
      * a REF-addressed read spends ONE more resolve proving its own anchor
        (Т4.1(а)) — the same call, so ~0.2 ms warm; a role-addressed read needs no
        extra one (the anchor IS what it resolved)."""
    from .explode_page import adapter_of

    adapter = adapter_of(payload["connection"])
    if adapter is None:
        raise ValidationError(_(
            "the mounts table cannot be read: there is no board connection"))
    return read_pads(adapter, payload["cfg"], payload["sheet_names"],
                     payload["anchors"], ref=payload.get("ref"),
                     role=payload.get("role"), cluster=payload.get("cluster"),
                     sheet=payload.get("sheet"),
                     snapshot=payload.get("snapshot"),
                     resolver=payload.get("resolver"))


def start_read_pads(connection, *, cfg, sheet_names, tree, ref=None, role=None,
                    cluster=None, sheet=None, snapshot=None, widgets=(),
                    on_ready, on_error=None):
    """Phase 1: the read on the WORKER under `start_long_op`; `on_ready(result)`
    then runs on the UI thread with the worker stopped (the shape `cell_layers.py`
    documents for its own two phases).

    Nothing is started — and nothing is shown — when there is no live connection
    or another operation already holds the shared kipy socket: `start_long_op` does
    NOT refuse on a busy socket, and the other owner's completion would release OUR
    token mid-read (the same two guards `cell_layers.start_*` makes, Т4.1(д)). Both
    refusals are a Log line, never a modal.

    `widgets` are the caller's guard widgets (the dialog's button): disabled while
    the read runs, so the same read cannot be started twice. Returns the
    LongOpController, or None when nothing was started.

    `tree` is read HERE, on the UI thread, and only through
    `sitting_anchors(tree)` — the worker gets tuples (Т4.1(б))."""
    from ..worker import socket_busy, start_long_op

    if not getattr(connection, "is_connected", False):
        logger.info("Read pads refused: there is no live board connection")
        return None
    if socket_busy(connection):
        logger.info("Read pads refused: another long operation already holds the "
                    "shared kipy socket")
        return None
    payload = {"connection": connection, "cfg": cfg, "sheet_names": sheet_names,
               "anchors": sitting_anchors(tree), "ref": ref, "role": role,
               "cluster": cluster, "sheet": sheet, "snapshot": snapshot}
    return start_long_op(
        connection, widgets, run_read_pads_worker, on_ready,
        on_error if on_error is not None else _log_read_failure, payload,
        busy_text=_("reading pads"))


def _log_read_failure(message: str) -> None:
    """Default failure handler of phase 1 — a Log line, never a modal."""
    logger.error("Reading the pads for the mounts table failed: %s", message)


# ── the write (once the plan is approved) ──────────────────────────────────

def write_plan(dock, tree, parent_node, plan: MountPlan) -> int:
    """Attach the approved nodes EXACTLY where the plan proved them (Д1), through
    the dock's own staging path, and return how many were written.

    Two refusals write NOTHING and stage NOTHING:

      * a refused plan (Д7 «всё или ничего») — the caller shows `plan.refusals`;
      * a `parent_node` that is not the place the plan was checked at. `MountPlan.
        parent_ref` carries that place, and a mismatch means the loader's verdict
        was asked about another spot — the promise would be false, so it is a red
        Log line and 0 (Т4.1(г)).

    One `_mark_dirty()` for the whole batch: the staging path serialises the whole
    tree anyway, so N nodes cost the same as one (Т1, §7.2)."""
    if not plan.ok:
        return 0
    actual = None if parent_node is None else getattr(parent_node, "ref", None)
    if plan.parent_ref != actual:
        logger.error(
            "Mounts not written: the plan was checked under parent %r but is "
            "being written under %r — nothing was written",
            plan.parent_ref, actual)
        return 0
    target = tree.nodes if parent_node is None else parent_node.children
    target.extend(plan.nodes)
    dock._mark_dirty()
    dock._rebuild_tabs()
    return len(plan.nodes)
