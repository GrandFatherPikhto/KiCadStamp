# kicadstamp/live_cluster_frame.py
"""The ONE "live cluster -> cell frame" reader, in the ENGINE layer.

Moved here from `gui/docks/live_position.py` (2026-11-04, plan
plan_2026_10_28_entity_outside_trees_in_place, commit А — a mechanical move, no
behaviour change): the redraw planner has to answer "where does this record stand
RIGHT NOW" for a record that no tree places, and `kicadstamp/` may not import
`gui/`. The moved code never had a Qt import — that module says so itself ("No
PyQt import here on purpose") — so the move is a copy, not a rewrite.

`live_position.py` re-exports every name below, so its own callers (the overlay
workers, the tree-node read, "Read current position", the diagnostics probes and
twelve test modules) keep importing from `gui.docks.live_position` exactly as
before — the same idiom as `registry_io`.

WHAT `live_cluster_frame` ANSWERS: (origin, rotation, mirror, role -> ref) of ONE
cell EXACTLY as it stands on the board right now, derived from the LIVE CLUSTER
alone (2026-09-10, plan overlay_frame_from_cluster). The cluster standing on the
board is the truth (Denis, 2026-09-10: "Размещение берётся bbox из текущего
положения кластера. И никак иначе. Никакой привязки к деревьям быть не должно"),
so a cell that has just been extracted — with an Entity but no tree node yet —
gets an answer like any other.

The ROLE -> REF map travels out with the frame (2026-11-04): it is the instance
the frame was fitted against, so a caller that must place the record at this place
can carry it as `ClonePlacement.refs` instead of identifying the roles a second
time — and a second identification is exactly how a twin instance gets swapped.
"""
import logging
from collections import Counter

from .cell_frame import (CellFrame, fit_cell_frame, instance_mirror,
                         pair_deviation_mm, reference_relative_pairs,
                         single_slot_rotation)
from .cluster_matching import cluster_prefix_match
from .constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from .domain.geometry import Vector2
from .exceptions import (
    NonRigidClusterError,
    ValidationError,
    format_read_fatal,
)
from .geometry.cell_anchor import cell_mount_offset
from .i18n import _
from .placement.services.component_resolver import resolve_pad_mount
from .placement.services.coordinate_position_calculator import (
    resolve_footprint_by_cluster_role,
)
from .placement.services.role_narrowing import own_cluster_candidates
from .refdes_duplicates import duplicate_refusal_lines
from .role_conflict_reasons import role_conflict_problems
from .utils.units import MM

logger = logging.getLogger(__name__)

__all__ = [
    "LiveClusterFrame",
    "live_cluster_frame",
    "role_multiplicity_in_cluster",
    "_live_cluster_frame",
    "_reference_slot",
    "_role_to_fp_by_refs",
]


class LiveClusterFrame:
    """Where ONE cell instance stands on the board RIGHT NOW.

    origin — the world point (nm) the cell's MOUNT lands on, i.e.
        `apply_clone_geometry`'s `origin` and `ClonePlacement.xy` after the mm
        conversion — so this IS the pose a placement needs, not a parallel one.
    rotation_deg — the cell's own world rotation (orthogonal, snapped to 90°).
    mirror — the physical side of the INSTANCE against the cell's own side
        (`cell_frame.instance_mirror`), i.e. exactly what `ClonePlacement.mirror`
        means as an operation.
    role_to_ref — {cell role: refdes} of the instance the frame was measured on.
    residual_mm — how far the live cluster is from a rigid copy of the cell (0.0
        when the frame was not fitted); > RIGID_TOLERANCE_MM means "a guess", and
        a caller that would DRAW or WRITE must refuse instead of using it.
    """

    __slots__ = ("origin", "rotation_deg", "mirror", "role_to_ref", "residual_mm")

    def __init__(self, origin: Vector2, rotation_deg: float, mirror: bool,
                 role_to_ref: dict | None = None, residual_mm: float = 0.0):
        self.origin = origin
        self.rotation_deg = float(rotation_deg)
        self.mirror = bool(mirror)
        self.role_to_ref = dict(role_to_ref or {})
        self.residual_mm = float(residual_mm)

    def as_tuple(self) -> tuple[Vector2, float, bool]:
        """The historical three-value answer — what every GUI caller reads."""
        return self.origin, self.rotation_deg, self.mirror

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (f"LiveClusterFrame(origin={self.origin!r}, "
                f"rotation_deg={self.rotation_deg}, mirror={self.mirror}, "
                f"residual_mm={self.residual_mm:.4f}, "
                f"roles={sorted(self.role_to_ref)})")


