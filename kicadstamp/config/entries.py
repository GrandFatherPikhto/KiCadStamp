# kicadstamp/config/entries.py

"""
config/entries.py — one-loader-per-entry for the config dataclasses
(config/models.py): the standalone _load_* functions and their *_KNOWN_KEYS
sets. Split out of loader.py during the T3.1 god-file decomposition
(2026-08-05).

These are the "pure/transformative" per-entry validators: each takes ONE YAML
dict and produces ONE dataclass (or raises ValidationError). They do not touch
the filesystem, includes, sheet names, or any cross-entry state — that
orchestration belongs to load_config() (config/loader.py), which imports these
and remains the single public entry point, and which re-exports them so
kicadstamp/config/__init__.py's `from .loader import ...` surface is unchanged.
"""
from typing import Any

from ..exceptions import ValidationError, format_fatal_error, check_unknown_keys
from ..i18n import _
from ..trees import Tree, tree_from_dict
from .models import (
    ThermalViaArrayConfig, TemplateVia, TemplateComponentSlot, TemplateTrack,
    Cell, CellPlacement, ManualSpoke, Chain, ClonePlacement, CoordinatePlacement,
    NetTrace, Entity, TreeInstance,
)
from .points import Point


def _load_template_via(data: dict[str, Any]) -> TemplateVia:
    net = data.get('net')
    if net is not None and not isinstance(net, str):
        raise ValidationError(format_fatal_error(
            _("via.net must be a string, not {type}").format(type=type(net).__name__),
            [_("got: {net!r} (offset_along_mm={along}, offset_across_mm={across})").format(
                net=net, along=data.get('offset_along_mm'), across=data.get('offset_across_mm')),
             _("looks like broken YAML – e.g. net_overrides accidentally nested under "
               "this via's net instead of being a top-level field of clone_placement "
               "(net_overrides is a sibling of cell/params, not under via)")]
        ))
    net_from_role = data.get('net_from_role')
    net_from_role_pad = data.get('net_from_role_pad')
    if net is not None and net_from_role is not None:
        raise ValidationError(format_fatal_error(
            _("via.net and via.net_from_role together"),
            [_("mutually exclusive ways to set a via's net: a static net (or "
               "null for the rule net) vs a live role-derived net (net_from_role) "
               "– pick exactly one per via. net_from_role is resolved at apply "
               "time from the placed role's real pad net")]
        ))
    if net_from_role_pad is not None and net_from_role is None:
        raise ValidationError(format_fatal_error(
            _("via.net_from_role_pad without via.net_from_role"),
            [_("net_from_role_pad={pad!r} only narrows which pad of the role "
               "the via's net comes from – it is not a net by itself; write "
               "net_from_role: <ROLE> (and optionally net_from_role_pad: <N>)")
             .format(pad=net_from_role_pad)]
        ))
    return TemplateVia(
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        net=net,
        net_from_role=net_from_role,
        net_from_role_pad=net_from_role_pad,
        drill_mm=data.get('drill_mm', 0.3),
        diameter_mm=data.get('diameter_mm', 0.6),
    )


def _load_template_track(data: dict[str, Any]) -> TemplateTrack:
    net = data.get('net')
    if net is not None and not isinstance(net, str):
        raise ValidationError(format_fatal_error(
            _("track.net must be a string, not {type}").format(type=type(net).__name__),
            [_("got: {net!r} (start_along_mm={along}, start_across_mm={across})").format(
                net=net, along=data.get('start_along_mm'), across=data.get('start_across_mm')),
             _("looks like broken YAML – e.g. placeholder like {{NET}} without quotes: "
               "YAML reads it as flow-mapping, not a string; use quotes: net: '{{NET}}'")]
        ))
    net_from_role = data.get('net_from_role')
    net_from_role_pad = data.get('net_from_role_pad')
    if net is not None and net_from_role is not None:
        raise ValidationError(format_fatal_error(
            _("track.net and track.net_from_role together"),
            [_("mutually exclusive ways to set a track's net: a static net (or "
               "null for the rule net) vs a live role-derived net (net_from_role) "
               "– pick exactly one per track. net_from_role is resolved at apply "
               "time from the placed role's real pad net")]
        ))
    if net_from_role_pad is not None and net_from_role is None:
        raise ValidationError(format_fatal_error(
            _("track.net_from_role_pad without track.net_from_role"),
            [_("net_from_role_pad={pad!r} only narrows which pad of the role "
               "the track's net comes from – it is not a net by itself; write "
               "net_from_role: <ROLE> (and optionally net_from_role_pad: <N>)")
             .format(pad=net_from_role_pad)]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, _("on track"))
    return TemplateTrack(
        start_along_mm=data.get('start_along_mm', 0.0),
        start_across_mm=data.get('start_across_mm', 0.0),
        end_along_mm=data.get('end_along_mm', 0.0),
        end_across_mm=data.get('end_across_mm', 0.0),
        width_mm=data.get('width_mm', 0.25),
        net=net,
        net_from_role=net_from_role,
        net_from_role_pad=net_from_role_pad,
        layer=layer,
    )


def _check_layer_value(value, where: str):
    if value is not None and value not in ('F.Cu', 'B.Cu'):
        raise ValidationError(format_fatal_error(
            _("invalid layer={value!r} {where}").format(value=value, where=where),
            [_("layer must be absolute: 'F.Cu' or 'B.Cu'")]
        ))


def _load_template_component_slot(data: dict[str, Any]) -> TemplateComponentSlot:
    # FIXED (found live 2026-08-06, Denis: Conn_PM5V): a missing/empty/null
    # role here used to either crash with a bare KeyError, or (if 'role' was
    # present but None) silently propagate a None role all the way into
    # placement — surfacing as a confusing runtime "role None is in cell but
    # not found anywhere on board" instead of a clear load-time error.
    if not data.get('role'):
        raise ValidationError(format_fatal_error(
            _("component slot without a role"),
            [_("every component slot needs role: <ROLE> – roles MUST be unique "
               "within a cell")]
        ))
    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' in slot {role!r}").format(role=data.get('role')),
            [_("relative 'side' is deprecated (see discussion v116): layer is now "
               "absolute – write layer: F.Cu or layer: B.Cu, or remove the field "
               "to inherit the cell layer")]
        ))
    layer = data.get('layer')
    _check_layer_value(layer, _("on slot {role!r}").format(role=data.get('role')))
    net_template = data.get('net_template')
    net_template_pad = data.get('net_template_pad')
    if net_template_pad is not None and net_template is None:
        raise ValidationError(format_fatal_error(
            _("net_template_pad without net_template in slot {role!r}").format(role=data.get('role')),
            [_("net_template_pad={pad!r} only narrows which pad of the role's "
               "candidate the net comes from — it is not a net by itself; "
               "write net_template: '<pattern>' too").format(pad=net_template_pad)]
        ))
    net_template_same_as_role = data.get('net_template_same_as_role')
    if net_template_same_as_role is not None and net_template is None:
        raise ValidationError(format_fatal_error(
            _("net_template_same_as_role without net_template in slot {role!r}").format(role=data.get('role')),
            [_("net_template_same_as_role={ref!r} only narrows which OTHER role's "
               "net this one shares — it is not a net by itself; write "
               "net_template: '<pattern>' too").format(ref=net_template_same_as_role)]
        ))
    if net_template_pad is not None and net_template_same_as_role is not None:
        raise ValidationError(format_fatal_error(
            _("both net_template_pad and net_template_same_as_role in slot {role!r}")
            .format(role=data.get('role')),
            [_("mutually exclusive — pick ONE mechanism per role: a fixed pad "
               "number (net_template_pad, safe only for fixed-pinout parts: ICs/"
               "diodes/polarized caps) or a same-net role reference "
               "(net_template_same_as_role, safe for symmetric 2-pin R/C)")]
        ))
    return TemplateComponentSlot(
        role=data['role'],
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        angle_deg=data.get('angle_deg', 0.0),
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        net_template=net_template,
        net_template_pad=net_template_pad,
        net_template_same_as_role=net_template_same_as_role,
        layer=layer,
    )


