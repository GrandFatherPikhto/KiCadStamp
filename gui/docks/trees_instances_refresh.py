# gui/docks/trees_instances_refresh.py
"""A template's `tree_instances:` tabs follow it WITHOUT a reload.

Measured live (09.11, HP-Power-v099, template `switch_a`, clone `switch_b`): a node deleted
from the template stayed on the clone's tab — and its copy stayed in `cfg.entities` — until
the whole trees section was reloaded. The EXPANSION was already right (`load_config` reads the
staged working set, so it produced the correct clone); only the dock's buffer lagged. Copies
are generated at LOAD (`config.tree_instances.expand_tree_instances`) and `reload_trees` ran
from the docks' `save` signals and this dock's own Save — nothing else.

What this module adds: a debounced re-read of the root config after anything the expansion
READS has changed, refreshing JUST the generated tabs. The rules, each with its reason:

* DEBOUNCED — one single-shot timer, 300 ms, restarted by every event: a node-form edit must
  not read the config on every keystroke.
* NOTHING WITHOUT A DECLARATION — no `tree_instances:` in the buffer means no timer and no
  config read, so a project that uses no clones pays nothing. The check reads the BUFFER
  (`dock._cfg`), never the file.
* THE SAME STEPS AS `reload_trees` — `trees_instances_sync.reload_root` + `apply_reloaded`, so
  the two paths cannot drift into two copies of the merge.
* PAGES, NOT THE WHOLE DOCK — `_rebuild_tabs` accepts and closes EVERY open form and drops
  every page; a refresh nobody asked for must not do that. Only a CHANGED SET of generated
  names falls back to it (`rebuild_clone_pages` answers False then).
* NO LOOP, NO DIRTY, NO DISK — the refresh never calls `_mark_dirty`/`_stage_trees`, never
  touches `_dirty`, never writes.
* A REFUSED EXPANSION (a template caught mid-edit) leaves the clones exactly as they were and
  says so ONCE — the same sentence repeated adds nothing to the Log.
* A LONG OPERATION WINS — the refresh is deferred and re-armed, never lost.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from kicadstamp.i18n import _

from ._common import WARN_STYLE, show_message
from .trees_instances_sync import (apply_reloaded, declared_instance_names,
                                   reload_root)

logger = logging.getLogger(__name__)

#: How long a burst of edits is collected before ONE config read. Long enough for a form
#: edit's several `_mark_dirty` calls, short enough to feel immediate.
DEBOUNCE_MS = 300


class InstancesRefresh:
    """Keeps the GENERATED instance tabs in step with the template they copy.

    Lives on the dock (`TreesDock._instances_refresh`); the dock delegates with one-liners and
    `dock_hub` wires the other docks' `saved` signals to the same `note_change`."""

    def __init__(self, dock, *, debounce_ms: int = DEBOUNCE_MS):
        self._dock = dock
        self._timer = QTimer(dock)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._flush)
        #: The refusal text last said out loud — the SAME sentence twice in a row is noise.
        self._last_refusal: Optional[str] = None

    # ── entry points ────────────────────────────────────────────────────────

    def note_change(self) -> None:
        """An edit the expansion READS has just landed. Nothing happens without a declaration:
        no timer is armed and the config is never read."""
        dock = self._dock
        if dock._root_path is None or not declared_instance_names(dock._cfg):
            return
        self._timer.start()          # a fresh event RESTARTS the single shot

    def flush(self) -> bool:
        """Run the pending refresh NOW — the test hook, and the tail of the timer. True when a
        refresh actually ran."""
        self._timer.stop()
        return self._flush()

    # ── the work ────────────────────────────────────────────────────────────

    def _flush(self) -> bool:
        dock = self._dock
        if dock._root_path is None or not declared_instance_names(dock._cfg):
            return False                     # nothing generated here — zero work
        if getattr(dock, "_active_op", None) is not None:
            # A socket-holding operation owns the dock: come back after it, don't lose the
            # edit. Re-arming the same single shot is the whole retry.
            self._timer.start()
            return False
        reloaded = reload_root(dock)
        if reloaded is None:
            return False                     # no root left to read
        if not reloaded.ok:
            self._report_refusal(reloaded.error)
            return False                     # the clones keep their previous state
        self._last_refusal = None
        previous = {t.name for t in dock._trees}
        apply_reloaded(dock, reloaded)
        # ONLY the GENERATED tabs are the refresh's business: a hand-written tree (the
        # template itself included) is the user's own buffer and its page must not be touched.
        generated = sorted(reloaded.names & dock._generated)
        if previous != {t.name for t in dock._trees}:
            # The SET of tabs changed (a declaration appeared or went): the one case a full
            # rebuild is allowed, since there is no page to reuse.
            dock._rebuild_tabs()
        elif not rebuild_clone_pages(dock, generated):
            dock._rebuild_tabs()
        dock._update_status_row()
        return True

    def _report_refusal(self, text: str) -> None:
        """One yellow Log line, never a dialog — and not the same one twice in a row."""
        if text == self._last_refusal:
            return
        self._last_refusal = text
        show_message(_("Tree instance tabs were not refreshed: {error}").format(error=text),
                     WARN_STYLE, logger)


