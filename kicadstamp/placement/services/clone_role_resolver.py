# kicadstamp/placement/services/clone_role_resolver.py
"""
clone_role_resolver.py — role‑to‑ref mapping for ClonePlacement, two
independent mechanisms:

  1. By selection (resolve_roles_by_selection) — for rare, one‑off sections
     (e.g. a single MCU on the board). The user selects the components of a
     specific, not‑yet‑placed instance with the mouse. Symmetric check: every
     role in the cell must be found in the selection exactly once, and
     conversely, no role in the selection may be absent from the cell.

  2. By nets (resolve_roles_by_nets) — for repeated cells (PI‑filters, DAC
     channels) where selection risks mixing up identical‑looking instances.
     The net for each role is: priority — explicit ClonePlacement.nets[role]
     (literal), otherwise TemplateComponentSlot.net_template (with placeholders,
     via net_resolution.resolve_net). No geometry‑based or ref‑pattern matching
     — only explicitly specified nets.

(The third, "by Cluster tag" mechanism — resolve_by_cluster_tag — was the
backend of ClonePlacement's cluster: mode, migrated 1:1 to CoordinatePlacement's
anchor-relative mode on 2026-08-12, Group 0 consolidation, and removed; its
exact-match filter survives as the shared resolve_unique_footprint_by_fields
below, which coordinate_position_calculator.py now uses.)

The mode is chosen BEFORE calling this module (see planner/orchestration):
nets or params set — mode is "by nets"; otherwise — "by selection". This is
final, no automatic mode switching inside the resolver.

Implementation notes (T3.1 god-file decomposition): the shared candidate-
narrowing cascade (anchor_sheet -> Cluster -> selection -> physical proximity)
lives in role_narrowing.py — it is the ONE narrowing logic reused by all three
resolution paths here (by-nets, by-selection, anchor-by-role). This module is
now only the resolution orchestration on top of it.
"""
import logging
import re

from ...domain.geometry import Vector2

from ...domain.board import Footprint

from ...config import Cell, CellPlacement, ClonePlacement, TemplateComponentSlot, \
    clone_placement_effective_name
from ...exceptions import ValidationError, format_fatal_error
from ...net_derive import PREFIX_REMAP, derive_role_nets
from ...net_resolution import ROLELESS_NETS, resolve_net, resolve_placeholder
from .component_pool import ROLE_FIELD_NAME
from ...i18n import _
from ...role_conflict_reasons import (
    has_refdes_copies,
    narrowed_by_hint,
    role_conflict_problems,
    role_scope_problems,
)
from .role_narrowing import (
    _narrow_ambiguous_candidates,
    _narrow_by_sheet_cluster_selection,
    narrow_candidates_by_sheet,
    own_cluster_candidates,
)
from .role_refusals import raise_ambiguous, raise_role_not_found

logger = logging.getLogger(__name__)


def match_unique_footprint_by_fields(matches, field_matches: dict, label: str) -> Footprint:
    """Given the ALREADY-COMPUTED list of footprints whose custom fields all
    equal field_matches (the same dict that produced them), return the single
    one or raise the fatal none/ambiguous ValidationError. Split out of
    resolve_unique_footprint_by_fields (2026-08-12, Group 4) so a caller
    that has already grouped the board's footprints ONCE — e.g.
    build_coordinate_moves' prebuilt (Role, Cluster) index — reuses the
    exact same error messages without a second adapter.get_footprints()
    scan per lookup."""
    if not matches:
        tag_desc = ", ".join(f"{field}={value!r}" for field, value in field_matches.items())
        raise ValidationError(format_fatal_error(
            _("{label}: no component tagged {tag_desc}").format(label=label, tag_desc=tag_desc),
            [_("tag the target component's {fields} fields first (RoleClusterTreeDock or "
               "fieldstool), or check for a typo").format(fields="/".join(field_matches))]
        ))
    if len(matches) > 1:
        refs = sorted(fp.ref for fp in matches)
        tag_desc = ", ".join(f"{field}={value!r}" for field, value in field_matches.items())
        raise ValidationError(format_fatal_error(
            _("{label}: {count} components tagged {tag_desc}, expected exactly one")
            .format(label=label, count=len(matches), tag_desc=tag_desc),
            [_("{fields} is meant to be unique per instance — fix the tagging: {refs}")
             .format(fields="/".join(field_matches), refs=refs)]
        ))
    return matches[0]


def resolve_unique_footprint_by_fields(adapter, field_matches: dict, label: str) -> Footprint:
    """Shared "find the ONE footprint whose custom fields match, fatal if
    none or if several" helper — the exact-match filter plus none/ambiguous
    fatals of the former resolve_by_cluster_tag (ClonePlacement's cluster:
    mode) and resolve_footprint_by_cluster_role (coordinate_position_calculator.py)
    were near-identical copies (2026-08-12, Group 3 consolidation); this is
    the single copy, reused by CoordinatePlacement's own cluster+role lookup
    and its anchor-relative mode.

    field_matches: {FIELD_NAME: value} — a footprint matches when EVERY
    field equals its value (EXACT equality, no prefix narrowing: ambiguity
    here is a tagging mistake, not something to resolve). label is the
    caller's error-message label (coordinate effective name).

    Returns the single matching footprint, or raises a fatal ValidationError
    naming the field/value(s) — no match at all, or more than one match
    (with the refs of all of them, so the tagging mistake is easy to fix)."""
    matches = [fp for fp in adapter.get_footprints()
              if all(adapter.get_field_value(fp, field) == value
                     for field, value in field_matches.items())]
    return match_unique_footprint_by_fields(matches, field_matches, label)


