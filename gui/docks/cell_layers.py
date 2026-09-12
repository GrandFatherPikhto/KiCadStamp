# gui/docks/cell_layers.py
"""The layer dialog of a cell read and its two-phase opening — Э3 of
plan_2026_09_12_cell_layer_dialog.md (design Р2/Р12).

    Layers to read
      [x] F.Cu
      [ ] In1.Cu    hidden on the board
      [x] In2.Cu
      [ ] B.Cu      empty in the selection

The dialog contains ONLY the layers — no paths, no roles, no preview. Nothing
here is stored on a `Cell`: the layer list is derived from the live board on
every read (Э1), so it cannot go stale, and the only thing remembered is the
user's CHOICE (gui_state.json, see gui/board_layers.remembered_read_layers).

WHY THE OPENING IS TWO-PHASE (P.3.2 of the plan). The list comes from the live
board, and a board read must never happen synchronously on the UI thread: the
~400 ms selection poll has a request in flight on the SHARED kipy REQ socket on
almost every tick, and a second owner interleaves transactions — the live
symptom being `ConnectionError: Operation canceled`
(plan_2026_09_12_ui_thread_board_reads). So:

  phase 1 — `start_long_op` reads the copper layers on the WORKER thread;
  phase 2 — its completion callback (UI thread, the worker has stopped) opens the
            dialog with the finished list; only OK starts the actual read, which
            is another long operation of the caller's own.

The same shape works in gui/docks/configurator.py:459 (`refresh_overlay_layers`).

The token check before phase 1 is not cosmetic (P.3.3): `start_long_op` does NOT
refuse while another owner holds the socket, and that owner's completion clears
the token mid-read — measured as a coredump with `QThread: Destroyed while thread
'' is still running`. When the socket is busy we do not start and do not show the
dialog at all.

The selection is NOT read from the board here either (P.3.4): which layers have
copper in it comes from the selection the ~400 ms tick already distributes
(gui/dock_hub.py `set_board_selection` -> CellDock.set_board_selection).
"""
import logging

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout,
                             QLabel, QVBoxLayout)

from kicadstamp.i18n import _

from ..board_layers import (
    LayerChoice,
    enabled_copper_layers,
    layer_choices,
    layers_to_remember,
    remember_read_layers,
    remembered_read_layers,
    selection_layer_names,
    skipped_empty_layers,
)
from ..worker import socket_busy, start_long_op

logger = logging.getLogger(__name__)

__all__ = ["CellLayersDialog", "open_cell_layers_dialog"]


class CellLayersDialog(QDialog):
    """One checkbox per copper layer of the live board, in stackup order.

    Each row shows the USER's own layer name (a renamed layer is renamed here
    too), and — next to it — why it is off or worth a second look: «empty in the
    selection», «hidden on the board». Toggling a box is the user's own decision
    and is reported through `touched_names()` so the automatic unchecking of an
    empty layer can be kept out of the remembered set (Э3)."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Layers to read"))
        self.resize(460, 250)
        self._rows = list(rows)
        self._boxes = {}
        self._touched = set()

        layout = QVBoxLayout(self)
        intro = QLabel(_("A layer you uncheck is not read at all — for Refresh "
                         "that also removes its records from this cell."))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        for index, row in enumerate(self._rows):
            box = QCheckBox(row.copper.display_name)
            box.setChecked(row.checked)
            # Connected AFTER setChecked: the opening state (built by
            # layer_choices) is not a user's touch and must never be remembered
            # as one.
            box.toggled.connect(
                lambda _state, name=row.copper.copper_name:
                self._touched.add(name))
            self._boxes[row.copper.copper_name] = box
            grid.addWidget(box, index, 0)
            note = _row_note(row)
            if note:
                label = QLabel(note)
                label.setEnabled(False)
                grid.addWidget(label, index, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox()
        read_button = buttons.addButton(
            _("Read"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        read_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def checked_names(self) -> list:
        """Canonical names left checked — the layer set this read runs with."""
        return [row.copper.copper_name for row in self._rows
                if self._boxes[row.copper.copper_name].isChecked()]

    def touched_names(self) -> set:
        """Canonical names whose box the USER toggled (opening state excluded)."""
        return set(self._touched)

    def note_for(self, copper_name: str) -> str:
        """The row's note text ('' when there is none) — the markers are part of
        what the user decides on, so tests assert on them."""
        for row in self._rows:
            if row.copper.copper_name == copper_name:
                return _row_note(row)
        return ""


def _row_note(row: LayerChoice) -> str:
    """Why this row deserves a note, as one line. A hidden layer is a
    WARNING, not a filter: we do not read it "for" the user or hide it from
    them (Э2) — the selection in KiCad cannot see it either."""
    notes = []
    if row.empty:
        notes.append(_("empty in the selection"))
    if not row.copper.visible:
        notes.append(_("hidden on the board"))
    return " · ".join(notes)


def _fetch_copper_layers(adapter):
    """Worker thread, phase 1: the live board's copper layers (Э1). The ONLY
    board read of the dialog path, and it never touches a widget."""
    return enabled_copper_layers(adapter._board)


def _ask_for_layers(parent, copper_layers, selection_items, on_ok) -> None:
    """Phase 2 (UI thread, phase 1 already finished): ask, and only on OK
    remember the user's own choice and continue with the read.

    `on_ok(chosen_names, empty_names)`: the read set AND the layers the dialog
    left off as empty — the latter is what the per-read Log report cannot derive
    by itself (Э5), because a layer with no copper in the selection leaves no
    trace there."""
    rows = layer_choices(copper_layers, remembered_read_layers(),
                         selection_layer_names(selection_items))
    dialog = CellLayersDialog(rows, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    chosen = dialog.checked_names()
    remember_read_layers(
        layers_to_remember(rows, chosen, dialog.touched_names()))
    on_ok(chosen, skipped_empty_layers(rows, chosen))


def open_cell_layers_dialog(parent, connection, adapter, selection_items, on_ok,
                            widgets=(), on_error=None) -> object:
    """Open the layer dialog and run `on_ok(chosen_names, empty_names)` on OK.

    `widgets` are the caller's guard widgets (its buttons): while phase 1 runs
    they are disabled, so the same action cannot be started twice — the second
    `start_long_op` on a held token is exactly the race the token check below
    protects the FIRST phase from.

    Returns the LongOpController, or None when nothing was started (no board, or
    another operation holds the shared socket — then nothing is shown at all,
    P.3.3)."""
    if adapter is None:
        return None
    if socket_busy(connection):
        # P.3.3: start_long_op would NOT refuse here, and the other operation's
        # completion would release OUR token mid-read.
        logger.info("Cell layers dialog refused: another long operation already "
                    "holds the shared kipy socket")
        return None
    return start_long_op(
        connection, widgets, _fetch_copper_layers,
        lambda copper_layers: _ask_for_layers(
            parent, copper_layers, selection_items, on_ok),
        on_error if on_error is not None else _log_layer_read_failure,
        adapter, busy_text=_("reading layers"))


def _log_layer_read_failure(message: str) -> None:
    """Default failure handler of phase 1 — a Log line, never a modal
    (plan_2026_09_11_no_modals_and_busy_kicad X.1)."""
    logger.error("Reading the board's copper layers failed: %s", message)
