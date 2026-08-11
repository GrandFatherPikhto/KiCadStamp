# kicadstamp/net_resolution.py
"""
net_resolution.py — three‑layer net name resolution for cloned cells
(TemplatePlacer), in order of increasing specificity:

  1. Literal ("GND") — as‑is, no placeholders.
  2. Placeholder ("DAC{channel}_DB1") — substituted from params
     (str.format), written manually into the cell at extraction/
     editing time — NOT automatically derived from any pattern.
  3. net_overrides — applied ON TOP of the result of steps 1‑2, by the
     resolved (already substituted) name — for point exceptions like
     hierarchical paths (/STM32F4xx/BOOT0) that do not fit even into
     parametrisation.

No automatic guessing anywhere — both mechanisms (params and net_overrides)
require explicit, hand‑written configuration.

Net-from-role resolution (net_from_role / net_from_role_pad on
TemplateVia/TemplateTrack) is the live counterpart: instead of a static net,
the net is read at apply time from the real pad of the role this via/track
belongs to (see resolve_net_from_role below and placement/services/
net_from_role_resolver.py for the classification side).
"""
from typing import Any
from .exceptions import ValidationError, format_fatal_error
from .i18n import _

# Rule nets — a via/track on one of these needs no role at all (net: null).
# Homed here (a neutral, low-level module) rather than in placement/services/
# net_from_role_resolver.py: that resolver re-exports it, but defining it here
# avoids an import cycle (net_resolution <- placement.services is fine, but
# placement/services would pull placement/__init__ -> planner -> clone_geometry
# -> net_resolution back).
RULE_NETS: set[str] = {"GND"}


def resolve_placeholder(template: str, params: dict[str, Any], what: str = "value") -> str:
    """
    Generic {placeholder} substitution from params (str.format) — the engine
    underneath resolve_net, also reused as-is for ClonePlacement.anchor_sheet
    (see clone_role_resolver.resolve_anchor_by_role): the Role field is shared
    across every instance of a reused sheet (e.g. channel.kicad_sch used 3×),
    but the sheet NAME differs per instance (Channel_0/Channel_1/...), so a
    clone_placement parametrized with params: {channel: N} needs
    anchor_sheet: 'Channel_{channel}' to actually narrow per‑instance — a
    literal anchor_sheet would have to be repeated, unparametrized, in every
    clone_placement copy.
    """
    try:
        return template.format(**params)
    except KeyError as e:
        raise ValidationError(format_fatal_error(
            _("{what} {template!r} has a placeholder with no parameter").format(what=what, template=template),
            [_("missing parameter {param} — add it to params of this clone_placement, "
               "or remove the placeholder").format(param=e)]
        ))


def resolve_net(net_template: str, params: dict[str, Any], net_overrides: dict[str, str]) -> str:
    """
    net_template — net name as written in the cell (TemplateVia.net),
    possibly with {placeholder}. params — substitution values (from
    ClonePlacement.params). net_overrides — point override of the final name
    (from ClonePlacement.net_overrides).
    """
    resolved = resolve_placeholder(net_template, params, what="net")
    return net_overrides.get(resolved, resolved)


