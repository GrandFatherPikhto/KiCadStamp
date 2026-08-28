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

from ...domain.geometry import Vector2

from ...domain.board import Footprint

from ...cluster_matching import cluster_prefix_match
from ...config import Cell, CellPlacement, ClonePlacement, clone_placement_effective_name
from ...exceptions import ValidationError, format_fatal_error
from ...net_resolution import RULE_NETS, resolve_net, resolve_placeholder
from .component_pool import ROLE_FIELD_NAME
from ...constants import CLUSTER_FIELD_NAME
from ...i18n import _
from .role_narrowing import (
    _narrow_ambiguous_candidates,
    _narrow_by_sheet_cluster_selection,
    narrow_candidates_by_sheet,
)

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
    candidates = [fp for fp in all_fps
                  if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role
                  and cluster_prefix_match(
                      adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '', cluster)]
    if sheet and sheet_names and len(candidates) > 1:
        candidates = narrow_candidates_by_sheet(candidates, sheet, sheet_names)
    return candidates[0] if len(candidates) == 1 else None


def candidate_nets(adapter, fp, rule_nets: set[str] | None = None) -> list[str]:
    """Sorted non-rule nets on a resolved candidate's pads."""
    rule = set(rule_nets) if rule_nets is not None else set(RULE_NETS)
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


