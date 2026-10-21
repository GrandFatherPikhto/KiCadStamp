# gui/docks/tree_place_drift.py
"""Point 2 of plan_2026_10_20_cells_save_keeps_trees_cfg_fresh: the warning a
PARTIAL curated redraw owes the user.

WHY it exists. "Redraw selected" runs a curated redraw over the CHECKED nodes
only, so an ancestor that is not checked is not moved at all. Yet for a kind
"placement" record (an Entity) the pose the redraw takes as a BASE is not read
from the board — it is computed from the tree
(`tree_position.resolve_record_live_position` ->
`entity_placement.resolve_entity_live_position`, and the same is true for the
internal mount base, `tree_position._internal_mount_base`). So when such an
ancestor stands somewhere else, the nodes below it are placed from the tree
pose while the ancestor itself stays where it stands — silently. Measured live
on 20.10 (3CH-AWG-TIA-v103): six such nodes, `fpga_vcca_35` exactly 3.0000 mm,
`mcu_boot0_mcu` 16.5990 mm and turned +90 deg.

WHAT this module does, and what it does NOT do:

  * WHO is plan-borne is not decided here: `tree_plan_warnings.plan_pose_subject`
    is THE ONE HOST of that rule (both planners and this module ask it). The
    acceptance of 21.10 found the copies it used to have disagreeing;
  * the PLAN pose is whatever the redraw itself reads for that ancestor
    (`resolve_base_live_position` / `resolve_base_rotation_deg`) — never a
    second computation of "its place in the tree";
  * the LIVE pose is `read_record_live_pose(..., strict=True)` — the one host of
    "where the board says this record stands". A cluster that is not a rigid
    copy of its cell REFUSES (`NonRigidClusterError`), and then NO deviation
    number is reported at all: a guess is not a number (the honest_live_read
    decision of 20.10);
  * the deviation is the DISTANCE between the two poses (`math.hypot` over whole
    nanometres), never the largest axis: "stands N mm" must be the number the
    comparison uses (21.10 — the axis version understated it by up to sqrt 2);
  * the comparison is `> RIGID_TOLERANCE_MM` / `> ANGLE_TOLERANCE_DEG`, and no
    second constant is introduced (Denis, 20.10). The turn goes through the
    project's own `cell_frame.normalize_deg` — no third copy of that formula;
  * the ancestor that IS in the run is skipped as a SUBJECT — for an internal
    mount the subject is the PLACING node, and when THAT node is redrawn it
    lands on its own plan, so no gap is left (21.10: the check used to test the
    mount NODE and cried wolf about a component that was being moved);
  * a plan-borne subject whose plan pose cannot be READ (a mount placed by a
    node that is not a "placement" record) gets one honest "could not check"
    line with the reason in words — silence would read as "checked, in place".

The result is a list of Log lines; the caller (gui/docks/cascade.py, a worker)
logs them and hands them to the dock's warning counter. Nothing is written.

Two recorded gaps, not oversights: module content of an embedded tree (stage-2
layout) is NOT covered, and the tree parents of a MOUNT ancestor are still
walked (an internal mount's base comes from the placing component, not from
where the mount node itself hangs — no profile holds a (kind mount) node yet).
"""
from __future__ import annotations

import dataclasses
import math

from kicadstamp.cell_frame import RIGID_TOLERANCE_MM, normalize_deg
from kicadstamp.constants import ANGLE_TOLERANCE_DEG
from kicadstamp.exceptions import (
    NonRigidClusterError,
    ValidationError,
    fatal_error_reason,
)
from kicadstamp.i18n import _
from kicadstamp.tree_plan_warnings import plan_pose_subject
from kicadstamp.tree_position import (
    resolve_base_live_position,
    resolve_base_rotation_deg,
)
from kicadstamp.utils.units import MM

from .live_position import read_record_live_pose

# Comparison in whole nanometres (Denis, 20.10: no fractional-millimetre compares).
_TOL_NM = int(RIGID_TOLERANCE_MM * MM)


@dataclasses.dataclass
class AncestorOutsidePlan:
    """One ancestor that this run will NOT move and whose pose the redraw takes
    from the tree plan.

    ref      — the record's name, i.e. what the warning shows;
    record   — the Record itself (kind "placement" by construction);
    children — the run's own names that hang below it (the ones that will land
               off by the ancestor's deviation);
    reason   — set when that deviation CANNOT be measured: the check then owes
               one honest "could not check" line, never silence."""

    ref: str
    record: object
    children: list = dataclasses.field(default_factory=list)
    reason: str | None = None