def role_multiplicity_in_cluster(adapter, cluster: str, roles) -> Counter:
    """{role: how many LIVE footprints carry it inside `cluster`} — the "is this
    a spoke cluster?" question of stage 1 (design_2026_09_17_spoke_cell_editing.md
    §1: a spoke is a cluster where some role of the cell occurs more than once).

    ONE pass over the cached footprint list, counted in memory — the same shape
    the diagnostic probe measures with
    (probe_spoke_cell_identification.role_multiplicity), and the reason the
    check costs nothing next to the frame read it guards.

    The Cluster comparison is cluster_prefix_match, NOT the exact equality
    resolve_footprint_by_cluster_role uses for the frame: "how many of this role
    live in this cluster tag" is the same prefix-tolerant (Cluster, Sheet)
    addressing question resolve_context_footprints asks, while "which footprint
    IS the instance" stays exact (that lookup is untouched)."""
    counts: Counter = Counter()
    wanted = {role for role in roles if role}
    if not cluster or not wanted:
        return counts
    for fp in adapter.get_footprints():
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role not in wanted:
            continue
        fp_cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or ""
        if cluster_prefix_match(fp_cluster, cluster):
            counts[role] += 1
    return counts


def _role_to_fp_by_refs(adapter, cell, role_to_ref: dict[str, str], label: str
                        ) -> dict:
    """The role -> live footprint map of ONE IDENTIFIED instance (R1 of
    plan_2026_09_17_spoke_s1_identify_by_selection.md): each role is read BY
    REF, and the working cluster is never searched.

    Both failures mean the same thing to the user — the identification no longer
    matches the board, so editing it would silently move the WRONG pair — and
    both are fatal with the word "stale" plus the names involved:
      * the ref is not on the board any more (deleted or renamed refdes);
      * the ref exists but its Role field is no longer the role the cell expects.
    A role absent from `role_to_ref` is simply left out (the caller decides);
    an EMPTY map can only mean the refs belong to another cell, which is stale
    too — never a crash on an empty reference slot."""
    # Ч4 (plan_2026_10_15_extract_and_read_paths_cleanup): the same reference
    # door the apply pipeline guards — a refdes on the board twice has no single
    # right answer, and `get_footprint` would silently take the first copy. The
    # refusal is the guard's OWN wording (read-only verdict: nothing is drawn).
    duplicate_lines = duplicate_refusal_lines(adapter.get_footprints(),
                                              role_to_ref.values())
    if duplicate_lines:
        raise ValidationError(format_read_fatal(duplicate_lines[0],
                                                duplicate_lines[1:]))
    role_to_fp: dict = {}
    for slot in cell.components:
        ref = role_to_ref.get(slot.role)
        if not ref:
            continue
        fp = adapter.get_footprint(ref)
        if fp is None:
            raise ValidationError(format_read_fatal(
                _("ref {ref!r} for role {role!r} is not on the live board — the "
                  "identification is stale, identify the instance again")
                .format(ref=ref, role=slot.role),
                [_("select the components of this cell instance on the board, "
                   "then press “Fill from selection” on the Source tab")]))
        live_role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if live_role != slot.role:
            raise ValidationError(format_read_fatal(
                _("ref {ref} now has Role {actual!r}, the cell expects "
                  "{expected!r} — the identification is stale, identify the "
                  "instance again")
                .format(ref=ref, actual=live_role, expected=slot.role),
                [_("select the components of this cell instance on the board, "
                   "then press “Fill from selection” on the Source tab")]))
        role_to_fp[slot.role] = fp
    if not role_to_fp:
        raise ValidationError(format_read_fatal(
            _("none of the remembered refs of {label} is a role of this cell — "
              "the identification is stale, identify the instance again")
            .format(label=label),
            [_("select the components of this cell instance on the board, then "
               "press “Fill from selection” on the Source tab")]))
    return role_to_fp


