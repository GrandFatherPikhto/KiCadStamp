# kicadstamp/placement/services/role_refusals.py
"""The TWO wordings of a failed anchor_role resolve — absence and ambiguity.

Extracted from `clone_role_resolver.py` by the "giants only shrink" rule (the
contract: a file past 800 lines gets no new code, only wiring — Ч1-Б of
plan_2026_10_15_honest_live_read needed one more wording, so the pair left the
module instead). Each wording has exactly ONE source, because the SAME question
is now answered from two places — the caller's snapshot and the live sweep — and
a caller must never be told two different things about the same absence or the
same ambiguity.

Both raise `ValidationError` built by `format_fatal_error`, so the CLI/MCP path
and the GUI's Log path show the identical text.
"""
from kicadstamp.exceptions import ValidationError, format_fatal_error
from kicadstamp.i18n import _


def raise_role_not_found(label: str, role: str | None) -> None:
    """The canonical "this Role is on nothing" fatal — ONE wording for both
    sources of candidates (the sweep and the snapshot)."""
    raise ValidationError(format_fatal_error(
        _("{label}: anchor_role {role!r} not found on any component on the board")
        .format(label=label, role=role),
        [_("check that the Role field is set in the schematic and propagated to the PCB "
           "(Update PCB from Schematic)")]
    ))


def raise_ambiguous(label: str, role: str | None, narrowed: list) -> None:
    """The canonical "this Role is on several components" fatal — ONE wording for
    both candidate sources (the snapshot and the live sweep)."""
    refs = sorted(fp.ref for fp in narrowed)
    raise ValidationError(format_fatal_error(
        _("{label}: anchor_role {role!r} is ambiguous").format(label=label, role=role),
        [_("candidates: {count} — {refs}. Solutions: refine anchor_sheet "
           "and/or anchor_cluster, OR select the desired instance on the board "
           "before running, OR use explicit anchor_ref instead of anchor_role: {first_ref!r}")
         .format(count=len(narrowed), refs=refs, first_ref=refs[0])]
    ))
