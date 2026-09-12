# gui/ui_utils.py
"""
Shared UI helpers for the kicadstamp GUI docks. The busy-indicator context
manager is the first one; the dock-level utilities (_show_message/
_set_combo_items/read-merge-write helpers) now live in gui/docks/_common.py
(see the gui/ cleanup roadmap, Phase 2). This file stays at package level
rather than under docks/ because gui/main_window.py may want it too, not
just the docks.

Since 2026-09-12 it also owns the ONE scroll wrap (wrap_in_scroll_area /
MinHeightScrollArea) that makes a squeezable container safe: the Config dock's
right pages and the Trees dock's form panels both go through it, so the pairing
that keeps form fields at their own height cannot drift between them.
"""
from contextlib import contextmanager
from typing import Iterable

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QAbstractScrollArea, QApplication, QFrame,
                             QScrollArea, QSizePolicy, QWidget)


@contextmanager
def busy(buttons: Iterable[QWidget] = ()):
    """Signals "work in progress" around a long, synchronous (UI-thread)
    operation — sets the wait cursor and disables the given buttons for the
    duration, restoring both afterwards.

    The operation itself still runs on the UI thread, so the GUI can't
    repaint mid-run — but the cursor + disabled buttons communicate 'busy'
    before and after, and — more importantly — disabling the trigger button
    prevents a second click from queueing a concurrent duplicate run (e.g.
    a double-click on Redraw starting two ApplyPipelines against the same
    pynng REQ socket).

    Buttons are restored to their PRE-existing enabled state (a button that
    was already disabled — e.g. Extract with nothing selected — stays
    disabled). Safe when no QApplication exists yet (unit-test edge): the
    cursor calls are skipped, and the context is a no-op apart from the
    buttons.
    """
    app = QApplication.instance()
    if app is not None:
        app.setOverrideCursor(Qt.CursorShape.WaitCursor)
    states = {button: button.isEnabled() for button in buttons}
    for button in buttons:
        button.setEnabled(False)
    try:
        yield
    finally:
        for button in buttons:
            button.setEnabled(states[button])
        if app is not None:
            app.restoreOverrideCursor()


class MinHeightScrollArea(QScrollArea):
    """QScrollArea whose HEIGHT floor is 1 and whose WIDTH floor is the
    content's own minimumSizeHint.

    Introduced for the Config dock's right pages (S.2 of techdocs/me/scroll.md)
    and reused since 2026-09-12 for the Trees dock's form panels — the two
    places where a container gets squeezed on purpose.

    QStackedWidget.minimumSizeHint() is the MAX over ALL its pages, hidden ones
    included, so a single tall page floors the whole dock (measured: one 495 px
    page pinned the Config dock at 522). Wrapping each page caps that
    contribution.

    The subclass caps the HEIGHT floor at 1 while leaving the WIDTH floor at the
    content's own minimumSizeHint: a plain QScrollArea reports a small,
    content-INDEPENDENT minimum width (measured 68 px for every page), which
    shrank the whole left dock area from 556 to 150 px — wrapping must be
    horizontally transparent, or the "fix" becomes a width regression.

    The height floor of 1 belongs HERE and nowhere else: it is what lets the
    area shrink below its content, after which the content itself scrolls
    instead of its fields being squeezed (see wrap_in_scroll_area)."""

    def minimumSizeHint(self) -> QSize:
        widget = self.widget()
        width = (widget.minimumSizeHint().width() if widget is not None
                 else super().minimumSizeHint().width())
        return QSize(width, 1)


def wrap_in_scroll_area(widget: QWidget) -> QWidget:
    """Return what actually goes into a container that can be squeezed:
    `widget` itself when it already scrolls its own content, otherwise a
    MinHeightScrollArea around it.

    WHY THIS EXISTS (measured 2026-09-12, diagnostics/probe_min_height_squeeze.py
    and diagnostics/probe_trees_dock_form_squeeze.py): a container pinned with
    `setMinimumHeight(1)` — gui/dock_hub.py:166 does exactly that on the central
    QTabWidget — loses its layout's own minimum, and Qt then squeezes every
    child PROPORTIONALLY instead of clipping the container. Fields are the
    visible damage: a QComboBox honestly reports minimumSizeHint().height() ==
    25, yet the container at 120 px squeezed it to 0.

    Inserting this scroll area between the container and the form is the fix:
    the area takes the squeeze (its height floor is pinned to 1) and its content
    keeps its own size and scrolls instead. It is the difference between
    "the frame gets smaller" and "the form gets smaller".

    Nesting a scroll area around a widget that already scrolls (QPlainTextEdit/
    tree/table/QAbstractScrollArea) is the project's documented anti-pattern
    (log_panel.py:157, pending.py:240) — such a widget is returned as-is."""
    if isinstance(widget, QAbstractScrollArea):
        return widget
    area = MinHeightScrollArea()
    area.setWidgetResizable(True)
    # The load-bearing line: with setWidgetResizable(True) the area still
    # stretches from its content, so its own minimum must be overridden
    # explicitly. 1, not 0 — Qt treats an explicit 0 as "unset" and falls back
    # to minimumSizeHint() (same sentinel as log_panel.py:157).
    area.setMinimumHeight(1)
    # NoFrame: a per-page frame is part of the padding/"bloat" a previous
    # attempt produced.
    area.setFrameShape(QFrame.Shape.NoFrame)
    # As-needed bars on both axes: nothing is reserved until the content really
    # has something to scroll.
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    # Vertical `Ignored` keeps the content from dictating the container's height
    # (the explicit setMinimumHeight(1) above caps it); horizontal stays
    # `Preferred` so the enclosing splitter still allocates width from the
    # hint/stretch — an `Ignored` horizontal policy collapsed a whole right page
    # to 0 px (measured with probe_splitter_and_log_floor).
    area.setSizePolicy(QSizePolicy.Policy.Preferred,
                       QSizePolicy.Policy.Ignored)
    area.setWidget(widget)
    return area


def _primary_screen():
    """QGuiApplication.primaryScreen(), behind a name of its own so the
    no-screen branch of resize_dialog_within_screen() is testable without
    faking a QScreen (monkeypatched in tests/gui/test_ui_utils.py)."""
    return QGuiApplication.primaryScreen()


def resize_dialog_within_screen(dialog: QWidget, width: int, height: int,
                                screen=None) -> None:
    """Resize a dialog to its DESIRED (width, height), capped by the screen it
    will appear on — the same "the frame may get smaller, the content may not"
    rule the scroll wrap above applies.

    Four dialogs asked for a fixed height (Cell 720x600, Project 560x620,
    Tools 520x560, Settings 780x540 — 2026-09-12). 620 px plus the window frame
    and the task bar does not fit a laptop screen at 125 % scaling, and the
    dialog then hangs off the bottom of the display with no way to reach its
    buttons. The desired size stays the DESIRED size; only the part that cannot
    fit is given up.

    Screen lookup is deliberately forgiving, because both failure modes are
    real: `dialog.screen()` returns None until the widget is shown (and this
    runs in __init__), and `primaryScreen()` returns None in a headless run.
    With no screen — the GUI test suite runs under QT_QPA_PLATFORM=offscreen
    with a real 800x800 screen, but a bare QApplication may have none — the
    resize simply happens unclamped. `screen` is injectable so tests do not
    have to fake a QScreen."""
    screen = screen or dialog.screen() or _primary_screen()
    if screen is None:
        dialog.resize(width, height)
        return
    available = screen.availableGeometry()
    dialog.resize(min(width, available.width()), min(height, available.height()))
