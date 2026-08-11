# kicadstamp/template_extraction.py
"""
template_extraction.py — extracts a spoke cell from the current selection
on the board (not from sheet_path/schematic hierarchy — we decided that
selection is more reliable and independent of hierarchical sheets).

Algorithm:
  1. Selection (expanding Groups, see adapter.get_selected_items()) is split
     into footprints, vias, and tracks; everything else is ignored.
  2. origin = lower‑left corner of the selection bounding box
     (min_x, max_y) — in KiCad's native coordinates this is visually the
     lower‑left corner because Y grows downward.
  3. Each footprint: along/across = its current position MINUS origin,
     angle as‑is (the current selection state is the "reference at rotation_deg=0",
     no separate recalculation needed).
  4. Each via: same formula, but WITHOUT a role — vias have no user fields,
     so it is impossible to automatically determine "which" component it belongs
     to; all extracted vias always go into the spoke‑level vias list (not inside
     a specific component slot). The user can manually move vias into
     components[i].vias in the resulting YAML if needed.
  5. Each SELECTED track is included ONLY IF BOTH its ends match (within
     POSITION_TOLERANCE_MM) something else in the selection — a pad of a
     selected component, a selected via, or the end of another selected track
     (for butt‑joints without a via). A track whose end goes "nowhere" (e.g.,
     a long track to +3V3 sticking out of the intended area) is skipped with
     a warning, rather than included as‑is: KiCad can select such a track
     entirely, even if it physically extends far beyond what we actually wanted
     to copy.

Roles (Role field) MUST be unique within the selection — fatal error at
extraction time, not only during later cell loading.

Implementation notes (T3.1 god-file decomposition): this module is now the
thin orchestration layer — the pure selection-geometry helpers live in
template_selection.py and the YAML comment renderer in
template_extraction_render.py. Both public names stay importable from here:
extract_template_from_selection (defined below) and render_uncertain_comments
(re-exported).
"""
import logging
from typing import Any

from kipy.board_types import FootprintInstance, Via, Track, BoardLayer

from .constants import ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .net_resolution import parametrize_net
from .placement.services.net_from_role_resolver import classify_net
from .utils.units import MM
from .i18n import _
from .template_selection import _find_origin, _filter_tracks_within_selection
from .template_extraction_render import render_uncertain_comments  # noqa: F401  (re-export, see module docstring)

logger = logging.getLogger(__name__)


def _selection_role_nets(adapter, footprints) -> dict[str, dict[str, set[str]]]:
    """{role: {pad: {nets}}} for the current selection — each footprint's Role
    field and its REAL pad nets. Used for net_from_role auto-suggestion during
    extract (plan step 4): the classifier then decides whether a via/track's
    net maps unambiguously to one selected role.
    """
    role_nets: dict[str, dict[str, set[str]]] = {}
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            continue  # already a fatal problem collected separately
        pads: dict[str, set[str]] = {}
        try:
            fp_pads = list(adapter.get_footprint_pads(fp))
        except TypeError:
            # Bare test mocks (and any adapter lacking pad data) expose a
            # non-iterable here; skip the footprint — it simply contributes no
            # role->net evidence, and extract falls back to the existing
            # literal/parametrize path for anything it cannot classify.
            continue
        for pad_idx, p in enumerate(fp_pads):
            if not p.net or not p.net.name:
                continue
            pad_num = getattr(p, "number", None)
            pads.setdefault(str(pad_num if pad_num is not None else pad_idx + 1),
                            set()).add(p.net.name)
        role_nets[role] = pads
    return role_nets


def _suggest_net_from_role(role_nets, net, rule_nets, points, components):
    """Try net_from_role auto-suggestion for a via/track's live net.

    Returns (role, pad_or_None) when the net maps unambiguously to a single
    selected role (lemma 2, or an explicit pad for a multi-net role, resolved
    geometrically when |R(n)| > 1); otherwise (None, None) — the caller keeps
    the existing literal/parametrize behavior exactly.
    """
    if not role_nets or net is None:
        return None, None
    try:
        role, pad = classify_net(role_nets, net, set(role_nets), rule_nets,
                                 points=points, components=components,
                                 use_geometry=True)
    except ValidationError:
        return None, None
    return role, pad