def resolve_single_role_candidate(all_fps, adapter, role: str, cluster: str,
                                  sheet: str | None = None,
                                  sheet_names: dict[str, str] | None = None):
    """The one real footprint whose Role==role and Cluster prefix-matches
    `cluster` — None if 0 or 2+ (ambiguous/absent, nothing to narrow to).
    `all_fps` is the caller's own adapter.get_footprints() snapshot — never
    fetched here, so callers iterating many roles pay for ONE live read,
    not one per role (this is exactly the shape suggest_role_nets_from_cluster
    already had inline; only pulled out, not changed).

    sheet/sheet_names (2026-08-16, Auto-fill Sheet narrowing): when Cluster+
    Role alone is ambiguous (2+ candidates) and a Sheet is provided, narrow
    via the SAME narrow_candidates_by_sheet the apply-time resolvers use —
    scoped by the placement's OWN sheet (clone.sheet), the (Sheet, Cluster,
    Role) addressing convention completed for the GUI auto-fill path. Only
    narrows if it actually reduces the set AND stays non-empty — a sheet that
    resolves nothing (empty/unknown sheet_names) keeps the original,
    still-ambiguous set, never a wrong guess."""
    # The candidate set is the role's components INSIDE `cluster` — through the
    # ONE owner of that rule (own_cluster_candidates), shared with the apply-time
    # resolvers (plan_2026_10_13_cluster_confined_roles).
    candidates = own_cluster_candidates(
        adapter,
        [fp for fp in all_fps if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role],
        cluster)
    if sheet and sheet_names and len(candidates) > 1:
        candidates = narrow_candidates_by_sheet(candidates, sheet, sheet_names)
    return candidates[0] if len(candidates) == 1 else None


def candidate_nets(adapter, fp, rule_nets: set[str] | None = None) -> list[str]:
    """Sorted non-rule nets on a resolved candidate's pads."""
    rule = set(rule_nets) if rule_nets is not None else set(ROLELESS_NETS)
    pads = adapter.get_footprint_pads(fp)
    return sorted({p.net_name for p in pads if p.net_name and p.net_name not in rule})


def suggest_role_nets_from_cluster(adapter, role_hints: dict[str, tuple[str | None, str | None]],
                                   cluster: str, rule_nets: set[str] | None = None,
                                   sheet: str | None = None,
                                   sheet_names: dict[str, str] | None = None) -> dict[str, str]:
    """Best-effort role -> net suggestion for PlacerDock's Nets tab "Auto-fill
    from board" button (2026-08-12, Denis: "если есть проблема, её можно сразу
    решить в ручном режиме" — auto-fill what's unambiguous, leave the rest for
    manual entry rather than blocking on it).

    role_hints: {role: (net_template_pad, net_template_same_as_role)} — exactly
    one of the pair is non-None, or both None (see the loader's
    mutual-exclusion fatal). SIGNATURE CHANGE (2026-08-16 afternoon,
    net_template_same_as_role) from role_pads: dict[str, str | None], which
    was itself the morning's change from roles: list[str].

    Per-role dispatch, in priority order:
      - net_template_same_as_role set: resolve the NAMED sibling role on the
        SAME target cluster (resolve_single_role_candidate) and, if found,
        RE-VERIFY it is still lemma-2-safe on the CURRENT live board (exactly
        one non-rule net — not trusted blindly from extraction time, the
        board may have changed since) before using its net. Sibling absent or
        no longer lemma-2-safe: role is simply left out (never a stale/wrong
        guess).
      - else net_template_pad set: read THAT SPECIFIC pad's net directly, no
        "exactly one" requirement — mechanical, deterministic (2026-08-16
        morning addition; pad numbers are only reliable cross-instance for
        fixed-pinout parts — ICs/diodes/polarized caps, see
        net_template_same_as_role in TemplateComponentSlot's docstring).
      - else: ORIGINAL lemma-2 rule — suggest only when the candidate has
        EXACTLY one non-rule net on its pads.
    Roles with 0 or 2+ candidates for the relevant resolution step are simply
    left out of the returned dict (unblocked, same as before — the caller
    leaves that row for manual entry).

    sheet/sheet_names (2026-08-16, Auto-fill Sheet narrowing): threaded into
    EVERY resolve_single_role_candidate call — the main role AND the
    net_template_same_as_role sibling lookup, which can hit the same
    Cluster+Role ambiguity on a reused hierarchical sheet (DAC_BUF live repro:
    AD_DAC+DAC_BUF -> IC2/IC3/IC4 board-wide, only the placement's own Sheet
    separates them). Passed through unchanged, see
    resolve_single_role_candidate for the exact narrowing contract.

    Read-only — never moves/tags/writes anything to the board. This is a GUI
    convenience, not part of the by-nets resolution ClonePositionCalculator
    actually runs at apply time (resolve_roles_by_nets above) — it only
    pre-fills the SAME clone.nets: field a human would otherwise type,
    verified for real by that resolver exactly as if typed by hand.
    """
    all_fps = adapter.get_footprints()
    suggestions: dict[str, str] = {}
    for role, (pad, same_as_role) in role_hints.items():
        fp = resolve_single_role_candidate(all_fps, adapter, role, cluster, sheet, sheet_names)
        if fp is None:
            continue
        if same_as_role is not None:
            sibling_fp = resolve_single_role_candidate(
                all_fps, adapter, same_as_role, cluster, sheet, sheet_names)
            if sibling_fp is not None:
                sibling_nets = candidate_nets(adapter, sibling_fp, rule_nets)
                if len(sibling_nets) == 1:
                    suggestions[role] = sibling_nets[0]
            continue
        if pad is not None:
            p = adapter.get_pad_by_number(fp, str(pad))
            if p is not None and p.net_name:
                suggestions[role] = p.net_name
            continue
        non_rule = candidate_nets(adapter, fp, rule_nets)
        if len(non_rule) == 1:
            suggestions[role] = non_rule[0]
    return suggestions