def _forest_index(linked_trees) -> tuple[dict, dict, dict]:
    """(ref -> LinkedNode, ref -> parent ref | None, ref -> owning LinkedTree)
    over EVERY linked tree — a redraw's `names` may span several trees (module
    content), and a parent chain must never stop at a tree border."""
    nodes: dict = {}
    parent: dict = {}
    owner: dict = {}

    def walk(linked_node, parent_ref, linked_tree):
        ref = linked_node.node.ref
        nodes[ref] = linked_node
        parent[ref] = parent_ref
        owner[ref] = linked_tree
        for child in linked_node.children:
            walk(child, ref, linked_tree)

    for linked_tree in linked_trees:
        for linked_node in linked_tree.nodes:
            walk(linked_node, None, linked_tree)
    return nodes, parent, owner


def _host_subject(cfg, linked_node, tree_name):
    """`plan_pose_subject` with a mount classifier's fatal turned into a reason.

    The classifier raises config-level fatals (ambiguity / module crossing /
    cycle). The load-time guard already reports them, but this check runs inside
    a worker: it must never break the redraw and it must never fall silent
    either (acceptance 21.10) — so the reason travels to the line, not the
    traceback."""
    try:
        return plan_pose_subject(cfg, linked_node, tree_name), None
    except Exception as exc:  # noqa: BLE001 — a warning check never breaks a redraw
        return None, fatal_error_reason(exc)


def _unreadable_plan(record, linked_node) -> str:
    """Why a plan-borne subject has no readable plan pose: the redraw lays the
    nodes below it from the tree, `resolve_base_live_position` answers such a
    pose only for a "placement" record, and a guess is not a number (21.10)."""
    kind = (getattr(record, "kind", None)
            or getattr(getattr(linked_node, "node", None), "kind", None)
            or "component")
    return _("its plan pose comes from the tree, but the node that places it is "
             "a {kind} record — that pose cannot be read").format(kind=kind)


def ancestors_outside_plan(cfg, linked_trees, names) -> list:
    """Every ancestor of a redrawn name that this run will NOT move and whose
    pose comes from the plan — ONE entry per SUBJECT, oldest first.

    The WHOLE chain is walked, not just the immediate parent (Denis, 20.10): with
    the father and the child checked and the grandfather shifted, the line must
    name the GRANDFATHER. The membership test is on the SUBJECT and nowhere
    else — for an internal mount that is the PLACING component, never the mount
    node: when the placing component IS redrawn it lands on its own plan, so no
    gap is left and a line about it would be false (acceptance 21.10). The walk
    continues past a redrawn ancestor, and nothing selected below an ancestor
    means it is never reached — that silence is the point (cell 11)."""
    wanted = set(names or ())
    index, parent, owner = _forest_index(linked_trees)
    found: dict = {}

    def entry(ref, record, reason=None):
        made = found.get(ref)
        if made is None:
            made = AncestorOutsidePlan(ref=ref, record=record, reason=reason)
            found[ref] = made
        return made

    for name in wanted:
        if name not in index:
            continue
        parent_ref = parent.get(name)
        while parent_ref is not None:
            node = index[parent_ref]
            subject, reason = _host_subject(cfg, node, owner[parent_ref].name)
            if reason is not None:
                # A mount we could not classify: its children ARE laid from the
                # tree, so say so instead of falling silent.
                entry(parent_ref, None, reason)
            elif subject is not None:
                ref, record = subject
                if record is None:                 # a mount's placing node
                    placing = index.get(ref)
                    record = None if placing is None else placing.record
                if ref not in wanted:
                    if getattr(record, "kind", None) != "placement":
                        entry(ref, record, _unreadable_plan(record, index.get(ref)))
                    else:
                        made = entry(ref, record)
                        if name not in made.children:
                            made.children.append(name)
            parent_ref = parent.get(parent_ref)
    return list(found.values())