def _reference_slot(cell, role_to_ref: dict[str, str]):
    """The cell slot to re-derive the origin from: cell.anchor_role's slot
    when that role resolved, else the first slot with a resolved ref."""
    if cell.anchor_role is not None:
        for slot in cell.components:
            if slot.role == cell.anchor_role and slot.role in role_to_ref:
                return slot
    for slot in cell.components:
        if slot.role in role_to_ref:
            return slot
    return None


def live_cluster_frame(adapter, cell, cluster: str, sheet: str, sheet_names,
                       role_to_ref: dict[str, str] | None = None, *,
                       snapshot=None, strict: bool = False,
                       refresh: bool = True) -> LiveClusterFrame:
    """The pose and the role map of ONE cell EXACTLY as it stands on the board
    right now — derived from the LIVE CLUSTER alone (2026-09-10, plan
    overlay_frame_from_cluster).

    MOVED here from gui/docks/cell_anchor_view.py (2026-09-11, plan
    tree_node_live_read_and_board_frame §2.1): it is the ONE "live cluster ->
    frame" reader, shared by the overlay (bbox/marker) and the tree-node live
    read, so they can never drift. No Qt import — this module stays
    unit-testable without a QApplication.

    The cell's roles are resolved to live footprints by the working
    (Cluster, Sheet) — resolve_footprint_by_cluster_role, the same exact-match
    resolver "Select on board" and the dumb placer use, sheet narrowing included
    — the reference slot is cell.anchor_role's when it resolved, else the first
    resolved slot (_reference_slot, live_position.py), and the frame comes out
    of THAT footprint's live position/angle through the pure inverse
    clone_origin_from_component. Mirror is read from the live footprint's own
    side against the cell's layer, never from a record: we draw what is on the
    board.

    Neither resolve_clone_context_live, nor materialize_entity_placements, nor
    cfg.clone_placements, nor the trees take any part in this path. The cluster
    standing on the board is the truth (Denis, 2026-09-10: "Размещение берётся
    bbox из текущего положения кластера. И никак иначе. Никакой привязки к
    деревьям быть не должно"), so a cell that has just been extracted — with an
    Entity but no tree node yet — gets an overlay like any other.

    `role_to_ref` (2026-09-17, stage 1 of the spoke work) — the OPTIONAL
    "role -> refdes" map of an IDENTIFIED instance. When it is given, each role
    is read from ITS ref and the cluster is not searched at all; a ref that left
    the board or changed Role is a "stale identification" fatal. That is the one
    link a spoke cell needs replaced (design §2.1: everything below this lookup
    reproduced the spoke frame to 0.0000 mm), so ordinary cells and spokes share
    this single path. When it is None (a context remembered before this stage,
    R5) the historical cluster search runs and a role that occurs more than once
    there is reported as a spoke — with the count — instead of the false
    "no footprint".

    Raises ValidationError with an HONEST message — "cluster X is not on the
    live board" / the role-conflict refusal of role_conflict_reasons
    («this refdes stands on the board twice — delete the extra copy» / «several
    distinct components carry this role — give them distinct roles»), which is
    the SAME wording the apply path gives, / "role Y of this cell has no
    footprint in cluster X" — never the old "place the cell first", and never
    the withdrawn "this is a spoke cell … Fill from selection" (2026-10-12:
    spokes are gone). The refusal carries the read-only footer, since a read
    places nothing (plan_2026_10_13_cluster_confined_roles, rule 4).

    2026-09-10 (plan stale_board_snapshot K.1): the board is REFRESHED first.
    The GUI's automatic poll tick is a deliberate no-op while connected, so
    adapter.get_footprints() (which resolve_footprint_by_cluster_role reads)
    can be a cache from the moment of connection — a cluster the user has just
    moved in KiCad would draw its overlay at the OLD position ("Show bbox" after
    moving the cluster, Denis 2026-09-10). Every other live-reading path in the
    project refreshes before it reads (board_overlay.read_marker/sweep_layer,
    cascade, apply_pipeline, cli); this one did not.

    That refresh is NOT free, and it is NOT always inside a worker — corrected
    2026-09-22 (Кj of plan_2026_09_22_live_adapter_class). It is one
    `adapter.refresh_board()`: a `get_board()` round trip that also drops BOTH
    adapter caches, so the next read pays a full 332-footprint re-read over IPC.
    The three overlay workers do hold the socket exclusively, but the re-hang
    path reaches this function from the UI THREAD, synchronously, under the
    door's sign — and gui/docks/trees_dock.py's _rehang_offset_or_ask NAMES that
    price in its reason rather than reciting the worker story. The UI-thread
    caller is kept from interleaving with the poll tick by the door's own
    `socket_busy` check plus ONE deferred retry (defer_while_socket_busy). Either
    way the refresh STAYS where it is: without it the frame comes from a cache
    that can predate a manual move in KiCad, which is the bug this paragraph
    exists for.

    `refresh` (2026-11-04, the engine caller): a caller that ALREADY knows the
    board generation is current passes False — the redraw planner reads this
    frame right after `ApplyPipeline` refreshed the board itself, and a refresh
    per record would drop the caches once per record. The GUI never passes it:
    a manual move in KiCad is exactly what it cannot see without a refresh.
    Default True keeps every existing caller byte for byte.

    `snapshot` (2026-09-22, Кj): the caller's `connection.snapshot`. The role →
    footprint IDENTITY then comes from it — one exact (Role, Cluster) match in
    memory — instead of a whole-board sweep PER CELL ROLE: an ordinary five-role
    cell made five `get_footprints()` calls plus 1665 `get_field_value()` reads for
    ONE parent (adapter CALLS, probe_2026_09_22_rehang_entity_parent.py). The
    POSITION still comes from the adapter's CURRENT generation — the resolver reads
    `adapter.get_footprint(ref)`, never the snapshot's frozen `Selected.fp` (whose
    position is as old as the snapshot). Default None = the historical sweep, byte
    for byte: cell_anchor_view, tree_from_selection and the diagnostic probes
    pass nothing and are untouched.

    WHAT THE SNAPSHOT DOES **NOT** REMOVE — corrected in Кl/Кk of the acceptance,
    because a call count is not what the board costs (the real adapter CACHES,
    kicadstamp/kicad/adapter.py): those five `get_footprints()` calls were ONE
    cache rebuild, and the refresh above EMPTIED that cache — so the first
    `adapter.get_footprint(ref)` of the snapshot path rebuilds all 332 footprints
    over IPC, i.e. it IS a full-board read (the ~110 ms miss the adapter's own
    docstring measures, the class Ш1 counted at 166/186 ms). The measured cache
    contract for ONE Entity-typed parent is therefore:

        get_board                          1  ->  1   (refresh_board)
        whole-board footprint read         1  ->  1   (the next line forces it)
        per-footprint field scan         332  ->  0   <-- the win, and it is real

    (diagnostics/probe_2026_09_22_rehang_entity_ipc.py — the Кk acceptance killer,
    which counts IPC round trips instead of calls.) So one Entity parent costs TWO
    board-wide reads on the UI thread, and a re-hang resolves two parents. The door
    sign names exactly that; the millisecond decision belongs to Т3.

    NOT covered, named rather than hidden: the two board-wide reads on this
    function's FAILURE path — `role_multiplicity_in_cluster` and the "is the
    cluster on the board at all" check — take no snapshot and still sweep. They
    run once, immediately before a fatal message, and are paid on an error, not
    on the ordinary resolve.

    `strict` (2026-10-20, plan_2026_10_15_honest_live_read Ч1-А; Denis: strict
    refusal at the EXISTING `RIGID_TOLERANCE_MM`, no second constant): when the
    fitted frame is NOT `is_rigid`, the read is REFUSED with
    `NonRigidClusterError` instead of returning a guessed pose. Only the
    consumers that would WRITE the guess into the config pass True (tree node
    read/move/re-hang, the node form, the marker read, tree-from-selection); the
    two overlay workers (`_ensure_bbox_worker`/`_ensure_marker_worker`) keep the
    default False and go on drawing what is on the board. `single_slot_rotation`
    is unaffected: one component has nothing to diverge from."""
    if refresh:
        adapter.refresh_board()
    label = _("cell {cell!r} on cluster {cluster!r}").format(
        cell=getattr(cell, "name", "?"), cluster=cluster)
    if role_to_ref is not None:
        # IDENTIFIED BY REFS (2026-09-17, stage 1 of the spoke work): the pair
        # the user picked on the board IS the instance. A spoke cluster holds the
        # cell's roles many times over, so the cluster search below cannot be
        # used there at all — and when refs ARE known, the cluster's opinion
        # does not matter, which is what makes this ONE path for ordinary cells
        # and spokes alike.
        role_to_fp = _role_to_fp_by_refs(adapter, cell, role_to_ref, label=label)
    else:
        # The historical cluster search — kept ONLY for a context remembered
        # before this stage (no refs, R5/Р2).
        # Only the roles that FAILED to resolve are counted (see the except
        # below): a role that resolved uniquely says nothing about the cluster,
        # so a cluster whose roles all resolve pays nothing for this check —
        # and, more importantly, a resolver that is PINNED to specific refs (the
        # diagnostic probe's way of asking "what would the frame be for THIS
        # pair?") is not short-circuited by a count that no longer describes it.
        role_to_fp: dict = {}
        unresolved: list = []
        for slot in cell.components:
            try:
                role_to_fp[slot.role] = resolve_footprint_by_cluster_role(
                    adapter, cluster, slot.role, label, sheet=sheet or None,
                    sheet_names=sheet_names, snapshot=snapshot)
            except ValidationError:
                # Absent, or ambiguous (several footprints carry it — a spoke).
                # The COUNT tells the two apart; that is what the check below is
                # for, and why this role list is kept at all.
                unresolved.append(slot.role)
                continue
        counts = role_multiplicity_in_cluster(adapter, cluster, set(unresolved))
        repeated = sorted((role, n) for role, n in counts.items() if n > 1)
        if repeated:
            # The role stands more than once inside this cluster: "no footprint"
            # would be a flat lie, so the SAME refusal the apply path gives is
            # raised here (role_conflict_reasons — ONE wording for both paths:
            # a duplicated refdes says "delete the extra copy", several distinct
            # refdes say "give them distinct roles in the schematic"), with the
            # read-only footer: a READ places and modifies nothing, so the usual
            # "Fix the config and run again" verdict would be false
            # (2026-10-13, plan_2026_10_13_cluster_confined_roles, rules 3 and 4).
            role = repeated[0][0]
            candidates = [fp for fp in adapter.get_footprints()
                          if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role]
            own = own_cluster_candidates(adapter, candidates, cluster)
            raise ValidationError(format_read_fatal(
                label, role_conflict_problems(adapter, role, cluster, own)))
        if not role_to_fp:
            on_board = {adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
                        for fp in adapter.get_footprints()}
            if cluster not in on_board:
                raise ValidationError(format_read_fatal(
                    _("cluster {cluster!r} is not on the live board")
                    .format(cluster=cluster),
                    [_("check the working Cluster on the Source tab, or place "
                       "the cluster on this board")]))
            first_role = cell.components[0].role if cell.components else "?"
            raise ValidationError(format_read_fatal(
                _("role {role!r} of this cell has no footprint in cluster "
                  "{cluster!r}").format(role=first_role, cluster=cluster),
                [_("the cell's roles must be the components tagged with the "
                   "working Cluster on the board")]))
    slot = _reference_slot(cell, role_to_fp)
    fp = role_to_fp[slot.role]
    # Mirror comes from the LIVE footprint's side against the REFERENCE SLOT's own
    # side — the ONE host of that rule (cell_frame.instance_mirror), never a second
    # copy of the comparison, and never clone.mirror. A cell may keep this slot on
    # the other side of the same board, and the instance is then still unmirrored
    # (2026-10-22, Д-1).
    mirror = instance_mirror(fp.layer, getattr(slot, "layer", None), cell.layer,
                             fallback=False)
    # resolve_pad_mount takes the role -> ref map of the RESOLVED instance (the
    # same shape the identification stores): built here from the live footprints
    # so the roles verified above are exactly the ones used.
    refs_by_role = {role: live_fp.ref for role, live_fp in role_to_fp.items()}
    resolved_mount = resolve_pad_mount(adapter, cell, refs_by_role, label)
    if resolved_mount is None:
        ax_mm, ay_mm = cell_mount_offset(cell)
    else:
        ax_mm, ay_mm = resolved_mount
    # The ROTATION is fitted from the stored offsets against the live deltas of
    # EVERY resolved role at once — the ONE cell<->world transform of
    # kicadstamp/cell_frame.py, the same one the geometry refresh uses. Never
    # from one component's angle: a two-pin part is symmetric and its angle
    # ambiguous by 180° (measured 2026-09-10: four capacitors gave +90 while
    # FB_PI_FLT gave -90). Mirror stays the physical layer rule above (which
    # side of the board the instance stands on), which the fit is constrained
    # to; how far the cluster is from a rigid copy of the cell comes back as
    # residual_mm (unused here — the overlay still draws what is on the board).
    slot_by_role = {s.role: s for s in cell.components}
    reference = (float(slot.offset_along_mm), float(slot.offset_across_mm),
                 fp.position.x / MM, fp.position.y / MM)
    pairs_by_role = {
        role: (float(slot_by_role[role].offset_along_mm),
               float(slot_by_role[role].offset_across_mm),
               live.position.x / MM, live.position.y / MM)
        for role, live in role_to_fp.items() if role in slot_by_role}
    relative_pairs = reference_relative_pairs(reference,
                                              list(pairs_by_role.values()))
    fit = fit_cell_frame(relative_pairs, mirror=mirror)
    # ONE resolved component (2026-10-13, plan_2026_10_13_single_slot_rotation —
    # Denis: "Read current position" reported 0 for a part he had to turn by
    # hand): with fewer than TWO resolved slots there is no pair to fit against,
    # and that component's live angle is the only, exact source — the rule and its
    # math live in the ONE owner (cell_frame.single_slot_rotation, which delegates
    # to the placement's own inverse formula). A MULTI-part cell keeps the fit: a
    # symmetric part's angle is ambiguous by 180° there, geometry is not.
    if strict and fit is not None and not fit.is_rigid:
        # The cluster on the board is NOT a rigid copy of the cell, so "where
        # this cell stands" is a guess — and a guess must not be written into a
        # config (plan_2026_10_15_honest_live_read, fact А). The refusal names
        # the worst ROLES, because "re-read the cell" is only actionable when
        # the user sees WHICH parts diverged. The pairs handed to it are the
        # SAME reference-relative ones the fit judged (zip keeps their order),
        # so the numbers in the text are the fit's own, never a second count.
        raise NonRigidClusterError(_non_rigid_refusal(
            cell, cluster, fit, dict(zip(pairs_by_role, relative_pairs)),
            role_to_fp), residual_mm=fit.residual_mm)
    single_rot: float | None = None
    if fit is None and len(role_to_fp) == 1:
        # `only_slot` is always found: role_to_fp only ever carries roles
        # resolved FROM the cell's own slots, and an unresolved one already
        # raised a named refusal above — so this reader has no silent "0°" path.
        only_role, only_fp = next(iter(role_to_fp.items()))
        only_slot = slot_by_role.get(only_role)
        if only_slot is not None:
            single_rot = single_slot_rotation(
                float(only_fp.angle_deg), float(only_slot.angle_deg),
                bool(mirror))
    frame = CellFrame.from_reference(
        rotation_deg=(fit.rotation_deg if fit is not None
                      else (single_rot if single_rot is not None else 0.0)),
        mirror=mirror, mount=(ax_mm, ay_mm), stored_ref=reference[:2],
        live_ref_mm=reference[2:],
        residual_mm=fit.residual_mm if fit is not None else 0.0)
    return LiveClusterFrame(origin=frame.placement_origin,
                            rotation_deg=frame.rotation_deg, mirror=frame.mirror,
                            role_to_ref=refs_by_role,
                            residual_mm=frame.residual_mm)