def suggest_role_nets_live(adapter, role_hints: dict[str, tuple[str | None, str | None]],
                           cluster: str, rule_nets: set[str] | None = None,
                           sheet: str | None = None,
                           sheet_names: dict[str, str] | None = None) -> dict[str, str]:
    """Phase 2 step 2.4 — the GUI "Auto-fill from board" LIVE backend.

    Combines the hint-based suggest_role_nets_from_cluster (net_template_pad /
    net_template_same_as_role) with the APPLY-side live auto-derivation
    (_auto_derive_live_net — derive_role_nets, live_pad), so the Nets tab shows
    EXACTLY what apply would derive (WYSIWYG): roles without cell hints that the
    hint-based path cannot fill now get their net from a UNIQUE instance's
    single non-rule net, or from the ONE non-rule net shared by ALL their
    candidates on this cluster (e.g. several C_IN_BULK on +3V3 inside one
    PI-filter cluster — the same shared-net case the apply auto-derive
    resolves).

    Hint-based suggestions win; live_pad fills the rest. Never a guess: a role
    is filled only when the live board gives a deterministic single net (the
    same discipline as _auto_derive_live_net)."""
    suggestions = suggest_role_nets_from_cluster(
        adapter, role_hints, cluster, rule_nets=rule_nets,
        sheet=sheet, sheet_names=sheet_names)
    all_fps = adapter.get_footprints()
    clone = ClonePlacement(cluster=cluster, cell="_gui_suggest", xy=(0, 0),
                           sheet=sheet)
    for role, (pad, same_as_role) in role_hints.items():
        if role in suggestions:
            continue
        if pad is not None or same_as_role is not None:
            continue  # the hint-based path owns those (or explicitly left them out)
        slot = TemplateComponentSlot(role=role)
        net, direct_ref, _source = _auto_derive_live_net(
            adapter, all_fps, role, clone, slot, sheet_names or {})
        if net is not None:
            suggestions[role] = net
    return suggestions


def candidate_nets_by_role(adapter, roles: list[str], cluster: str,
                           rule_nets: set[str] | None = None,
                           sheet: str | None = None,
                           sheet_names: dict[str, str] | None = None) -> dict[str, list[str]]:
    """NEW (2026-08-16) — for GUI Net-combobox narrowing, NOT auto-fill.
    {role: [net, ...]} for every role with exactly one Cluster+Role
    candidate, REGARDLESS of net count (1, 2, 3...) — unlike
    suggest_role_nets_from_cluster, does not require exactly one. A role
    missing from the result had 0 or 2+ ref candidates (nothing to narrow —
    caller falls back to the full board net list). Empty list value = a
    candidate was found but has zero non-rule nets (unusual — surface as-is,
    don't hide it as 'nothing to narrow')."""
    all_fps = adapter.get_footprints()
    result: dict[str, list[str]] = {}
    for role in roles:
        fp = resolve_single_role_candidate(all_fps, adapter, role, cluster, sheet, sheet_names)
        if fp is not None:
            result[role] = candidate_nets(adapter, fp, rule_nets)
    return result


_CHANNEL_RE = re.compile(r"^(?:/)?Channel_(\d+)(?:/.*)?$")


def _target_channel(clone) -> str | None:
    """The target channel implied by the placement's OWN Cluster (e.g.
    'Channel_1' or '/Channel_1/...') — None when the cluster is not a Channel_N
    name. Used by the prefix_remap auto-derivation (Phase 2 step 2.1)."""
    cluster = getattr(clone, "cluster", None)
    if not cluster:
        return None
    m = _CHANNEL_RE.match(cluster)
    return f"Channel_{m.group(1)}" if m else None


def _prefix_remap_local_net(net: str, clone) -> str | None:
    """derive_role_nets priority 2 (prefix_remap), TwinMap.twin_net semantics,
    applied in apply: a LITERAL local hierarchical net '/Channel_N/...' in the
    cell is auto-remapped to the target channel '/Channel_M/...' when the
    placement's own Cluster names a DIFFERENT Channel_M — so a cell extracted on
    Channel_0 clones to Channel_1 without a manual {channel} param or nets:.
    Returns the remapped net, or None when no remap applies (flat or global net,
    same channel, non-channel cluster — never a guess)."""
    m = re.match(r"^/(Channel_\d+)/(.*)$", net)
    if not m:
        return None
    target = _target_channel(clone)
    if target is None or target == m.group(1):
        return None
    return f"/{target}/" + m.group(2)


def _role_designated_net(adapter, fp, slot) -> str | None:
    """The role's identifying net on a live footprint — the same rule the GUI
    auto-fill (suggest_role_nets_from_cluster) uses: net_template_pad -> that
    pad's net; else exactly one non-rule net (lemma 2); else None (a bridging
    role without a designated pad cannot be reduced to one net — never a
    guess)."""
    rule = set(ROLELESS_NETS)
    pad = getattr(slot, "net_template_pad", None)
    if pad:
        p = adapter.get_pad_by_number(fp, str(pad))
        if p is not None and p.net_name and p.net_name not in rule:
            return p.net_name
        return None
    non_rule = {p.net_name for p in adapter.get_footprint_pads(fp)
                if p.net_name and p.net_name not in rule}
    return next(iter(non_rule)) if len(non_rule) == 1 else None


