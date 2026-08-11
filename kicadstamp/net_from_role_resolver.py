# kicadstamp/net_from_role_resolver.py
"""Pure net-from-role resolution logic (plan step 2).

Ports the algorithm validated live in
`kicadstamp/diagnostics/net_from_role_audit.py` (lemma 2 / explicit pad /
geometric tiebreak — see techdocs/handoff/handoff_2026_08_11_net_from_role_
audit_validation.md) into the core, WITHOUT the cluster-name-matching
machinery that the OFFLINE diagnostic needed. Here the set of roles and their
real pad nets is already known at the call site:

  - apply: `role_to_ref` is resolved before geometry (clone_position_calculator),
    so `role_nets` is built from the resolved refs' real pads;
  - extract: roles + their live pad nets come from the current selection.

This module is pure geometry + graph math: no live-board access, no YAML, no
printing. Callers supply the role->net data and get back `(role, pad_or_None)`
for the via/track's net, or a fatal `ValidationError` when the net cannot be
resolved (no role pad on it, or ambiguity that geometry cannot break) — no
silent fallback (project rule: what breaks must say exactly what to fix).

Classification (same as the diagnostic):
  - rule   : net is a rule net (GND by default) -> `net: null`, no role needed;
  - lemma2 : a role whose non-rule nets are exactly {net} -> (role, None);
  - pad    : only multi-net roles sit on this net -> (role, pad) — the pad
             names which pad of the role carries `net`;
  - fallback/ambiguous: no candidate on `net`, or several lemma2 candidates
    with geometry disabled -> ValidationError.
"""

from __future__ import annotations

from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _

# Rule nets — a via/track on one of these needs no role at all (net: null).
# Homed in net_resolution.py (the neutral low-level net module); imported here
# directly — this module sits alongside it at the top level, so there is no
# placement -> net_resolution caller chain to worry about (that chain is why
# RULE_NETS was originally defined in net_resolution, not here). Configurability
# is added as a separate step only if a real need appears (plan §3).
from kicadstamp.net_resolution import RULE_NETS  # noqa: E402


def canonical_role(role: str) -> str:
    """Casefold a role name for matching.

    Deliberately NO board/schematic-specific synonyms here (e.g. a cell's
    FB_PI_FLT vs the schematic's PI_FB): this is a general core module, and a
    per-board name-mapping is a config concern, not code. Callers that need
    synonyms normalise both sides of their input to a single namespace BEFORE
    calling the resolver (see the diagnostic's local _canonical_role)."""
    return role.casefold()


def _casefold_index(role_nets: dict) -> dict[str, str]:
    """{canonical role -> actual role} for a role->nets map.

    Callers may name roles in a different case/synonym than the source of
    `role_nets` (C_In_Bulk in a cell vs C_IN_BULK on the board) — canonicalise
    before matching.
    """
    return {canonical_role(r): r for r in role_nets}


def role_net_sets(role_nets: dict[str, dict[str, set[str]]], role: str):
    """(all_nets, {pad: net}) for one role in one context."""
    pads = role_nets.get(role, {})
    all_nets = set()
    for ns in pads.values():
        all_nets |= ns
    return all_nets, pads


# --------------------------------------------------------------------------
# Geometry (pure, rotation-invariant local coordinates)
# --------------------------------------------------------------------------

def _slot_pos(comp) -> tuple[float, float]:
    """(along, across) local centre of a component slot — accepts a
    TemplateComponentSlot dataclass or a raw dict (diagnostic wrapper)."""
    if isinstance(comp, dict):
        return (float(comp.get("offset_along_mm", 0.0)),
                float(comp.get("offset_across_mm", 0.0)))
    return float(comp.offset_along_mm), float(comp.offset_across_mm)


def _slot_role(comp) -> str:
    if isinstance(comp, dict):
        return str(comp.get("role") or "")
    return str(comp.role)


def slot_by_role(comps, role: str):
    """First component slot whose role canonicalises to `role` (case/synonym
    tolerant). Returns None if no slot matches — the caller must not rely on
    a phantom origin for the tiebreak."""
    want = canonical_role(role)
    for c in comps:
        if canonical_role(_slot_role(c)) == want:
            return c
    return None


def local_points(entry) -> list[tuple[float, float]]:
    """Local (along, across) points of a via (1) or track (2 endpoints).

    Accepts a TemplateVia/TemplateTrack dataclass or a raw dict. Cell
    coordinates are rotation-invariant among themselves: distance between two
    local points does not change under the cell's rotation, so choosing the
    nearest component by local distance is valid without the absolute
    transform.
    """
    if isinstance(entry, dict):
        if "offset_along_mm" in entry:  # a via
            return [(float(entry.get("offset_along_mm", 0.0)),
                     float(entry.get("offset_across_mm", 0.0)))]
        return [  # a track
            (float(entry.get("start_along_mm", 0.0)),
             float(entry.get("start_across_mm", 0.0))),
            (float(entry.get("end_along_mm", 0.0)),
             float(entry.get("end_across_mm", 0.0))),
        ]
    if hasattr(entry, "offset_along_mm"):  # TemplateVia
        return [(float(entry.offset_along_mm), float(entry.offset_across_mm))]
    return [  # TemplateTrack
        (float(entry.start_along_mm), float(entry.start_across_mm)),
        (float(entry.end_along_mm), float(entry.end_across_mm)),
    ]


