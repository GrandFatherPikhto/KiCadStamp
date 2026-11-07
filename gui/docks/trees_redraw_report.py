# gui/docks/trees_redraw_report.py
"""The ONE wording of a finished curated Redraw — Qt-free (Н2, 07.11).

Why this module exists: `TreesDock._finish_redraw` printed "Redraw: 0/0 ok" when
the worker came back with an EMPTY result. An empty plan is not a success — it
is exactly what the user sees when the tree's anchor failed during
materialization (`entity_placement.materialize_entity_placements` logs a WARNING
naming the tree and the node and skips it). Denis met this live on HP-Power-v099:
"Redraw" of the neighbouring nodes silently did nothing, and the dock said "ok".

The lines live here, not in the giant (`trees_dock.py` is under the file-size
ratchet): the giant keeps the wiring — one call per line of output.
"""
from kicadstamp.i18n import _

__all__ = ["redraw_log_lines", "redraw_status_text"]


def _counts(results) -> tuple[int, int]:
    """(ok, total) of one worker result list of (name, good, error) triples."""
    ok = sum(1 for _n, good, _e in results if good)
    return ok, len(results)


def redraw_log_lines(results) -> list[tuple[str, str]]:
    """(level, text) pairs for the Log — one line per fact, `level` a logger
    METHOD name so the caller does not spell the mapping out again.

    An EMPTY result is a WARNING ("nothing was planned ... see the Log above"),
    never "0/0 ok": the reason lives in the lines materialization already wrote.
    A non-empty result keeps the historical pair of lines — byte for byte."""
    ok, total = _counts(results)
    if total == 0:
        return [("warning", _(
            "Redraw: nothing was planned for the selected nodes — see the Log "
            "above (a tree whose anchor failed is skipped there)"))]
    lines = [("info", _("Redraw: {ok}/{total} ok").format(ok=ok, total=total))]
    failed = total - ok
    if failed:
        lines.append(("warning", _(
            "Redraw: {failed} record(s) failed — see the log above")
            .format(failed=failed)))
    return lines


def redraw_status_text(results, warnings) -> str:
    """The status-bar text of the same run. "nothing was planned" replaces the
    "ok" wording on an empty result; warnings keep their own line, exactly as
    before (the count only — the text of each warning is in the Log)."""
    if not results:
        return _("nothing was planned — see the log")
    if warnings:
        return _("{count} warning(s) — see log").format(count=len(warnings))
    ok, total = _counts(results)
    return _("Redraw: {ok}/{total} ok").format(ok=ok, total=total)
