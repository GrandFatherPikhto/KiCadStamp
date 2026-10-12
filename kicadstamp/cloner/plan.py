# kicadstamp/cloner/plan.py
"""
`verify_channel_net_mapping()` — the net part of trace transfer in production:
it runs net_matching (Kuhn + Tarjan SCC, Phase 0) on the two channels'
role<->net evidence. SCC ambiguity is DIAGNOSTIC, never a stop (safe-default
thesis: every member of an ambiguous SCC is a formally correct answer) — exactly
the plan rule "SCC = диагностика, не стоп".

This is all that is left of the Phase 3 `clone-plan` module: its
`plan_clone_placements()` / `clone_placements_to_dict()` generator wrote a root
`clone_placements:` block and was removed with that section
(plan_2026_10_08_remove_placer.md). The live consumer is `channel_copy.py`
(Clone Sheets), which calls the verifier here directly.

The role<->net graph is built by the caller from the per-footprint Role + pad
nets the PCB parser captures (see PcbFootprint.role / .pad_nets).
"""

from ..exceptions import ValidationError
from ..i18n import _
from ..net_matching import Graph, match_template_to_target


def verify_channel_net_mapping(source_role_nets: dict[str, list[str]],
                               target_role_nets: dict[str, list[str]],
                               source_channel: str, target_channel: str) -> list[str]:
    """Phase 3 step 3.2: run net_matching (Kuhn + Tarjan SCC, Phase 0) on the
    two channels' role<->net evidence. Returns diagnostics:
      - a ValidationError from net_matching (roles/nets not isomorphic) — the
        prefix-remap produced nets that don't exist on the target;
      - one entry per SCC ambiguity group (electrically symmetric roles) —
        every assignment in the group is a valid answer; disambiguate by
        sheet/cluster when several instances share these nets.
    NEVER raises — SCC is a diagnostic layer, not a hard human stop."""
    diagnostics: list[str] = []
    if not source_role_nets or not target_role_nets:
        return diagnostics

    def _graph(role_nets: dict[str, list[str]]) -> Graph:
        return Graph(
            roles={role: {i + 1: net for i, net in enumerate(pads)}
                   for role, pads in role_nets.items()},
        )

    try:
        _mapping, ambiguous = match_template_to_target(
            _graph(source_role_nets), _graph(target_role_nets))
    except ValidationError as exc:
        diagnostics.append(_("net_matching {src} -> {dst}: {error}")
                           .format(src=source_channel, dst=target_channel, error=exc))
        return diagnostics
    for group in ambiguous:
        diagnostics.append(
            _("net_matching {src} -> {dst}: electrically symmetric roles in group "
              "{roles} — any assignment is valid; disambiguate by sheet/cluster "
              "when several instances share these nets")
            .format(src=source_channel, dst=target_channel,
                    roles=", ".join(sorted(group))))
    return diagnostics
