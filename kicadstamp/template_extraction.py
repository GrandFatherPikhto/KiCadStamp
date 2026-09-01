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
  5. Each SELECTED track/via is included ONLY IF its connected component
     (via coincident endpoints, track‑to‑track joints, or touching a via)
     reaches at least one REAL anchor — a pad of a KEPT footprint (see
     template_selection.py's _filter_tracks_and_vias_within_selection, a
     connected‑components closure). A track/via whose component only ever
     touches OTHER excluded material (e.g., a track‑to‑track chain belonging
     to a cluster whose footprints were dropped by "Keep only one Cluster")
     is skipped as a whole component with a warning. When the selection has
     no usable kept‑pad geometry (via‑only extraction / mocks), the
     historical both‑ends‑match rule is preserved as a fallback.

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

from .domain.geometry import BoardLayer, Vector2

from .domain.board import Footprint, Via, Track

from .constants import ROLE_FIELD_NAME
from .exceptions import ValidationError, format_fatal_error
from .kicad.adapter import KiCadBoardAdapter
from .net_resolution import RULE_NETS, discover_net_template_pattern, parametrize_net
from .net_from_role_resolver import classify_net
from .utils.units import MM
from .i18n import _
from .template_selection import _find_origin, _filter_tracks_and_vias_within_selection
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
            if not p.net_name:
                continue
            pad_num = getattr(p, "number", None)
            pads.setdefault(str(pad_num if pad_num is not None else pad_idx + 1),
                            set()).add(p.net_name)
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


def _auto_net_pattern_map(adapter, selection_role_nets: dict[str, dict[str, set[str]]],
                          rule_nets: set[str]) -> dict[str, str]:
    """Phase 1 step 1.3: auto-discover {param} net patterns from the SAME roles'
    nets across OTHER board instances (one full-board scan, grouped by role).

    Returns {literal_net: pattern} for every discoverable single-token pattern
    (e.g. /Channel_0/DAC/DB0 -> /Channel_{channel}/DAC/DB0), or {} when none.
    GUARDED (never guesses — plan rule): discover_net_template_pattern's limiter
    (a) exactly one differing segment AND (b) round-trip via parametrize_net
    must both hold; otherwise the net stays literal. Rule nets are never
    considered (they need no role)."""
    out: dict[str, str] = {}
    if not selection_role_nets:
        return out
    try:
        all_fps = adapter.get_footprints() if hasattr(adapter, "get_footprints") else []
    except Exception:
        return out
    # Collect per-role nets across the whole board (selection included).
    role_literals: dict[str, set[str]] = {}
    for fp in all_fps:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None or role not in selection_role_nets:
            continue
        try:
            pads = adapter.get_footprint_pads(fp)
        except TypeError:
            continue
        for p in pads:
            if p.net_name and p.net_name not in rule_nets:
                role_literals.setdefault(role, set()).add(p.net_name)
    for role, literals in role_literals.items():
        found = discover_net_template_pattern(sorted(literals))
        if found is None:
            continue
        pattern, _param_name, _value = found
        for lit in sorted(literals):
            out[lit] = pattern
    return out


