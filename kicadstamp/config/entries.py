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
from .models import (
    ThermalViaArrayConfig, TemplateVia, TemplateComponentSlot, TemplateTrack,
    Cell, CellPlacement, ManualSpoke, Rule, ClonePlacement, CoordinatePlacement,
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
    return TemplateComponentSlot(
        role=data['role'],
        offset_along_mm=data.get('offset_along_mm', 0.0),
        offset_across_mm=data.get('offset_across_mm', 0.0),
        angle_deg=data.get('angle_deg', 0.0),
        vias=[_load_template_via(v) for v in data.get('vias', [])],
        net_template=data.get('net_template'),
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
    )


_CELL_PLACEMENT_KNOWN_KEYS = {
    'name', 'cell', 'role', 'xy', 'rotation_deg', 'mirror', 'layer',
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
        nets=data.get('nets', {}) or {},
        params=data.get('params', {}) or {},
        net_overrides=data.get('net_overrides', {}) or {},
        refs=data.get('refs', {}) or {},
    )


_POINT_KNOWN_KEYS = {
    'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster', 'anchor_pad',
    'anchor_point', 'xy', 'anchor_origin', 'shift_x_mm', 'shift_y_mm',
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


_MANUAL_SPOKE_KNOWN_KEYS = {
    'pad', 'cell', 'shift_x_mm', 'shift_y_mm', 'rotation_deg',
    'retired', 'cluster', 'skip',
}


def _load_manual_spoke(data: dict[str, Any], rule_label: str) -> ManualSpoke:
    check_unknown_keys(data, _MANUAL_SPOKE_KNOWN_KEYS,
                       _("unknown fields in spoke (pad {pad!r}) of rule (net {net!r})")
                       .format(pad=data.get('pad', '?'), net=rule_label))
    return ManualSpoke(
        pad=data['pad'],
        cell=data['cell'],
        shift_x_mm=data.get('shift_x_mm', 0.0),
        shift_y_mm=data.get('shift_y_mm', 0.0),
        rotation_deg=data.get('rotation_deg', 0.0),
        retired=data.get('retired', False),
        cluster=data.get('cluster'),
        skip=data.get('skip', False),
    )


_RULE_KNOWN_KEYS = {
    'net', 'spokes', 'anchor_ref', 'anchor_role', 'anchor_sheet',
    'anchor_cluster', 'anchor_point', 'name', 'retired', 'skip',
}


def _load_rule(rule_data: dict[str, Any]) -> Rule:
    """Extracted 2026-08-05 from load_config's own inline loop (same
    standalone-per-entry shape as _load_point/_load_thermal_via_array/
    _load_clone_placement) so gui/docks/rules.py can validate a single Rule
    the same clean way those docks already validate their own entry type.
    The cross-rule name/net collision check stays in load_config — it
    needs the WHOLE rules list, not just one entry."""
    rule_net = rule_data.get('net')
    check_unknown_keys(rule_data, _RULE_KNOWN_KEYS,
                       _("unknown fields in rule (net {net!r})").format(net=rule_net))
    anchor_ref = rule_data.get('anchor_ref')
    anchor_role = rule_data.get('anchor_role')
    anchor_sheet = rule_data.get('anchor_sheet')
    anchor_cluster = rule_data.get('anchor_cluster')
    anchor_point = rule_data.get('anchor_point')

    if anchor_ref and anchor_role:
        raise ValidationError(format_fatal_error(
            _("anchor_ref and anchor_role together in rule (net {net!r})").format(net=rule_net),
            [_("mutually exclusive: either by refdes (anchor_ref) or by Role field "
               "(anchor_role), not both")]
        ))
    if anchor_sheet and not anchor_role:
        raise ValidationError(format_fatal_error(
            _("anchor_sheet without anchor_role in rule (net {net!r})").format(net=rule_net),
            [_("anchor_sheet only narrows ambiguity of anchor_role, it is not an anchor itself")]
        ))
    if anchor_point and (anchor_ref or anchor_role):
        raise ValidationError(format_fatal_error(
            _("anchor_point together with anchor_ref/anchor_role in rule (net {net!r})")
            .format(net=rule_net),
            [_("anchor_point={point!r} names a points: entry that already carries its own "
               "anchor — mutually exclusive with anchor_ref/anchor_role").format(point=anchor_point)]
        ))
    if not anchor_ref and not anchor_role and not anchor_point:
        raise ValidationError(format_fatal_error(
            _("rule (net {net!r}) without anchor_ref/anchor_role/anchor_point").format(net=rule_net),
            [_("a spoke rule must have an anchor – anchor_ref: <ref> (component whose "
               "pads are listed in spokes), anchor_role: <ROLE> (survives re‑annotation), "
               "or anchor_point: <name from points:>")]
        ))
    spokes = [_load_manual_spoke(spoke_data, rule_net) for spoke_data in rule_data.get('spokes', [])]
    return Rule(net=rule_net, spokes=spokes, anchor_ref=anchor_ref,
               anchor_role=anchor_role, anchor_sheet=anchor_sheet,
               anchor_cluster=anchor_cluster, anchor_point=anchor_point,
               name=rule_data.get('name'),
               retired=rule_data.get('retired', False),
               skip=rule_data.get('skip', False))


_THERMAL_VIA_ARRAY_KNOWN_KEYS = {
    'retired', 'anchor_ref', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'anchor_point', 'pad', 'net', 'rows', 'cols', 'margin_mm', 'pattern',
    'drill_mm', 'diameter_mm', 'name', 'skip',
}


_CLONE_PLACEMENT_KNOWN_KEYS = {
    'name', 'cell', 'role', 'cluster', 'xy', 'rotation_deg',
    'nets', 'params', 'net_overrides', 'retired', 'skip', 'ignore_selection',
    'anchor_ref', 'anchor_pad', 'anchor_role', 'anchor_sheet', 'anchor_cluster',
    'anchor_point', 'layer', 'mirror', 'refs', 'by_selection',
    'side',  # deprecated – recognised separately to give a migration message
    'origin_x_mm', 'origin_y_mm',  # deprecated – recognised to give a migration message
}


def _load_clone_placement(data: dict[str, Any]) -> ClonePlacement:
    name = data.get('name', '?')
    if not data.get('name'):
        raise ValidationError(format_fatal_error(
            _("clone_placement without name"),
            [_("every clone_placement must have a name – used in --only "
               "(kicadstamp_cli.py) for isolated runs; write name: <string>. "
               "Previously missing name would silently substitute '?' – that was a bug")]
        ))
    check_unknown_keys(data, _CLONE_PLACEMENT_KNOWN_KEYS,
                       _("unknown fields in clone_placement {name!r}").format(name=name),
                       extra_hint=_(" (e.g. 'pad' won't work; use 'anchor_pad')"))

    anchor_ref = data.get('anchor_ref')
    anchor_pad = data.get('anchor_pad')
    anchor_role = data.get('anchor_role')
    anchor_sheet = data.get('anchor_sheet')
    anchor_cluster = data.get('anchor_cluster')
    anchor_point = data.get('anchor_point')

    cell = data.get('cell')
    role = data.get('role')
    cluster = data.get('cluster')
    _content_count = sum(x is not None for x in (cell, role, cluster))
    if _content_count > 1:
        raise ValidationError(format_fatal_error(
            _("more than one of cell/role/cluster set in clone_placement {name!r}").format(name=name),
            [_("these are mutually exclusive ways to define the content: a ready-made cell "
               "(cell), a single-component placement by Role field (role), or a "
               "single-component placement by an existing Cluster field (cluster) — "
               "pick exactly one")]
        ))
    if _content_count == 0:
        raise ValidationError(format_fatal_error(
            _("neither cell, role, nor cluster set in clone_placement {name!r}").format(name=name),
            [_("need cell: <name from cells:> (ready-made cell), role: <ROLE> (single component "
               "found by its Role field), or cluster: <CLUSTER> (single component found by an "
               "already-assigned Cluster field)")]
        ))
    if cluster is not None and (data.get('nets') or data.get('params') or data.get('by_selection')):
        raise ValidationError(format_fatal_error(
            _("cluster together with nets/params/by_selection in clone_placement {name!r}").format(name=name),
            [_("cluster: finds its single target by an exact, already-unique Cluster field match — "
               "nets/params/by_selection (selection-vs-nets role resolution) have no meaning here, "
               "remove them")]
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

    if not has_anchor and 'xy' not in data:
        raise ValidationError(format_fatal_error(
            _("no anchor and no absolute coordinates in clone_placement {name!r}").format(name=name),
            [_("either set xy: [x, y] (absolute point on board), "
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
        name=name,
        cell=cell,
        role=role,
        cluster=cluster,
        xy=xy,
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
        layer=layer,
        mirror=bool(data.get('mirror', False)),
        refs=data.get('refs', {}) or {},
        by_selection=by_selection,
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
    )


_COORDINATE_PLACEMENT_KNOWN_KEYS = {
    'cluster', 'role', 'name', 'x_mm', 'y_mm', 'center_x_mm', 'center_y_mm',
    'radius_mm', 'angle_deg', 'rotation_deg', 'anchor', 'anchor_pad',
    'retired', 'skip',
}


def _load_coordinate_placement(data: dict[str, Any]) -> CoordinatePlacement:
    """One coordinate_placements: entry — the "dumb placer" (see
    CoordinatePlacement's own docstring in config/models.py). Public as
    load_coordinate_placement (config/__init__.py) so the GUI's
    CoordinatePlacerDock can validate one row before writing the whole
    table, the same way load_clone_placement/load_thermal_via_array already
    let their GUI docks validate a single entry before a full-file write."""
    label = data.get('name') or f"{data.get('cluster')}/{data.get('role')}"

    cluster = data.get('cluster')
    role = data.get('role')
    if not cluster or not role:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r} missing cluster/role").format(label=label),
            [_("both cluster: <CLUSTER> and role: <ROLE> are required — Role must already be "
               "unique within that Cluster instance for this to resolve to exactly one component")]
        ))

    check_unknown_keys(data, _COORDINATE_PLACEMENT_KNOWN_KEYS,
                       _("unknown fields in coordinate_placements entry {label!r}").format(label=label))

    has_cartesian = data.get('x_mm') is not None or data.get('y_mm') is not None
    has_polar = any(data.get(k) is not None for k in
                    ('center_x_mm', 'center_y_mm', 'radius_mm', 'angle_deg'))
    if has_cartesian and has_polar:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r} has both Cartesian (x_mm/y_mm) and polar "
              "(center_x_mm/center_y_mm/radius_mm/angle_deg) fields").format(label=label),
            [_("these are mutually exclusive position modes — pick exactly one")]
        ))
    if has_cartesian:
        if data.get('x_mm') is None or data.get('y_mm') is None:
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: Cartesian mode needs BOTH x_mm and y_mm")
                .format(label=label),
                [_("got x_mm={x!r}, y_mm={y!r}").format(x=data.get('x_mm'), y=data.get('y_mm'))]
            ))
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
    elif has_polar:
        missing = [k for k in ('center_x_mm', 'center_y_mm', 'radius_mm', 'angle_deg')
                  if data.get(k) is None]
        if missing:
            raise ValidationError(format_fatal_error(
                _("coordinate_placements entry {label!r}: polar mode needs all four of "
                  "center_x_mm/center_y_mm/radius_mm/angle_deg").format(label=label),
                [_("missing: {missing}").format(missing=missing)]
            ))
        x_mm = y_mm = None
        center_x_mm, center_y_mm = float(data['center_x_mm']), float(data['center_y_mm'])
        radius_mm, angle_deg = float(data['radius_mm']), float(data['angle_deg'])
        rotation_deg = float(data['rotation_deg']) if data.get('rotation_deg') is not None else None
    else:
        raise ValidationError(format_fatal_error(
            _("coordinate_placements entry {label!r} has no position").format(label=label),
            [_("set either x_mm/y_mm (Cartesian) or center_x_mm/center_y_mm/radius_mm/angle_deg "
               "(polar)")]
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
        name=data.get('name'),
        x_mm=x_mm, y_mm=y_mm,
        center_x_mm=center_x_mm, center_y_mm=center_y_mm,
        radius_mm=radius_mm, angle_deg=angle_deg,
        rotation_deg=rotation_deg,
        anchor=anchor,
        anchor_pad=str(anchor_pad) if anchor_pad is not None else None,
        retired=data.get('retired', False),
        skip=data.get('skip', False),
    )