def _auto_derive_live_net(adapter, all_fps, role, clone, slot, sheet_names):
    """Phase 2 steps 2.1/2.2 — auto-derive the expected net of a role with NO
    explicit source (no clone.nets[role], no cell net_template) from the LIVE
    target board, delegating the priority rule to the Phase-0 contract
    derive_role_nets.

    Evidence (never a silent guess):
      1. a UNIQUE instance of the role on the target (Role + the placement's own
         Cluster + sheet) — its designated net (net_template_pad, else the single
         non-rule net, lemma 2);
      2. else the single distinct non-rule net shared by ALL the role's
         candidates (N identical instances on one net — the normal ambiguity
         cascade then disambiguates them).

    Step 2.2 — the TWO-MATCHINGS separation (mini-design §1, plan §2.2): this
    helper produces only the EXPECTED NET (a candidate filter). The instance
    disambiguation among identical candidates on a shared/global net stays
    EXCLUSIVELY with the placement cascade (sheet -> Cluster -> selection ->
    proximity) — Kuhn/net_matching (Role<->Net correspondence for LOCAL nets,
    full two-cluster snapshots — Phase 3 verify_channel_net_mapping) is NEVER
    applied to instance selection here. The Phase-0 contract's kuhn priority is
    consumed SAFE-DEFAULT (never a stop; an ambiguous SCC group is a valid
    answer and a diagnostic, not a fatal) wherever the caller supplies a Kuhn
    mapping — currently the apply path has no such evidence (a ClonePlacement
    has no source cluster; cf. design_2026_08_28_phase2_step2_1_mini.md §7).

    Returns (expected_net, direct_ref, source):
      - (net, None, 'live_pad') — derived expected net (source is the
        derive_role_nets provenance);
      - (None, ref, 'live_instance') — unique instance but not reducible to one
        net (bridging without a designated pad) — the caller maps it directly;
      - (None, None, None) — nothing deterministic (caller's error path)."""
    cluster = getattr(clone, "cluster", None)
    sheet = getattr(clone, "sheet", None)
    # Live candidates come from the OWN cluster only, through the same owner the
    # apply-time resolvers use (plan_2026_10_13_cluster_confined_roles) — the
    # live net of a role is never derived from a foreign cluster's component.
    candidates = own_cluster_candidates(
        adapter,
        [fp for fp in all_fps if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role],
        cluster)
    if sheet and sheet_names and len(candidates) > 1:
        narrowed = narrow_candidates_by_sheet(candidates, sheet, sheet_names)
        if narrowed:
            candidates = narrowed

    if len(candidates) == 1:
        live_net = _role_designated_net(adapter, candidates[0], slot)
        if live_net is not None:
            derivations = derive_role_nets(
                roles=[role], role_source_nets={},
                live_pad_nets={role: live_net})
            derivation = derivations.get(role)
            if derivation is not None:
                return derivation.net, None, derivation.source
        return None, candidates[0].ref, "live_instance"

    shared = {n for c in candidates for n in
              {p.net_name for p in adapter.get_footprint_pads(c)
               if p.net_name and p.net_name not in ROLELESS_NETS}}
    if len(shared) == 1:
        net = next(iter(shared))
        # Step 2.2 separation note (debug — the common PI-filter/power-rail case
        # resolves this way every apply; a warning would be noise): several
        # identical instances share one net, the EXPECTED net is derived live,
        # the INSTANCE is disambiguated by the cascade, never by Kuhn.
        logger.debug(_("[{name}] role {role!r}: {count} identical candidates share "
                       "net {net!r} — expected net derived live (live_pad); the "
                       "instance is resolved by the placement cascade, net-matching "
                       "(Kuhn) is never applied to instance selection")
                     .format(name=clone_placement_effective_name(clone), role=role,
                             count=len(candidates), net=net))
        derivations = derive_role_nets(
            roles=[role], role_source_nets={},
            live_pad_nets={role: net})
        derivation = derivations.get(role)
        if derivation is not None:
            return derivation.net, None, derivation.source
    return None, None, None


def _cell_auto_derivable(adapter, all_fps, cell, clone, sheet_names) -> bool:
    """Phase 2 step 2.3 — can the WHOLE cell be resolved by-nets AUTOMATICALLY
    on the live target? True when EVERY cell role either carries an explicit
    net source (net_template) or is live-derivable (unique instance per
    Role+Cluster+sheet, or a single shared non-rule net across its candidates)
    — i.e. an unambiguous SOURCE INSTANCE exists for every role, so an implicit
    clone (no nets/params/by_selection) defaults to by-nets (auto) instead of
    forcing a selection. False means the cell is a one-off that genuinely needs
    by-selection."""
    for slot in cell.components:
        if slot.net_template is not None:
            continue
        net, direct_ref, _source = _auto_derive_live_net(
            adapter, all_fps, slot.role, clone, slot, sheet_names)
        if net is None and direct_ref is None:
            return False
    return True


def clone_uses_selection_mode(clone: ClonePlacement, *,
                              adapter=None, cell=None, sheet_names=None) -> bool:
    """
    Returns True if the clone is in "by selection" mode:
      - by_selection: true is explicitly set (priority — see ClonePlacement.
        by_selection is needed separately from implicit inference because params
        is ALSO used for resolving placeholders in via/track nets — without this
        flag, a params intended only for via resolution would silently switch
        the whole clone_placement to "by nets" mode, breaking roles resolved by
        selection), OR
      - (legacy pure path — no adapter/cell) neither nets nor params are set:
        the old implicit default, kept for config-only callers (validation) and
        backward compatibility; OR
      - (Step 2.3 adaptive path — adapter+cell given) no nets/params set AND
        the cell has NO unambiguous source instance to auto-derive from (see
        _cell_auto_derivable) — a genuine one-off that needs a selection.
    With auto-derivation (Phase 2 steps 2.1/2.2) the implicit mode is chosen by
    the availability of an unambiguous source instance on the live board:
    all-derivable -> by-nets (auto), else -> by-selection.
    This is the single place where the decision is made —
    ClonePositionCalculator, dependency_order, board_items_resolver and
    validation.py must all ask here, not duplicate the rule.
    """
    if clone.by_selection:
        return True
    if clone.nets or clone.params:
        return False
    if adapter is None or cell is None:
        return True  # legacy default for config-only callers
    return not _cell_auto_derivable(
        adapter, adapter.get_footprints(), cell, clone, sheet_names or {})