def _load_cell(name: str, data: dict[str, Any]) -> Cell:
    components = [_load_template_component_slot(c) for c in data.get('components', [])]

    roles = [c.role for c in components]
    duplicates = {r for r in roles if roles.count(r) > 1}
    if duplicates:
        raise ValidationError(format_fatal_error(
            _("role appears twice in cell {name!r}").format(name=name),
            [_("role {role!r} appears {count} times in components of this cell – "
               "roles inside a cell must be unique (see anchor_id/cell_name/role "
               "in the placement registry)").format(role=r, count=roles.count(r))
             for r in sorted(duplicates)]
        ))

    # Cross-reference check (2026-08-16, net_template_same_as_role): the
    # referenced "same-net role" must actually exist in THIS cell — the
    # single-slot loader has no visibility into sibling slots, so this
    # cell-wide check lives here, next to role-uniqueness.
    role_set = set(roles)
    for slot in components:
        ref = slot.net_template_same_as_role
        if ref is not None and ref not in role_set:
            raise ValidationError(format_fatal_error(
                _("net_template_same_as_role={ref!r} in slot {role!r} is not a role of cell {name!r}")
                .format(ref=ref, role=slot.role, name=name),
                [_("net_template_same_as_role must reference ANOTHER role of THIS "
                   "cell (cell roles: {roles}) — it names whose net this role "
                   "shares, so it must exist here").format(roles=sorted(role_set))]
            ))

    if 'reference_side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'reference_side' in cell {name!r}").format(name=name),
            [_("renamed (see discussion v116): use layer: F.Cu or layer: B.Cu – "
               "absolute cell layer, as extracted")]
        ))
    layer = data.get('layer', 'F.Cu')
    _check_layer_value(layer, _("in cell {name!r}").format(name=name))

    clone_placements = [_load_cell_placement(name, cp) for cp in data.get('clone_placements', [])]
    nested_names = [cp.name for cp in clone_placements]
    dup_names = {n for n in nested_names if nested_names.count(n) > 1}
    if dup_names:
        raise ValidationError(format_fatal_error(
            _("name appears twice among clone_placements of cell {name!r}").format(name=name),
            [_("name {dup!r} appears {count} times — nested clone_placement names must be unique "
               "within their cell (used to build the registry key for nested content)")
             .format(dup=n, count=nested_names.count(n)) for n in sorted(dup_names)]
        ))

    # anchor_xy/anchor_role/anchor_pad — display-only metadata for the cell
    # editor, see Cell's own docstring: mutually exclusive, never consumed
    # by any resolver, so a bad value here is only ever a UI-authoring
    # mistake, not a placement-breaking one.
    anchor_xy_raw = data.get('anchor_xy')
    anchor_role = data.get('anchor_role')
    anchor_pad = data.get('anchor_pad')
    anchor_xy: tuple[float, float] | None = None
    if anchor_xy_raw is not None:
        if anchor_role is not None:
            raise ValidationError(format_fatal_error(
                _("anchor_xy together with anchor_role in cell {name!r}").format(name=name),
                [_("these are mutually exclusive ways to mark the cell's own local (0,0) — "
                   "pick one")]
            ))
        if not (isinstance(anchor_xy_raw, (list, tuple)) and len(anchor_xy_raw) == 2):
            raise ValidationError(format_fatal_error(
                _("anchor_xy must be a 2-element [x, y] list in cell {name!r}").format(name=name),
                [_("got: {xy!r}").format(xy=anchor_xy_raw)]
            ))
        anchor_xy = (float(anchor_xy_raw[0]), float(anchor_xy_raw[1]))
    if anchor_pad is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_pad without anchor_role in cell {name!r}").format(name=name),
            [_("anchor_pad only narrows anchor_role — it is not an anchor by itself")]
        ))
    if anchor_role is not None and anchor_role not in {c.role for c in components}:
        raise ValidationError(format_fatal_error(
            _("anchor_role {role!r} is not a component of cell {name!r}").format(
                role=anchor_role, name=name),
            [_("anchor_role must name one of this cell's own components: {roles}")
             .format(roles=sorted({c.role for c in components}))]
        ))

    return Cell(
        name=name,
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        components=components,
        tracks=[_load_template_track(t) for t in data.get('tracks', [])],
        anchor_xy=anchor_xy,
        anchor_role=anchor_role,
        anchor_pad=anchor_pad,
        clone_placements=clone_placements,
        layer=layer,
        comment=data.get('comment'),
    )


_CELL_PLACEMENT_KNOWN_KEYS = {
    'name', 'cell', 'role', 'xy', 'rotation_deg', 'mirror', 'layer',
    'sheet', 'cluster',
    'nets', 'params', 'net_overrides', 'refs',
}


def _load_cell_placement(cell_name: str, data: dict[str, Any]) -> CellPlacement:
    """Loads one entry of a Cell's own clone_placements: — a nested,
    closed-boundary reference to another cell/role. See CellPlacement's
    docstring (config/models.py) for why anchor_*/by_selection/
    ignore_selection are deliberately absent from _CELL_PLACEMENT_KNOWN_KEYS."""
    name = data.get('name', '?')
    if not data.get('name'):
        raise ValidationError(format_fatal_error(
            _("nested clone_placement without name in cell {cell!r}").format(cell=cell_name),
            [_("every nested clone_placement must have a name — used to build the registry "
               "key for its content, write name: <string>")]
        ))
    check_unknown_keys(
        data, _CELL_PLACEMENT_KNOWN_KEYS,
        _("unknown fields in nested clone_placement {name!r} of cell {cell!r}")
        .format(name=name, cell=cell_name),
        extra_hint=_(" (cell placements are closed-boundary — no anchor_ref/anchor_role/"
                     "anchor_sheet/anchor_cluster/anchor_pad/by_selection/ignore_selection "
                     "here, only xy: relative to the parent cell's own (0,0))"))

    cell = data.get('cell')
    role = data.get('role')
    if cell is not None and role is not None:
        raise ValidationError(format_fatal_error(
            _("cell and role together in nested clone_placement {name!r} of cell {cell_name!r}")
            .format(name=name, cell_name=cell_name),
            [_("these are mutually exclusive ways to define the content: either a reference to "
               "another cell (cell), or a single-component placement by role (role), not both")]
        ))
    if cell is None and role is None:
        raise ValidationError(format_fatal_error(
            _("neither cell nor role set in nested clone_placement {name!r} of cell {cell_name!r}")
            .format(name=name, cell_name=cell_name),
            [_("need either cell: <name from cells:>, or role: <ROLE> for a single-component "
               "placement without a separate cell")]
        ))

    xy_raw = data.get('xy')
    if xy_raw is not None:
        if not (isinstance(xy_raw, (list, tuple)) and len(xy_raw) == 2):
            raise ValidationError(format_fatal_error(
                _("xy must be a 2-element [x, y] list in nested clone_placement {name!r} "
                  "of cell {cell_name!r}").format(name=name, cell_name=cell_name),
                [_("got: {xy!r}").format(xy=xy_raw)]
            ))
        xy = (float(xy_raw[0]), float(xy_raw[1]))
    else:
        xy = (0.0, 0.0)

    layer = data.get('layer')
    _check_layer_value(layer, _("in nested clone_placement {name!r} of cell {cell_name!r}")
                       .format(name=name, cell_name=cell_name))

    return CellPlacement(
        name=name,
        cell=cell,
        role=role,
        xy=xy,
        rotation_deg=data.get('rotation_deg', 0.0),
        mirror=bool(data.get('mirror', False)),
        layer=layer,
        sheet=data.get('sheet'),
        cluster=data.get('cluster'),
        nets=data.get('nets', {}) or {},
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        refs=data.get('refs', {}) or {},
    )


