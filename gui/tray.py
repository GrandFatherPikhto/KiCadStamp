# gui/tray.py
"""The system-tray icon: its menu, and the three things a user can do with it.

Ч1-Т4 (2026-10-15, plan_2026_10_15_extract_and_read_paths_cleanup): lifted out of
MainWindow, which is one of the project's giants (§45). This is pure UI glue —
build the icon and its menu, take them away again, show/hide the window on a click —
and it is the same split gui/status_bar.py made for the status bar.

The WINDOW keeps one-line seams with the SAME names, and they are its PUBLIC
surface for the tray: DockHub connects `_set_tray_enabled` BY ATTRIBUTE
(`getattr` — see gui/dock_hub.py, which also builds against a plain QMainWindow in
tests), the menu's own slots are `_toggle_visibility` / `_on_tray_activated` /
`_quit`, and the icon itself stays on the window as `_tray_icon` (the tray guards in
tests/gui/test_tray.py read it). This module owns what those seams DO.

What is deliberately NOT here: any decision about the way OUT of the program. The
icon's Quit opens the same question sequence as the title-bar X — that rule lives in
gui/exit_guard.py, and this menu only wires the action.

`QMenu()` is built WITHOUT a parent, exactly as it was in the window: the menu is
handed to the tray icon through setContextMenu(), which is what keeps it alive for
as long as the icon is.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from kicadstamp.i18n import _

from .app_icon import build_app_icon

logger = logging.getLogger(__name__)

__all__ = ["on_activated", "set_enabled", "toggle_visibility"]


def set_enabled(window, checked: bool) -> None:
    """Build the tray icon and its menu (`checked` True), or take them away again.

    Idempotent: a second True with an icon already built is a no-op, and False with
    no icon is one too — DockHub re-emits the applied checkbox on every Settings
    apply(), so both happen in normal use. The icon is parented to `window` (so Qt
    tears it down with the window) and stored on `window._tray_icon`."""
    if checked:
        if window._tray_icon is not None:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning(_("Tray icon requested but no system tray is available here."))
        window._tray_icon = QSystemTrayIcon(build_app_icon(), window)
        window._tray_icon.setToolTip(_("KiCadStamp"))
        menu = QMenu()
        menu.addAction(_("Show/Hide"), window._toggle_visibility)
        menu.addAction(_("Open fieldstool"), window.open_fieldstool)
        menu.addSeparator()
        menu.addAction(_("Quit"), window._quit)
        window._tray_icon.setContextMenu(menu)
        window._tray_icon.activated.connect(window._on_tray_activated)
        window._tray_icon.show()
    else:
        if window._tray_icon is not None:
            window._tray_icon.hide()
            window._tray_icon = None


def on_activated(window, reason: QSystemTrayIcon.ActivationReason) -> None:
    """A single or double click on the icon toggles the window's visibility — the
    same action the menu's "Show/Hide" entry carries. Other activation reasons
    (middle click, context menu) do nothing."""
    if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                  QSystemTrayIcon.ActivationReason.DoubleClick):
        toggle_visibility(window)


def toggle_visibility(window) -> None:
    """Hide the window when it is visible, bring it to the front when it is not."""
    if window.isVisible():
        window.hide()
    else:
        window.bring_to_front()
