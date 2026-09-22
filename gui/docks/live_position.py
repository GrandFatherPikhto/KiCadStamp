# gui/docks/live_position.py
"""Shared "read the current live position of a record's referent" resolvers
for the Config Tree forms' "Read current position" buttons (design
2026-08_29_config_tree_read_live_position.md).

The Config Tree forms (Placer coordinate/clone, Rules origin) previously only
let the user TYPE coordinates; this module is the single source of the
"referent -> live position/rotation" step, reused by every form instead of
being duplicated. It deliberately does NOT touch the board for anything other
than reading: pure resolvers over the existing kicadstamp services
(resolve_footprint_by_ref / resolve_footprint_by_role /
resolve_footprint_by_cluster_role / resolve_point_chain /
resolve_roles_by_nets / resolve_roles_by_selection) plus the pure geometry
inverse clone_origin_from_component (clone_geometry.py) for a clone's cell
origin.

No PyQt import here on purpose — this module is unit-testable without a
QApplication (the forms own the buttons/fields/fill-in; this module only
computes). Raises the same fatal ValidationError the underlying resolvers
raise on none/ambiguous — the "never guess silently" principle — and the GUI
handler turns it into a QMessageBox warning."""
import logging
from collections import Counter
from dataclasses import dataclass

from kicadstamp.cell_frame import CellFrame, fit_cell_frame, reference_relative_pairs
from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.config import clone_placement_effective_name
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.geometry.cell_anchor import cell_mount_offset
from kicadstamp.geometry.clone_geometry import clone_origin_from_component
from kicadstamp.geometry.spoke_layout import rotate_local_offset
from kicadstamp.i18n import _
from kicadstamp.placement.services.clone_role_resolver import (
    clone_uses_selection_mode,
    resolve_footprint_by_role,
    resolve_roles_by_nets,
    resolve_roles_by_selection,
)
from kicadstamp.placement.services.component_resolver import (
    resolve_anchor_pad_position,
    resolve_footprint_by_ref,
    resolve_pad_mount,
)
from kicadstamp.placement.services.coordinate_position_calculator import (
    resolve_footprint_by_cluster_role,
)
from kicadstamp.placement.services.point_resolver import resolve_point_chain
from kicadstamp.utils.units import MM

logger = logging.getLogger(__name__)


def entity_mount_fallback_reason(cfg, entity) -> str | None:
    """None when an Entity CAN be read from the live cluster of its own cell
    (it names a cell, that cell exists in cfg, and it carries the cluster tag);
    otherwise the human reason why the caller must fall back to the historical
    zero-slot / tree-placement read (plan_2026_09_11_entity_live_position_mount_point
    §P.1.3).

    The fallback path measures a DIFFERENT point than the cell's MOUNT A
    (cell_mount_offset) — up to |A| apart, 5.53 mm on Denis's measured
    `pif_oa_n2v5` — so it is never silent: whoever takes it logs this reason."""
    if cfg is None or getattr(entity, "cell", None) is None:
        return _("the Entity has no cell")
    if cfg.cells.get(entity.cell) is None:
        return _("cell {cell!r} is not in the config").format(cell=entity.cell)
    if not getattr(entity, "cluster", None):
        return _("the Entity has no cluster")
    return None


@dataclass
class LiveRead:
    """The live referent of a record, read from the board RIGHT NOW.

    position — absolute board position (nm, same unit as Footprint.position).
    rotation_deg — the referent footprint's own angle, or None when the kind
        has no rotation concept (a point referent).
    footprint — the single physical footprint (None for a point referent that
        resolved through a shift — the point is then not physically AT that
        footprint, same rule as ResolvedPoint.footprint)."""

    position: Vector2
    rotation_deg: float | None
    footprint: Footprint | None


def read_coordinate_live(adapter, cluster: str, role: str,
                         sheet: str | None, sheet_names, label: str,
                         *, snapshot=None) -> LiveRead:
    """CoordinatePlacement's referent: the ONE component with this exact
    (Role, Cluster) — resolve_footprint_by_cluster_role (the same resolver the
    "dumb placer" apply path and Select-on-board use). Fatal on 0 or 2+.

    snapshot (2026-09-22, plan_2026_09_22_board_door_finish Ш1): the caller's
    ``connection.snapshot``, when the caller is the GUI and has one. Identity is
    then answered from that already-read data instead of by a whole-board sweep
    (no get_footprints, no per-footprint get_field_value); the POSITION still
    comes from the live adapter, never from the snapshot's cached footprint."""
    fp = resolve_footprint_by_cluster_role(
        adapter, cluster, role, label, sheet=sheet, sheet_names=sheet_names,
        snapshot=snapshot)
    return LiveRead(position=fp.position, rotation_deg=fp.angle_deg, footprint=fp)


