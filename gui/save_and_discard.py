# gui/save_and_discard.py
"""The two GLOBAL project doors that must settle an open editor form first —
File > Save (Ctrl+S) and File > Discard unsaved changes… (plan_2026_10_23_node
_form_without_apply.md, Ч1).

WHY: an edit in the Trees dock's node form lives in the WIDGETS until it is
accepted; the working set never saw it. So Save used to answer "Nothing to
save" over a half-typed rename, and Discard used to drop it without a word.
Both doors now go through this module, which is also why they moved out of
`gui/main_window.py` (a giant at its ceiling — rule 45: the bodies go beside
the flow that explains them).

The two doors answer DIFFERENTLY on purpose:

* Save ACCEPTS the form as its FIRST act and REFUSES to write when the edit
  does not validate (the file must never get a half-edited tree, and the user
  must see why);
* Discard does NOT accept: the user has just asked for the unsaved changes to
  be thrown away, so the draft goes with the working set — named in ONE info
  line, never silently.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMessageBox

from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.i18n import _

from .docks import trees_accept

logger = logging.getLogger(__name__)

__all__ = ["discard_unsaved", "settle_forms"]


def _trees_dock(window):
    """The Trees dock of `window`, or None (a rig/window without the hub)."""
    hub = getattr(window, "_dock_hub", None)
    return getattr(hub, "trees_dock", None)


def settle_forms(window, *, refuse: bool) -> bool:
    """Accept whatever the open Trees form holds. True = the door may go on.

    `refuse=True` is Save: a form that does not validate CANCELS the write and
    says so in the status line (the red Log line is written by the acceptance
    itself). `refuse=False` is the way OUT of the program: the exit goes on, a
    failed accept is only reported — an exit silently refused because of a
    half-typed rename would be worse than a lost draft the Log names."""
    dock = _trees_dock(window)
    if dock is None:
        return True
    policy = trees_accept.REFUSE if refuse else trees_accept.PROCEED
    refused = trees_accept.accept_forms(dock, policy=policy)
    if refuse and refused:
        window.status_label.setText(
            _("Save refused — the node form was not applied: {names}").format(
                names=", ".join(refused)))
        return False
    return True


def discard_unsaved(window) -> None:
    """File > Discard unsaved changes… — drop the working set and reload every
    dock from disk.

    Moved VERBATIM from `MainWindow._discard_project` (Ч1): the confirm dialog,
    the "Save instead" branch and the reload are unchanged. The ONE addition is
    the editor form: it is NOT accepted (the user said discard) and its draft
    is named in one info line, so throwing it away is never silent."""
    if not WORKING_SET.is_dirty():
        return
    reply = QMessageBox.question(
        window, _("Discard unsaved changes"),
        _("Discard will revert unsaved CONFIG edits, but NOT Redraw steps "
          "already applied to the board. Continue?"),
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel)
    if reply == QMessageBox.StandardButton.Save:
        window._save_project()
        return
    if reply != QMessageBox.StandardButton.Discard:
        return
    dock = _trees_dock(window)
    for name in trees_accept.touched_names(dock) if dock is not None else []:
        logger.info(_("Unapplied node edit {ref} was discarded with the working "
                      "set.").format(ref=name))
    with trees_accept.discarding(dock):
        WORKING_SET.clear()
        window._dock_hub.reload_project_from_disk()
    window._update_dirty_indicator()
    window.status_label.setText(_("Unsaved config changes discarded"))
