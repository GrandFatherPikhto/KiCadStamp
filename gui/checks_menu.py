# gui/checks_menu.py
"""The "Checks" main-menu — one item per check, a separator, then "Run all".

Plan: plan_2026_10_23_checks_tab_top_level.md §8 (Denis, 23.10: the menu sits BETWEEN Tools
and View). Same data as the page: `gui/checks_registry.CHECKS` (its titles are already
translated literals) — this module only turns each row into a menu item, so no check name is
written twice.

An item is a SHORTCUT, not a second door: it selects that check's row on the page and calls the
page's own `run(script)` — the same guard (a project is open, nothing else runs), the same
socket-busy defer, the same one process at a time. "Run all" queues every check in registry
order and starts the next one only after the previous finished.

Every item is disabled without a project and while ANY check runs (`ChecksPage.can_run`), and
re-enabled when the page says something changed (`state_changed`): `gui/main_window.py` builds
this menu with ONE call and owns no list of its own.
"""
from kicadstamp.i18n import _

from .checks_registry import CHECKS


def build_checks_menu(main_window, page):
    """Add the "Checks" menu to `main_window`'s menu bar and keep its items in step with the
    page. Returns the QMenu (the caller stores it on the window, the tests read it).

    Call it WHILE building the menu bar, right after Tools: the order of `addMenu` calls is
    the order of the bar.
    """
    menu = main_window.menuBar().addMenu(_("Checks"))
    items = {}
    for check in CHECKS:
        action = menu.addAction(check.title)
        action.setToolTip(check.tooltip)
        action.triggered.connect(
            lambda _checked=False, script=check.script: page.run_from_menu(script))
        items[check.script] = action
    menu.addSeparator()
    run_all = menu.addAction(_("Run all"))
    run_all.setToolTip(_("Run every check, one after another"))
    run_all.triggered.connect(lambda _checked=False: page.run_all())

    def sync(*_args) -> None:
        """One rule for every item: runnable = a project is open and nothing is running."""
        runnable = page.can_run()
        for action in items.values():
            action.setEnabled(runnable)
        run_all.setEnabled(runnable)

    page.state_changed.connect(sync)
    sync()
    return menu
