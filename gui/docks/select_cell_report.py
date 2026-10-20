# gui/docks/select_cell_report.py
"""What the CellDock SAYS about one «Select cell» / «Select cell components»
worker result — the whole answer table in one pure function.

Split out of `gui/docks/cell_editor.py` on 2026-10-20 (доделка Ч4 of
plan_2026_10_15_extract_and_read_paths_cleanup): the Ч4 duplicate branch pushed
that giant from 2812 to 2820 lines, and the "giants only shrink" rule is a
ratchet — the ceiling for cell_editor.py is 2812. The split is a verbatim lift:
the slot now asks this module for (text, style) and shows it, and nothing here
touches a widget, a board or the config (Qt-free, pure data in / data out).

The answers, in the order the worker's result can carry them:

  * `error`      — the worker itself failed                       ERROR
  * `duplicate`  — a refdes stands on the board twice: the duplicate guard's OWN
                   lines, joined verbatim (Ч4 — never re-worded as "stale": the
                   fix is deleting the extra copy, not re-identifying)  ERROR
  * `stale`      — the remembered refs no longer match the board   WARNING
  * `line`       — «Select cell» with copper: the counters line    SUCCESS
  * `identified` — the identified pair got selected                SUCCESS
  * `selected`   — a whole cluster got selected                    SUCCESS
  * otherwise    — nothing of that cluster is on the board         WARNING

No modal is ever part of an answer: this stage's rule is "no modal windows", and
the error path here used to be the last QMessageBox.warning of the three live
flows of that dock (X.1.3 of test_cell_editor_live_flows…)."""
from __future__ import annotations

from kicadstamp.i18n import _

from ._common import (ERROR_STYLE as _ERROR_STYLE,
                      SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE)

__all__ = ["select_cell_report"]


def select_cell_report(result: dict) -> tuple[str, str]:
    """(text, style) for ONE worker result of «Select cell» /
    «Select cell components» — see the module docstring for the order."""
    if result.get("error"):
        return (_("Select cluster failed: {error}").format(error=result["error"]),
                _ERROR_STYLE)
    if result.get("duplicate"):
        # Ч4: the duplicate guard's lines, VERBATIM (both copies, which one is an
        # orphan, what to do) — and nothing was selected.
        return "\n".join(result["duplicate"]), _ERROR_STYLE
    if result.get("stale"):
        return (_("the remembered refs are stale ({reasons}) — nothing was "
                  "selected; identify the instance again with “Fill from "
                  "selection” on the cell-anchor page")
                .format(reasons="; ".join(result["stale"])), _WARN_STYLE)
    if result.get("line"):
        # «Select cell» with copper (СЦ-2): the honest counters line, ready for
        # "Re-read by selection".
        return result["line"], _SUCCESS_STYLE
    # часть 3, п.4: the CellDock buttons are gone, so these two lines send the
    # user to the ENTITY leaf's item instead — a message pointing at a caption
    # that is no longer on screen is the untruth Р6 of
    # plan_2026_09_17_cell_dialog_min_width removed.
    if result.get("identified"):
        return (_("Selected the {count} identified footprint(s) of this cell on "
                  "the board — ready for “Update from selection…”.")
                .format(count=result["selected"]), _SUCCESS_STYLE)
    if result["selected"]:
        return (_("Selected {count} footprint(s) of cluster {cluster!r} on the "
                  "board — ready for “Update from selection…”.")
                .format(count=result["selected"], cluster=result["cluster"]),
                _SUCCESS_STYLE)
    return (_("No footprint of cluster {cluster!r} is on the current board — the "
              "remembered context no longer resolves; select the cluster by hand.")
            .format(cluster=result["cluster"]), _WARN_STYLE)
