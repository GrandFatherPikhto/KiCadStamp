# gui/docks/trees_mounts_flow.py
"""Т4 of plan_2026_11_03_add_mounts_table — the READS and the WRITE of the
mounts table, and nothing of its window (that is Т5).

WHAT THIS MODULE ANSWERS, in one place:

  * `resolve_instance` — WHICH instance the table is about (Д2). Given a Ref it
    reads that footprint's Role/Cluster and its sheet (the SAME two owners Д2
    names: the adapter for the field values, `sheet_names.sheet_of_names` for the
    sheet); given a role anchor it resolves through `ComponentResolver.
    resolve_anchor_fp` — the resolver `tree_position.mount_node_base` uses, so the
    pads the table shows are the pads the redraw will find. A Ref on a footprint
    WITHOUT a Role is refused in words, because the anchor written to the file is
    always the role one (the anchor grammar is not touched by this задача).
  * `read_pads` — the rows (`tree_mounts.pad_rows`) plus the instance-aware
    «уже есть» map (`occupied_for_instance`).
  * `start_read_pads` — phase 1 on the WORKER through the door (`start_long_op`,
    the shared kipy socket's only in-flight owner), with `socket_busy` checked
    first (the same guard `cell_layers.py` uses: `start_long_op` does NOT refuse
    while another owner holds the socket, and that owner's completion would
    release OUR token mid-read).
  * `write_plan` — attaching an approved `MountPlan` exactly where the plan
    proved it, through the dock's own staging path (`_mark_dirty`, one writer).

THE ADAPTER IS THE DOCK'S OWN, TAKEN INSIDE THE WORKER (Д of the Т1 review, §12):
`start_long_op` hands the CONNECTION over, and `connection.board.adapter` is read
on the worker thread — the door's own rule (`gui/docks/explode_page.adapter_of`).
This is not a style choice: a FRESH adapter cannot answer the resolve at all
until `refresh_board()` loads the document — `get_selected_items()`, the first
call `resolve_footprint_by_role` makes, dies on it with
`AttributeError: 'NoneType' object has no attribute 'document'` (measured in Т1).
The dock already holds a loaded adapter, and it is the one this read uses.

Qt-free at import on purpose: the reading half is plain data in, plain data out,
so its cells need no window. The two Qt-touching seams (`start_long_op`,
`adapter_of`) are imported INSIDE the functions that need them.
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
    mount_nodes,
    pad_number,
    pad_rows,
    sits_on_its_pad,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PADS_READ_SIGN",
    "occupied_for_instance",
    "read_pads",
    "resolve_instance",
    "start_read_pads",
    "write_plan",
]


# ── The door sign ──────────────────────────────────────────────────────────
#
# A sign names WHY the read is on the worker and WHAT it costs, measured to the
# leaves (deepseek.md §31). The numbers are Т1's live measurement
# (probe_2026_11_03_mount_pads_read.py, HiPiMS-Controller-v099, 175 footprints,
# 03.11.2026) plus Т4's own measurement of the instance-aware map.
PADS_READ_SIGN = (
    "read the board for the mounts table — ONE selection read plus the anchor "
    "resolve (the same ComponentResolver the redraw uses), then ONE resolve per "
    "mount of this tree that shares the instance's Role, so «уже есть» answers "
    "about the INSTANCE and not merely about one spelling of the anchor. "
    "Measured 03.11.2026: ~1 ms with the dock's warm adapter, 14.7-15.8 ms on a "
    "fresh one; the 25 same-role mounts of the v103 FPGA tree add 3.1 ms "
    "(median; a 2-mount tree 0.4 ms); the pads themselves are in memory "
    "(0.8 ms for 145 pads), so nothing here is per-pad IPC")


# ── Д2: which instance ─────────────────────────────────────────────────────

def _resolver(adapter, cfg, sheet_names, snapshot, resolver=None):
    """The resolver this module uses — injected by cells, `ComponentResolver`
    otherwise. THE one place the class is chosen, so the table, the anchors of the
    tree and the redraw can never end up with different resolvers."""
    if resolver is not None:
        return resolver
    return ComponentResolver(adapter, cfg, sheet_names, snapshot=snapshot)


def resolve_instance(adapter, cfg, sheet_names, *, ref: Optional[str] = None,
                     role: Optional[str] = None, cluster: Optional[str] = None,
                     sheet: Optional[str] = None, snapshot=None,
                     resolver=None) -> dict:
    """The instance the table is about: {ref, role, cluster, sheet, footprint}.

    Two ways in (Д2), and they end in the SAME dict:

      * `ref` — a way to FIND the instance: the Role/Cluster come from that
        footprint's fields and the sheet from its own hierarchical chain, and the
        ANCHOR WRITTEN to the file stays the role one. A footprint without a Role
        is a refusal in words (the grammar has no ref-anchor for a mount, and this
        задача does not change it);
      * `role` (+ optional sheet/cluster) — the anchor itself, resolved by
        `ComponentResolver.resolve_anchor_fp`. Its refusals (not found, ambiguous
        «уточни Sheet») are the resolver's own and are re-raised unchanged, so
        this module never invents a second wording for them."""
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
        names = resolve_sheet_path_names(footprint, sheet_names or {})
        return {"footprint": footprint, "ref": footprint.ref,
                "role": found_role,
                "cluster": adapter.get_field_value(footprint, CLUSTER_FIELD_NAME) or None,
                "sheet": sheet_of_names(names)}
    if not role:
        raise ValidationError(_(
            "the mounts table needs an instance: a Ref, or a Role with a sheet "
            "and a cluster"))
    footprint = _resolver(adapter, cfg, sheet_names, snapshot, resolver) \
        .resolve_anchor_fp(None, role, sheet, cluster, label=role)
    return {"footprint": footprint, "ref": footprint.ref, "role": role,
            "cluster": cluster, "sheet": sheet}


# ── Д6: «уже есть» — по ЭКЗЕМПЛЯРУ, не только по написанию якоря ─────────

def occupied_for_instance(adapter, cfg, sheet_names, tree, *, role: str,
                          instance_ref: str, snapshot=None, resolver=None
                          ) -> dict[str, str]:
    """{pad number: mount ref} for the pads of THIS instance that already carry a
    mount standing on them (Т2.1(в), Т4).

    Why this exists at all: `tree_mounts.mount_key` is STRUCTURAL on purpose — the
    offline tool has no board — so `(role FPGA)(pad 1)` and
    `(role FPGA)(sheet FPGA)(cluster FPGA)(pad 1)` are two keys while on a board
    with one FPGA both are pad 1 of IC1. Д1а pre-fills Sheet/Cluster, so a NEW
    mount is written in full while Денис's older ones may be shorter; only a
    resolve can tell that they name the same pad.

    Two filters keep the map honest, both of them ОДНО правило из `tree_mounts`:

      * `sits_on_its_pad` — a mount with an offset or a turn on that pad is a
        different point and does not occupy the row (§16);
      * the mount's anchor must resolve to `instance_ref` — the same footprint the
        table is about. A mount of the SAME role on ANOTHER instance (channel 1 vs
        channel 0) must not grey a row here, and a mount of another role cannot
        name this footprint at all (a footprint has exactly one Role), so those
        are skipped WITHOUT a resolve — that skip is what keeps the cost at "the
        mounts of this tree that share the role" (~3 ms for 25, Т4 measurement).

    An anchor that does not resolve (a stale record, a channel whose sheet no
    longer exists) is SKIPPED, not fatal: this map only greys rows, and the plan's
    own rules still guard the write."""
    resolve_fp = _resolver(adapter, cfg, sheet_names, snapshot, resolver)
    out: dict[str, str] = {}
    for node in mount_nodes(tree):
        if not sits_on_its_pad(node):
            continue
        anchor = getattr(node, "anchor", None)
        if anchor is None or anchor.role != role or not anchor.anchor_pad:
            continue
        try:
            footprint = resolve_fp.resolve_anchor_fp(
                None, anchor.role, anchor.anchor_sheet, anchor.anchor_cluster,
                label=node.ref)
        except ValidationError:
            continue
        if getattr(footprint, "ref", None) != instance_ref:
            continue
        out.setdefault(pad_number(anchor.anchor_pad), node.ref)
    return out


# ── the read itself (worker side) ──────────────────────────────────────────

def read_pads(adapter, cfg, sheet_names, tree, *, ref=None, role=None,
              cluster=None, sheet=None, snapshot=None, resolver=None) -> dict:
    """Plain data: the instance (without its footprint object) and the table's
    rows + the instance-aware «уже есть» map. Never touches a widget."""
    instance = resolve_instance(adapter, cfg, sheet_names, ref=ref, role=role,
                                cluster=cluster, sheet=sheet, snapshot=snapshot,
                                resolver=resolver)
    rows = pad_rows(adapter.get_footprint_pads(instance["footprint"]))
    occupied = occupied_for_instance(
        adapter, cfg, sheet_names, tree, role=instance["role"],
        instance_ref=instance["ref"], snapshot=snapshot, resolver=resolver)
    return {"instance": {key: value for key, value in instance.items()
                         if key != "footprint"},
            "rows": rows, "occupied": occupied}


def run_read_pads_worker(payload: dict) -> dict:
    """`start_long_op` worker entry — plain data in, plain data out.

    The CONNECTION travels in the payload and the adapter is read from it HERE,
    on the worker thread (see the module docstring: a fresh adapter would not even
    answer the resolve). `cfg`, `tree` and `sheet_names` ride along because the
    read needs the tree's own mounts for the «уже есть» map."""
    from .explode_page import adapter_of

    adapter = adapter_of(payload["connection"])
    if adapter is None:
        raise ValidationError(_(
            "the mounts table cannot be read: there is no board connection"))
    return read_pads(adapter, payload["cfg"], payload["sheet_names"],
                     payload["tree"], ref=payload.get("ref"),
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

    `widgets` are the caller's guard widgets (the dialog's button): disabled while
    the read runs, so the same read cannot be started twice. Returns the
    LongOpController, or None when nothing was started (another operation holds
    the shared kipy socket — then nothing is read and nothing is shown)."""
    from ..worker import socket_busy, start_long_op

    if socket_busy(connection):
        logger.info("Read pads refused: another long operation already holds the "
                    "shared kipy socket")
        return None
    payload = {"connection": connection, "cfg": cfg, "sheet_names": sheet_names,
               "tree": tree, "ref": ref, "role": role, "cluster": cluster,
               "sheet": sheet, "snapshot": snapshot}
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

    Refused plan → NOTHING is written (Д7 «всё или ничего»); the caller shows
    `plan.refusals`. `parent_node` must be the node whose ref the plan was given as
    `parent_ref` — the plan checked the loader's verdict AT THAT PLACE, and
    writing elsewhere would be a promise this function cannot keep.

    One `_mark_dirty()` for the whole batch: the staging path serialises the whole
    tree anyway, so N nodes cost the same as one (Т1, §7.2)."""
    if not plan.ok:
        return 0
    target = tree.nodes if parent_node is None else parent_node.children
    target.extend(plan.nodes)
    dock._mark_dirty()
    dock._rebuild_tabs()
    return len(plan.nodes)