_POINT_KNOWN_KEYS = {
    'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster', 'anchor_pad',
    'anchor_point', 'xy', 'anchor_origin', 'shift_x_mm', 'shift_y_mm',
    'comment',
}
_BOARD_ORIGIN_KINDS = {'grid', 'drill'}


def _load_point(name: str, data: dict[str, Any]) -> Point:
    check_unknown_keys(data, _POINT_KNOWN_KEYS,
                       _("unknown fields in point {name!r}").format(name=name))

    anchor_ref = data.get('anchor_ref')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_pad = data.get('anchor_pad')
    anchor_point = data.get('anchor_point')
    xy = data.get('xy')
    anchor_origin = data.get('anchor_origin')

    if anchor_origin is not None and anchor_origin not in _BOARD_ORIGIN_KINDS:
        raise ValidationError(format_fatal_error(
            _("invalid anchor_origin {value!r} in point {name!r}").format(value=anchor_origin, name=name),
            [_("must be 'grid' (Place > Set Grid Origin, visual only) or 'drill' "
               "(Place > Drill/Place Origin — the auxiliary axis drill/position files "
               "use, and Gerbers optionally via their own plot option)")]
        ))

    # Exactly one "base": (anchor_ref or anchor_role) / anchor_point / xy / anchor_origin.
    base_kind_count = sum([
        anchor_ref is not None or anchor_role is not None,
        anchor_point is not None,
        xy is not None,
        anchor_origin is not None,
    ])
    if base_kind_count == 0:
        raise ValidationError(format_fatal_error(
            _("point {name!r} has no anchor").format(name=name),
            [_("set exactly one of: anchor_ref/anchor_role (+ optional anchor_sheet/"
               "anchor_cluster/anchor_pad), anchor_point (chain to another point), "
               "xy (literal absolute coordinate), or anchor_origin (the board's own "
               "live grid/drill-place origin)")]
        ))
    if base_kind_count > 1:
        raise ValidationError(format_fatal_error(
            _("point {name!r} has more than one anchor base").format(name=name),
            [_("anchor_ref/anchor_role, anchor_point, xy, and anchor_origin are mutually "
               "exclusive — pick exactly one way to define this point's base position")]
        ))
    if anchor_ref is not None and anchor_role is not None:
        raise ValidationError(format_fatal_error(
            _("anchor_ref and anchor_role together in point {name!r}").format(name=name),
            [_("mutually exclusive: either by refdes (anchor_ref) or by Role field "
               "(anchor_role), not both")]
        ))
    if anchor_sheet is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet without anchor_role in point {name!r}").format(name=name),
            [_("anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")]
        ))
    if anchor_pad is not None and anchor_ref is None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_pad without anchor_ref/anchor_role in point {name!r}").format(name=name),
            [_("anchor_pad={pad!r} is set but no anchor specified").format(pad=anchor_pad)]
        ))

    shift_x_mm = data.get('shift_x_mm', 0.0)
    shift_y_mm = data.get('shift_y_mm', 0.0)
    if xy is not None and (shift_x_mm or shift_y_mm):
        raise ValidationError(format_fatal_error(
            _("shift on a literal xy point {name!r}").format(name=name),
            [_("xy is already an absolute coordinate — edit it directly instead of "
               "combining it with shift_x_mm/shift_y_mm")]
        ))
    if xy is not None:
        if not (isinstance(xy, (list, tuple)) and len(xy) == 2):
            raise ValidationError(format_fatal_error(
                _("xy must be a 2-element [x, y] list in point {name!r}").format(name=name),
                [_("got: {xy!r}").format(xy=xy)]
            ))
        xy = (float(xy[0]), float(xy[1]))

    return Point(
        name=name,
        anchor_ref=anchor_ref,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_point=anchor_point,
        xy=xy,
        anchor_origin=anchor_origin,
        shift_x_mm=shift_x_mm,
        shift_y_mm=shift_y_mm,
        comment=data.get('comment'),
    )


def _point_is_footprint_eligible(points: dict[str, Point], name: str, _visited=None) -> bool:
    """True if the point named `name` (transitively, through any anchor_point
    chain) resolves to a live footprint with no shift applied anywhere along
    the way — the requirement for Rule/ThermalViaArrayConfig's anchor_point,
    which need a component to look up named pads from (spoke.pad/tva.pad),
    not just a coordinate (see ThermalViaArrayConfig.anchor_point docstring
    in config/models.py). Pure static walk over points: definitions — no live
    board access, shift/xy are literal YAML values. A cycle in the walk just
    returns False here (not fatal) — the precise, definitive cycle error is
    raised at RUNTIME by dependency_order.py's Kahn's algorithm; this check
    does not duplicate that detection, it only needs a bounded walk."""
    if _visited is None:
        _visited = set()
    if name in _visited:
        return False
    _visited.add(name)
    point = points.get(name)
    if point is None:
        return False  # unknown name — reported separately, see _check_anchor_point
    if point.shift_x_mm or point.shift_y_mm:
        return False
    if point.xy is not None:
        return False
    if point.anchor_origin is not None:
        return False
    if point.anchor_point is not None:
        return _point_is_footprint_eligible(points, point.anchor_point, _visited)
    return point.anchor_ref is not None or point.anchor_role is not None


def _load_mutually_exclusive_position(data: dict[str, Any], mode_specs, label: str) -> str | None:
    """Shared "exactly one position mode" validator — the "fatal if both /
    fatal if half-populated" branches were manually copied in
    _load_manual_spoke / _load_clone_placement / _load_coordinate_placement;
    this is the single copy (2026-08-12, Group 3 consolidation).

    mode_specs: iterable of (mode_name, fields, all_required) where
    mode_name is the human-readable mode label used in error messages (e.g.
    "Cartesian"/"polar") and `fields` the YAML field names that make up the
    mode. A mode is ACTIVE when any of its fields is present in data (not
    None). all_required=True makes a mode fatal when only PART of its fields
    are set (e.g. radius_mm without angle_deg); False allows a partial mode
    (e.g. shift_x_mm alone, the rest defaulting to 0).

    Returns the single active mode's name, or None if none is set. Fatal
    when two+ modes are active at once, or when an all_required mode is only
    partially populated — messages name the concrete conflicting/missing
    fields."""
    active: list[str] = []
    incomplete: list[tuple[str, tuple, list[str]]] = []
    for mode_name, fields, all_required in mode_specs:
        present = [f for f in fields if data.get(f) is not None]
        if not present:
            continue
        active.append(mode_name)
        if all_required and len(present) != len(fields):
            missing = [f for f in fields if data.get(f) is None]
            incomplete.append((mode_name, tuple(fields), missing))

    if len(active) > 1:
        def _render(name: str, fields) -> str:
            return fields[0] if len(fields) == 1 else f"{name} ({'/'.join(fields)})"
        modes = " and ".join(
            _render(name, fields) for name, fields, _ in mode_specs if name in active)
        raise ValidationError(format_fatal_error(
            _("{label} has both {modes} — mutually exclusive position modes")
            .format(label=label, modes=modes),
            [_("these are mutually exclusive position modes — pick exactly one")]
        ))
    if incomplete:
        mode_name, fields, missing = incomplete[0]
        # The title names the mode's FULL field set (same as the original
        # per-caller messages did): "BOTH a and b" for 2-field modes,
        # "all of: a, b, ..." otherwise. The hint lists only what's actually
        # missing.
        if len(fields) == 2:
            title = _("{label}: {mode} mode needs BOTH {a} and {b}").format(
                label=label, mode=mode_name, a=fields[0], b=fields[1])
        else:
            title = _("{label}: {mode} mode needs all of: {fields}").format(
                label=label, mode=mode_name, fields=", ".join(fields))
        raise ValidationError(format_fatal_error(
            title,
            [_("missing: {missing}").format(missing=", ".join(missing))]
        ))
    return active[0] if active else None