def resolve_net_from_role(role: str, pad: str | None, role_to_ref: dict[str, str],
                          adapter, rule_nets: set[str] | None = None) -> str:
    """Resolve a via/track's net from a role's real pad, live (apply time).

    role_to_ref[role] -> adapter.get_footprint(ref) -> pad (explicit, or the
    single non-rule-net pad, live-derived) -> pad.net.name.

    This is the apply-side counterpart of net_from_role on TemplateVia/
    TemplateTrack: the classification (which role/pad a via belongs to) lives
    in net_from_role_resolver.py; here we only translate an
    already-chosen (role, pad) into the net that role's real footprint carries
    RIGHT NOW on this board instance.

    Fatal (ValidationError) if role not in role_to_ref, ref not on the board,
    the pad is not found, or (pad omitted) the resolved footprint carries
    zero or more-than-one non-rule nets — that structural assumption (this
    role has exactly one non-rule net) was asserted at extract time and must
    hold on THIS instance too, or apply must stop, not guess.

    Called OUTSIDE the geometry layer (clone_position_calculator resolves it
    before apply_clone_geometry) — preserves the "geometry does not touch the
    live board" boundary (clone_geometry.py docstring).
    """
    rule = set(rule_nets) if rule_nets is not None else set(RULE_NETS)

    ref = role_to_ref.get(role)
    if ref is None:
        raise ValidationError(format_fatal_error(
            _("role {role!r} not resolved in this clone").format(role=role),
            [_("net_from_role references role {role!r}, but role_to_ref for this "
               "clone does not contain it — the role resolution (by cluster / "
               "selection / nets) did not find a footprint for this role; check "
               "the cell's components and the clone's role-resolution mode")
             .format(role=role)]
        ))
    fp = adapter.get_footprint(ref)
    if fp is None:
        raise ValidationError(format_fatal_error(
            _("footprint {ref!r} (role {role!r}) not found on the board")
            .format(ref=ref, role=role),
            [_("role_to_ref maps role {role!r} to ref {ref!r}, but there is no "
               "such footprint on the live board — board out of sync with "
               "schematic?").format(role=role, ref=ref)]
        ))

    if pad is not None:
        p = adapter.get_pad_by_number(fp, str(pad))
        if p is None or not p.net or not p.net.name:
            raise ValidationError(format_fatal_error(
                _("pad {pad!r} of {ref!r} (role {role!r}) not found or has no net")
                .format(pad=pad, ref=ref, role=role),
                [_("net_from_role_pad names pad {pad!r} on role {role!r}, but the "
                   "resolved footprint {ref!r} has no such connected pad — wrong "
                   "pad number?").format(pad=pad, ref=ref, role=role)]
            ))
        return p.net.name

    # No explicit pad — lemma 2: this role must carry exactly one non-rule net.
    non_rule: set[str] = set()
    for p in adapter.get_footprint_pads(fp):
        if p.net and p.net.name and p.net.name not in rule:
            non_rule.add(p.net.name)
    if len(non_rule) == 1:
        return next(iter(non_rule))
    raise ValidationError(format_fatal_error(
        _("role {role!r} ({ref!r}) has {count} non-rule nets, not exactly one")
        .format(role=role, ref=ref, count=len(non_rule)),
        [_("net_from_role without net_from_role_pad requires the role to carry "
           "exactly one non-rule net (lemma 2), but {ref!r} carries {nets} — "
           "add net_from_role_pad to pick one explicitly")
         .format(ref=ref, nets=sorted(non_rule))]
    ))


def parametrize_net(literal_net: str, net_template_map: dict[str, str],
                     params: dict[str, Any]) -> str:
    """
    Reverse operation of resolve_net — for extract, not for apply.

    literal_net — real net name read from the board (v.net.name).
    net_template_map — explicit, hand‑written mapping literal->pattern
    (e.g. {"DAC1_DB1": "DAC{channel}_DB1"}), set once at extract time via
    --net-template. params — the same params that will later resolve the pattern
    at apply time (passed to extract via --param only for verification, NOT
    written to the cell).

    NO guessing of placeholder position by substring — the pattern is fully
    written by the user. The only thing this function does is check that the
    written pattern, when resolved with the given params, yields exactly the
    literal it was taken from (round‑trip), and fatally fails on any mismatch
    (typical cause: typo in the pattern or wrong parameter).
    """
    if literal_net not in net_template_map:
        return literal_net
    pattern = net_template_map[literal_net]
    check = resolve_net(pattern, params, {})
    if check != literal_net:
        raise ValidationError(format_fatal_error(
            _("--net-template for {literal!r} fails round‑trip check").format(literal=literal_net),
            [_("pattern {pattern!r} with params={params} resolves to {check!r}, "
               "not to {literal!r} — typo in pattern or wrong parameter passed via --param")
             .format(pattern=pattern, params=params, check=check, literal=literal_net)]
        ))
    return pattern