def clone_uses_selection_mode(clone: ClonePlacement) -> bool:
    """
    Returns True if the clone is in "by selection" mode:
      - by_selection: true is explicitly set (priority — see ClonePlacement.
        by_selection is needed separately from implicit inference because params
        is ALSO used for resolving placeholders in via/track nets — without this
        flag, a params intended only for via resolution would silently switch
        the whole clone_placement to "by nets" mode, breaking roles resolved by
        selection), OR
      - neither nets nor params are set (old implicit behaviour, default for
        backward compatibility).
    This is the single place where the decision is made — both
    ClonePositionCalculator and validation.py must ask here, not duplicate the rule.
    """
    if clone.by_selection:
        return True
    return not (clone.nets or clone.params)


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
            if len(candidates) == 1:
                ref = candidates[0].ref
                role_to_ref[role] = ref
                logger.info(_("[{name}] role {role!r} -> {ref} (unique on whole board, no selection needed)")
                            .format(name=clone_name, role=role, ref=ref))
                continue

            narrowed, note = _narrow_ambiguous_candidates(
                candidates, clone, adapter, selected_refs, anchor_position, clone_name, role, sheet_names
            )
            if len(narrowed) == 1:
                role_to_ref[role] = narrowed[0].ref
            else:
                refs = sorted(fp.ref for fp in narrowed)
                problems.append(_("role {role!r} is in cell, not found in selection, and ambiguous on board "
                                  "({count} candidates: {refs}){note} — check this placement's own Cluster "
                                  "(name) is tagged correctly on the board, and/or set anchor_sheet, OR select "
                                  "the desired instance on the board before running")
                                .format(role=role, count=len(narrowed), refs=refs, note=note))

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
    Mapping by explicit/parameterised nets (without mouse selection as the
    PRIMARY mechanism — but current selection, if any, participates as a
    narrowing step, see below).

    Ambiguity resolution cascade (each step only NARROWS, never chooses for the user):
      0. clone.refs[role] — explicit override, bypassing search entirely. Breaks
         on re‑annotation (refdes is not stable) — last resort, not the main path.
      1. candidates = Role field matches AND sits on the expected net.
      2. if several candidates AND the placement's OWN sheet (clone.sheet) is
         set — narrow to candidates whose human‑readable hierarchical path
         (via sheet_names, see _fp_on_sheet) contains this sheet segment. Added
         2026-07-28 for anchor_sheet: a GLOBAL net (e.g. +3V3 shared by every
         PI‑filter on the whole board, not per‑channel like DAC_OUT_P) leaves
         candidates=every instance of the role board‑wide, and if the schematic
         groups each channel's instance under its own Channel_N sheet, this is
         the only signal that survives. Split 2026-08-15: internal narrowing now
         reads clone.sheet (the placement's own identity), NOT anchor_sheet —
         which narrows only the EXTERNAL anchor (resolve_footprint_by_role), the
         same 08-14 anchor_cluster-style conflation resolved for the Sheet
         dimension.
      3. if several candidates AND the placement's OWN Cluster (clone.cluster —
         the GUI's "Cluster:" field on the Source tab writes straight into
         cluster, see gui/docks/placer.py) matches — narrow to candidates whose
         Cluster field matches by prefix segments (see cluster_prefix_match).
         This is the main path for the typical case "N identical roles on one
         sheet because the net is common power, not per‑channel". Independent
         of step 2 — a reused hierarchical sheet shares custom fields (Cluster
         included) across every instance, so Cluster alone can't disambiguate
         THOSE cases; anchor_sheet (step 2) is what's needed there instead.
         Split 2026-08-14: the Cluster read here is clone.cluster, NOT
         clone.anchor_cluster — that field narrows only the EXTERNAL anchor
         (resolve_footprint_by_role); the two were conflated into one field
         before (Denis: "Мы печатаем два раза кластер размещаемого целла.
         Зачем?!").
      4. still several — narrow to intersection with the CURRENT selection on
         the board, if non‑empty and narrows something.
      5. still several and anchor_position is set — narrow by physical proximity
         to the anchor of THIS clone_placement: the closest candidate wins, but
         only with a clear gap (closest is at least twice as close as the second)
         — otherwise it's a coin toss, fatal. Independent of refdes/sheet/net —
         survives re‑annotation.
      6. still several — FATAL: candidates are indistinguishable by all
         available means. Suggest either splitting roles by names in the
         schematic, checking this placement's own Cluster (cluster) tagging,
         setting this placement's sheet, selecting the desired instance, or
         (last resort) explicit refs.
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
    ambiguous: list = []   # (role, expected_net, matched) for second pass

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

    # --- first pass: unambiguous by Role+net ---
    for slot in cell.components:
        role = slot.role
        if role in role_to_ref:
            continue

        if role in clone.nets:
            net_template = clone.nets[role]
        elif slot.net_template is not None:
            net_template = slot.net_template
        else:
            problems.append(_("role {role!r}: no net for mapping (neither in nets "
                              "of {name!r}, nor in cell net_template) — in 'by nets' "
                              "mode, a net is required for every role")
                           .format(role=role, name=clone_placement_effective_name(clone)))
            continue

        expected_net = resolve_net(net_template, clone.params, clone.net_overrides)

        candidates = fps_by_role.get(role, [])
        matched = []
        for fp in candidates:
            pads = adapter.get_footprint_pads(fp)
            nets_on_fp = {p.net_name for p in pads if p.net_name}
            if expected_net in nets_on_fp:
                matched.append(fp)

        # Bridging-role narrowing by the DESIGNATED PAD (Phase 1 step 1.2,
        # review 2026-08-28): a bridging role has TWO different nets on its
        # pads and net_template_pad marks which pad carries the designated
        # net. When several candidates carry the designated net (e.g. a
        # shared power rail routed onto a DIFFERENT pad number per instance),
        # narrow to candidates whose pad with that number also carries the
        # designated net. STRICTLY a secondary discriminator — it only ever
        # NARROWS, never turns a match into a miss: if the pad check yields
        # an empty set (pad numbering is unreliable for electrically
        # symmetric parts), keep the primary net-value results and let the
        # normal ambiguity cascade decide.
        if len(matched) > 1 and getattr(slot, "net_template_pad", None):
            pad_narrowed = [
                fp for fp in matched
                if any(p.number == slot.net_template_pad and p.net_name == expected_net
                       for p in adapter.get_footprint_pads(fp))
            ]
            if pad_narrowed:
                matched = pad_narrowed

        if not candidates:
            problems.append(_("role {role!r}: NO component with this role on the board at all "
                              "(check the Role field in the schematic, and that Update PCB from Schematic was run)")
                            .format(role=role))
        elif not matched:
            found_nets = sorted({n for fp in candidates for n in
                                 {p.net_name for p in adapter.get_footprint_pads(fp) if p.net_name}})
            refs = sorted(fp.ref for fp in candidates)
            problems.append(_("role {role!r}: component(s) {refs} with this role exist on the board, "
                              "but none is on net {expected!r} — they are actually on {found} "
                              "(check params/net name or the schematic connection)")
                            .format(role=role, refs=refs, expected=expected_net, found=found_nets))
        elif len(matched) > 1:
            ambiguous.append((role, expected_net, matched))
        else:
            role_to_ref[role] = matched[0].ref

    # --- narrowing ambiguous: anchor_sheet -> Cluster -> selection -> physical proximity (common function) ---
    for role, expected_net, matched in ambiguous:
        narrowed, note = _narrow_ambiguous_candidates(
            matched, clone, adapter, selected_refs, anchor_position,
            clone_placement_effective_name(clone), role, sheet_names
        )

        if len(narrowed) == 1:
            role_to_ref[role] = narrowed[0].ref
        else:
            refs = sorted(fp.ref for fp in narrowed)
            # The narrowing for INTERNAL roles always tries the placement's OWN
            # Cluster (clone.cluster — required and non-empty, see entries.py),
            # so `placement_cluster` is effectively always set here; the else
            # branch is a defensive fallback only (cluster missing/empty on a
            # non-config object, e.g. a directly-constructed test double).
            placement_sheet = getattr(clone, "sheet", None)
            placement_cluster = getattr(clone, "cluster", None)
            if placement_sheet or placement_cluster:
                narrowed_by = ", ".join(
                    (_("this placement's sheet {sheet!r}").format(sheet=placement_sheet) if placement_sheet else "",
                     _("this placement's Cluster {cluster!r}").format(cluster=placement_cluster) if placement_cluster else "")
                ).strip(", ")
                cluster_hint = _(" (already narrowed by {narrowed_by}, but not enough)").format(narrowed_by=narrowed_by)
            else:
                cluster_hint = _(" (neither this placement's sheet nor its Cluster set — if these components are "
                                 "physically different instances, one of them would narrow to one)")
            problems.append(
                _("role {role!r}: ambiguity — {count} components on net {net!r}{cluster_hint}{note}: {refs}. "
                  "Solutions: check this placement's own Cluster (cluster) is tagged correctly on the board, "
                  "and/or set this placement's sheet, OR select the desired instance on the board before running, "
                  "OR split roles by net names in the schematic (e.g. DAC_PI_3V3_C1 vs DAC_PI_AVDD_C1), "
                  "OR use explicit refs: {{ {role}: {first_ref} }}")
                .format(role=role, count=len(narrowed), net=expected_net,
                        cluster_hint=cluster_hint, note=note, refs=refs,
                        first_ref=refs[0])
            )

    if problems:
        raise ValidationError(format_fatal_error(
            _("net‑based mapping failed ({name!r})").format(name=clone_placement_effective_name(clone)),
            problems
        ))

    logger.info(_("[{name}] mapped by nets: {count} roles").format(
        name=clone_placement_effective_name(clone), count=len(role_to_ref)))
    return role_to_ref


def resolve_footprint_by_role(adapter, anchor_role: str, anchor_sheet: str | None,
                              anchor_cluster: str | None, sheet_names: dict[str, str],
                              label: str) -> Footprint:
    """
    Resolves ANY anchor component by anchor_role (Role field on the board,
    NOT a cell role — this is different: here we search for the anchor itself
    among ALL footprints on the board, not roles inside the cloned cell).
    Not tied to ClonePlacement — used both by it (resolve_anchor_by_role below,
    thin wrapper) and by Rule (see manual_position_calculator.py) for anchor_role
    in spoke paths. The same ambiguity narrowing cascade:

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
    """
    all_fps = adapter.get_footprints()
    candidates = [fp for fp in all_fps
                  if adapter.get_field_value(fp, ROLE_FIELD_NAME) == anchor_role]

    if not candidates:
        raise ValidationError(format_fatal_error(
            _("{label}: anchor_role {role!r} not found on any component on the board")
            .format(label=label, role=anchor_role),
            [_("check that the Role field is set in the schematic and propagated to the PCB "
               "(Update PCB from Schematic)")]
        ))

    selected_items = adapter.get_selected_items()
    selected_refs = {i.ref for i in selected_items
                     if isinstance(i, Footprint)}

    narrowed = _narrow_by_sheet_cluster_selection(
        candidates, adapter, selected_refs,
        anchor_sheet, anchor_cluster,
        sheet_names, label, anchor_role,
    )

    if len(narrowed) == 1:
        return narrowed[0]

    refs = sorted(fp.ref for fp in narrowed)
    raise ValidationError(format_fatal_error(
        _("{label}: anchor_role {role!r} is ambiguous").format(label=label, role=anchor_role),
        [_("candidates: {count} — {refs}. Solutions: refine anchor_sheet "
           "and/or anchor_cluster, OR select the desired instance on the board "
           "before running, OR use explicit anchor_ref instead of anchor_role: {first_ref!r}")
         .format(count=len(narrowed), refs=refs, first_ref=refs[0])]
    ))


def resolve_anchor_by_role(adapter, clone: ClonePlacement, sheet_names: dict[str, str]) -> Footprint:
    """Thin wrapper of resolve_footprint_by_role for ClonePlacement — backward
    compatibility for calling code (clone_position_calculator.py).

    anchor_sheet supports {placeholder} substitution from clone.params (same
    mechanism as nets/net_template, via resolve_placeholder) — unlike Rule
    (manual_position_calculator.py), which has no params field and always
    uses anchor_sheet literally."""
    anchor_sheet = clone.anchor_sheet
    if anchor_sheet is not None:
        anchor_sheet = resolve_placeholder(anchor_sheet, clone.params, what="anchor_sheet")
    return resolve_footprint_by_role(adapter, clone.anchor_role, anchor_sheet,
                                     clone.anchor_cluster, sheet_names,
                                     clone_placement_effective_name(clone))