_MANUAL_SPOKE_KNOWN_KEYS = {
    'pad', 'cell', 'shift_x_mm', 'shift_y_mm', 'rotation_deg',
    'radius_mm', 'angle_deg',
    'retired', 'cluster', 'skip',
}


def _load_manual_spoke(data: dict[str, Any], rule_label: str) -> ManualSpoke:
    check_unknown_keys(data, _MANUAL_SPOKE_KNOWN_KEYS,
                       _("unknown fields in spoke (pad {pad!r}) of rule (net {net!r})")
                       .format(pad=data.get('pad', '?'), net=rule_label))

    # Position — EXACTLY ONE of two mutually exclusive modes (see ManualSpoke's
    # docstring): Cartesian shift (shift_x_mm/shift_y_mm, the default) OR polar
    # (radius_mm/angle_deg). Fatal if BOTH are given, or if only ONE of the
    # polar pair is — shared validator, see _load_mutually_exclusive_position.
    _load_mutually_exclusive_position(
        data,
        [("Cartesian", ("shift_x_mm", "shift_y_mm"), False),
         ("polar", ("radius_mm", "angle_deg"), True)],
        _("spoke (pad {pad!r}) of rule (net {net!r})")
        .format(pad=data.get('pad', '?'), net=rule_label),
    )

    return ManualSpoke(
        pad=data['pad'],
        cell=data['cell'],
        shift_x_mm=data.get('shift_x_mm', 0.0),
        shift_y_mm=data.get('shift_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        radius_mm=data.get('radius_mm'),
        angle_deg=data.get('angle_deg'),
        retired=data.get('retired', False),
        cluster=data.get('cluster'),
        skip=data.get('skip', False),
    )


_CHAIN_KNOWN_KEYS = {
    'net', 'spokes', 'anchor_ref', 'anchor_role', 'anchor_sheet',
    'anchor_cluster', 'anchor_point', 'name', 'sheet', 'retired', 'skip',
    'comment',
}


def _load_chain(chain_data: dict[str, Any]) -> Chain:
    """Extracted 2026-08-05 from load_config's own inline loop (same
    standalone-per-entry shape as _load_point/_load_thermal_via_array/
    _load_clone_placement) so gui/docks/chain.py can validate a single Chain
    the same clean way those docks already validate their own entry type.
    The cross-chain name/net collision check stays in load_config — it
    needs the WHOLE chains list, not just one entry."""
    chain_net = chain_data.get('net')
    check_unknown_keys(chain_data, _CHAIN_KNOWN_KEYS,
                       _("unknown fields in chain (net {net!r})").format(net=chain_net))
    anchor_ref = chain_data.get('anchor_ref')
    anchor_role = chain_data.get('anchor_role')
    anchor_sheet = chain_data.get('anchor_sheet')
    anchor_cluster = chain_data.get('anchor_cluster')
    anchor_point = chain_data.get('anchor_point')
    sheet = chain_data.get('sheet')

    if anchor_ref and anchor_role:
        raise ValidationError(format_fatal_error(
            _("anchor_ref and anchor_role together in chain (net {net!r})").format(net=chain_net),
            [_("mutually exclusive: either by refdes (anchor_ref) or by Role field "
               "(anchor_role), not both")]
        ))
    if anchor_sheet and not anchor_role:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet without anchor_role in chain (net {net!r})").format(net=chain_net),
            [_("anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")]
        ))
    if anchor_point and (anchor_ref or anchor_role):
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_ref/anchor_role in chain (net {net!r})")
            .format(net=chain_net),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role").format(point=anchor_point)]
        ))
    if not anchor_ref and not anchor_role and not anchor_point:
        raise ValidationError(format_fatal_error(
            _("chain (net {net!r}) without anchor_ref/anchor_role/anchor_point").format(net=chain_net),
            [_("a spoke chain must have an anchor – anchor_ref: <ref> (component whose "
               "pads are listed in spokes), anchor_role: <ROLE> (survives re‑annotation), "
               "or anchor_point: <name from points:>")]
        ))
    spokes = [_load_manual_spoke(spoke_data, chain_net) for spoke_data in chain_data.get('spokes', [])]
    return Chain(net=chain_net, spokes=spokes, anchor_ref=anchor_ref,
                anchor_role=anchor_role, anchor_sheet=anchor_sheet,
                anchor_cluster=anchor_cluster, anchor_point=anchor_point,
                sheet=sheet,
                name=chain_data.get('name'),
                retired=chain_data.get('retired', False),
                skip=chain_data.get('skip', False),
                comment=chain_data.get('comment'))


_NET_TRACE_KNOWN_KEYS = {
    'net', 'anchor_role', 'anchor_sheet', 'anchor_cluster', 'anchor_pad',
    'tracks', 'vias', 'retired', 'skip', 'comment',
}


def _load_net_trace(data: dict[str, Any]) -> NetTrace:
    """One net_traces: entry — the flat single-record net-trace binding (see
    NetTrace's docstring in config/models.py). Extracted 2026-08-21 as a
    standalone per-entry validator (same shape as _load_rule/_load_clone_
    placement) so the GUI could later validate a single entry before writing;
    the list-level duplicate-net check stays in load_config() — it needs the
    WHOLE net_traces list, not one entry.

    anchor_role — REQUIRED (the NetTrace record always resolves its anchor by
    Role field, matching the plan: no anchor_ref/anchor_point variant).
    anchor_sheet/anchor_cluster/anchor_pad are all optional narrowings of
    that role and are only meaningful together with it (which is guaranteed
    here by anchor_role being required)."""
    net = data.get('net')
    check_unknown_keys(data, _NET_TRACE_KNOWN_KEYS,
                       _("unknown fields in net_traces entry (net {net!r})").format(net=net))
    if not net:
        raise ValidationError(format_fatal_error(
            _("net_traces entry without net"),
            [_("every net_traces entry must have net: <NET NAME> — the net "
               "name is the record's --only identity and must be unique across "
               "the whole net_traces list; one record per net")]))
    anchor_role = data.get('anchor_role')
    if not anchor_role:
        raise ValidationError(format_fatal_error(
            _("net_traces entry (net {net!r}) without anchor_role").format(net=net),
            [_("every net_traces entry needs anchor_role: <ROLE> — the anchor "
               "footprint is resolved by its Role field over the whole board at "
               "both extract time (origin) and apply time (anchor). "
               "anchor_sheet/anchor_cluster narrow its ambiguity, anchor_pad "
               "moves the anchor point to a specific pad of it")]))

    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_pad = data.get('anchor_pad')

    tracks = [_load_template_track(t) for t in data.get('tracks', [])]
    vias = [_load_template_via(v) for v in data.get('vias', [])]

    # net_traces tracks MUST carry an explicit absolute layer. Unlike cells:
    # (where layer: None legitimately means "inherit the cell layer"), a net
    # trace has no enclosing cell to inherit from — a silent default to F.Cu
    # would route copper onto the wrong side with no warning (found at review
    # 2026-08-21: net_trace_planner._layer_to_board used to default None ->
    # F.Cu). extract-net always writes the real layer explicitly; a hand-edited
    # record without layer: is a config error, not something to guess.
    for i, t in enumerate(tracks):
        if t.layer not in ('F.Cu', 'B.Cu'):
            raise ValidationError(format_fatal_error(
                _("net_traces track (net {net!r}, index {idx}) has no layer").format(
                    net=net, idx=i),
                [_("extract-net always writes layer: F.Cu or B.Cu explicitly — a "
                   "net trace has no cell to inherit a layer from (unlike cells: "
                   "where layer: null means 'inherit'). If hand-editing, add "
                   "layer: F.Cu or layer: B.Cu to this track")]))

    return NetTrace(
        net=net,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        tracks=tracks,
        vias=vias,
        retired=data.get('retired', False),
        skip=data.get('skip', False),
        comment=data.get('comment'),
    )