def rebuild_clone_pages(dock, names) -> bool:
    """Re-render the pages of the named trees IN PLACE: the same page object, the same tab
    index, the same active tab, and the other tabs untouched.

    False = a page could not be found or built (a tree with no page widget), and the caller
    falls back to the dock's full `_rebuild_tabs`.

    Two traps, both measured here (the cells that found them are named):

    * `_node_items` is keyed by REF and is filled by the RENDER, so the rows of THIS page must
      go first — and «this page» is decided by the row's OWN tree widget, not by the buffer
      tree: a rebuilt copy is a NEW Tree object, so walking the new nodes would leave the refs
      of the OLD ones behind (cell а).
    * the widget's SIGNALS are blocked for the render: `clear()` emits `itemSelectionChanged`,
      and the dock's handler re-fills the ACTIVE page's form with a fresh widget — the template
      page's open form died by a refresh of somebody else's page (cell ж). The expansion state
      is applied directly by the render, so nothing else needs those signals."""
    order = {tree.name: index for index, tree in enumerate(dock._trees)}
    active = dock._current_tab_tree_name()
    for name in names:
        index = order.get(name)
        if index is None:
            continue                     # gone: the caller's full rebuild handles that
        page = dock.tree_tabs.widget(index)
        tree_widget = dock._tree_widget_of_page(page)
        if tree_widget is None:
            return False
        tree = dock._trees[index]
        panel = dock._form_panel_of_page(page)
        for ref, item in list(dock._node_items.items()):
            if item.treeWidget() is tree_widget:
                dock._node_items.pop(ref, None)
        # The LIVE expansion of that tab (not the persisted one): a refresh must not collapse
        # rows the user had opened.
        state = dock._capture_tree_expansion(tree_widget)
        if panel is not None:
            dock._set_page_content(panel, QWidget())
            panel.setProperty("_panel_built", False)
        dock._rebuilding_tabs = True     # the render's expand events are not user state
        tree_widget.blockSignals(True)
        try:
            tree_widget.clear()
            dock._render_tree(tree_widget, tree, state)
        finally:
            tree_widget.blockSignals(False)
            dock._rebuilding_tabs = False
    if active is not None and dock._current_tab_tree_name() != active:
        dock._switch_to_tree(active)     # never move the user off their tab
    if active in set(names):
        dock._rebuild_active_form_panel()
    return True


def wire_instance_refresh(hub) -> None:
    """Connect every dock that stages a section the instance EXPANSION reads to the Trees
    dock's debounced refresh: a save → `note_change` → ONE debounced config read → only the
    generated tabs are re-rendered.

    `imprint_place_dock.saved` deliberately stays on `reload_trees`: it fires at the end of a
    modal dialog, where a full rebuild costs nobody an open form, and the existing guard
    (`test_imprint_place_saved_reaches_trees_dock_reload`) keeps standing."""
    for dock in (hub.entity_dock, hub.cells_dock, hub.net_trace_dock):
        dock.saved.connect(hub.trees_dock.note_instances_changed)
