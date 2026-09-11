# gui/docks/extract_diagnostics.py
"""Presentation of the structured ClusterRejection reasons produced by
gui/docks/reead.py as a user-facing message.

This is the ONLY place the engine's reasons become translated text: the engine
(gui/docks/reead.py) stays free of Qt and gettext (plan_2026_09_11_extract_
selection_diagnostics V.2). "Extract tree...", "Extract cluster..." and
"Instantiate from Cell..." all route through here, so the four gates read the
same everywhere.

The Ref lists in a message are capped (the same `missing[:8]` discipline the
stale-snapshot line in gui/main_window.py uses) so a growing selection cannot
blow up a QMessageBox; rejections_log_detail() returns the UNTRUNCATED version
for the Log.
"""
from typing import Iterable

from kicadstamp.i18n import _

from .reead import (
    REASON_NO_CLUSTER_TAG,
    REASON_PAIR_NOT_IN_SNAPSHOT,
    REASON_PARTIAL,
    REASON_SHEET_MAP_EMPTY,
    REASON_SHEET_UNRESOLVED,
)

# How many refs fit inside the on-screen message. The full list is always
# available through rejections_log_detail() (the Log).
_MAX_REFS = 8


def _join_refs(refs, limit) -> str:
    refs = list(refs or ())
    if limit is None or len(refs) <= limit:
        return ", ".join(refs)
    return ", ".join(refs[:limit]) + _(" (+{n} more)").format(n=len(refs) - limit)


def _describe(rej, *, refs_limit=_MAX_REFS) -> str:
    """Translate ONE ClusterRejection into a sentence, or "" for a reason this
    build does not know (forward-compatible: skip it, never crash)."""
    if rej.reason == REASON_NO_CLUSTER_TAG:
        return _(
            "The selection has {count} footprint(s) without a Cluster tag: "
            "{refs}. A cluster can be extracted only from components that carry "
            "the Cluster field (not vias/tracks).").format(
                count=len(rej.refs), refs=_join_refs(rej.refs, refs_limit))
    if rej.reason == REASON_SHEET_MAP_EMPTY:
        return _(
            "No sheet could be resolved: the config has no schematic_dir (or "
            "schematic_files) — without it sheet names are unknown everywhere, "
            "so sheet-based narrowing cannot work and a Cluster placed once per "
            "channel cannot be told apart. Set schematic_dir in the project "
            "root config.")
    if rej.reason == REASON_SHEET_UNRESOLVED:
        return _(
            "Cluster {cluster!r}: its sheet did not resolve even though "
            "schematic_dir is set — check that the schematic files cover this "
            "footprint's sheet path.").format(cluster=rej.cluster)
    if rej.reason == REASON_PAIR_NOT_IN_SNAPSHOT:
        return _(
            "Cluster {cluster!r} / sheet {sheet!r}: this pair is not in the "
            "board snapshot — Refresh the snapshot (the component may have been "
            "added or renamed after connecting).").format(
                cluster=rej.cluster, sheet=rej.sheet)
    if rej.reason == REASON_PARTIAL:
        return _(
            "Cluster {cluster!r} / sheet {sheet!r}: selected {selected} of "
            "{total}, missing: {missing}.").format(
                cluster=rej.cluster, sheet=rej.sheet,
                selected=rej.selected, total=rej.total,
                missing=_join_refs(rej.missing, refs_limit))
    return ""


def _dedupe(texts: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for text in texts:
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def format_cluster_rejections(rejections: Iterable) -> str:
    """Every rejection as its own line — the WHOLE picture, never just the
    first cause (plan V.2). Identical lines (e.g. the global "no
    schematic_dir") collapse to one. Empty string when there is nothing to
    say, so the caller can fall back to its own generic wording."""
    return "\n".join(_dedupe(_describe(rej) for rej in rejections))


def rejections_log_detail(rejections: Iterable) -> str:
    """The same reasons with UNTRUNCATED Ref lists, for the Log (the on-screen
    message keeps the short list — see _MAX_REFS)."""
    return "\n".join(_dedupe(
        _describe(rej, refs_limit=None) for rej in rejections))