_THERMAL_VIA_ARRAY_KNOWN_KEYS = {
    'retired', 'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'anchor_point', 'pad', 'net', 'rows', 'cols', 'margin_mm', 'pattern',
    'drill_mm', 'diameter_mm', 'name', 'skip', 'comment',
}


_CLONE_PLACEMENT_KNOWN_KEYS = {
    'cluster', 'cell', 'xy', 'rotation_deg',
    'nets', 'params', 'net_overrides', 'retired', 'skip', 'ignore_selection',
    'anchor_ref', 'anchor_pad', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'anchor_point', 'layer', 'mirror', 'refs', 'by_selection',
    'sheet', 'name', 'comment',
    'radius_mm', 'angle_deg',
    'side',  # deprecated – recognised separately to give a migration message
    'origin_x_mm', 'origin_y_mm',  # deprecated – recognised to give a migration message
}


# Positional keys that are FORBIDDEN on an Entity by construction — an Entity
# is the "what" of a placement and carries NO position (position lives only in
# a trees: node, kind "placement", or a tree anchor; see
# design_2026_08_30_entity_placement_grammar.md). Their presence is a hard
# load-time fatal, not a silent ignore: it means the author put a position on
# the wrong object.
_ENTITY_FORBIDDEN_KEYS = (
    'xy', 'anchor_ref', 'anchor_role', 'anchor_point',
    'anchor_sheet', 'anchor_cluster', 'anchor_pad',
    'rotation_deg', 'radius_mm', 'angle_deg',
)

_ENTITY_KNOWN_KEYS = {
    'name', 'cell', 'nets', 'params', 'net_overrides',
    'cluster', 'sheet', 'retired', 'skip', 'ignore_selection',
    'by_selection', 'refs', 'layer', 'mirror', 'comment',
}


def _load_entity(data: dict[str, Any]) -> Entity:
    """One entities: entry — the "what" of a placement, WITHOUT position
    (see Entity's docstring in config/models.py). Loader mirrors
    _load_clone_placement's per-field discipline: name/cell required,
    unknown keys fatal, positional keys fatal, by_selection+nets fatal,
    layer value checked."""
    name = data.get('name')
    if not name:
        raise ValidationError(format_fatal_error(
            _("entity without name"),
            [_("every entities entry needs a name: — the identity for --only/"
               "registry and the reference a trees: node (kind 'placement') "
               "points at via its ref")]))
    check_unknown_keys(data, _ENTITY_KNOWN_KEYS,
                       _("unknown fields in entity {name!r}").format(name=name))

    cell = data.get('cell')
    if not cell:
        raise ValidationError(format_fatal_error(
            _("entity {name!r} without cell").format(name=name),
            [_("cell: <name from cells:> is REQUIRED — an Entity is a configured "
               "use of a Cell (the reusable form library), exactly like "
               "ClonePlacement.cell")]))

    for forbidden in _ENTITY_FORBIDDEN_KEYS:
        if forbidden in data:
            raise ValidationError(format_fatal_error(
                _("positional field {field!r} in entity {name!r}").format(
                    field=forbidden, name=name),
                [_("an Entity carries NO position — position lives only in a "
                   "trees: node (kind 'placement') or in a tree anchor. Move "
                   "{field!r} to the tree node / anchor grammar instead")
                 .format(field=forbidden)]))

    nets = data.get('nets', {}) or {}
    by_selection = bool(data.get('by_selection', False))
    if by_selection and nets:
        raise ValidationError(format_fatal_error(
            _("by_selection: true with non-empty nets in entity {name!r}").format(name=name),
            [_("nets is an explicit role->net mapping for 'by nets' mode; in "
               "selection mode roles are resolved by mouse selection, not by "
               "nets — nets is meaningless here. Either remove nets, or remove "
               "by_selection: true")]))

    layer = data.get('layer')
    _check_layer_value(layer, _("in entity {name!r}").format(name=name))

    return Entity(
        name=name,
        cell=cell,
        nets=nets,
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        cluster=data.get('cluster'),
        sheet=data.get('sheet'),
        retired=data.get('retired', False),
        skip=data.get('skip', False),
        ignore_selection=data.get('ignore_selection', False),
        by_selection=by_selection,
        refs=data.get('refs', {}) or {},
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        comment=data.get('comment'),
    )