def dist_to_component(points: list[tuple[float, float]], comp) -> float:
    """Min Euclidean distance (mm) from a via/track's points to a component's
    local centre. Pure local-cell geometry (rotation-invariant)."""
    cx, cy = _slot_pos(comp)
    best = float("inf")
    for px, py in points:
        d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        if d < best:
            best = d
    return best


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def classify_net(role_nets: dict[str, dict[str, set[str]]], net: str,
                 candidate_roles: set[str],
                 rule_nets: set[str] = RULE_NETS,
                 points: list[tuple[float, float]] | None = None,
                 components: list | None = None,
                 use_geometry: bool = True) -> tuple[str | None, str | None]:
    """Resolve which role (and pad) carries `net`.

    Args:
        role_nets:       {role: {pad: {nets}}} — the roles and their real pad
                         nets in the current context (one clone / selection).
        net:             the via/track's (already resolved) net.
        candidate_roles: roles of the cell/selection that may own this via —
                         subset of the keys in `role_nets` by canonical name.
        rule_nets:       nets that need no role (net: null).
        points:          optional local (along, across) points of the via/track
                         — enables the geometric tiebreak for |R(n)| > 1.
        components:      optional component slots (with role/offset_along_mm/
                         offset_across_mm) to measure the tiebreak against.
        use_geometry:    False disables the tiebreak (deterministic first
                         candidate instead of nearest) — for callers that have
                         no geometry and must not silently guess.

    Returns:
        (role, pad_or_None):
          - (None, None)   net is a rule net -> write `net: null`;
          - (role, None)   lemma 2 — role with exactly one non-rule net;
          - (role, pad)    multi-net role — `pad` is the pad carrying `net`.

    Raises:
        ValidationError when no candidate role touches `net` (fallback), when
        several lemma2 candidates exist but geometry is unavailable/disabled
        (|R(n)| > 1 with no way to break the tie). Never guesses silently.
    """
    if net in rule_nets:
        return None, None  # rule net — no role needed

    candidates = candidates_for(role_nets, net, candidate_roles)
    if not candidates:
        raise ValidationError(format_fatal_error(
            _("no role pad on net {net!r} (M(n) empty)").format(net=net),
            [_("none of the candidate roles {roles} touches net {net!r} — this is "
               "a template/board mismatch, not something to guess")
             .format(roles=sorted(canonical_role(r) for r in candidate_roles)
                     or [_("<no candidate roles>")], net=net)]))

    # Lemma 2: prefer a role whose non-rule nets are exactly {net} — no pad
    # needed, the net identifies the role structurally.
    lemma2_roles = lemma2_candidates(role_nets, candidates, rule_nets, net)
    if len(lemma2_roles) == 1:
        return lemma2_roles[0], None
    if len(lemma2_roles) > 1:
        if use_geometry and points is not None and components is not None:
            r = min(lemma2_roles,
                    key=lambda rr: _dist_geometric(points, components, rr))
            return r, None
        raise ValidationError(format_fatal_error(
            _("net {net!r} is shared by several single-net roles: {roles}")
            .format(net=net, roles=sorted(lemma2_roles)),
            [_("several roles each have {net!r} as their only non-rule net — "
               "geometry (nearest component) or an explicit role choice is "
               "required; without it this cannot be resolved deterministically")
             .format(net=net)]))

    # All candidates are multi-net roles -> explicit pad is mandatory. Any
    # candidate whose pad carries `net` resolves to the same net, so choosing
    # deterministically (geometry or first) is not a net guess.
    if use_geometry and points is not None and components is not None \
            and len(candidates) > 1:
        r = min(candidates, key=lambda rr: _dist_geometric(points, components, rr))
    else:
        r = sorted(candidates)[0]
    _all_nets, pads = role_net_sets(role_nets, r)
    pad_txt = sorted(p for p, ns in pads.items() if net in ns)[0]
    return r, pad_txt


def candidates_for(role_nets: dict[str, dict[str, set[str]]], net: str,
                   candidate_roles: set[str]) -> list[str]:
    """Roles (actual keys of `role_nets`, canonical-tolerant) whose pads touch
    `net`. Shared by classify_net and the diagnostic's report — the decision
    logic lives in one place."""
    role_index = _casefold_index(role_nets)
    out: list[str] = []
    for r in candidate_roles:
        actual = role_index.get(canonical_role(r))
        if actual is not None and net in role_net_sets(role_nets, actual)[0]:
            out.append(actual)
    return out


def lemma2_candidates(role_nets: dict[str, dict[str, set[str]]], candidates: list[str],
                      rule_nets: set[str], net: str) -> list[str]:
    """Roles among `candidates` whose non-rule nets are exactly {net} (lemma 2)
    — sorted deterministically. Shared by classify_net and the diagnostic's
    report (which shows this subset in its "nearest of [...]" note)."""
    return [r for r in sorted(candidates)
            if {n for n in role_net_sets(role_nets, r)[0] if n not in rule_nets} == {net}]


def _dist_geometric(points, components, role: str) -> float:
    """Distance from via/track points to the nearest slot matching `role`.

    Slots missing from `components` are deliberately not a candidate for the
    tiebreak: a role without a slot cannot own the via geometrically.
    """
    comp = slot_by_role(components, role)
    if comp is None:
        return float("inf")
    return dist_to_component(points, comp)
