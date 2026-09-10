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
from dataclasses import dataclass

from kicadstamp.config import clone_placement_effective_name
from kicadstamp.domain.board import Footprint
from kicadstamp.domain.geometry import Vector2
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
                         sheet: str | None, sheet_names, label: str) -> LiveRead:
    """CoordinatePlacement's referent: the ONE component with this exact
    (Role, Cluster) — resolve_footprint_by_cluster_role (the same resolver the
    "dumb placer" apply path and Select-on-board use). Fatal on 0 or 2+."""
    fp = resolve_footprint_by_cluster_role(
        adapter, cluster, role, label, sheet=sheet, sheet_names=sheet_names)
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
