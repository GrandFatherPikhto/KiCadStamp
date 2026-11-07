# gui/fieldstool_confirm_dialogs.py
"""The two CONFIRMATION windows of the fieldstool window — "Confirm apply" and
"Confirm sync from schematic".

Moved out of gui/fieldstool_window.py on 07.11 (plan
plan_2026_11_07_dialog_sizes_and_row_shift, part Б): that module is a giant that
may only shrink (rule 45), and these two are windows with a size of their own.
`MainWindow._confirm_apply()` / `._confirm_sync()` stay as one-line delegates, so
the cells that patch those METHODS (and `QDialog.exec`) keep working unchanged.

Both dialogs are ANONYMOUS `QDialog` instances: the size they remember needs an
EXPLICIT key, or every anonymous window in the app would share one
`dialog_size:QDialog` entry.
"""
from __future__ import annotations

from typing import List

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget,
                             QVBoxLayout)

from kicadstamp.i18n import _

from .docks.pending import PendingEdit


def confirm_apply_dialog(parent, report: List) -> bool:
    """Confirmation dialog for Apply — a plain QMessageBox with one line
    of text per changed ref used to grow into an enormous window on a
    real board (hundreds of pending edits), pushing its OK/Cancel
    buttons off-screen. The summary lives in a height-capped QListWidget
    instead — it gets its own scrollbar once the list outgrows that cap,
    so the dialog itself (and its buttons) stays a sane, fixed size
    regardless of how many refs are being applied.

    DIRECTION, stated in the dialog itself (2026-09-16,
    plan_2026_09_16_commit_document_and_pending_direction Э2/Т2.2, Р3/Р4):
    Apply writes the BOARD value INTO the schematic, so the schematic's own
    value is REPLACED. The old dialog said only "About to write N change(s)"
    and listed `'old' -> 'new'` — on a board that lags behind the schematic
    (the normal state while Role/Cluster are being edited in eeschema;
    measured 16.09 on profile hipims-generator-v099: 42 rows where the
    schematic was right and the board stale) one OK silently reverted the
    schematic AND cleared the Role/Cluster of every component the board had
    empty. Hence three changes, none of them a new window (Р4): the header
    line names the direction, rows read "schematic … -> board …", and an
    empty BOARD value gets its own line saying the schematic value will be
    CLEARED — such rows are still applied (clearing the board and then
    applying is a legitimate flow, Т2.2).

    CANCEL is the default button here: an Enter press must not write
    anything, because the safe answer to this dialog is "no" far more often
    than "yes". The summary list keeps its height cap, so the new lines do
    not grow the dialog (Р5)."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(_("Confirm apply"))
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(
        _("This REPLACES the values in the SCHEMATIC with the values from the "
          "BOARD ({count} change(s)).").format(count=len(report))))
    layout.addWidget(QLabel(
        _("If you edited Role/Cluster in the schematic editor, press Cancel and "
          "run Update PCB from Schematic (F8) instead.")))
    erased = [r for r in report if not r.new_value]
    if erased:
        layout.addWidget(QLabel(
            _("⚠ {count} value(s) on the board are empty — the schematic "
              "value(s) will be CLEARED.").format(count=len(erased))))

    summary_list = QListWidget()
    for r in report:
        summary_list.addItem(
            _("{refs} .{field}: schematic {old!r} -> board {new!r}").format(
                refs=",".join(r.refs), field=r.field,
                old=r.old_value, new=r.new_value))
    summary_list.setMaximumHeight(300)
    layout.addWidget(summary_list)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    # Cancel is the DEFAULT (see the docstring): Enter must cancel, and the
    # OK button must not steal the default role back (Qt's autoDefault
    # gives the first AcceptRole button the default otherwise).
    ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    ok_button.setDefault(False)
    ok_button.setAutoDefault(False)
    cancel_button.setDefault(True)
    cancel_button.setAutoDefault(True)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted


def confirm_sync_dialog(parent, edits: List[PendingEdit],
                        stored_records: int = 0) -> bool:
    """Same height-capped QListWidget summary pattern as _confirm_apply
    (Apply), direction reversed: board's CURRENT value -> what's about to
    be written (the schematic's value). Т5б adds the SECOND half in words
    when there is something to lose — the stored records this action drops
    for the same components."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(_("Confirm sync from schematic"))
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(
        _("About to write {count} field(s) on the LIVE BOARD, reverting "
          "them to match the schematic:").format(count=len(edits))))
    if stored_records:
        layout.addWidget(QLabel(
            _("This also DROPS {count} of your stored value(s) for the same "
              "components — your notes give way to the schematic.").format(
                  count=stored_records)))
    summary_list = QListWidget()
    for e in edits:
        summary_list.addItem(f"{e.ref}.{e.field}: {e.new_value!r} -> {e.old_value!r}")
    summary_list.setMaximumHeight(300)
    layout.addWidget(summary_list)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted
