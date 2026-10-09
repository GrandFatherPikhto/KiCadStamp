# gui/explode_wiring.py
"""The «Разнос» (Explode) WIRING — the flow that used to live inside the giants
(Д8, Р3а-6 of plan ``plan_2026_10_05_explode_r2_r3_tab_and_reread.md``).

ONE owner of the flow: which doors open the tab, what the lock disables, when the
state is re-read, how the record's file is found for an ownership transfer, and
the "Put back and quit" question. The giants keep one-line DELEGATES only —
``DockHub._open_explode`` / ``reread_cell_for_explode`` / ``_apply_explode_lock`` /
``refresh_explode_state``, ``MainWindow._return_clusters_before_quit``,
``CellDock._explode_transfer_context`` — because the menus, the guard's signal and
the cells call THOSE names.

It is a collaborator of ``DockHub`` (handed in the constructor), not a copy of it:
every call goes through the hub's own attributes, so there is still exactly ONE
window state. No Qt widget is built here: the tab itself stays in
``gui/docks/explode_page.py``.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMessageBox

from kicadstamp.i18n import _

from .docks._common import (
    ERROR_STYLE as _ERROR_STYLE,
    WARN_STYLE as _WARN_STYLE,
    show_message,
)

logger = logging.getLogger(__name__)

__all__ = ["ExplodeWiring", "load_cfg", "resolve_transfer_context"]


def load_cfg(root):
    """The loaded config for an instance lookup, or None (a config that will not
    load is a hint, not a fatal). Not a board read."""
    from kicadstamp.config.loader import load_config
    try:
        cfg, _ctx = load_config(str(root))
        return cfg
    except Exception:  # noqa: BLE001 — a broken config is not our task
        return None


def resolve_transfer_context(root_path, transfers):
    """Р3а-2/Р3а-6: (cfg, {identity: file}, refusal_lines) for a set of transfers.

    The config and EVERY record's OWN file are resolved ACROSS THE INCLUDE GRAPH
    before anything is written — ``gui/docks/rename.find_list_entry_file`` is the
    ONE host of that rule (a record that lives in an included file is edited there,
    never in the root by mistake). Every problem is a REFUSAL line: never a silent
    log, and never a half-applied transfer."""
    from pathlib import Path

    from kicadstamp.explode_transfer import precheck_transfers

    from .docks.rename import find_list_entry_file

    if not transfers or root_path is None:
        return None, {}, []
    try:
        from kicadstamp.config import load_config
        cfg, _ctx = load_config(str(root_path))
    except Exception as e:  # noqa: BLE001 — a refusal line, not a silent log
        return None, {}, [_(
            "could not read the project config for the transfer: {error}"
        ).format(error=e)]
    root = Path(root_path)
    entry_files: dict = {}
    refusals: list = []
    for tr in transfers:
        if tr.identity in entry_files:
            continue
        path = find_list_entry_file(
            root, "net_traces", {"name": tr.identity, "net": tr.identity})
        if path is None:
            refusals.append(_(
                "net_traces {name}: the record's file was not found in the "
                "project graph — the read is refused, nothing was changed"
            ).format(name=tr.identity))
        else:
            entry_files[tr.identity] = str(path)
    if not refusals:
        refusals += precheck_transfers(cfg, transfers, entry_files)
    return cfg, entry_files, refusals


class ExplodeWiring:
    """See the module docstring: the ONE host of the «Разнос» flow."""

    def __init__(self, hub) -> None:
        self._hub = hub

    # ── doors ───────────────────────────────────────────────────────────────
    def open_tab(self, entity, file_path=None) -> None:
        """The ONE opener of the "Explode" tab, for every door.

        The tab lives on the ENTITY page (step 2 of plan_2026_10_09_entity_page):
        the door carries the ENTITY NAME, the page is opened ON that entity, and
        the address (cell, cluster, sheet) — plus the entity's OWN file — is read
        from the entity RECORD by the page itself (Денис, 09.10.2026: the board is
        touched only where the address is visible). No instance resolver and no
        cell lists here."""
        hub = self._hub
        if not entity:
            show_message(_("The Explode tab lives on an entity — open it from an "
                           "entity leaf."), _ERROR_STYLE, logger)
            return
        root = hub.root_metadata_dock.root_path
        if root is None:
            show_message(_("Set the project root first."), _ERROR_STYLE, logger)
            return
        hub.explode_page.set_root_path(root)
        hub._focus_config_tree_dock()
        entity_page = getattr(hub, "_entity_page", None)
        if entity_page is not None:
            hub.config_tree_dock.show_page(entity_page)
        # The SAME entity by name is NOT reloaded (that would drop unsaved input);
        # load_entity feeds the Explode tab its address via _sync_explode_context.
        if getattr(hub.entity_dock, "_current_name", None) != entity:
            hub.entity_dock.load_entity(entity)
        else:
            hub.entity_dock._sync_explode_context()
        hub.entity_dock.select_explode_tab()

    def reread(self, name, file_path=None) -> None:
        """The "Explode" tab's "Re-read cell from selection" — the SAME read the
        cell window's "Update from selection" runs, with the ownership transfer
        allowed (one function for every door, never a second read).

        Р3а-3: the transfer is allowed ONLY for the instance the JOURNAL was made
        for. ``ExplodeGuard.transfer_enabled`` is the ONE rule (it shares its
        address comparison with the worker); when the tab's nominal instance is not
        the journal's, no address is carried and the read subtracts the
        inter-cluster copper as usual (Н4), with a yellow line. The worker re-checks
        the address it actually RESOLVED, so a selection naming another instance is
        refused there too."""
        hub = self._hub
        if not name:
            return
        guard = getattr(hub, "explode_guard", None)
        journal = guard.journal if guard is not None else None
        page = getattr(hub, "explode_page", None)
        cluster = getattr(page, "_cluster", None)
        sheet = getattr(page, "_sheet", None)
        enabled = bool(guard is not None
                       and guard.transfer_enabled(name, cluster, sheet))
        hub.cells_dock.refresh_from_selection_requested(
            name, file_path, explode_transfer=True,
            explode_journal=journal if enabled else None)

    # ── the lock (РЗ7) ──────────────────────────────────────────────────────
    def apply_lock(self, active: bool) -> None:
        """While the clusters are exploded the user must not leave the Explode tab
        nor change WHO is exploded — the ENTITY page's own tab strip (the other
        tabs), the Config tree (a different entity) and the window's tab strip are
        disabled, and the Config right view is pinned to the ENTITY page (a switch
        away is rolled back in ``_on_config_right_page_changed``). The Explode tab
        and its buttons stay usable: ``isEnabled`` accounts for ancestors (Р2а-1,
        measured offscreen)."""
        hub = self._hub
        unlocked = not active
        hub.left_tabs.tabBar().setEnabled(unlocked)
        hub.config_tree_dock.tree.setEnabled(unlocked)
        hub.entity_dock.tabs.tabBar().setEnabled(unlocked)
        if active:
            hub.config_tree_dock.set_current_page(hub._entity_page)
            # Post-crash / restart (Р3а-0 п.5): show the JOURNAL's address so the
            # user sees exactly who is exploded. Read purely from the journal — the
            # entity record may not even be loadable in this state.
            journal = hub.explode_guard.journal or {}
            cell = journal.get("cell")
            if cell:
                hub.explode_page.set_root_path(hub.root_metadata_dock.root_path)
                hub.explode_page.set_context(
                    cell, journal.get("cluster"), journal.get("sheet"), None)
            hub.entity_dock.select_explode_tab()

    def pin_right_page(self, index: int) -> bool:
        """The Config right-QView switch attempted while exploded: name it and roll
        the view back to the ENTITY page. True = it was pinned (the caller
        returns)."""
        hub = self._hub
        if not hub.explode_guard.active or index == hub._entity_page:
            return False
        show_message(_("Clusters are exploded — press \"Put back\" first "
                       "(the \"Explode\" tab)."), _WARN_STYLE, logger)
        hub.config_tree_dock.set_current_page(hub._entity_page)
        return True

    # ── the state (Р2б) ─────────────────────────────────────────────────────
    def refresh_state(self) -> None:
        """Kick the tab's WORKER state read (door §31: no board read here).

        The journal path depends on the board IDENTITY — an IPC read — so it is
        read on a worker (``explode_page.explode_state_worker``), and the UI thread
        only applies a ready answer: has / none / unknown. ``active`` always comes
        from the journal, never memory.

        This is the AUTOMATIC path (connect / manual refresh), so the read is QUIET
        (Р2б-2): a failure leaves the lock alone and is a DEBUG line, never the red
        line every connect on a busy socket would otherwise print.

        A hub built without a guard simply has nothing to refresh."""
        hub = self._hub
        guard = getattr(hub, "explode_guard", None)
        if guard is None:
            return
        page = getattr(hub, "explode_page", None)
        if page is None:
            self.apply_lock(guard.active)
            return
        page.refresh_state(quiet=True)

    # ── the way out (Р2-5) ─────────────────────────────────────────────────
    def return_clusters_before_quit(self, proceed) -> bool:
        """With the clusters exploded, ask "Вернуть и выйти / Отмена".

        True = go ahead now (nothing exploded, or already returned). False = STAY:
        "Отмена", or a restore is in flight — the real quit then runs from its
        success callback (``proceed``), never before the board is back."""
        hub = self._hub
        window = hub.main_window
        guard = hub.explode_guard
        if not guard.active or window._explode_exit_ok:
            return True
        if QMessageBox.question(
                window, _("Clusters are exploded"),
                _("Clusters are exploded. Put back and quit?"),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return False

        def _ok() -> None:
            window._explode_exit_ok = True
            proceed()

        hub.explode_page.request_restore(on_success=_ok)
        return False
