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
from kicadstamp.domain.board import Footprint, Track, Via
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
    """The clone's role -> live-ref map — the resolution block both
    read_clone_origin_live and read_cell_anchor_offset_live need. by-nets or
    by-selection (the SAME branch as apply/Select-on-board), honoring
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
    # does (design_2026_09_05 v2).
    ax_mm, ay_mm = cell_mount_offset(cell)
    origin, rotation = clone_origin_from_component(
        fp.position, fp.angle_deg, slot, clone.mirror, ax_mm, ay_mm)
    return LiveRead(position=origin, rotation_deg=rotation, footprint=fp)


def _world_pos_to_cell_local_offset(adapter, cfg, clone, sheet_names,
                                    world_pos: Vector2,
                                    is_mirror: bool) -> tuple[float, float]:
    """The SHARED tail both live-anchor readers reuse (2026-09-06, plan
    cell_anchor_from_selection): an absolute world point -> (ax_mm, ay_mm) in
    the CELL's own local (unrotated, unmirrored) frame RELATIVE TO THE CELL'S
    CURRENT MOUNT. The mount (origin + rotation_deg) is read for the SAME
    clone via read_clone_origin_live; a mirrored clone's world point is first
    un-mirrored about the vertical axis through the origin (the same X-flip as
    clone_geometry's _mirror_x) — stored cell offsets are described
    unmirrored, so the point must be unmirrored too. Then the difference is
    inverted through the placement rotation:
      delta = world_pos - origin
      (ax_mm, ay_mm) = rotate_local_offset(delta.x/MM, delta.y/MM, -rotation_deg)
    Fatal ValidationError when the clone's mount can't be derived (the same
    "never guess" discipline read_clone_origin_live enforces)."""
    origin_read = read_clone_origin_live(adapter, cfg, clone, sheet_names)
    origin = origin_read.position
    wx = world_pos.x
    wy = world_pos.y
    if is_mirror:
        wx = 2 * origin.x - wx  # un-mirror about the vertical axis through origin
    delta_x_mm = (wx - origin.x) / MM
    delta_y_mm = (wy - origin.y) / MM
    offset = rotate_local_offset(delta_x_mm, delta_y_mm, -origin_read.rotation_deg)
    return (offset.x / MM, offset.y / MM)


def read_cell_anchor_offset_live(adapter, cfg, clone, sheet_names,
                                 role: str, pad: str) -> tuple[float, float]:
    """(ax_mm, ay_mm) of ONE pad of the live-resolved role's footprint,
    expressed in the CELL's own local (unrotated, unmirrored) frame RELATIVE
    TO THE CELL'S CURRENT MOUNT (the point read_clone_origin_live returns) —
    the value the Placer's "Role + Pad" anchor adds to the cell's current
    mount A to get the pad's stored-frame anchor_xy (design_2026_09_05 v2;
    the mutation-based rebase of design_2026_09-04 is gone).

    Resolves role + pad -> the pad's absolute world position, then delegates
    the "world -> cell-local offset" inversion to the shared
    _world_pos_to_cell_local_offset tail (the same one
    read_cell_anchor_offset_from_selection uses).

    Fatal ValidationError when the cell/role doesn't resolve to a live ref, or
    that ref has no such pad — same "never guess" discipline as every other
    resolver in this module."""
    cell = cfg.cells.get(clone.cell)
    if cell is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r} not found in config").format(cell=clone.cell),
            [_("extract/save the cell and make sure include: is wired (see Extract)")]))
    role_to_ref = _resolve_clone_role_to_ref(adapter, cfg, clone, cell, sheet_names)
    ref = role_to_ref.get(role)
    name = clone_placement_effective_name(clone)
    if ref is None:
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: role {role!r} is not on the live board").format(
                name=name, role=role),
            [_("place the cell on the board first, or check its nets/selection "
               "resolution — never a guess")]))
    fp = adapter.get_footprint(ref)
    if fp is None:
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: role {role!r} resolved to {ref!r}, but that ref "
              "is not on the live board").format(name=name, role=role, ref=ref),
            [_("the board changed since the last apply — place the component first")]))
    pad_pos = resolve_anchor_pad_position(adapter, fp, pad, name)
    return _world_pos_to_cell_local_offset(
        adapter, cfg, clone, sheet_names, pad_pos, clone.mirror)


def _selection_item_label(item) -> str:
    """A short user-facing kind name for one raw get_selected_items() item,
    used by read_cell_anchor_offset_from_selection's "only a Via is
    supported" fatal message."""
    if isinstance(item, Via):
        return _("via")
    if isinstance(item, Footprint):
        return _("footprint")
    if isinstance(item, Track):
        return _("track")
    return _("board item")


def read_cell_anchor_offset_from_selection(adapter, cfg, clone,
                                           sheet_names) -> tuple[float, float]:
    """(ax_mm, ay_mm) of the CURRENT LIVE SELECTION's single Via, expressed in
    the cell's own local (unrotated, unmirrored) frame RELATIVE TO THE CELL'S
    CURRENT MOUNT — the Placer "Point" tab's "Take from selection"
    (2026-09-06; v1 scope — only a Via is a supported source, see the plan
    §6). Like read_cell_anchor_offset_live, the world point comes from the
    live board, but the source is the current selection (exactly ONE Via), not
    a Role+Pad resolve; the mount inversion is the same
    _world_pos_to_cell_local_offset shared tail.

    Fatal ValidationError (never a guess): nothing selected, more than one
    object selected, or the single selected object is not a Via."""
    cell = cfg.cells.get(clone.cell)
    if cell is None:
        raise ValidationError(format_fatal_error(
            _("cell {cell!r} not found in config").format(cell=clone.cell),
            [_("extract/save the cell and make sure include: is wired (see Extract)")]))
    name = clone_placement_effective_name(clone)
    items = list(adapter.get_selected_items())
    if not items:
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: nothing is selected on the board — 'Take from "
              "selection' needs exactly one Via").format(name=name),
            [_("select the Via whose position should become the cell's mount "
               "point, then press the button again")]))
    if len(items) != 1:
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: select exactly ONE object — {count} objects are "
              "currently selected").format(name=name, count=len(items)),
            [_("select only the Via whose position should become the cell's "
               "mount point")]))
    item = items[0]
    if not isinstance(item, Via):
        raise ValidationError(format_fatal_error(
            _("clone {name!r}: only a Via is supported as the anchor source "
              "(v1) — selected: {kind}").format(
                  name=name, kind=_selection_item_label(item)),
            [_("select a Via — a footprint/pad source is covered by the "
               "Component tab's Role + Pad mode instead")]))
    return _world_pos_to_cell_local_offset(
        adapter, cfg, clone, sheet_names, item.position, clone.mirror)


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