def resolve_roles_by_selection(adapter, cell: Cell, clone: ClonePlacement,
                               anchor_position: Vector2 | None = None,
                               sheet_names: dict[str, str] | None = None) -> dict[str, str]:
    """
    Mapping by current selection — but selection is MANDATORY ONLY when the role
    is truly ambiguous:
      1. role is in selection -> use it (priority over everything below).
      2. role is NOT in selection, but it is unique on the WHOLE board -> resolve
         directly, no selection needed.
      3. role is NOT in selection and is ambiguous on the board -> same narrowing
         cascade as in resolve_roles_by_nets: the placement's OWN sheet (`sheet`,
         not `anchor_sheet` — 2026-08-15 split) -> the placement's own Cluster
         (`name`, not `anchor_cluster` — 2026-08-14 split) -> selection (again,
         in case the selection contains some of these candidates without the
         role itself... rare but harmless) -> physical proximity to anchor ->
         FATAL with the exact list if still ambiguous.
    """
    sheet_names = sheet_names or {}
    items = adapter.get_selected_items()
    footprints = [i for i in items if isinstance(i, Footprint)]
    selected_refs = {fp.ref for fp in footprints}
    clone_name = clone_placement_effective_name(clone)

    cell_roles = {slot.role for slot in cell.components}

    role_to_ref: dict[str, str] = {}
    problems: list[str] = []

    for fp in footprints:
        ref = fp.ref
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is None:
            problems.append(_("{ref}: no {field!r} field").format(ref=ref, field=ROLE_FIELD_NAME))
            continue
        if role not in cell_roles:
            problems.append(_("{ref}: role {role!r} is not in the cell "
                              "(cell roles: {roles})")
                            .format(ref=ref, role=role, roles=sorted(cell_roles)))
            continue
        # A selection that contradicts the entity's OWN cluster is refused, not
        # accepted quietly (plan_2026_10_13_cluster_confined_roles, cell К11):
        # the entity addresses ITS cluster, so a picked component of another
        # cluster (or an untagged one) is named and not taken — the same owner
        # the by-nets path uses.
        placement_cluster = getattr(clone, "cluster", None)
        if placement_cluster and not own_cluster_candidates(adapter, [fp], placement_cluster):
            problems.extend(role_scope_problems(adapter, role, placement_cluster, []))
            continue
        if role in role_to_ref:
            problems.append(_("role {role!r} appears twice in selection: {ref1!r} and {ref2!r}")
                            .format(role=role, ref1=role_to_ref[role], ref2=ref))
            continue
        role_to_ref[role] = ref

    missing = cell_roles - set(role_to_ref.keys())
    if missing:
        all_fps_by_role: dict[str, list] = {}
        for fp in adapter.get_footprints():
            role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
            if role in missing:
                all_fps_by_role.setdefault(role, []).append(fp)

        for role in sorted(missing):
            candidates = all_fps_by_role.get(role, [])
            if not candidates:
                problems.append(_("role {role!r} is in cell but not found anywhere on board")
                                .format(role=role))
                continue
            # The candidate set is the role's components INSIDE the entity's own
            # cluster — the same rule (and the same owner) as the by-nets path
            # (plan_2026_10_13_cluster_confined_roles, rule 1).
            placement_cluster = getattr(clone, "cluster", None)
            own = own_cluster_candidates(adapter, candidates, placement_cluster)
            if not own:
                problems.extend(role_scope_problems(adapter, role, placement_cluster, own))
                continue
            if placement_cluster and has_refdes_copies(own):
                problems.extend(role_conflict_problems(
                    adapter, role, placement_cluster, own, extra=narrowed_by_hint(clone)))
                continue
            if len(own) == 1:
                ref = own[0].ref
                role_to_ref[role] = ref
                logger.info(_("[{name}] role {role!r} -> {ref} (single in its own cluster, no selection needed)")
                            .format(name=clone_name, role=role, ref=ref))
                continue

            narrowed, _note = _narrow_ambiguous_candidates(
                own, clone, adapter, selected_refs, anchor_position, clone_name, role, sheet_names
            )
            if len(narrowed) == 1:
                role_to_ref[role] = narrowed[0].ref
                continue
            problems.extend(role_conflict_problems(
                adapter, role, placement_cluster, narrowed,
                extra=narrowed_by_hint(clone)))

    if problems:
        raise ValidationError(format_fatal_error(
            _("selection does not match cell composition ({name!r})").format(name=clone_name),
            problems
        ))

    logger.info(_("[{name}] mapped by selection: {count} roles").format(name=clone_name, count=len(role_to_ref)))
    return role_to_ref


