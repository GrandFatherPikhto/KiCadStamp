# gui/read_outcome.py
"""What a read TOOK out of the selection — said in ONE line, and the items that
become the new board selection (part Г of
plan_2026_10_08_narrowing_net_traces_cost; Denis 2026-10-08).

WHY: «Update from selection» / «Add selected copper» are explicit actions, and
the user's next question is always the same — "did it read what I selected?".
The answer exists in the worker (the selection was split, filtered and narrowed
there), and it was never said: the counts of what was SKIPPED were spread over
several lines (the instance line, the subtraction line, the per-layer report) and
the selection itself stayed as the user left it. The rule, in Denis' words:

    «чтение ЗАМЕНЯЕТ выделение ровно тем, что прочитано; не взятое из выделения
    пропадает из него» + «read N of M selected items; skipped K: <reasons>».

So this module owns TWO things, and nothing else:

  * `ReadOutcome.items` — exactly what the read took (the plan's own inputs, in
    the plan's own order), which the worker hands to ONE ``adapter.select_items``;
  * `ReadOutcome.line` — the one Log line, with the skip REASONS and their counts
    (another cluster / other record's copper / inter-node copper / layer off).

Qt-free and board-free by design (deepseek.md §45): the worker passes plain data
in, the finish handler prints the line. A REFUSAL builds nothing (the caller keeps
its own red line and never touches the selection) — `read_outcome` is simply not
called on that path, and the caller asserts that by construction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from kicadstamp.i18n import _

logger = logging.getLogger(__name__)

__all__ = ["ReadOutcome", "read_outcome", "replace_selection", "select_after_read"]


@dataclass(frozen=True)
class ReadOutcome:
    """``items`` — the live items the new selection is set to (components + the
    copper that entered the read). ``line`` — the ONE Log line, empty when the
    read took nothing at all (nothing to say beyond the other lines).
    ``selected``/``read``/``skipped`` are the numbers of that line, kept as data
    so a cell can assert them without parsing the text."""

    items: tuple = ()
    line: str = ""
    selected: int = 0
    read: int = 0
    skipped: int = 0

    @property
    def replaced(self) -> bool:
        """True when the read has something to select back. A read of nothing
        (an empty selection) leaves the board selection alone — an explicit
        action that took nothing must not silently CLEAR it."""
        return bool(self.items)


def _skip_parts(*, selected_components, plan_components, layer_off,
                other_records, inter_node, skipped) -> list:
    """The reasons of the line, as ``[(label, count), ...]`` — only the non-zero
    ones, and only the parts the caller can name. A remainder the parts do not
    explain is named as such (`not read`), never hidden."""
    parts: list = []
    # A component the read did NOT take is one of another cluster (the read takes
    # its own instance's components, wherever they sit) — by IDENTITY, so a live
    # item the plan really holds is never counted as a skip.
    plan_ids = {id(fp) for fp in plan_components}
    other_cluster = sum(1 for fp in selected_components if id(fp) not in plan_ids)
    if other_cluster:
        parts.append((_("another cluster"), other_cluster))
    if other_records:
        parts.append((_("another record's copper"), other_records))
    if inter_node:
        parts.append((_("inter-node copper"), inter_node))
    if layer_off:
        parts.append((_("layer off"), layer_off))
    known = sum(count for _label, count in parts)
    if skipped > known:
        parts.append((_("not read"), skipped - known))
    return parts


def read_outcome(*, footprints, vias, raw_tracks, plan_footprints, plan_vias,
                 plan_tracks, prelude, layer_report) -> ReadOutcome:
    """Build the outcome of ONE read from what the worker already knows.

    ``footprints``/``vias``/``raw_tracks`` — the LIVE selection as it came off the
    board (raw_tracks BEFORE the layer filter: a track on a layer that is not read
    was selected and is then NOT read — that is exactly one of the reasons).
    ``plan_*`` — what the read actually plans on (the instance's components and the
    kept copper; on the no-prelude path the selection itself, minus the filtered
    tracks). ``prelude`` — the mixed-selection prelude, for the counts of the
    copper subtracted for OTHER records and for the inter-node copper (net_traces)
    it subtracted (``subtracted_records`` / ``subtracted_net_traces``).
    ``layer_report`` — the per-layer report of the track filter (``read`` /
    ``skipped_manual``), already used for the layer lines."""
    plan_footprints = list(plan_footprints or ())
    plan_vias = list(plan_vias or ())
    plan_tracks = list(plan_tracks or ())
    selected_total = len(footprints or ()) + len(vias or ()) + len(raw_tracks or ())
    read_total = len(plan_footprints) + len(plan_vias) + len(plan_tracks)
    skipped = max(0, selected_total - read_total)
    items = tuple(plan_footprints) + tuple(plan_vias) + tuple(plan_tracks)
    if not selected_total:
        # Nothing was selected: nothing to report and nothing to select back.
        return ReadOutcome(items=items, selected=0, read=0, skipped=0)

    # A layer that had copper in the selection and was not read: the count of the
    # tracks that stayed out (the layer report names the layers themselves, and
    # `layer_off` is 0 when every layer was read).
    layer_off = (max(0, len(raw_tracks or ()) - len(plan_tracks))
                 if layer_report.get("skipped_manual") else 0)
    other_records = sum(count for _label, count in
                        getattr(prelude, "subtracted_records", ()) or ())
    inter_node = sum(count for _label, count in
                     getattr(prelude, "subtracted_net_traces", ()) or ())
    parts = _skip_parts(selected_components=list(footprints or ()),
                        plan_components=plan_footprints, layer_off=layer_off,
                        other_records=other_records, inter_node=inter_node,
                        skipped=skipped)
    line = _("read {read} of {selected} selected item(s)").format(
        read=read_total, selected=selected_total)
    if skipped:
        line += _("; skipped {count}: {reasons}").format(
            count=skipped,
            reasons="; ".join(f"{label} — {count}" for label, count in parts))
    return ReadOutcome(items=items, line=line, selected=selected_total,
                       read=read_total, skipped=skipped)


def replace_selection(adapter, *, footprints, vias, raw_tracks, plan_footprints,
                      plan_vias, plan_tracks, prelude, layer_report) -> ReadOutcome:
    """Build the outcome AND set the board selection to it — the ONE call both reads
    make, so the docks keep no rule of their own (rule 45: a giant gets wiring).

    A selection write is BEST-EFFORT and is the last thing the read does: the plan
    is already built and must not be lost because KiCad refused a highlight. The
    outcome comes back either way — the line is what the Log needs."""
    outcome = read_outcome(
        footprints=footprints, vias=vias, raw_tracks=raw_tracks,
        plan_footprints=plan_footprints, plan_vias=plan_vias,
        plan_tracks=plan_tracks, prelude=prelude, layer_report=layer_report)
    if outcome.replaced:
        try:
            adapter.select_items(list(outcome.items))
        except Exception:  # noqa: BLE001 — a selection write is best-effort
            logger.exception("select-after-read failed")
    return outcome


def select_after_read(adapter, *, selection, kept, prelude, layer_report) -> ReadOutcome:
    """`replace_selection` for a READ's OWN data, in ONE call: ``selection`` is
    what the board had selected — ``(footprints, vias, raw_tracks)`` — and
    ``kept`` is what the read went on with — ``(footprints, vias, tracks)``.
    Both groups are exactly what the worker already holds.

    Exists so a dock's worker keeps WIRING only (rule 45): naming those six lists
    at every call site is what grew `gui/docks/cell_editor.py` by ten lines for
    part Г of plan_2026_10_08_narrowing_net_traces_cost."""
    sel_f, sel_v, sel_t = selection
    keep_f, keep_v, keep_t = kept
    return replace_selection(
        adapter, footprints=sel_f, vias=sel_v, raw_tracks=sel_t,
        plan_footprints=keep_f, plan_vias=keep_v, plan_tracks=keep_t,
        prelude=prelude, layer_report=layer_report)