def _load_clone_placement(data: dict[str, Any]) -> ClonePlacement:
    cluster = data.get('cluster')
    if not cluster:
        raise ValidationError(format_fatal_error(
            _("clone_placement without cluster"),
            [_("every clone_placement must have a cluster – the physical Cluster "
               "tag written onto the board's components (read by role_narrowing.py); "
               "write cluster: <string>. The save/--only identity (name:) is "
               "optional and falls back to cluster")]
        ))
    check_unknown_keys(data, _CLONE_PLACEMENT_KNOWN_KEYS,
                       _("unknown fields in clone_placement {name!r}").format(name=cluster),
                       extra_hint=_(" (e.g. 'pad' won't work; use 'anchor_pad')"))

    anchor_ref = data.get('anchor_ref')
    anchor_pad = data.get('anchor_pad')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_point = data.get('anchor_point')
    # Own-identity sheet (split 2026-08-15 from anchor_sheet — see
    # ClonePlacement's field comment in models.py): narrows ambiguous roles
    # INSIDE the cell, unlike anchor_sheet which narrows only the external
    # anchor. Deliberately NOT resolved through resolve_placeholder (own
    # identity, not a templated external field).
    sheet = data.get('sheet')
    name = data.get('name')

    cell = data.get('cell')
    if not cell:
        raise ValidationError(format_fatal_error(
            _("clone_placement {name!r} without cell").format(name=name),
            [_("cell: <name from cells:> is REQUIRED — the role:/cluster: single-component "
               "modes were migrated to coordinate_placements: (anchor-relative mode) on "
               "2026-08-12; use coordinate_placements: for a single-component placement, "
               "or give this clone_placement a real cell:")]
        ))

    if anchor_ref is not None and anchor_role is not None:
        raise ValidationError(format_fatal_error(
            _("anchor_ref and anchor_role together in clone_placement {name!r}").format(name=name),
            [_("these are mutually exclusive ways to define the anchor – either by refdes "
               "(anchor_ref) or by Role field (anchor_role), not both")]
        ))

    if anchor_sheet is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet without anchor_role in clone_placement {name!r}").format(name=name),
            [_("anchor_sheet={sheet!r} is set but anchor_role is missing – "
               "anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")
             .format(sheet=anchor_sheet)]
        ))

    if anchor_point is not None and (anchor_ref is not None or anchor_role is not None):
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_ref/anchor_role in clone_placement {name!r}")
            .format(name=name),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role").format(point=anchor_point)]
        ))
    if anchor_point is not None and anchor_pad is not None:
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_pad in clone_placement {name!r}").format(name=name),
            [_("anchor_point already resolves to a full position — anchor_pad has no "
               "meaning on top of it; set anchor_pad on the points: entry itself instead")]
        ))

    if anchor_pad is not None and anchor_ref is None and anchor_role is None and anchor_point is None:
        raise ValidationError(format_fatal_error(
            _("anchor_pad without anchor_ref/anchor_role in clone_placement {name!r}").format(name=name),
            [_("anchor_pad={pad!r} is set but no anchor specified – "
               "use anchor_ref: IC1 or anchor_role: SOME_ROLE").format(pad=anchor_pad)]
        ))

    has_anchor = anchor_ref is not None or anchor_role is not None or anchor_point is not None
    # Position — EXACTLY ONE of two mutually exclusive modes, via the shared
    # _load_mutually_exclusive_position: xy (Cartesian) OR polar
    # radius_mm/angle_deg. xy keeps its implicit (0,0) default and stays
    # valid; fatal only when BOTH are set, or when only ONE polar field is
    # (confirmed with Denis 2026-08-12). The both/half fatals live in the
    # shared validator — this block only adds the anchor-less "no absolute
    # position at all" check. Note: xy-activation is by VALUE (is not None),
    # so a degenerate `xy: null` no longer counts as a second active mode
    # next to polar (previously `'xy' in data` fatalled on that too).
    mode = _load_mutually_exclusive_position(
        data,
        [("xy", ("xy",), False),
         ("polar", ("radius_mm", "angle_deg"), True)],
        _("clone_placement {name!r}").format(name=name),
    )
    has_polar = mode == "polar"

    if not has_anchor and 'xy' not in data and not has_polar:
        raise ValidationError(format_fatal_error(
            _("no anchor and no absolute coordinates in clone_placement {name!r}").format(name=name),
            [_("either set xy: [x, y] (absolute point on board), "
               "or radius_mm/angle_deg (polar absolute position), "
               "or anchor_ref/anchor_role (+ optionally anchor_pad), or anchor_point, "
               "for anchor‑based placement")]
        ))

    if 'side' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'side' in clone_placement {name!r}").format(name=name),
            [_("side is now set by an explicit pair: layer: F.Cu|B.Cu (where we place – fact) "
               "+ mirror: true (how we place – operation, only meaningful when the layer changes "
               "relative to the cell)")]
        ))
    if 'origin_x_mm' in data or 'origin_y_mm' in data:
        raise ValidationError(format_fatal_error(
            _("deprecated fields 'origin_x_mm'/'origin_y_mm' in clone_placement {name!r}").format(name=name),
            [_("renamed to xy: [x, y] — write xy: [{x}, {y}] instead")
             .format(x=data.get('origin_x_mm', 0.0), y=data.get('origin_y_mm', 0.0))]
        ))

    xy_raw = data.get('xy')
    if xy_raw is not None:
        if not (isinstance(xy_raw, (list, tuple)) and len(xy_raw) == 2):
            raise ValidationError(format_fatal_error(
                _("xy must be a 2-element [x, y] list in clone_placement {name!r}").format(name=name),
                [_("got: {xy!r}").format(xy=xy_raw)]
            ))
        xy = (float(xy_raw[0]), float(xy_raw[1]))
    else:
        xy = (0.0, 0.0)

    # has_polar guarantees BOTH radius_mm and angle_deg (shared validator).
    if has_polar:
        radius_mm = float(data['radius_mm'])
        angle_deg = float(data['angle_deg'])
    else:
        radius_mm = None
        angle_deg = None

    by_selection = bool(data.get('by_selection', False))
    nets = data.get('nets', {}) or {}
    if by_selection and nets:
        raise ValidationError(format_fatal_error(
            _("by_selection: true with non-empty nets in clone_placement {name!r}").format(name=name),
            [_("nets is an explicit role->net mapping for 'by nets' mode; in selection mode "
               "roles are resolved by mouse selection, not by nets – nets is meaningless here. "
               "Either remove nets, or remove by_selection: true")]
        ))

    layer = data.get('layer')
    _check_layer_value(layer, _("in clone_placement {name!r}").format(name=name))

    return ClonePlacement(
        cluster=cluster,
        cell=cell,
        xy=xy,
        radius_mm=radius_mm,
        angle_deg=angle_deg,
        rotation_deg=data.get('rotation_deg', 0.0),
        nets=nets,
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        retired=data.get('retired', False),
        skip=data.get('skip', False),
        ignore_selection=data.get('ignore_selection', False),
        anchor_ref=anchor_ref,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_role=anchor_role,
        anchor_sheet=anchor_sheet,
        anchor_cluster=anchor_cluster,
        anchor_point=anchor_point,
        sheet=sheet,
        name=name,
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        refs=data.get('refs', {}) or {},
        by_selection=by_selection,
        comment=data.get('comment'),
    )


def _load_thermal_via_array(tva_data: dict[str, Any]) -> ThermalViaArrayConfig:
    """One thermal_via_arrays: entry — split out of load_config()'s loop
    (2026-08-03) so the GUI's ThermalViaArrayDock can validate a single
    entry before writing it, the same way _load_clone_placement (public as
    load_clone_placement) already does for clone_placements. The list-level
    duplicate-name check stays in load_config() — it needs the whole list,
    not a single entry."""
    if 'target_ref' in tva_data:
        raise ValidationError(format_fatal_error(
            _("deprecated field 'target_ref' in thermal_via_arrays"),
            [_("renamed for consistency: use anchor_ref")]
        ))
    if not tva_data.get('name'):
        raise ValidationError(format_fatal_error(
            _("thermal_via_arrays entry without name"),
            [_("every thermal_via_arrays entry must have a name – used in --only "
               "(kicadstamp_cli.py) for isolated runs, and to tell entries apart; write "
               "name: <any understandable string>, e.g. name: fpga_thermal")]
        ))
    check_unknown_keys(tva_data, _THERMAL_VIA_ARRAY_KNOWN_KEYS,
                       _("unknown fields in thermal_via_arrays entry {name!r}")
                       .format(name=tva_data.get('name')))
    if tva_data.get('anchor_point') is not None and (
            tva_data.get('anchor_ref') is not None or tva_data.get('anchor_role') is not None):
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_ref/anchor_role in thermal_via_arrays "
              "entry {name!r}").format(name=tva_data.get('name')),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role")
             .format(point=tva_data.get('anchor_point'))]
        ))
    return ThermalViaArrayConfig(
        retired=tva_data.get('retired', False),
        anchor_ref=tva_data.get('anchor_ref'),
        anchor_role=tva_data.get('anchor_role'),
        anchor_sheet=tva_data.get('anchor_sheet'),
        anchor_cluster=tva_data.get('anchor_cluster'),
        anchor_point=tva_data.get('anchor_point'),
        pad=tva_data.get('pad', ''),
        net=tva_data.get('net', 'GND'),
        rows=tva_data.get('rows', 4),
        cols=tva_data.get('cols', 4),
        margin_mm=tva_data.get('margin_mm', 0.5),
        pattern=tva_data.get('pattern', 'grid'),
        drill_mm=tva_data.get('drill_mm', 0.3),
        diameter_mm=tva_data.get('diameter_mm', 0.5),
        name=tva_data.get('name'),
        skip=tva_data.get('skip', False),
        comment=tva_data.get('comment'),
    )


_COORDINATE_PLACEMENT_KNOWN_KEYS = {
    'cluster', 'role', 'name', 'sheet', 'x_mm', 'y_mm', 'center_x_mm', 'center_y_mm',
    'radius_mm', 'angle_deg', 'rotation_deg', 'anchor', 'anchor_pad',
    'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster', 'anchor_point',
    'retired', 'skip', 'comment',
}