def extract_template_from_selection(
    adapter: KiCadBoardAdapter,
    name: str,
    params: dict[str, Any] | None = None,
    net_template_map: dict[str, str] | None = None,
    origin_via_net: str | None = None,
    origin_component_role: str | None = None,
    origin_component_pad: str | None = None,
    origin_component_cluster: str | None = None,
    origin_component_sheet: str | None = None,
    net_template_role: dict[str, str] | None = None,
    rule_nets: set[str] | None = None,
    items: list[Any] | None = None,
    annotations: list[tuple[str, str, str]] | None = None,
    raw_selection: bool = False,
    origin: Vector2 | None = None,
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

    origin_component_cluster/origin_component_sheet — OPTIONAL refinements of
    origin_component_role (2026-08-31): when several SELECTED components share
    the role (same role in different Clusters/Channels), they narrow the
    candidates via the same sheet -> Cluster cascade the role-anchor resolver
    uses (see _find_origin / _narrow_by_sheet_cluster_selection). Sheet
    narrowing needs Config.sheet_names, which this core function does not
    carry — it is a no-op here (the GUI pre-resolves the origin with
    sheet_names; the CLI has no config anyway). Cluster narrowing reads the
    board Cluster field directly and always applies.

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
    "inherit the enclosing Chain's own net" (kicadstamp/geometry/
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

    raw_selection — OPTIONAL bool (default False). When True, the pad-
    connectivity filter (_filter_tracks_and_vias_within_selection) is skipped
    entirely: every selected track/via goes into the cell exactly as selected,
    with no "connected to a kept footprint's pad" check. This is an explicit
    opt-in BYPASS of the filter — the filter itself remains the default
    behaviour, nothing about it changes. Use it when the user knows all the
    selected copper is theirs (e.g. a via/copper array with no single anchor
    component in the selection, or a quick draft capture). The "Tracks in
    selection: N, taken into cell: M" log line is suppressed in this mode,
    since nothing is filtered and the "the rest extend beyond the selection"
    wording would be misleading.
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
            [_("a net can't be both \"always the enclosing Chain's own net\" and "
               "\"always resolved from this param\" — pick one per net")]
        ))
    items = items if items is not None else adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, Footprint)]
    vias = [i for i in items if isinstance(i, Via)]
    tracks_selected = [i for i in items if isinstance(i, Track)]
    ignored = [i for i in items if not isinstance(i, (Footprint, Via, Track))]

    if ignored:
        logger.warning(_("{count} selected objects — not footprint, via, or track, "
                         "ignored (cell only supports these)").format(count=len(ignored)))

    if raw_selection:
        tracks, vias = tracks_selected, vias
    elif tracks_selected or vias:
        tracks, vias = _filter_tracks_and_vias_within_selection(
            tracks_selected, vias, footprints, adapter)
    else:
        tracks, vias = [], []
    if not raw_selection and len(tracks) < len(tracks_selected):
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
        ref = fp.ref
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

    if origin is None:
        # Caller (CLI/scripts) didn't pre-resolve the origin — the default
        # path: derive it from the selection ourselves, exactly as always.
        origin = _find_origin(footprints, vias, origin_via_net, origin_component_role,
                              origin_component_pad, adapter,
                              origin_component_cluster=origin_component_cluster,
                              origin_component_sheet=origin_component_sheet)
        origin_desc = (_("via on net {net!r}") if origin_via_net
                       else _("component with role {role!r}") if origin_component_role
                       else _("bbox of selection (lower‑left corner)"))
        origin_desc = origin_desc.format(net=origin_via_net, role=origin_component_role) if '{' in origin_desc else origin_desc
    else:
        # Explicit caller-supplied origin (GUI Sub-placements, 2026-08-25): the
        # SAME Vector2 the worker resolved once from the FULL pre-exclusion
        # selection — used verbatim so the Sub-placement xy and the flat
        # geometry are guaranteed to share one coordinate system. The
        # origin_*_ kwargs are ignored here (nothing to derive).
        origin_desc = _("explicit (caller-supplied)")
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

    # Roles already classified as lemma-2-safe THIS PASS (role -> its one
    # non-rule net) — populated as the loop below processes each slot, so a
    # LATER slot in the selection can reference an EARLIER one. Order in the
    # selection is arbitrary (kipy doesn't guarantee it), so a role whose
    # ONLY safe sibling appears LATER in iteration order won't find it on
    # this pass — acceptable: falls through to net_template_pad or the
    # existing "fill in manually" warning, same graceful degradation as
    # today, never worse.
    # Soft-deprecation accounting (Phase 1 step 1.4, plan rule: the manual
    # flags stay as optional overrides — backward compatible — but a notice
    # fires in the log when they are REDUNDANT given the new auto-derivation;
    # never guess silently, but don't silently keep telling the user to type
    # what is now automatic either). net_template_map_used = the explicit
    # --net-template/--param map actually parametrized at least one via/track;
    # component_net_template_used = at least one component's net_template came
    # from that map (single-net role or bridging designated net).
    net_template_map_used = False
    component_net_template_used = False
    lemma2_role_nets: dict[str, str] = {}
    components = []
    for fp in footprints:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        along_mm = round((fp.position.x - origin.x) / MM, 4)
        across_mm = round((fp.position.y - origin.y) / MM, 4)
        slot = {
            "role": role,
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "angle_deg": fp.angle_deg,
        }
        if fp.layer != tpl_layer:
            slot["layer"] = 'F.Cu' if fp.layer == BoardLayer.BL_F_Cu else 'B.Cu'

        if role in net_template_role:
            literal = net_template_role[role]
            fp_pads = adapter.get_footprint_pads(fp)
            fp_nets = sorted({p.net_name for p in fp_pads if p.net_name})
            if literal not in fp_nets:
                raise ValidationError(format_fatal_error(
                    _("--net-template-role for role {role!r} asks for net {literal!r}, "
                      "but it is not on any pad of {ref}").format(role=role, literal=literal,
                                                                   ref=fp.ref),
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
            component_net_template_used = True
            # Phase 1 step 1.4 soft-deprecation: for a bridging role (2+ nets
            # from net_template_map on its pads) whose explicit literal equals
            # the auto-derived DESIGNATED net, --net-template-role changed
            # nothing — extract now sets net_template without it.
            mapped = [n for n in fp_nets if n in net_template_map]
            if len(mapped) > 1 and literal == mapped[0]:
                logger.warning(_("--net-template-role for role {role!r} = {literal!r} is "
                                 "redundant — it equals the auto-derived designated net, "
                                 "extract now sets net_template for bridging roles without "
                                 "this flag")
                               .format(role=role, literal=literal))
            slot["net_template"] = parametrize_net(literal, net_template_map, params)
            # Prefer a same-net lemma-2-safe sibling ALREADY classified this
            # pass (net_template_same_as_role — cross-instance-safe, pad-number
            # independent); fall back to the pad number (today's mechanism,
            # safe only for fixed-pinout parts) when no sibling is available.
            same_as = next((r for r, n in lemma2_role_nets.items() if n == literal), None)
            if same_as is not None:
                slot["net_template_same_as_role"] = same_as
            else:
                pad_num = next((p.number for p in fp_pads if p.net_name == literal), None)
                if pad_num is not None:
                    slot["net_template_pad"] = str(pad_num)
        else:
            # No explicit --net-template-role: auto-derive net_template from the
            # role's own pad nets (plan_2026_08_31_extract_auto_nets_hide_tabs.md).
            # A net present in net_template_map is parametrized (cross-cluster
            # portable); otherwise the LITERAL net is used (fine for a global
            # rail like +3V3 — apply's live auto-derivation covers exotic
            # cross-cluster cases). Only NON-rule nets count as "the role's
            # nets" (a lone GND is not single-net evidence, and a signal+GND
            # pad set is single-net, not bridging).
            fp_pads = adapter.get_footprint_pads(fp)
            fp_nets = sorted({p.net_name for p in fp_pads if p.net_name})
            mapped = [n for n in fp_nets if n in net_template_map]
            candidates = mapped or [n for n in fp_nets
                                    if n not in rule_nets and n not in RULE_NETS]
            if len(candidates) == 1:
                net = candidates[0]
                if mapped:
                    component_net_template_used = True
                slot["net_template"] = parametrize_net(net, net_template_map, params)
                # lemma-2-safe role — record for LATER same-net siblings, do
                # NOT record net_template_pad/net_template_same_as_role for
                # ITSELF: a role with exactly one non-rule net needs neither
                # (2026-08-16 fix — previously always wrote net_template_pad
                # here even when redundant AND, worse, unsafe: C_ADJ_BULK/
                # R_FB_BOT both hit exactly this branch and got a pad number
                # they never needed, which then broke on a different instance).
                lemma2_role_nets[role] = net
            elif len(candidates) > 1:
                # Bridging role (ferrite/inductor/fuse between two rails): two
                # DIFFERENT nets on its pads. Auto-derive a DESIGNATED net
                # (deterministic: first in sorted order) + its pad, so
                # net_template is set WITHOUT --net-template-role
                # (plan_2026_08_28_auto_nets_full_automation.md, Phase 1 step
                # 1.2). BOTH nets are still captured on the copper — each
                # via/track touching this role gets net_from_role(+pad) via the
                # auto-suggestion below; net_template here is only the
                # by-nets/GUI "identifying" net. The pad number is safe for
                # fixed-pinout bridging parts (pad->net is deterministic across
                # instances); a symmetric bridging part would instead want
                # net_template_same_as_role.
                designated = candidates[0]
                if mapped:
                    component_net_template_used = True
                slot["net_template"] = parametrize_net(designated, net_template_map, params)
                same_as = next((r for r, n in lemma2_role_nets.items() if n == designated), None)
                if same_as is not None:
                    slot["net_template_same_as_role"] = same_as
                else:
                    pad_num = next((p.number for p in fp_pads if p.net_name == designated), None)
                    if pad_num is not None:
                        slot["net_template_pad"] = str(pad_num)
                logger.debug(_("  {ref} (role {role}): bridging — {count} nets on pads, "
                               "net_template set to designated {designated!r} without "
                               "--net-template-role")
                             .format(ref=fp.ref, role=role, count=len(candidates),
                                     designated=designated))
        components.append(slot)
        logger.debug(_("  {ref} (role {role}): along={along}, across={across}, angle={angle}{layer}{net}")
                     .format(ref=fp.ref, role=role,
                             along=along_mm, across=across_mm, angle=fp.angle_deg,
                             layer=_(", layer={layer}").format(layer=slot.get('layer')) if 'layer' in slot else "",
                             net=_(", net_template={nt}").format(nt=slot.get('net_template')) if 'net_template' in slot else ""))

    # Live role -> {pad: {nets}} of the selection, for net_from_role
    # auto-suggestion below (plan step 4). Built once, used by both via and
    # track loops; empty when the selection has no role/net evidence, in which
    # case extract behaves exactly as before.
    selection_role_nets = _selection_role_nets(adapter, footprints)

    # Phase 1 step 1.3: auto {param} patterns from the same roles' nets across
    # OTHER instances (guarded — never guesses; only used when a via/track net
    # is not classifiable to a role and not already in net_template_map).
    auto_patterns = _auto_net_pattern_map(adapter, selection_role_nets, rule_nets)

    spoke_vias = []
    for v in vias:
        along_mm = round((v.position.x - origin.x) / MM, 4)
        across_mm = round((v.position.y - origin.y) / MM, 4)
        via_net = v.net_name
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
                net_template_map_used = True
                via_net = parametrize_net(via_net, net_template_map, params)
            elif via_net in auto_patterns:
                via_net = auto_patterns[via_net]
        entry = {
            "offset_along_mm": along_mm,
            "offset_across_mm": across_mm,
            "net": via_net,
            "drill_mm": round(v.drill_mm, 4),
            "diameter_mm": round(v.diameter_mm, 4),
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
        track_net = t.net_name
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
                net_template_map_used = True
                track_net = parametrize_net(track_net, net_template_map, params)
            elif track_net in auto_patterns:
                track_net = auto_patterns[track_net]
        entry = {
            "start_along_mm": start_along_mm,
            "start_across_mm": start_across_mm,
            "end_along_mm": end_along_mm,
            "end_across_mm": end_across_mm,
            "width_mm": round(t.width_mm, 4),
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

    # Phase 1 step 1.4 soft-deprecation: the explicit map was provided but not
    # used anywhere — net_from_role and auto-patterns covered every net, so the
    # user can drop --net-template/--param. Only an informational notice: the
    # flags keep working as overrides (backward compatibility).
    if net_template_map and not net_template_map_used and not component_net_template_used:
        logger.warning(_("--net-template/--param was not needed — every via/track net was "
                         "resolved from a role (net_from_role) or auto-parametrized, and no "
                         "component net_template came from the map; you can drop these flags "
                         "(net definition is automatic now)"))
    logger.info(_("Extracted cell {name!r}: {comp} components, {vias} spoke‑level vias, {tracks} tracks")
                .format(name=name, comp=len(components), vias=len(spoke_vias), tracks=len(spoke_tracks)))
    result = {"vias": spoke_vias, "components": components, "tracks": spoke_tracks, "layer": tpl_layer_str}
    return {name: result}
