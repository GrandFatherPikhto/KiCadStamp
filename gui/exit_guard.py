# gui/exit_guard.py
"""The way OUT of the program — the two questions, and the order they are asked in.

У1 (2026-10-15, Ч1 of plan_2026_10_15_extract_and_read_paths_cleanup): one
unsaved-changes question, then one «Вернуть и выйти», asked in that order by BOTH
real ways out — the title-bar X with the tray disabled (MainWindow.closeEvent) and
MainWindow._quit (the tray menu's Quit and File > &Quit). Cancel of either question
cancels the quit; the hide-to-tray branch asks nothing.

Why a module rather than methods on the window: the RULES live in the docstrings
here (one question from its one host, the deliberate dialog exception, the once-per-
exit flag and why it is the one the explode flow already had), and gui/main_window.py
is one of the project's giants (§45) — prose like that belongs beside the flow it
explains, not in the widget. gui/explode_wiring.py is the same shape for the "Разнос"
flow, and this module is its sibling for the exit.

Qt-light on purpose: nothing here builds a widget, calls event.ignore() or reaches
for the window's base class. The window performs the Qt calls it owns, and keeps
thin seams with the SAME names — MainWindow.closeEvent / _confirm_unsaved_before_exit
/ _quit / _do_quit — because the Ч1 guards, the mutations, `quit_action` and DockHub's
tray wiring all call THOSE names.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from . import settings
from .save_and_discard import settle_forms

__all__ = [
    "confirm_unsaved_before_exit",
    "do_quit",
    "quit",
    "tray_hides_instead_of_quitting",
]


def tray_hides_instead_of_quitting(window) -> bool:
    """True when the title-bar X must only HIDE the window: with the tray enabled
    the program keeps running, so no staged edit can be lost and the unsaved-
    changes question would be a lie.

    Read from the APPLIED settings state, never from the checkbox — a toggle alone
    is a draft until the Settings dialog's apply() persists it (see
    gui/docks/configurator.py)."""
    return bool(settings.state.get("tray_enabled", False))


def confirm_unsaved_before_exit(window) -> bool:
    """True = the way out may go on, False = STAY (the user cancelled).

    У1 (2026-10-15, Ч1 of plan_2026_10_15_extract_and_read_paths_cleanup): the
    ONE unsaved-changes guard of the way OUT of the program, asked by the
    title-bar X (closeEvent, real-quit branch) and by _quit (the tray menu's
    Quit and File > &Quit) — and asked BEFORE the exploded-clusters question.

    It is the SAME question, from the SAME host, as the project switch's:
    RootMetadataDock._confirm_discard_changes, because WORKING_SET holds every
    dock's staged config edits, so one answer covers all of them. No second
    wording and no second dialog exist here on purpose (a plain QMainWindow with
    no such dock — a test rig — has nothing to lose and answers True).

    Discard is NOT destructive on this path: that guard only RETURNS True —
    the caller's root switch is what clears the working set (see its
    docstring) — so "Discard" followed by a Cancel of the clusters question
    leaves the staged edits exactly where they were; the process is still
    alive. Save that FAILED returns False (the guard keeps a dirty set dirty),
    so the exit is cancelled too.

    DELIBERATE EXCEPTION to the "no QMessageBox in the GUI" rule (Денис,
    2026-10-04 — backlog_2026_10_04_unsaved_changes_on_quit.md): the action is
    IRREVERSIBLE and Denis asked for the dialog himself. The dialog itself
    lives in the guard named above; DO NOT remove this call when sweeping
    modals out of the GUI.

    Asked ONCE per way out: `_explode_exit_ok` is set only by the return-
    clusters restore, which is also the ONLY thing that re-enters the exit
    (its `proceed` callback is self.close()/_do_quit), so a True flag proves
    the question has already been asked on THIS way out. Deliberately NOT a
    flag of its own: return_clusters_before_quit()'s False means BOTH "Cancel"
    and "the restore is still in flight", so a flag reset on any False would
    ask a second time on that legitimate re-entry — while a flag not reset
    would swallow the question on the NEXT, genuinely new attempt (an exit
    cancelled at the clusters question must ask about the edits again)."""
    if getattr(window, "_explode_exit_ok", False):
        return True
    # Ч1 of plan_2026_10_23_node_form_without_apply.md: an edit still sitting in
    # the Trees node form is accepted BEFORE this question. After it, the guard
    # would have answered "nothing to save" over an edit that then turns the
    # working set dirty — and the exit would lose it without a word. The answer
    # here is PROCEED: a form that does not validate is reported in the Log, but
    # it never becomes a silent refusal to leave the program.
    settle_forms(window, refuse=False)
    dock = getattr(window, "root_metadata_dock", None)
    if dock is None:      # a plain QMainWindow in tests: nothing to lose
        return True
    return dock._confirm_discard_changes()


def quit(window) -> None:
    """Tray menu's Quit AND File > &Quit — a real quit regardless of the tray
    checkbox. QApplication.quit() doesn't invoke closeEvent on any window (it
    just stops the event loop), so this deliberately bypasses self.close()/
    closeEvent entirely rather than needing a "really quit" flag.

    У1 (2026-10-15, Ч1): the SAME way out as closeEvent's real quit — first the
    unsaved-changes question (confirm_unsaved_before_exit), then Р2-5's
    "Вернуть и выйти" while the clusters are exploded. Cancel of either one
    returns without quitting."""
    if not confirm_unsaved_before_exit(window):
        return
    if not window._return_clusters_before_quit(window._do_quit):
        return
    window._do_quit()


def do_quit(window) -> None:
    """The last act of both real ways out: persist the window/dock settings and
    stop the event loop (the poll worker and the single-instance guard are stopped
    by QApplication.aboutToQuit, the one hook both paths share — see
    MainWindow.__init__)."""
    window._persist_settings()
    QApplication.instance().quit()