def _load_coordinate_placement(data: dict[str, Any]) -> CoordinatePlacement:
    """One coordinate_placements: entry — the "dumb placer" (see
    CoordinatePlacement's own docstring in config/models.py). Public as
    load_coordinate_placement (config/__init__.py) so the GUI's merged
    PlacerDock coordinate mode can validate a single entry before a
    one-entry save/place, the same way load_clone_placement/
    load_thermal_via_array already let their GUI docks validate a single
    entry before writing.

    Three mutually exclusive position modes (see CoordinatePlacement's
    docstring): Cartesian-absolute, polar-around-fixed-centre, and — added
    2026-08-12 (Group 0 consolidation, migrated 1:1 from ClonePlacement's
    role:/cluster: variant) — anchor-relative. The shared
    _load_mutually_exclusive_position handles each mode-pair; the
    anchor-vs-absolute split is a small gate here because the anchor mode
    REUSES x_mm/y_mm/radius_mm/angle_deg as its OFFSET (mirroring
    ClonePlacement's "absolute without anchor, offset with anchor" duality),
    so the three modes cannot be expressed as one disjoint field-set."""
    label = data.get('name') or f"{data.get('cluster')}/{data.get('role')}"

    cluster = data.get('cluster')
    role = data.get('role')
    # Own-identity sheet (2026-08-15): OPTIONAL narrowing of Cluster+Role to
    # one physical instance when the same sheet is cloned/reused and Cluster
    # alone is identical across copies (Denis, live: AD_DAC/IC2). Distinct
    # from anchor_sheet below — that one narrows the OTHER, anchor component
    # in anchor-relative mode, not this placement's own identity.
    sheet = data.get('sheet')
    if not cluster or not role:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r} missing cluster/role").format(label=label),
            [_("both cluster: <CLUSTER> and role: <ROLE> are required — Role must already be "
               "unique within that Cluster instance for this to resolve to exactly one component")]
        ))

    check_unknown_keys(data, _COORDINATE_PLACEMENT_KNOWN_KEYS,
                       _("unknown fields in coordinate_placements entry {label!r}").format(label=label))

    # ── Anchor-relative vs absolute ─────────────────────────────────────────
    anchor_ref = data.get('anchor_ref')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_point = data.get('anchor_point')

    # Anchor-identity cross-validation — UNCONDITIONAL (same as ClonePlacement):
    # a stray anchor_sheet/anchor_ref/anchor_role/anchor_pad combination is a
    # config error regardless of which position mode is used.
    if anchor_ref is not None and anchor_role is not None:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r}: anchor_ref and anchor_role together")
            .format(label=label),
            [_("these are mutually exclusive ways to define the anchor — either by refdes "
               "(anchor_ref) or by Role field (anchor_role), not both")]
        ))
    if anchor_sheet is not None and anchor_role is None:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r}: anchor_sheet without anchor_role")
            .format(label=label),
            [_("anchor_sheet={sheet!r} is set but anchor_role is missing — anchor_sheet "
               "only narrows ambiguity of anchor_role, it is not an anchor itself")
             .format(sheet=anchor_sheet)]
        ))
    if anchor_point is not None and (anchor_ref is not None or anchor_role is not None):
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r}: anchor_point together with "
              "anchor_ref/anchor_role").format(label=label),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role").format(point=anchor_point)]
        ))
    if anchor_point is not None and data.get('anchor_pad') is not None:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r}: anchor_point together with anchor_pad")
            .format(label=label),
            [_("anchor_point already resolves to a full position — anchor_pad has no "
               "meaning on top of it; set anchor_pad on the points: entry itself instead")]
        ))

    has_anchor = any(v is not None for v in (anchor_ref, anchor_role, anchor_point))

    if has_anchor:
        # ANCHOR-RELATIVE: x_mm/y_mm (Cartesian) or radius_mm/angle_deg (polar)
        # become the OFFSET from the anchor component (or its anchor_pad).
        if any(data.get(k) is not None for k in ('center_x_mm', 'center_y_mm')):
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: anchor together with "
                  "center_x_mm/center_y_mm").format(label=label),
                [_("center_x_mm/center_y_mm only make sense for the fixed-centre polar "
                   "ABSOLUTE mode — with an anchor, radius_mm/angle_deg are already the "
                   "polar offset from that anchor")]
            ))
        if data.get('anchor') == 'pad':
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: anchor: pad together with "
                  "anchor_ref/anchor_role/anchor_point").format(label=label),
                [_("anchor: pad means 'this component's own pad lands on the target' — "
                   "meaningless in anchor-relative mode, where the target already IS "
                   "anchor + offset; drop anchor: pad, and use anchor_pad only for the "
                   "ANCHOR component's pad")]
            ))

        # Offset — Cartesian x_mm/y_mm XOR polar radius_mm/angle_deg. Both are
        # optional as a whole (no offset = exactly on the anchor, like
        # ClonePlacement's anchor-with-no-xy); a half-polar is still fatal.
        offset_mode = _load_mutually_exclusive_position(
            data,
            [("Cartesian", ("x_mm", "y_mm"), False),
             ("polar", ("radius_mm", "angle_deg"), True)],
            _("coordinate_placements entry {label!r} (anchor offset)").format(label=label),
        )
        # rotation_deg stays raw (None when not given) — the calculator
        # resolves the default (angle_deg in polar-offset, 0.0 in Cartesian-
        # offset), the same loader-store/calculator-resolve split as the
        # absolute polar mode.
        if offset_mode == "polar":
            x_mm = y_mm = None
            center_x_mm = center_y_mm = None
            radius_mm = float(data['radius_mm'])
            angle_deg = float(data['angle_deg'])
            rotation_deg = float(data['rotation_deg']) if data.get('rotation_deg') is not None else None
        else:
            x_mm = float(data['x_mm']) if data.get('x_mm') is not None else 0.0
            y_mm = float(data['y_mm']) if data.get('y_mm') is not None else 0.0
            center_x_mm = center_y_mm = radius_mm = angle_deg = None
            rotation_deg = float(data['rotation_deg']) if data.get('rotation_deg') is not None else None

        # The self-referential `anchor` concept has no meaning here (target IS
        # "anchor + offset"); anchor_pad instead names the ANCHOR component's pad.
        # (anchor_pad-without-a-footprint-anchor can't happen here: reaching this
        # branch means ref/role/point is set, and point+pad was already rejected
        # unconditionally above — so pad here always has a footprint anchor.)
        anchor = 'center'
        anchor_pad = data.get('anchor_pad')
    else:
        # ABSOLUTE modes — Cartesian x_mm/y_mm XOR fixed-centre polar.
        mode = _load_mutually_exclusive_position(
            data,
            [("Cartesian", ("x_mm", "y_mm"), True),
             ("polar", ("center_x_mm", "center_y_mm", "radius_mm", "angle_deg"), True)],
            _("coordinate_placements entry {label!r}").format(label=label),
        )
        if mode == "Cartesian":
            rotation_deg_raw = data.get('rotation_deg')
            if rotation_deg_raw is None:
                raise ValidationError(format_fatal_error(
                    _("coordinate_placements entry {label!r}: Cartesian mode needs an explicit "
                      "rotation_deg").format(label=label),
                    [_("there is no polar angle to fall back on in Cartesian mode — set "
                       "rotation_deg: 0 explicitly if no rotation is wanted")]
                ))
            x_mm, y_mm = float(data['x_mm']), float(data['y_mm'])
            center_x_mm = center_y_mm = radius_mm = angle_deg = None
            rotation_deg = float(rotation_deg_raw)
        elif mode == "polar":
            x_mm = y_mm = None
            center_x_mm, center_y_mm = float(data['center_x_mm']), float(data['center_y_mm'])
            radius_mm, angle_deg = float(data['radius_mm']), float(data['angle_deg'])
            # Stored raw (None if not given) — angle_deg becomes rotation by
            # default only in the calculator (resolve_target_position).
            rotation_deg = float(data['rotation_deg']) if data.get('rotation_deg') is not None else None
        else:
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r} has no position").format(label=label),
                [_("set either x_mm/y_mm (Cartesian), center_x_mm/center_y_mm/radius_mm/angle_deg "
                   "(polar), or anchor_ref/anchor_role/anchor_point (anchor-relative)")]
            ))

        anchor = data.get('anchor', 'center')
        if anchor not in ('center', 'pad'):
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: anchor must be 'center' or 'pad', got {anchor!r}")
                .format(label=label, anchor=anchor),
                []
            ))
        anchor_pad = data.get('anchor_pad')
        if anchor == 'pad' and anchor_pad is None:
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: anchor: pad needs anchor_pad").format(label=label),
                [_("set anchor_pad: <pad number> — which pad of THIS component should land on the "
                   "target point")]
            ))
        if anchor == 'center' and anchor_pad is not None:
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: anchor_pad set but anchor is 'center'")
                .format(label=label),
                [_("anchor_pad only has meaning with anchor: pad — either add that, or remove anchor_pad")]
            ))

    return CoordinatePlacement(
        cluster=cluster,
        role=role,
        sheet=sheet,
        name=data.get('name'),
        x_mm=x_mm, y_mm=y_mm,
        center_x_mm=center_x_mm, center_y_mm=center_y_mm,
        radius_mm=radius_mm, angle_deg=angle_deg,
        rotation_deg=rotation_deg,
        anchor=anchor,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        anchor_ref=anchor_ref, anchor_role=anchor_role,
        anchor_sheet=anchor_sheet, anchor_cluster=anchor_cluster,
        anchor_point=anchor_point,
        retired=data.get('retired', False),
        skip=data.get('skip', False),
        comment=data.get('comment'),
    )