def resolve_roles_by_nets(adapter, cell: Cell, clone: ClonePlacement | CellPlacement,
                          anchor_position: Vector2 | None = None,
                          sheet_names: dict[str, str] | None = None) -> dict[str, str]:
    """
    Mapping by explicit/parameterised nets (the current selection, if any,
    participates only as a narrowing step).

    Expected net per role (Phase 2 step 2.1 — nets:/params:/net_overrides are
    OPTIONAL overrides): explicit clone.nets[role] -> a PARAMETRISED cell
    net_template or a prefix-remapped literal '/Channel_0/...' -> auto-derived
    from the live board via derive_role_nets (live_pad: the unique instance's
    designated net, or the single non-rule net shared by all candidates). A
    LITERAL cell net_template (no `{param}`, no channel remap) is NOT a net
    source any more (2026-10-13): captured from the SOURCE instance, it may only
    CORROBORATE a single own-cluster component (a Log line) — it never selects
    and never separates candidates. Auto-derivation produces the EXPECTED NET
    only; instance disambiguation stays with the cascade below (never Kuhn).

    Candidate scope and cascade (plan_2026_10_13_cluster_confined_roles, cells
    К1–К9 are guarded in tests/placement/test_clone_reuse_cell_across_power_rails.py):
      0. clone.refs[role] — explicit override, bypassing the search entirely
         (breaks on re-annotation: refdes is not stable — last resort).
      1. `own` = the role's components whose Cluster field matches clone.cluster
         (own_cluster_candidates); another cluster or an EMPTY Cluster field is
         NOT a candidate — the refusal NAMES it instead (role_conflict_reasons).
         A record without `cluster` (refs/sheet-only addressing) skips this step.
      2. two copies of ONE refdes inside `own` — refused BEFORE any narrowing
         ("delete the extra copy": no net, sheet, selection or proximity can
         tell two copies of one symbol apart).
      3. a REAL net source narrows INSIDE `own` (the only legal separator):
         explicit nets / a parametrised net_template / the live live_pad.
      4. still several, all in `own` — the previous cascade: the placement's own
         sheet -> the current selection -> physical proximity to the anchor
         (the closest wins only with a clear 2x gap, else a coin toss).
      5. still several — FATAL through role_conflict_problems ("give them
         distinct roles in the schematic" — the R9/D5 R_PROT case).
    """
    sheet_names = sheet_names or {}
    selected_items = adapter.get_selected_items()
    selected_refs = {i.ref for i in selected_items
                     if isinstance(i, Footprint)}

    all_fps = adapter.get_footprints()
    fps_by_role: dict[str, list] = {}
    fps_by_ref = {}
    for fp in all_fps:
        fps_by_ref[fp.ref] = fp
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if role is not None:
            fps_by_role.setdefault(role, []).append(fp)

    role_to_ref: dict[str, str] = {}
    problems: list[str] = []

    # --- step 0: explicit refs ---
    for role, ref in clone.refs.items():
        if role not in {s.role for s in cell.components}:
            problems.append(_("refs: role {role!r} does not exist in cell {cell!r}")
                            .format(role=role, cell=cell.name))
            continue
        fp = fps_by_ref.get(ref)
        if fp is None:
            problems.append(_("refs: component {ref!r} (role {role!r}) not found on board")
                            .format(ref=ref, role=role))
            continue
        role_to_ref[role] = ref
        logger.info(_("[{name}] role {role!r} -> {ref} (explicit refs)")
                    .format(name=clone_placement_effective_name(clone), role=role, ref=ref))

    # --- first pass: Role + the OWN cluster (+ its net) ---
    for slot in cell.components:
        role = slot.role
        if role in role_to_ref:
            continue

        # An entity carrying a cluster addresses its components BY that cluster
        # (2026-10-13, plan_2026_10_13_cluster_confined_roles, rule 1): a
        # component of ANOTHER cluster, or one with an EMPTY Cluster field, is
        # not an answer — not when the expected net leaves it as the only
        # candidate, never. The filter lives in role_narrowing
        # (own_cluster_candidates) — ONE place, shared with the by-selection path.
        placement_cluster = getattr(clone, "cluster", None)
        candidates = fps_by_role.get(role, [])
        own = own_cluster_candidates(adapter, candidates, placement_cluster)

        if not candidates:
            problems.append(_("role {role!r}: NO component with this role on the board at all "
                              "(check the Role field in the schematic, and that Update PCB from Schematic was run)")
                            .format(role=role))
            continue
        if not own:
            problems.extend(role_scope_problems(adapter, role, placement_cluster, own))
            continue
        # ONE refdes standing twice INSIDE the own cluster is refused BEFORE any
        # narrowing (plan, cascade step 2): neither a net nor a sheet, a selection
        # or physical proximity can tell two copies of one symbol apart, so "the
        # closest copy" would be a silent guess.
        if placement_cluster and has_refdes_copies(own):
            problems.extend(role_conflict_problems(adapter, role, placement_cluster, own))
            continue

        # Expected net of the role — the priority chain of Phase 2 step 2.1 with
        # ONE 2026-10-13 change: a LITERAL cell net_template is NOT a net source
        # any more (it was captured from the SOURCE instance and says nothing
        # about THIS cluster — rule 2). Real sources: explicit clone.nets[role], a
        # PARAMETRISED net_template, a channel prefix-remap, and the live own
        # cluster (live_pad). The baked literal may only CORROBORATE a single own
        # candidate (a Log line) — it never selects and never narrows.
        expected_net: str | None = None
        net_source: str | None = None
        baked_net: str | None = None
        if role in clone.nets:
            # Explicit per-clone override — the user asked for THIS net, so an
            # unresolvable placeholder here is a genuine user error (fatal,
            # never silently auto-derived).
            net_template = clone.nets[role]
            expected_net = resolve_net(net_template, clone.params, clone.net_overrides,
                                       sheet=clone.sheet, cluster=clone.cluster)
            net_source = "nets"
        elif slot.net_template is not None:
            net_template = slot.net_template
            try:
                resolved = resolve_net(net_template, clone.params, clone.net_overrides,
                                       sheet=clone.sheet, cluster=clone.cluster)
            except ValidationError:
                # A cell net_template with an unresolved {placeholder} (no matching
                # params) is NOT an actionable explicit net source — it falls
                # through to the live paths below (Phase 2 step 2.1).
                resolved = None
            if resolved is not None:
                if "{" in net_template:
                    # A parametrised net_template is the user's explicit choice.
                    expected_net = resolved
                    net_source = "net_template"
                else:
                    # prefix_remap (derive_role_nets priority 2, TwinMap.twin_net
                    # semantics): a LITERAL local '/Channel_0/...' net is
                    # auto-remapped to the target channel when the placement's own
                    # Cluster names a different Channel_N. Otherwise the literal is
                    # the source instance's breadcrumb, kept for corroboration only.
                    remapped = _prefix_remap_local_net(resolved, clone)
                    if remapped is not None:
                        expected_net = remapped
                        net_source = PREFIX_REMAP
                    else:
                        baked_net = resolved
        if expected_net is None and placement_cluster:
            # The LIVE own cluster — a REAL source (live_pad): the role's
            # designated net on its own instance(s), via the same
            # net_template_pad / net_template_same_as_role hints the GUI Auto-fill
            # uses (a bridging role has no single lemma-2 net of its own).
            role_hint = (getattr(slot, "net_template_pad", None),
                         getattr(slot, "net_template_same_as_role", None))
            suggested = suggest_role_nets_from_cluster(
                adapter, {role: role_hint}, placement_cluster,
                rule_nets=ROLELESS_NETS, sheet=getattr(clone, "sheet", None),
                sheet_names=sheet_names or {})
            if role in suggested:
                expected_net = suggested[role]
                net_source = "live_pad"
        if expected_net is None:
            # Live auto-derivation (derive_role_nets priority 1): the unique own
            # instance's designated net, or the single non-rule net shared by ALL
            # the role's own candidates (N identical instances on one rail).
            auto_net, _auto_ref, auto_source = _auto_derive_live_net(
                adapter, all_fps, role, clone, slot, sheet_names)
            if auto_net is not None:
                expected_net = auto_net
                net_source = auto_source

        logger.debug(_("[{name}] role {role!r}: expected net {net!r} (source: {source})")
                     .format(name=clone_placement_effective_name(clone), role=role,
                             net=expected_net, source=net_source))

        # ONE own candidate IS the answer: a baked literal never overrides it
        # (rule 2 / cascade step 3). A REAL source must still agree with it — an
        # explicit nets:/param mismatch keeps today's fatal, never a silent skip.
        if len(own) == 1:
            only = own[0]
            nets_on_only = {p.net_name for p in adapter.get_footprint_pads(only) if p.net_name}
            if net_source is not None and expected_net not in nets_on_only:
                problems.append(_("role {role!r}: component(s) {refs} with this role exist on the board, "
                                  "but none is on net {expected!r} — they are actually on {found} "
                                  "(check params/net name or the schematic connection)")
                                .format(role=role, refs=[only.ref], expected=expected_net,
                                        found=sorted(nets_on_only)))
                continue
            if (baked_net is not None and baked_net != expected_net
                    and baked_net not in nets_on_only):
                # A Log line, NEVER a refusal: the literal came from the source
                # instance, the single own component IS the answer (live regress:
                # spoke_vdd_01_mcu — its cell's baked +3V3_VDDA against the live
                # +3V3_VDD of the own cluster).
                logger.info(_("[{name}] role {role!r}: the cell's baked net_template {baked!r} "
                              "is not on the own cluster's component {ref} (net {live!r}) — "
                              "the component decides, not the baked literal")
                            .format(name=clone_placement_effective_name(clone), role=role,
                                    baked=baked_net, ref=only.ref, live=expected_net))
            role_to_ref[role] = only.ref
            continue

        # Several DIFFERENT refdes in the OWN cluster (copies were refused above):
        # narrow by the real source net, then by the PREVIOUS cascade — the sheet,
        # the current selection, physical proximity — but only INSIDE `own`, since
        # the own-cluster step already happened.
        matched = ([fp for fp in own
                    if expected_net in {p.net_name for p in adapter.get_footprint_pads(fp)}]
                   if expected_net is not None else list(own))

        # Bridging-role narrowing by the DESIGNATED PAD (Phase 1 step 1.2,
        # review 2026-08-28): a bridging role has TWO different nets on its pads
        # and net_template_pad marks which pad carries the designated net. When
        # several candidates carry the designated net, narrow to candidates whose
        # pad with that number also carries it. STRICTLY a secondary discriminator
        # — it only ever NARROWS, never turns a match into a miss: an empty pad
        # check (unreliable numbering on symmetric parts) keeps the net results.
        if len(matched) > 1 and getattr(slot, "net_template_pad", None):
            pad_narrowed = [
                fp for fp in matched
                if any(p.number == slot.net_template_pad and p.net_name == expected_net
                       for p in adapter.get_footprint_pads(fp))
            ]
            if pad_narrowed:
                matched = pad_narrowed

        if not matched:
            found_nets = sorted({n for fp in own for n in
                                 {p.net_name for p in adapter.get_footprint_pads(fp) if p.net_name}})
            refs = sorted(fp.ref for fp in own)
            problems.append(_("role {role!r}: component(s) {refs} with this role exist on the board, "
                              "but none is on net {expected!r} — they are actually on {found} "
                              "(check params/net name or the schematic connection)")
                            .format(role=role, refs=refs, expected=expected_net, found=found_nets))
            continue

        if len(matched) == 1:
            role_to_ref[role] = matched[0].ref
            continue

        narrowed, _note = _narrow_ambiguous_candidates(
            matched, clone, adapter, selected_refs, anchor_position,
            clone_placement_effective_name(clone), role, sheet_names)

        if len(narrowed) == 1:
            role_to_ref[role] = narrowed[0].ref
            continue
        # Nothing told these distinct components apart — the honest refusal of
        # rule 3 (the duplicated-refdes case was refused before the cascade).
        problems.extend(role_conflict_problems(
            adapter, role, placement_cluster, narrowed,
            extra=narrowed_by_hint(clone)))

    if problems:
        raise ValidationError(format_fatal_error(
            _("net‑based mapping failed ({name!r})").format(name=clone_placement_effective_name(clone)),
            problems
        ))

    logger.info(_("[{name}] mapped by nets: {count} roles").format(
        name=clone_placement_effective_name(clone), count=len(role_to_ref)))
    return role_to_ref