def read_anchor_live(adapter, fields: dict, points: dict, sheet_names,
                     label: str) -> LiveRead:
    """An AnchorOriginWidget identity block (its build() generic dict, mode
    'anchor' or 'point') resolved to the live referent:
      - anchor: ref -> resolve_footprint_by_ref; role -> resolve_footprint_by_role
        (+ sheet/cluster narrowing); pad -> resolve_anchor_pad_position.
      - point: resolve_point_chain (a point has no rotation concept ->
        rotation_deg None).
    Fatal on none/ambiguous — used by Rules-origin (and as the anchor half of
    the Placer anchor-relative reads)."""
    mode = fields.get("mode")
    if mode == "point":
        resolved = resolve_point_chain(adapter, points, fields.get("point"), sheet_names)
        return LiveRead(position=resolved.position, rotation_deg=None,
                        footprint=resolved.footprint)
    if "ref" in fields:
        fp = resolve_footprint_by_ref(adapter, fields["ref"], label)
    else:
        fp = resolve_footprint_by_role(
            adapter, fields["role"], fields.get("sheet"), fields.get("cluster"),
            sheet_names, label)
    position = fp.position
    if "pad" in fields:
        position = resolve_anchor_pad_position(adapter, fp, fields["pad"], label)
    return LiveRead(position=position, rotation_deg=fp.angle_deg, footprint=fp)


def _resolve_clone_role_to_ref(adapter, cfg, clone, cell, sheet_names) -> dict[str, str]:
    """The clone's role -> live-ref map — the resolution block
    read_clone_origin_live needs. by-nets or by-selection (the SAME branch as
    apply/Select-on-board), honoring
    clone.ignore_selection through the adapter's temporarily_ignore_selection
    when present. Shared (2026-09-04, design cell_internal_anchor) so the
    Role+Pad rebase never duplicates the resolver logic blindly."""
    ignore_ctx = getattr(adapter, "temporarily_ignore_selection", None)

    def _resolve() -> dict[str, str]:
        if clone_uses_selection_mode(clone, adapter=adapter, cell=cell,
                                     sheet_names=sheet_names):
            return resolve_roles_by_selection(adapter, cell, clone, sheet_names=sheet_names)
        return resolve_roles_by_nets(adapter, cell, clone, sheet_names=sheet_names)

    if callable(ignore_ctx):
        with ignore_ctx(clone.ignore_selection):
            return _resolve()
    return _resolve()