# tree_instances: — short sheet-/cluster-parameterized references to a template
# tree (2026-09-02, plan tree_instances; cluster axis added 2026-09-03, plan
# tree_instances_cluster). Three required string fields (template/name/sheet)
# plus an OPTIONAL cluster override; the actual expansion into full Tree+Entity
# dicts happens dict-level in config/tree_instances.py (expand_tree_instances,
# BEFORE these loaders run); this loader only parses the DECLARATION into
# cfg.tree_instances — the GUI's read-only-instance index and the persistence
# source. Missing fields here are fatal with the same discipline as every other
# list-section record.
_TREE_INSTANCE_KNOWN_KEYS = {"template", "name", "sheet", "cluster"}


def _load_tree_instance(data: dict[str, Any]) -> TreeInstance:
    """One tree_instances: entry -> TreeInstance (a declaration, NOT the
    materialized tree). All three fields (template/name/sheet) are required and
    must be non-empty strings: expansion needs template (which tree to copy),
    name (the generated tree's unique name) and sheet (what gets substituted).
    `cluster` is OPTIONAL (2026-09-03): when present it must be a non-empty
    string — it is substituted into the generated Entity copies' `cluster`
    (and the role-anchor cluster); when absent (None) the generated copies
    inherit the template's own cluster unchanged (today's behaviour)."""
    if not isinstance(data, dict):
        raise ValidationError(format_fatal_error(
            _("tree_instances: entry must be a mapping, got {type}")
            .format(type=type(data).__name__),
            [_("a tree_instances: entry is a dict with 'template'/'name'/'sheet'")]))
    template = data.get('template')
    name = data.get('name')
    sheet = data.get('sheet')
    for field_label, value in (("template", template), ("name", name), ("sheet", sheet)):
        if not isinstance(value, str) or not value:
            raise ValidationError(format_fatal_error(
                _("tree_instances: entry missing required {field}:").format(field=field_label),
                [_("every tree_instances: entry needs template:/name:/sheet: (all "
                   "non-empty strings) — template names the source tree, name is the "
                   "generated tree's name, sheet is substituted into the copies")]))
    cluster = data.get('cluster')
    if cluster is not None and (not isinstance(cluster, str) or not cluster):
        raise ValidationError(format_fatal_error(
            _("tree_instances: entry {name!r} has an empty cluster:").format(name=name),
            [_("cluster:, when present, must be a non-empty string — omit the key "
               "entirely to inherit the template's own cluster unchanged")]))
    check_unknown_keys(data, _TREE_INSTANCE_KNOWN_KEYS,
                       _("unknown fields in tree_instances entry {name!r}")
                       .format(name=name))
    return TreeInstance(template=template, name=name, sheet=sheet, cluster=cluster)


# trees: — optional curated-redraw list section (design_2026_08_27_trees_in_
# config_file.md). The dict shape is the same plain-dict that sexp_format.py
# / yaml.safe_load produce for the trees: section; _load_tree wraps the
# already-tested trees.py::tree_from_dict (the dict bridge) with the config's
# usual known-key discipline and fatal formatting.
_TREE_KNOWN_KEYS = {"name", "anchor", "nodes"}
# Anchor grammar v2 (design_2026_08_30_entity_placement_grammar.md §2.2.3):
# origin / ref(+external) / role(+sheet/cluster/pad) / point.
_TREE_ANCHOR_KNOWN_KEYS = {"ref", "origin", "external", "role", "point",
                           "sheet", "cluster", "pad"}
_TREE_NODE_KNOWN_KEYS = {"ref", "kind", "xy", "polar", "rotation", "name", "group", "children",
                         "pivot_xy", "pivot_polar"}


def _check_tree_node_keys(data: Any, label: str) -> None:
    """Recursively fatal on an unknown key anywhere in a tree's node subtree
    (nodes -> nested children) — same check_unknown_keys discipline every
    other config record has."""
    if not isinstance(data, dict):
        return
    check_unknown_keys(data, _TREE_NODE_KNOWN_KEYS,
                       _("unknown fields in {label}").format(label=label))
    for child in data.get("children", []) or []:
        _check_tree_node_keys(child, f"{label} node")


def _load_tree(data: dict[str, Any], seen_refs: set[str] | None = None) -> Tree:
    """One trees: entry -> Tree. seen_refs (optional, shared across the whole
    include graph by load_config) enforces node-ref uniqueness across files.

    Raises ValidationError (via format_fatal_error) on any structural
    violation — a hand-authored tree fails loudly at load, same discipline as
    the other per-entry loaders."""
    if not isinstance(data, dict):
        raise ValidationError(format_fatal_error(
            _("trees: entry must be a mapping, got {type}").format(type=type(data).__name__),
            [_("a trees: entry is a dict with 'name'/'anchor'/'nodes'")]))

    check_unknown_keys(data, _TREE_KNOWN_KEYS,
                       _("unknown fields in trees entry {name!r}")
                       .format(name=data.get("name", "?")))
    anchor = data.get("anchor")
    if isinstance(anchor, dict):
        check_unknown_keys(anchor, _TREE_ANCHOR_KNOWN_KEYS,
                           _("unknown fields in tree anchor {name!r}")
                           .format(name=data.get("name", "?")))
    for i, node in enumerate(data.get("nodes", []) or []):
        _check_tree_node_keys(node, f"tree {data.get('name', '?')} node {i}")

    try:
        return tree_from_dict(data, seen_refs=seen_refs)
    except ValidationError as e:
        raise ValidationError(format_fatal_error(
            _("invalid trees entry {name!r}").format(name=data.get("name", "?")),
            [str(e)])) from e