def _deviation(used_pos, used_deg, live) -> tuple[float, float | None]:
    """(DISTANCE between the plan and the live pose in WHOLE nm, turn or None).

    The distance, not the largest axis: the line says "stands N mm", so N must be
    what the tolerance is compared with. (2.5, 2.5) mm is 3.5355 mm away, not
    2.5000 — the axis version understated the deviation by up to sqrt 2 and
    compared a different number than it printed (acceptance 21.10)."""
    dx = int(live.position.x) - int(used_pos.x)
    dy = int(live.position.y) - int(used_pos.y)
    distance_nm = math.hypot(dx, dy)
    turn = None
    if live.rotation_deg is not None and used_deg is not None:
        turn = normalize_deg(float(live.rotation_deg) - float(used_deg))
    return distance_nm, turn


def _could_not_check(ref: str, reason: str) -> str:
    return _("Tree redraw: could not check {ref!r}: {reason}").format(
        ref=ref, reason=reason)


def _lines_for(adapter, cfg, ancestor: AncestorOutsidePlan, sheet_names) -> list:
    """The Log lines this ONE ancestor earns — usually none."""
    ref, record = ancestor.ref, ancestor.record
    if ancestor.reason is not None:
        # The deviation cannot be measured (no readable plan pose, or a mount
        # the classifier refused): one honest line, no invented number.
        return [_could_not_check(ref, ancestor.reason)]
    try:
        used_pos = resolve_base_live_position(adapter, cfg, ref, record, {},
                                              sheet_names)
        used_deg = resolve_base_rotation_deg(adapter, cfg, ref, record,
                                             sheet_names)
    except Exception as exc:  # noqa: BLE001 — a warning check never breaks a redraw
        return [_could_not_check(ref, fatal_error_reason(exc))]
    try:
        live = read_record_live_pose(adapter, cfg, ref, record, sheet_names,
                                     strict=True)
    except NonRigidClusterError as exc:
        residual = getattr(exc, "residual_mm", None)
        if residual is None:
            return [_could_not_check(ref, fatal_error_reason(exc))]
        return [_("Tree redraw: could not check {ref!r}: the cluster on the board "
                  "is not a rigid copy of its cell (residual {residual:.3f} mm) — "
                  "put the parts back onto the cell's places or re-read the cell; "
                  "until then the nodes below it are placed from the tree pose")
                .format(ref=ref, residual=float(residual))]
    except ValidationError as exc:
        return [_could_not_check(ref, fatal_error_reason(exc))]
    except Exception as exc:  # noqa: BLE001 — same rule as above
        return [_could_not_check(ref, fatal_error_reason(exc))]

    distance_nm, turn = _deviation(used_pos, used_deg, live)
    turned = turn is not None and abs(turn) > ANGLE_TOLERANCE_DEG
    if distance_nm <= _TOL_NM and not turned:
        return []
    return [_("Tree redraw: ancestor {ref!r} is NOT being redrawn and stands "
              "{mm:.4f} mm from its place in the tree (planned ({px:.4f}, "
              "{py:.4f}), live ({lx:.4f}, {ly:.4f}); turn {deg:+.2f} deg) — the "
              "nodes below it are placed from the tree pose, so {children} will "
              "land off by that amount relative to where {ref!r} stands. Redraw "
              "the whole tree, or put {ref!r} in its place first.")
            .format(ref=ref,
                    mm=distance_nm / MM,
                    px=used_pos.x / MM, py=used_pos.y / MM,
                    lx=live.position.x / MM, ly=live.position.y / MM,
                    deg=0.0 if turn is None else turn,
                    children=", ".join(repr(c) for c in ancestor.children))]


def place_drift_lines(adapter, cfg, linked_trees, names, sheet_names=None) -> list:
    """The Log lines for a curated redraw about to place `names` — the SUBJECTS
    (ancestors, or the components that place a mount ancestor) it will NOT move
    but whose pose it takes from the plan.

    Read-only and best-effort BY CONTRACT: a check that cannot be made becomes
    one honest "could not check" line, never an exception and never a made-up
    number. The price (measured live 20.10: median 30.7 / 31.8 ms per ancestor on
    two runs, 3CH-AWG-TIA-v103) is ONE cluster read per ancestor — 1–3 per
    partial redraw, and ZERO on a whole-tree redraw, where every ancestor is in
    `names`."""
    sheet_names = sheet_names or {}
    lines: list = []
    for ancestor in ancestors_outside_plan(cfg, linked_trees, names):
        lines.extend(_lines_for(adapter, cfg, ancestor, sheet_names))
    return lines