def _live_cluster_frame(adapter, cell, cluster: str, sheet: str, sheet_names,
                        role_to_ref: dict[str, str] | None = None, *,
                        snapshot=None, strict: bool = False,
                        refresh: bool = True) -> tuple[Vector2, float, bool]:
    """The historical THREE-VALUE answer `(origin, rotation_deg, mirror)` — the
    shape every GUI caller and every test of `live_position` reads.

    Kept as the re-exported name (2026-11-04) so the readers that were already in
    `gui/docks/live_position.py` — the overlay workers, the tree-node read,
    "Read current position" and twelve test modules that monkeypatch exactly this
    symbol — keep working without a single edit. The engine callers that also
    need the role map ask `live_cluster_frame` instead."""
    return live_cluster_frame(
        adapter, cell, cluster, sheet, sheet_names, role_to_ref,
        snapshot=snapshot, strict=strict, refresh=refresh).as_tuple()


def _non_rigid_refusal(cell, cluster, fit, relative_by_role, role_to_fp) -> str:
    """The ONE wording of the non-rigid refusal (Ч1-А).

    Names the cluster and the cell, the worst deviation, the 2–3 roles that
    diverged most with their refdes and their own deviation, and BOTH exits
    Denis asked for: put the parts back onto the cell's places (Redraw), or —
    if the board is right now — re-read the cell from the selection. The
    read-only footer comes from `format_read_fatal` itself.

    `relative_by_role` — the reference-RELATIVE pairs (`reference_relative_pairs`)
    the fit itself judged, per role: the per-role numbers in the text are the
    same arithmetic as `residual_mm`, never a parallel count."""
    worst = sorted(
        ((role, role_to_fp[role].ref,
          pair_deviation_mm(pair, fit.rotation_deg, fit.mirror))
         for role, pair in relative_by_role.items()),
        key=lambda item: item[2], reverse=True)[:3]
    roles = ", ".join(f"{role} ({ref}): {dev:.3f} mm"
                      for role, ref, dev in worst)
    return format_read_fatal(
        _("live cluster {cluster!r} is not a rigid copy of cell {cell!r} "
          "(worst deviation {dev:.3f} mm)").format(
              cluster=cluster, cell=getattr(cell, "name", "?"),
              dev=fit.residual_mm),
        [_("worst roles: {roles}").format(roles=roles),
         _("put the component(s) back onto the cell's places (Redraw) — or, if "
           "the board is right now, re-read the cell from the selection")])