def _role_candidates(adapter, role: str, snapshot) -> list:
    """Every footprint carrying `role` — the IDENTITY half of the resolver.

    From the caller's board snapshot (``connection.snapshot``, one ``Selected``
    per footprint, its ``role`` being the same store-aware value the adapter's
    own ``get_field_value`` answers) when the caller has one, else by sweeping
    the board: one ``get_footprints()`` plus one ``get_field_value()`` PER
    footprint (the 996 field scans of the 22.09.2026 UI-thread measurement).

    The position is deliberately NOT taken from here: ``Selected.fp`` belongs to
    the snapshot's generation, so the caller of this module's snapshot branch
    re-reads the CHOSEN ref from the adapter — see `_current_generation`.

    An EMPTY snapshot is treated as NO snapshot, deliberately: the connection
    carries ``[]`` until its first poll rebuilds it, and "the board has nothing"
    would be a lie about a board nobody has looked at yet (found live — the
    re-hang guard of tests/gui/test_trees_dock.py went from green to a fatal the
    moment a stand-in's empty snapshot was believed). Both ends of the question
    — the emptiest board and the missing role — are the same fatal anyway."""
    if snapshot:
        from_snapshot = [selected.fp for selected in snapshot
                         if selected.role == role]
        if from_snapshot:
            return from_snapshot
        # A MISS in a non-empty snapshot is NOT an answer (fact Б, Denis 15.10,
        # plan_2026_10_15_honest_live_read): the snapshot can have been built
        # from the PREVIOUS board, and "the board has nothing" would then be a
        # lie about a component standing right in front of the user. The LIVE
        # board decides below — the same rule as its neighbour
        # resolve_footprint_by_cluster_role. Only a HIT is free.
    return [fp for fp in adapter.get_footprints()
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role]


