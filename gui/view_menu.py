# gui/view_menu.py
"""The "View" main-menu — one `toggleViewAction` per real top-level dock.

Moved out of `gui/main_window.py` (23.10, Д-2 of the Ч1 acceptance): the window is a giant and
the block is mechanical — a loop over `DockHub.docks`, nothing of the window's own. The comment
that lived next to it moves with it, because it is the REASON the block looks like this:

View menu (2026-08-27, handoff sync_skip_message_and_view_menu): the app had no menu bar at all,
so a closed dock had no way back short of restarting. Every QDockWidget ships a ready-made
toggleViewAction() (checkable, self-tracks shown/hidden) — wire one per real top-level dock
(DockHub.docks), see gui/dock_hub.py for why the docks' internal panels are excluded. Since task
T (2026-09-10) the Components/Config/Trees widgets are pages of the central QTabWidget, not
docks: they cannot be floated or closed, so they correctly have no entry here — and since
plan_2026_10_23_checks_tab_top_level (23.10) the Checks page is a page too, while the bottom area
holds the Log and the copper-duplicates panel.
"""
from kicadstamp.i18n import _


def build_view_menu(window, docks):
    """Add the "View" menu to `window`'s menu bar, one action per dock. Returns the QMenu.

    `docks` is `DockHub.docks` — every REAL top-level dock, each of which owns a
    `toggleViewAction()`. Nothing else is added here: a page of the central tab row is not a
    dock and cannot be closed, so it must not appear in this menu.
    """
    menu = window.menuBar().addMenu(_("View"))
    for dock in docks:
        menu.addAction(dock.toggleViewAction())
    return menu
