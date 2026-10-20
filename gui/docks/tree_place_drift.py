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

  * the PLAN pose is whatever the redraw itself reads for that ancestor
    (`resolve_base_live_position` / `resolve_base_rotation_deg`) — never a
    second computation of "its place in the tree";
  * the LIVE pose is `read_record_live_pose(..., strict=True)` — the one host of
    "where the board says this record stands". A cluster that is not a rigid
    copy of its cell REFUSES (`NonRigidClusterError`), and then NO deviation
    number is reported at all: a guess is not a number (the honest_live_read
    decision of 20.10);
  * the comparison is `> RIGID_TOLERANCE_MM` / `> ANGLE_TOLERANCE_DEG`, done in
    WHOLE nanometres, and no second constant is introduced (Denis, 20.10);
  * only ancestors whose pose the redraw takes from the PLAN are examined
    (kind "placement", and the placing node of an internal mount). A clone or a
    coordinate ancestor is read LIVE by the redraw, so the nodes below it follow
    it and a warning there would be false.

The result is a list of Log lines; the caller (gui/docks/cascade.py, a worker)
logs them and hands them to the dock's warning counter. Nothing is written.

Module content of an embedded tree (stage-2 layout) is deliberately NOT covered
— a recorded gap of the plan, not an oversight.
"""
from __future__ import annotations

import dataclasses

from kicadstamp.cell_frame import RIGID_TOLERANCE_MM
from kicadstamp.constants import ANGLE_TOLERANCE_DEG
from kicadstamp.exceptions import (
    NonRigidClusterError,
    ValidationError,
    fatal_error_reason,
)
from kicadstamp.i18n import _
from kicadstamp.tree_plan_warnings import plain_tree_by_name
from kicadstamp.tree_position import (
    resolve_base_live_position,
    resolve_base_rotation_deg,
)
from kicadstamp.trees import resolve_internal_mount_match
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
               off by the ancestor's deviation)."""

    ref: str
    record: object
    children: list = dataclasses.field(default_factory=list)


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


def _plan_owner(cfg, index: dict, linked_tree, linked_node):
    """(ref, record) whose pose the redraw takes from the PLAN for this node, or
    None when the redraw reads this node LIVE (then the nodes below it follow it
    and there is nothing to warn about).

    kind "placement" — an Entity: its "live" resolution IS the tree, by design.
    kind "mount" — an INTERNAL mount lays its children from the tree pose of the
    node that places the anchored role (`_internal_mount_base` reads only the
    pad's SHAPE), so the owner is that placing node. A LIVE mount (a role outside
    the tree) is resolved from the board and is skipped."""
    node = linked_node.node
    if node.kind == "placement":
        # Both halves must say "placement": the tree node's kind AND the record's
        # kind (link_trees derives one from the other, so a disagreement is a bug
        # somewhere else — and it must not turn into a false warning here).
        record = linked_node.record
        return (node.ref, record) if getattr(record, "kind", None) == "placement" else None
    if node.kind != "mount":
        return None
    plain = plain_tree_by_name(cfg, linked_tree.name)
    if plain is None:
        return None
    match = resolve_internal_mount_match(cfg, plain, node)
    if match is None:
        return None
    placing = index.get(match.node.ref)
    if placing is None or placing.record is None:
        return None
    if getattr(placing.record, "kind", None) != "placement":
        return None
    return (placing.node.ref, placing.record)


def ancestors_outside_plan(cfg, linked_trees, names) -> list:
    """Every ancestor of a redrawn name that this run will NOT move and whose
    pose comes from the plan — ONE entry per ancestor, oldest first.

    The WHOLE chain is walked, not just the immediate parent (Denis, 20.10): with
    the father and the child checked and the grandfather shifted, the line must
    name the GRANDFATHER. An ancestor that IS in `names` is skipped as a subject
    (it will be moved onto its planned place) but the walk continues past it.
    Nothing selected below an ancestor means it is never reached — that silence
    is the point (клетка 11)."""
    wanted = set(names or ())
    index, parent, owner = _forest_index(linked_trees)
    found: dict = {}
    for name in wanted:
        if name not in index:
            continue
        parent_ref = parent.get(name)
        while parent_ref is not None:
            if parent_ref not in wanted:
                subject = _plan_owner(cfg, index, owner[parent_ref],
                                      index[parent_ref])
                if subject is not None:
                    ref, record = subject
                    entry = found.get(ref)
                    if entry is None:
                        entry = AncestorOutsidePlan(ref=ref, record=record)
                        found[ref] = entry
                    if name not in entry.children:
                        entry.children.append(name)
            parent_ref = parent.get(parent_ref)
    return list(found.values())


def _normalized_deg(delta: float) -> float:
    """Relative angle in (-180, 180] — the project's own normalisation."""
    return (delta + 180.0) % 360.0 - 180.0


def _deviation(used_pos, used_deg, live) -> tuple[int, float | None]:
    """(largest per-axis drift in WHOLE nm, normalised turn or None)."""
    drift_nm = max(abs(int(live.position.x) - int(used_pos.x)),
                   abs(int(live.position.y) - int(used_pos.y)))
    turn = None
    if live.rotation_deg is not None and used_deg is not None:
        turn = _normalized_deg(float(live.rotation_deg) - float(used_deg))
    return drift_nm, turn


def _could_not_check(ref: str, reason: str) -> str:
    return _("Tree redraw: could not check {ref!r}: {reason}").format(
        ref=ref, reason=reason)


def _lines_for(adapter, cfg, ancestor: AncestorOutsidePlan, sheet_names) -> list:
    """The Log lines this ONE ancestor earns — usually none."""
    ref, record = ancestor.ref, ancestor.record
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

    drift_nm, turn = _deviation(used_pos, used_deg, live)
    turned = turn is not None and abs(turn) > ANGLE_TOLERANCE_DEG
    if drift_nm <= _TOL_NM and not turned:
        return []
    return [_("Tree redraw: ancestor {ref!r} is NOT being redrawn and stands "
              "{mm:.4f} mm from its place in the tree (planned ({px:.4f}, "
              "{py:.4f}), live ({lx:.4f}, {ly:.4f}); turn {deg:+.2f} deg) — the "
              "nodes below it are placed from the tree pose, so {children} will "
              "land off by that amount relative to where {ref!r} stands. Redraw "
              "the whole tree, or put {ref!r} in its place first.")
            .format(ref=ref,
                    mm=drift_nm / MM,
                    px=used_pos.x / MM, py=used_pos.y / MM,
                    lx=live.position.x / MM, ly=live.position.y / MM,
                    deg=0.0 if turn is None else turn,
                    children=", ".join(repr(c) for c in ancestor.children))]


def place_drift_lines(adapter, cfg, linked_trees, names, sheet_names=None) -> list:
    """The Log lines for a curated redraw about to place `names` — the ancestors
    it will NOT move but whose pose it takes from the plan.

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
