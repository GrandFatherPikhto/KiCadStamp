# gui/checks_menu.py
"""The "Checks" main-menu — one item per check, the copper-duplicates SEARCH, a separator,
then "Run all", a separator, then "Clear copper duplicates".

Plan: plan_2026_10_23_checks_tab_top_level.md §8 (Denis, 23.10: the menu sits BETWEEN Tools
and View) and §Ч1б (23.10: the copper-duplicates panel moved into the Checks page, and its
Clear's second door is this menu). Same data as the page: `gui/checks_registry.CHECKS` (its
titles are already translated literals) plus the page's own panel row — this module only
turns each row into a menu item, so no check name is written twice.

An item is a SHORTCUT, not a second door: it selects that row on the page and calls the
page's own `run(script)` — the same guard (a project is open, nothing else runs), the same
socket-busy defer, the same one process at a time. "Run all" queues every check in registry
order and then the panel's SEARCH (never its clear). "Clear copper duplicates" calls the
panel's own `clear()` — the very door its Clear button uses, with the very same conditions.

Enablement has TWO rules, and the difference is deliberate:

* every run/search item is disabled without a project and while ANY operation runs
  (`ChecksPage.can_run`), re-enabled when the page says something changed
  (`state_changed`) — `gui/main_window.py` builds this menu with ONE call and owns no list;
* "Clear copper duplicates" alone follows the PANEL (`DedupePage.can_clear`: a root to
  journal into, a found list, nothing in flight), so it can be off while every other item
  is on.
"""
from kicadstamp.i18n import _

from .checks_registry import CHECKS, DEDUPE_ROW


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

    dedupe_item = menu.addAction(_("Copper duplicates"))
    dedupe_item.setToolTip(_("Find duplicate vias/tracks on the live board"))
    dedupe_item.triggered.connect(
        lambda _checked=False: page.run_from_menu(DEDUPE_ROW))
    items[DEDUPE_ROW] = dedupe_item

    menu.addSeparator()
    run_all = menu.addAction(_("Run all"))
    run_all.setToolTip(_("Run every check, one after another"))
    run_all.triggered.connect(lambda _checked=False: page.run_all())

    menu.addSeparator()
    clear_dedupe = menu.addAction(_("Clear copper duplicates"))
    clear_dedupe.setToolTip(
        _("Remove the extra copies the last search found — the same door the panel's "
          "Clear button uses"))
    clear_dedupe.triggered.connect(lambda _checked=False: page.dedupe.clear())

    def sync(*_args) -> None:
        """Two rules (see the module docstring): the runs follow the PAGE, the clear
        follows the PANEL."""
        runnable = page.can_run()
        for action in items.values():
            action.setEnabled(runnable)
        run_all.setEnabled(runnable)
        clear_dedupe.setEnabled(not page.is_running() and page.dedupe.can_clear())

    page.state_changed.connect(sync)
    page.dedupe.changed.connect(sync)
    sync()
    return menu