def _current_generation(adapter, chosen: Footprint, snapshot) -> Footprint | None:
    """The object the resolver RETURNS: `chosen` itself when the candidates came
    from the adapter, and the adapter's OWN current generation of it when they
    came from a snapshot.

    Why the re-read (plan_2026_09_22_board_door_finish §4.3): a snapshot answers
    "which footprint is this", never "where is it NOW" — its ``Selected.fp`` was
    copied at poll time.

    None — the snapshot's ref has left the live board (deleted, renamed, or the
    snapshot belongs to ANOTHER board): that is a snapshot fact, not a board
    answer, so the caller asks the LIVE board (fact Б of
    plan_2026_10_15_honest_live_read). Only the live sweep may say "not found"."""
    if not snapshot:
        return chosen
    return adapter.get_footprint(chosen.ref)


def resolve_footprint_by_role(adapter, anchor_role: str, anchor_sheet: str | None,
                              anchor_cluster: str | None, sheet_names: dict[str, str],
                              label: str, *, snapshot=None, quiet: bool = False) -> Footprint:
    """
    Resolves ANY anchor component by anchor_role (Role field on the board,
    NOT a cell role — this is different: here we search for the anchor itself
    among ALL footprints on the board, not roles inside the cloned cell).
    Not tied to ClonePlacement — used by it (resolve_anchor_by_role below, thin
    wrapper) and by every other anchor_role consumer. The same ambiguity
    narrowing cascade:

      1. candidates = all footprints with Role == anchor_role.
      2. several — narrow by anchor_sheet (if set): the human‑readable path of fp
         (via sheet_names, see kicadstamp/sheet_names.py) contains this segment
         (see _fp_on_sheet).
      2b. still several — narrow by anchor_cluster (if set):
          Cluster field matches by prefix segments (see cluster_prefix_match) —
          independent of anchor_sheet, read from the schematic, not from UUID/sheet_path.
      3. still several — narrow to the current selection on the board.
      4. still several, or 0 — FATAL with candidate list and hints
         (anchor_sheet/anchor_cluster/selection/explicit anchor_ref).

    label — only for error messages (clone_placement_effective_name(clone)
    for ClonePlacement, rule.net for Rule — Rule has no "name", net serves
    as label).
    sheet_names — {uuid: Sheetname}, see Config.sheet_names; empty dictionary
    (schematic_dir/schematic_files not set) — anchor_sheet then never narrows
    anything (fatal checked earlier in validation.py).

    snapshot (2026-09-22, plan_2026_09_22_live_adapter_class Т2-4/Т2-7, the А+
    decision) — the caller's ``connection.snapshot`` when it OWNS one. Then the
    candidate LIST (the identity question, which is what the whole-board sweep
    was paying for) comes from the snapshot in memory, the narrowing cascade
    below runs unchanged — its cluster step still asks the adapter, but only
    when the role is genuinely ambiguous, i.e. when 2+ candidates survive the
    sheet narrowing — and the returned object comes from the adapter's CURRENT
    generation. `None` (the default, and what apply/CLI/MCP pass) = the sweep,
    byte for byte. (``quiet`` is handed to the cascade — its docstring owns it.)"""
    selected_items = adapter.get_selected_items()
    selected_refs = {i.ref for i in selected_items
                     if isinstance(i, Footprint)}

    # The candidate LIST comes from `_role_candidates`, which already refuses to
    # let a snapshot MISS decide anything (fact Б).
    candidates = _role_candidates(adapter, anchor_role, snapshot)
    if not candidates:
        raise_role_not_found(label, anchor_role)

    narrowed = _narrow_by_sheet_cluster_selection(
        candidates, adapter, selected_refs,
        anchor_sheet, anchor_cluster,
        sheet_names, label, anchor_role, quiet=quiet,
    )
    if len(narrowed) != 1:
        raise_ambiguous(label, anchor_role, narrowed)

    live = _current_generation(adapter, narrowed[0], snapshot)
    if live is not None:
        return live

    # The snapshot named a ref the live board no longer has (deleted, renamed,
    # or a snapshot of ANOTHER board): that is a snapshot fact, not a board
    # answer, so the LIVE board decides — one sweep, paid only on this miss
    # (fact Б of plan_2026_10_15_honest_live_read).
    candidates = _role_candidates(adapter, anchor_role, None)
    if not candidates:
        raise_role_not_found(label, anchor_role)
    narrowed = _narrow_by_sheet_cluster_selection(
        candidates, adapter, selected_refs,
        anchor_sheet, anchor_cluster,
        sheet_names, label, anchor_role, quiet=quiet,
    )
    if len(narrowed) != 1:
        raise_ambiguous(label, anchor_role, narrowed)
    return narrowed[0]


def resolve_anchor_by_role(adapter, clone: ClonePlacement, sheet_names: dict[str, str]) -> Footprint:
    """Thin wrapper of resolve_footprint_by_role for ClonePlacement — backward
    compatibility for calling code (clone_position_calculator.py).

    anchor_sheet supports {placeholder} substitution from clone.params (same
    mechanism as nets/net_template, via resolve_placeholder) — the removed Rule
    had no params field and always used anchor_sheet literally."""
    anchor_sheet = clone.anchor_sheet
    if anchor_sheet is not None:
        anchor_sheet = resolve_placeholder(anchor_sheet, clone.params, what="anchor_sheet")
    return resolve_footprint_by_role(adapter, clone.anchor_role, anchor_sheet,
                                     clone.anchor_cluster, sheet_names,
                                     clone_placement_effective_name(clone))
