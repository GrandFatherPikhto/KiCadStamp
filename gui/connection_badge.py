# gui/connection_badge.py
"""ConnectionBadge — the status bar's persistent "connected / not connected"
pill (2026-10-14, plan_2026_10_14_connection_badge).

WHY IT IS A WIDGET OF ITS OWN. The connection state used to be written into the
same stretchable LEFT label as one-off messages ("Project saved", "Nothing to
save"), so saving a project silently erased it (Denis, live 2026-10-14: "just
save the project and it gets overwritten, like Project saved..."). The badge is
a PERMANENT status-bar widget next to dirty_label / busy_label / latency_label —
the shape those three already have: one long-lived label, written from ONE place
(MainWindow._set_connection_badge), which a one-off message in the left label
can no longer touch.

WHY A FILLED PILL RATHER THAN A COLOURED DOT. The fill is the badge's OWN
background, so it reads the same in the dark and the light theme — the project's
status colours are plain hex values, not palette roles (see gui/docks/_common.py)
— and there is no dangling "●" to be mistaken for the unsaved-changes ● that
already lives at the right edge of the same bar (Д6 of the plan). The colour is
decoration ON PURPOSE: the state is legible from the TEXT alone (the board's name
and component count vs. "Not connected"), so a colour-blind reader loses nothing.

WHAT IT IS NOT: it reads no board, owns no timer and knows no connection. It
renders the outcome of a poll somebody else ran, which is why the only way to
turn it red is to hand it an error. Whoever lost KiCad — the slow poll tick or
the fast selection tick — reports it; a deliberate `connection.disconnect()`
that no poll saw (fieldstool's Apply, gui/fieldstool_window.py:1115) is NOT
mirrored here on purpose (Д3): a hook there would blink the badge red in the
middle of an Apply, and the next poll tick turns it red anyway (≤ 5 s by
default, DEFAULT_RECONNECT_INTERVAL_MS).
"""
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QLabel

from kicadstamp.i18n import _

from .docks._common import BADGE_CONNECTED_STYLE, BADGE_DISCONNECTED_STYLE

# The two states as a stored VALUE, never as a comparison of the painted
# stylesheet: a guard asserting on QSS text would break on any cosmetic tweak,
# i.e. it would become a nuisance instead of a watchman.
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"


class ConnectionBadge(QLabel):
    """The status bar's connection pill.

    Two writers, both of them the result of a poll: set_connected() after a
    successful SLOW poll (the one that rebuilds the snapshot), and
    set_disconnected() on ANY poll that lost KiCad — including the fast
    selection tick, which is the sooner of the two (≤ 400 ms, so the badge turns
    red on whichever tick noticed, not on the slow one alone).

    The START state is the widget's own, written here rather than by
    MainWindow: a badge nobody has reported to yet says "Not connected" because
    that is the truth (no successful read has happened yet, so there is no error
    to show either). Keeping it here also means the widget stays self-consistent
    when a test builds it on its own.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = STATE_DISCONNECTED
        self.set_disconnected(None)

    def show_poll_result(self, *, error: Optional[str] = None,
                         board_name: Optional[str] = None,
                         count: int = 0) -> None:
        """THE entry point the poll handlers use: hand over a poll's outcome and
        the badge renders it.

        ``error is None`` means the poll DID read the board (that is exactly the
        convention the two MainWindow finish handlers already branch on: connect()
        and refresh() return an error string instead of raising), so it paints
        green with the board's name and the count. Any error — from either the
        slow poll or the fast selection tick — paints red and goes into the
        tooltip. The two explicit setters below stay the same two states, this is
        the one place that decides WHICH of them a result means."""
        if error is None:
            self.set_connected(board_name, count)
        else:
            self.set_disconnected(error)

    def set_connected(self, board_name: Optional[str], count: int) -> None:
        """Green: WHICH board the last read was about, and how many components
        that read found.

        *board_name* is the raw value ``adapter.get_board_filename()`` returned
        ('3CH-AWG-TIA-v103.kicad_pcb') — the stem is taken HERE, in the one place
        that decides what the user reads. None is a legitimate value, not an
        error (a test double with no adapter at all, or a board whose filename
        KiCad did not carry): the badge then says "Connected" and keeps the
        count (Д4 of the plan).
        """
        self.state = STATE_CONNECTED
        stem = Path(str(board_name)).stem if board_name else ""
        self.setText(_("{board} · {count} components").format(
            board=stem, count=count) if stem else
            _("Connected · {count} components").format(count=count))
        # The T.2 qualifier, kept honest: the number is the snapshot's size as
        # of the LAST board read, not "now" — the automatic tick is a no-op once
        # connected (see MainWindow._poll's docstring), so every rebuild is a
        # connect, a manual Refresh, or a dock's out-of-cycle refresh.
        self.setToolTip(_("Components as of the last board read"))
        self.setStyleSheet(BADGE_CONNECTED_STYLE)

    def set_disconnected(self, error: Optional[str]) -> None:
        """Red: the last poll could not reach KiCad.

        The error text goes to the TOOLTIP as it is — never shortened: a status
        bar one line wide cannot carry a socket error, and the diagnosis must
        not be lost together with the message that used to sit in the left label
        (Д1 of the plan). No error at all (startup) leaves the tooltip empty
        rather than filled with an empty-string stand-in.
        """
        self.state = STATE_DISCONNECTED
        self.setText(_("Not connected"))
        self.setToolTip(error or "")
        self.setStyleSheet(BADGE_DISCONNECTED_STYLE)