def read_clone_origin_live(adapter, cfg, clone, sheet_names) -> LiveRead:
    """A ClonePlacement's CELL ORIGIN (its cell-local (0,0)) read from the
    live board, re-derived from ONE placed component via the pure inverse
    clone_origin_from_component (clone_geometry.py) — the "ячейка" case of
    "Read current position".

    The reference slot is cell.anchor_role when that role resolves, else the
    FIRST cell slot that resolved to a ref. Component resolution uses the SAME
    branch as apply/Select-on-board (clone_uses_selection_mode ->
    resolve_roles_by_nets / resolve_roles_by_selection, honoring
    clone.ignore_selection through the adapter's temporarily_ignore_selection
    when present). Fatal ValidationError when the cell is unreachable, or
    nothing resolved — never a guess."""
    cell = cfg.cells.get(clone.cell)
    if cell is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r} not found in config").format(cell=clone.cell),
            [_("extract/save the cell and make sure include: is wired (see Extract)")]))

    role_to_ref = _resolve_clone_role_to_ref(adapter, cfg, clone, cell, sheet_names)
    slot = _reference_slot(cell, role_to_ref)
    if slot is None:
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: no component resolved to read the cell origin from")
            .format(name=clone_placement_effective_name(clone)),
            [_("place the cell on the board first, or check its nets/selection "
               "resolution — never a guess")]))
    ref = role_to_ref[slot.role]
    fp = adapter.get_footprint(ref)
    if fp is None:
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: role {role!r} resolved to {ref!r}, but that ref "
              "is not on the live board").format(
                name=clone_placement_effective_name(clone), role=slot.role, ref=ref),
            [_("the board changed since the last apply — place the component first")]))
    # The inverse recovers the cell's MOUNT point (its anchor A, or the
    # default bbox corner when no anchor is set) — pass A so the reference
    # slot's stored (bbox-frame) offset is reduced exactly as apply_clone_geometry
    # does (design_2026_09_05 v2). For a pad-anchor cell (anchor_role+anchor_pad
    # without anchor_xy) the forward path (apply_clone_geometry) uses a LIVE
    # resolved_mount, so the inverse MUST use the same value — otherwise
    # "Read current position" and the direct geometry would drift apart
    # (plan_2026_09_09_cell_anchor_v2 §A.7). resolve_pad_mount returns None for
    # every offline case (anchor_xy set — GUARD 1 — or no pad anchor), so
    # cell_mount_offset keeps its role there, exactly like the forward path.
    resolved_mount = resolve_pad_mount(adapter, cell, role_to_ref,
                                       clone_placement_effective_name(clone))
    if resolved_mount is None:
        ax_mm, ay_mm = cell_mount_offset(cell)
    else:
        ax_mm, ay_mm = resolved_mount
    origin, rotation = clone_origin_from_component(
        fp.position, fp.angle_deg, slot, clone.mirror, ax_mm, ay_mm)
    return LiveRead(position=origin, rotation_deg=rotation, footprint=fp)


def world_pos_to_cell_local_offset(origin: Vector2, rotation_deg: float,
                                   is_mirror: bool, world_pos: Vector2
                                   ) -> tuple[float, float]:
    """An absolute world point -> (along_mm, across_mm) in the CELL's own local
    (unrotated, unmirrored) frame RELATIVE TO A MOUNT the caller has already
    resolved. A mirrored instance's world point is first un-mirrored about the
    vertical axis through the origin (the same X-flip as clone_geometry's
    _mirror_x) — stored cell offsets are described unmirrored, so the point must
    be unmirrored too. Then the difference is inverted through the rotation:
      delta = world_pos - origin
      (along_mm, across_mm) = rotate_local_offset(delta.x/MM, delta.y/MM, -rotation_deg)

    PURE — no board access, no clone: the caller supplies the frame, whether it
    came from a clone (_world_pos_to_cell_local_offset below) or from the live
    cluster (cell_anchor_view._live_cluster_frame). 2026-09-10: split out when
    the overlay stopped deriving its frame from a placement."""
    wx = world_pos.x
    wy = world_pos.y
    if is_mirror:
        wx = 2 * origin.x - wx  # un-mirror about the vertical axis through origin
    delta_x_mm = (wx - origin.x) / MM
    delta_y_mm = (wy - origin.y) / MM
    offset = rotate_local_offset(delta_x_mm, delta_y_mm, -rotation_deg)
    return (offset.x / MM, offset.y / MM)