def extract_template_from_selection(
    adapter: KiCadBoardAdapter,
    name: str,
    params: dict[str, Any] | None = None,
    net_template_map: dict[str, str] | None = None,
    origin_via_net: str | None = None,
    origin_component_role: str | None = None,
    origin_component_pad: str | None = None,
    net_template_role: dict[str, str] | None = None,
    rule_nets: set[str] | None = None,
    items: list[Any] | None = None,
    annotations: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """
    Builds a dict {name: {vias: [...], components: [...], tracks: [...]}}
    ready to be written to YAML under the 'cells' key. Fatal (ValidationError)
    if: nothing suitable is selected, a selected component has no Role field,
    or a role appears twice in the selection.

    items — OPTIONAL explicit list of FootprintInstance/Via/Track (same shape
    adapter.get_selected_items() returns). None (default) — live GUI
    selection, unchanged from before. Explicit — the caller (e.g. a script
    using kicadstamp.explore.Board.select_items()) fully describes what to
    extract instead of requiring a mouse selection in KiCad. Deliberately an
    explicit parameter, not inferred from whether anything is currently
    selected — same principle as ClonePlacement.by_selection (see
    config/models.py): an implicit mode switch here would risk silently
    extracting the wrong thing if a stale selection happens to be present.

    params/net_template_map — both optional and only work as a pair
    (see --param/--net-template in kicadstamp_cli.py): net_template_map is an
    explicit literal‑to‑pattern mapping written once by the user at extraction;
    params are the values that will later resolve the pattern at apply time,
    used here ONLY for verification (see net_resolution.parametrize_net).
    Without net_template_map behaviour is unchanged: via.net stays literal,
    role net_template stays empty.

    origin_via_net/origin_component_role — both optional, mutually exclusive
    (see --origin-by-via-net/--origin-by-component-role in CLI).
    Without them origin is the selection bbox. With them origin is taken from
    the current position of the specific via/component. origin_component_pad is
    ONLY a refinement of origin_component_role (see --origin-by-component-pad):
    without it origin is the component centre, with it the position of the
    specific pad (same principle as anchor_pad in ClonePlacement).

    net_template_role — OPTIONAL, {role: literal_net} (see
    --net-template-role in CLI). Needed only for components with MULTIPLE nets
    from net_template_map on their pads (inductors/ferrite beads/fuses bridging
    two rails) — for those the auto‑inference below cannot choose which net is
    "the role's" (see warning about "N nets from --net-template on pads"), and
    without this parameter net_template remains empty until manual YAML editing.
    No guessing here either: if the role is in net_template_role but the
    specified net is not actually on the component's pads — fatal, not silent.

    rule_nets — OPTIONAL (see --rule-net in CLI), a set of literal net names
    to write as via.net/track.net: null instead of the literal (or an alias)
    — the SAME null a ManualSpoke-placed cell's via/track already treats as
    "inherit the enclosing Rule's own net" (kicadstamp/geometry/
    spoke_layout.py's _resolve_via/_resolve_track: `via.net or rule_net`).
    Only meaningful for a cell meant to be reused across several Rules with
    DIFFERENT nets (e.g. the same decoupling-cap-pair cell placed once per
    power rail) — component net_template is untouched by this (ManualSpoke
    placement never reads it, only ClonePlacement does). Fatal if a net is in
    BOTH rule_nets and net_template_map/params — a net cannot be simultaneously
    "always the rule's own net" and "always resolved from this specific param".

    annotations — OPTIONAL output parameter (list appended to in place, same
    "explicit opt-in" shape as items above). When given, every case where
    net_template could not be determined unambiguously (see "N nets from
    --net-template on pads" warning below) also appends a
    (role, field_name, hint) tuple, so the caller (kicadstamp_cli.py's
    cmd_extract) can render it as a commented placeholder line in the
    written YAML via render_uncertain_comments() instead of leaving the gap
    only visible in the log.
    """
    params = params or {}
    net_template_role = net_template_role or {}
    net_template_map = dict(net_template_map or {})
    rule_nets = set(rule_nets or ())
    # Auto‑inference for the simple case: if the literal net name EQUALS a
    # param value exactly (not part of a longer string), net_template can be
    # derived automatically — no need for explicit --net-template.
    # Explicit net_template_map entries always take priority.
    for key, value in params.items():
        if value not in net_template_map:
            net_template_map[value] = f"{{{key}}}"

    both = rule_nets & set(net_template_map)
    if both:
        raise ValidationError(format_fatal_error(
            _("net(s) {nets} are in both --rule-net and --param/--net-template")
            .format(nets=sorted(both)),
            [_("a net can't be both \"always the enclosing Rule's own net\" and "
               "\"always resolved from this param\" — pick one per net")]
        ))
    items = items if items is not None else adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, FootprintInstance)]
    vias = [i for i in items if isinstance(i, Via)]
    tracks_selected = [i for i in items if isinstance(i, Track)]
    ignored = [i for i in items if not isinstance(i, (FootprintInstance, Via, Track))]

    if ignored:
        logger.warning(_("{count} selected objects — not footprint, via, or track, "
                         "ignored (cell only supports these)").format(count=len(ignored)))

    tracks = _filter_tracks_within_selection(tracks_selected, footprints, vias, adapter) \
        if tracks_selected else []
    if len(tracks) < len(tracks_selected):
        logger.info(_("Tracks in selection: {total}, taken into cell: {kept} "
                      "(the rest extend beyond the selection, see warning above)")
                    .format(total=len(tracks_selected), kept=len(tracks)))

    if not footprints and not vias and not tracks:
        raise ValidationError(format_fatal_error(
            _("nothing to extract"),
            [_("Nothing is selected (or selected objects are not footprints/vias/tracks) — "
               "select the desired board area in KiCad before running")]
        ))

    problems: list[str] = []
    roles_seen: dict[str, str] = {}
    for fp in footprints:
        ref = fp.reference_field.text.value
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(_("{ref}: no {field!r} field — every selected component "
                              "must have a Role for template extraction")
                            .format(ref=ref, field=ROLE_FIELD_NAME))
            continue
        if role in roles_seen:
            problems.append(_("role {role!r} appears twice in selection: "
                              "{ref1!r} and {ref2!r} — roles must be unique")
                            .format(role=role, ref1=roles_seen[role], ref2=ref))
            continue
        roles_seen[role] = ref

    if problems:
        raise ValidationError(format_fatal_error(_("problems in current selection"), problems))

    origin = _find_origin(footprints, vias, origin_via_net, origin_component_role,
                          origin_component_pad, adapter)
    origin_desc = (_("via on net {net!r}") if origin_via_net
                   else _("component with role {role!r}") if origin_component_role
                   else _("bbox of selection (lower‑left corner)"))
    origin_desc = origin_desc.format(net=origin_via_net, role=origin_component_role) if '{' in origin_desc else origin_desc
    logger.info(_("Origin ({desc}): ({x:.3f}, {y:.3f}) mm")
                .format(desc=origin_desc, x=origin.x/MM, y=origin.y/MM))

    # Layers — FACT, absolute: cell layer = majority layer of selection,
    # components on it inherit without a field, deviating ones get an explicit
    # layer. No relative sides.
    back_count = sum(1 for fp in footprints if fp.layer == BoardLayer.BL_B_Cu)
    tpl_is_back = back_count > len(footprints) / 2
    tpl_layer_str = 'B.Cu' if tpl_is_back else 'F.Cu'
    tpl_layer = BoardLayer.BL_B_Cu if tpl_is_back else BoardLayer.BL_F_Cu
    if 0 < back_count < len(footprints):
        logger.info(_("Mixed selection: {back} on B.Cu, {front} on F.Cu; cell layer = {layer}, "
                      "deviating components will have explicit layer")
                    .format(back=back_count, front=len(footprints)-back_count, layer=tpl_layer_str))
    logger.info(_("Cell layer: {layer}").format(layer=tpl_layer_str))

    components = []
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        along_mm = round((fp.position.x - origin.x) / MM, 4)
        across_mm = round((fp.position.y - origin.y) / MM, 4)
        slot = {
            "role": role,
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "angle_deg": fp.orientation.degrees,
        }
        if fp.layer != tpl_layer:
            slot["layer"] = 'F.Cu' if fp.layer == BoardLayer.BL_F_Cu else 'B.Cu'

        if role in net_template_role:
            literal = net_template_role[role]
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            if literal not in fp_nets:
                raise ValidationError(format_fatal_error(
                    _("--net-template-role for role {role!r} asks for net {literal!r}, "
                      "but it is not on any pad of {ref}").format(role=role, literal=literal,
                                                                   ref=fp.reference_field.text.value),
                    [_("actual nets on pads: {nets} — check typo in "
                       "--net-template-role or in the role itself").format(nets=fp_nets)]
                ))
            if literal not in net_template_map:
                raise ValidationError(format_fatal_error(
                    _("--net-template-role for role {role!r} asks for net {literal!r}, "
                      "which is not in net_template_map").format(role=role, literal=literal),
                    [_("add {literal!r} to --net-template/net_template (or to params "
                       "if it equals a parameter value) — otherwise there is no pattern to build")
                     .format(literal=literal)]
                ))
            slot["net_template"] = parametrize_net(literal, net_template_map, params)
        elif net_template_map:
            fp_nets = sorted({p.net.name for p in adapter.get_footprint_pads(fp)
                              if p.net and p.net.name})
            mapped = [n for n in fp_nets if n in net_template_map]
            if len(mapped) == 1:
                slot["net_template"] = parametrize_net(mapped[0], net_template_map, params)
            elif len(mapped) > 1:
                hint = _("could not determine automatically — {count} matching nets on pads "
                         "({nets}) — fill in manually or use --net-template-role {role}=<net>") \
                    .format(count=len(mapped), nets=mapped, role=role)
                logger.warning(_("  {ref} (role {role}): {count} nets from --net-template on pads "
                                 "({nets}) — net_template not set, fill it manually in the "
                                 "resulting YAML, or use --net-template-role {role}=<net> in advance")
                               .format(ref=fp.reference_field.text.value, role=role,
                                       count=len(mapped), nets=mapped))
                if annotations is not None:
                    annotations.append((role, "net_template", hint))
        components.append(slot)
        logger.debug(_("  {ref} (role {role}): along={along}, across={across}, angle={angle}{layer}{net}")
                     .format(ref=fp.reference_field.text.value, role=role,
                             along=along_mm, across=across_mm, angle=fp.orientation.degrees,
                             layer=_(", layer={layer}").format(layer=slot.get('layer')) if 'layer' in slot else "",
                             net=_(", net_template={nt}").format(nt=slot.get('net_template')) if 'net_template' in slot else ""))

    # Live role -> {pad: {nets}} of the selection, for net_from_role
    # auto-suggestion below (plan step 4). Built once, used by both via and
    # track loops; empty when the selection has no role/net evidence, in which
    # case extract behaves exactly as before.
    selection_role_nets = _selection_role_nets(adapter, footprints)

    spoke_vias = []
    for v in vias:
        along_mm = round((v.position.x - origin.x) / MM, 4)
        across_mm = round((v.position.y - origin.y) / MM, 4)
        via_net = v.net.name if v.net else None
        role_net = role_net_pad = None
        if via_net in rule_nets:
            via_net = None
        elif via_net is not None:
            # Try net_from_role BEFORE net_template_map: a via whose net maps
            # unambiguously to one selected role is written as net_from_role
            # (optionally with pad) instead of a literal/parametrised net —
            # the cell then resolves that net live on ANY cluster it is
            # applied to. On fallback/ambiguity keep the existing path.
            role_net, role_net_pad = _suggest_net_from_role(
                selection_role_nets, via_net, rule_nets,
                [(along_mm, across_mm)], components)
            if role_net is not None:
                via_net = None
            elif net_template_map:
                via_net = parametrize_net(via_net, net_template_map, params)
        entry = {
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "net": via_net,
            "drill_mm": round(v.drill_diameter / MM, 4),
            "diameter_mm": round(v.diameter / MM, 4),
        }
        if role_net is not None:
            entry["net_from_role"] = role_net
            if role_net_pad is not None:
                entry["net_from_role_pad"] = role_net_pad
        spoke_vias.append(entry)
        logger.debug(_("  via: along={along}, across={across}, net={net}")
                     .format(along=along_mm, across=across_mm,
                             net=role_net or via_net))

    spoke_tracks = []
    for t in tracks:
        start_along_mm = round((t.start.x - origin.x) / MM, 4)
        start_across_mm = round((t.start.y - origin.y) / MM, 4)
        end_along_mm = round((t.end.x - origin.x) / MM, 4)
        end_across_mm = round((t.end.y - origin.y) / MM, 4)
        track_net = t.net.name if t.net else None
        role_net = role_net_pad = None
        if track_net in rule_nets:
            track_net = None
        elif track_net is not None:
            # Same net_from_role auto-suggestion as the via loop (plan step 4).
            role_net, role_net_pad = _suggest_net_from_role(
                selection_role_nets, track_net, rule_nets,
                [(start_along_mm, start_across_mm), (end_along_mm, end_across_mm)],
                components)
            if role_net is not None:
                track_net = None
            elif net_template_map:
                track_net = parametrize_net(track_net, net_template_map, params)
        entry = {
            "start_along_mm": start_along_mm,
            "start_across_mm": start_across_mm,
            "end_along_mm": end_along_mm,
            "end_across_mm": end_across_mm,
            "width_mm": round(t.width / MM, 4),
            "net": track_net,
        }
        if role_net is not None:
            entry["net_from_role"] = role_net
            if role_net_pad is not None:
                entry["net_from_role_pad"] = role_net_pad
        if t.layer != tpl_layer:
            entry["layer"] = 'F.Cu' if t.layer == BoardLayer.BL_F_Cu else 'B.Cu'
        spoke_tracks.append(entry)
        logger.debug(_("  track: ({sx},{sy}) -> ({ex},{ey}), net={net}{layer}")
                     .format(sx=start_along_mm, sy=start_across_mm,
                             ex=end_along_mm, ey=end_across_mm,
                             net=role_net or track_net,
                             layer=_(", layer={layer}").format(layer=entry['layer']) if 'layer' in entry else ""))

    logger.info(_("Extracted cell {name!r}: {comp} components, {vias} spoke‑level vias, {tracks} tracks")
                .format(name=name, comp=len(components), vias=len(spoke_vias), tracks=len(spoke_tracks)))
    result = {"vias": spoke_vias, "components": components, "tracks": spoke_tracks, "layer": tpl_layer_str}
    return {name: result}