def _world_pos_to_cell_local_offset(adapter, cfg, clone, sheet_names,
                                    world_pos: Vector2,
                                    is_mirror: bool) -> tuple[float, float]:
    """The clone-based twin the live-anchor readers use (2026-09-06, plan
    cell_anchor_from_selection): resolves the mount (origin + rotation_deg) for
    the SAME clone via read_clone_origin_live, then delegates the pure inversion
    to world_pos_to_cell_local_offset. Fatal ValidationError when the clone's
    mount can't be derived (the same "never guess" discipline
    read_clone_origin_live enforces)."""
    origin_read = read_clone_origin_live(adapter, cfg, clone, sheet_names)
    return world_pos_to_cell_local_offset(
        origin_read.position, origin_read.rotation_deg, is_mirror, world_pos)


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
    role_to_fp: dict = {}
    for slot in cell.components:
        ref = role_to_ref.get(slot.role)
        if not ref:
            continue
        fp = adapter.get_footprint(ref)
        if fp is None:
            raise ValidationError(format_fatal_error(
                _("ref {ref!r} for role {role!r} is not on the live board — the "
                  "identification is stale, identify the instance again")
                .format(ref=ref, role=slot.role),
                [_("select the components of this cell instance on the board, "
                   "then press “Fill from selection” on the Source tab")]))
        live_role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if live_role != slot.role:
            raise ValidationError(format_fatal_error(
                _("ref {ref} now has Role {actual!r}, the cell expects "
                  "{expected!r} — the identification is stale, identify the "
                  "instance again")
                .format(ref=ref, actual=live_role, expected=slot.role),
                [_("select the components of this cell instance on the board, "
                   "then press “Fill from selection” on the Source tab")]))
        role_to_fp[slot.role] = fp
    if not role_to_fp:
        raise ValidationError(format_fatal_error(
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


def _live_cluster_frame(adapter, cell, cluster: str, sheet: str, sheet_names,
                        role_to_ref: dict[str, str] | None = None):
    """(mount, rotation_deg, mirror) of ONE cell EXACTLY as it stands on the
    board right now — derived from the LIVE CLUSTER alone (2026-09-10, plan
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
    live board" / "role Y occurs N times in cluster X" / "role Y of this cell
    has no footprint in cluster X" — never the old "place the cell first".

    2026-09-10 (plan stale_board_snapshot K.1): the board is REFRESHED first.
    The GUI's automatic poll tick is a deliberate no-op while connected, so
    adapter.get_footprints() (which resolve_footprint_by_cluster_role reads)
    can be a cache from the moment of connection — a cluster the user has just
    moved in KiCad would draw its overlay at the OLD position ("Show bbox" after
    moving the cluster, Denis 2026-09-10). Every other live-reading path in the
    project refreshes before it reads (board_overlay.read_marker/sweep_layer,
    cascade, apply_pipeline, cli); this one did not. The refresh happens inside
    the worker (all three overlay workers are dispatched through start_long_op,
    which holds the socket exclusively), so it creates no extra races."""
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
                    sheet_names=sheet_names)
            except ValidationError:
                # Absent, or ambiguous (several footprints carry it — a spoke).
                # The COUNT tells the two apart; that is what the check below is
                # for, and why this role list is kept at all.
                unresolved.append(slot.role)
                continue
        counts = role_multiplicity_in_cluster(adapter, cluster, set(unresolved))
        repeated = sorted((role, n) for role, n in counts.items() if n > 1)
        if repeated:
            # A repeated role IS a spoke cluster, and "no footprint" would be a
            # flat lie: the footprints are there, there are just several of
            # them. The count is said out loud and the fix is named.
            role, occurrences = repeated[0]
            raise ValidationError(format_fatal_error(
                _("role {role!r} occurs {count} times in cluster {cluster!r} — "
                  "this is a spoke cell: select one pair and use “Fill from "
                  "selection” on the Source tab")
                .format(role=role, count=occurrences, cluster=cluster),
                [_("a spoke repeats the cell's roles inside one cluster, so the "
                   "cluster alone cannot say WHICH pair is edited; identifying "
                   "the pair pins the editor to exactly those components")]))
        if not role_to_fp:
            on_board = {adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
                        for fp in adapter.get_footprints()}
            if cluster not in on_board:
                raise ValidationError(format_fatal_error(
                    _("cluster {cluster!r} is not on the live board")
                    .format(cluster=cluster),
                    [_("check the working Cluster on the Source tab, or place "
                       "the cluster on this board")]))
            first_role = cell.components[0].role if cell.components else "?"
            raise ValidationError(format_fatal_error(
                _("role {role!r} of this cell has no footprint in cluster "
                  "{cluster!r}").format(role=first_role, cluster=cluster),
                [_("the cell's roles must be the components tagged with the "
                   "working Cluster on the board")]))
    slot = _reference_slot(cell, role_to_fp)
    fp = role_to_fp[slot.role]
    # Mirror comes from the LIVE footprint's side against the cell's own layer
    # (clone_geometry's rule) — not from clone.mirror.
    mirror = (fp.layer == BoardLayer.BL_B_Cu) != (cell.layer == "B.Cu")
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
    fit = fit_cell_frame(
        reference_relative_pairs(reference, [
            (float(slot_by_role[role].offset_along_mm),
             float(slot_by_role[role].offset_across_mm),
             live.position.x / MM, live.position.y / MM)
            for role, live in role_to_fp.items() if role in slot_by_role]),
        mirror=mirror)
    frame = CellFrame.from_reference(
        rotation_deg=fit.rotation_deg if fit is not None else 0.0,
        mirror=mirror, mount=(ax_mm, ay_mm), stored_ref=reference[:2],
        live_ref_mm=reference[2:],
        residual_mm=fit.residual_mm if fit is not None else 0.0)
    return frame.placement_origin, frame.rotation_deg, frame.mirror


@dataclass
class LiveRecordPose:
    """Where a config RECORD stands on the board RIGHT NOW.

    position — absolute board position (nm, same unit as Footprint.position).
    rotation_deg — the record's own live angle, or None when the kind has no
        rotation concept (a point base).
    mirror — True when the live instance is MIRRORED relative to its cell. No
        storage in the trees layer carries a mirror (tree_position.py /
        link_trees.py / entity_placement.py never read or write one), so a
        mirrored read must be REFUSED by the caller, never quietly imported.
    from_cluster — True when the pose came from the Entity's LIVE CLUSTER
        (_live_cluster_frame), False when it fell back to the historic
        resolve_base_* path (a placement whose Entity has no cell/cluster yet,
        or any non-placement kind)."""
    position: Vector2
    rotation_deg: float | None
    mirror: bool
    from_cluster: bool = False


def read_record_live_pose(adapter, cfg, ref: str, record, sheet_names, *,
                          snapshot=None) -> LiveRecordPose:
    """THE "where this record stands on the board right now" dispatcher
    (plan_2026_09_11_tree_node_live_read_and_board_frame §2.2).

    A kind="placement" record (an Entity) is read from the LIVE CLUSTER of its
    own cell (entity.cell / entity.cluster / entity.sheet against cfg.cells) via
    _live_cluster_frame — the SAME reader the overlay uses, NEVER the tree that
    places it. That is the whole point of the shared cell_frame contract: a
    placement read through its placement record only ever echoes the config
    back (the Entity's tree-node offset/rotation), so a cluster the user moved
    by hand in KiCad stays invisible (bug 0.1 of the plan).

    Any other kind — and a placement whose Entity has no cell/cluster yet (a
    freshly created Entity: cell/cluster are optional, and the tree node IS its
    only position source until the cluster is placed) — falls back to the
    historic resolve_base_live_position / resolve_base_rotation_deg pair, so
    every existing resolution path keeps working unchanged. That path has no
    mirror concept (mirror=False).

    That fallback is never SILENT (§P.1.3 of
    plan_2026_09_11_entity_live_position_mount_point): it measures a different
    point than the cell's mount A, so it logs which Entity and why."""
    if record is not None and getattr(record, "kind", None) == "placement":
        entity = record.obj
        cell_name = getattr(entity, "cell", None)
        cluster = getattr(entity, "cluster", None)
        cell = (cfg.cells.get(cell_name)
                if cfg is not None and cell_name else None)
        if cell is not None and cluster:
            pos, rot, mirror = _live_cluster_frame(
                adapter, cell, cluster, getattr(entity, "sheet", None) or "",
                sheet_names)
            return LiveRecordPose(position=pos, rotation_deg=rot,
                                  mirror=mirror, from_cluster=True)
        reason = entity_mount_fallback_reason(cfg, entity)
        if reason is not None:
            logger.warning(
                _("Entity {name!r}: cannot read the live mount ({reason}) — "
                  "using the historical tree/zero-slot position instead, which "
                  "may differ from the cell's mount A")
                .format(name=getattr(entity, "name", ref), reason=reason))
    # Local import: this module is load-time light on purpose (the GUI imports
    # it from several docks), and tree_position pulls in the whole placement
    # service stack.
    from kicadstamp.tree_position import (
        resolve_base_live_position,
        resolve_base_rotation_deg,
    )
    # snapshot (Т2-4а of plan_2026_09_22_live_adapter_class): forwarded so the
    # role branch of the record's anchor resolves its identity in memory instead
    # of sweeping the board. None (the default) = the sweep, byte for byte.
    pos = resolve_base_live_position(adapter, cfg, ref, record, {}, sheet_names,
                                     snapshot=snapshot)
    rot = resolve_base_rotation_deg(adapter, cfg, ref, record, sheet_names,
                                    snapshot=snapshot)
    return LiveRecordPose(position=pos, rotation_deg=rot, mirror=False)
