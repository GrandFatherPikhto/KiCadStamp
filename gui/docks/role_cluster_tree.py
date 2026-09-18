# gui/docks/role_cluster_tree.py
"""
RoleClusterTreeDock — groups either the live PCB footprint snapshot or
fieldstool's parsed-schematic component list by Role or by Cluster, and
highlights/routes the picked component(s) accordingly. First real panel of
the GUI (see gui/main_window.py's docstring for why this one first:
validates the whole IPC -> model -> UI -> highlight-on-board chain on the
simplest possible, read-only case before anything writes to the board).

Two modes, toggled by the "Not yet applied" checkbox:
- Live (default, unchecked) — today's original behavior: data comes from
  kicadstamp.explore.Selected (live PCB footprints), a click pushes the
  selection onto the real board, and (Cluster grouping only) a group click
  fires the cluster_picked signal for PlacerDock.
- Schematic ("not yet applied", checked) — data comes from fieldstool's
  already-parsed SchematicComponent list (gui/docks/fieldstool_dock.py's
  embedded fieldstool MainWindow, read fresh at every rebuild, never
  cached here), FILTERED to only refs with an actual schematic-vs-board
  discrepancy right now (fieldstool_window.pending_refs — 2026-08-03; used
  to list every schematic component unconditionally, which meant a
  component stayed listed here even after a successful Apply left nothing
  outstanding). For picking a fieldstool target without needing a live
  board selection, the same job fieldstool's own now-deleted internal tree
  used to do. A click calls straight into fieldstool's existing
  _on_tree_leaf_picked()/_on_group_picked() (reusing its staging/combo-fill
  logic verbatim) and brings the fieldstool tab to front.

Both modes share one filter/build/view-state-preservation pipeline —
normalized into a small _Row(ref, role, cluster, divergent) so the tree
itself doesn't need two families of build methods. Cluster grouping is a
real nested tree in both modes, split on '/' (Channel_1/PI_FILTER),
matching the segment-hierarchy cluster_prefix_match's callers already
rely on elsewhere (see kicadstamp/explore.py's Board.select() docstring)
— NOT a flat group on the exact string (fieldstool's old tree used to do
that for Cluster; this is a deliberate, approved behavior change to match
this dock's existing Cluster handling instead).

Delete selected / Clear all (2026-08-03, Denis: "для отладки и проверки")
— live-mode only (schematic mode has no live footprint to write to, see
_on_mode_changed): both blank out Role AND Cluster on real board
footprints via KiCadBoardAdapter.set_field_values_bulk (one commit, so
KiCad's own Ctrl+Z undoes the whole batch). Delete selected targets
whatever's currently selected in THIS tree (a leaf or a whole group,
collected the same way _on_clicked's board-highlight already does via
_collect_refs); Clear all targets every footprint in the current live
snapshot regardless of selection/filter, behind a confirmation dialog —
its blast radius (the WHOLE board) is qualitatively different from a
targeted selection, same reasoning ConfigTreeDock's "Remove this file"
confirmation already uses for a comparably consequential action. Runs
through gui/worker.py's start_long_op like every other board-writing
action in this codebase (Placer's Redraw, Extract) — never a bespoke
synchronous board write.

Since Т5б (2026-09-18, plan_2026_09_18_field_overrides_store) they ALSO drop
OUR store records for the same components (see _forget_store_records). Blanking
the board alone would be a lie: our value outranks the board (Т2), so a record
left behind brings the erased Role straight back — "удалил, а оно есть", which
is worse than "не удалилось". The store half covers EVERY affected component,
INCLUDING the ones the board half had to skip for a missing field: those are
exactly the ones whose record would otherwise resurrect. A hard board error
leaves the store untouched — the whole operation failed and a retry is
expected; half-doing it would change the effective value behind an error
message.

Tag selected (2026-09-08, plan role_cluster_selection_tagging) — the SET-side
mirror of Delete selected/Clear all, so authoring Role/Cluster no longer needs
an offline fieldstool/.kicad_sch round-trip: a second row (two editable combo
boxes + one button) SETS a typed Role and/or Cluster value on every component
in the current tree selection. Both fields are independently optional (empty
field = "don't touch it", NOT "erase it" — erasure stays Clear all's job),
which covers both "one Cluster for a whole group" and "narrowed subgroup, one
Role". Combo suggestions are repopulated from the Role/Cluster values already
visible in the live snapshot — no separate fixed vocabulary to maintain.

Т5б moved its ADDRESS from the board to the project's OVERRIDE STORE — the same
authoring act the cell editor's Refs table performs, entered another way. A
board tag would be invisible the moment the store holds a record (Т2), so
"tagged" would silently mean nothing for precisely the components that already
have a note. It records the typed value for every selected component whose
footprint gives a symbol uuid — the store's key — and refuses the rest BY NAME
(С12), the same discipline as Т5's tables. Unlike a TABLE, a tag is an explicit
act carrying a typed value, so it is NOT sparse: the user just typed this value
for these components, and "I tagged it, why is there no record?" is not a
question worth inventing. No worker and no socket either: the store is a file,
and the only thing Tag needs from the board is the LAST READ (where the keys
come from), never a live connection.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from PyQt6.QtCore import QItemSelectionModel, Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLineEdit,
                              QMessageBox, QPushButton, QSplitter, QTabWidget,
                              QTreeView, QVBoxLayout, QWidget)

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.exceptions import ValidationError
from kicadstamp.explore import Selected
from kicadstamp.field_overrides import SOURCE_ROLE_CLUSTER_TREE, symbol_uuid_of
from kicadstamp.i18n import _

from .. import settings
from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE,
                      highlight_stylesheet_for, show_message, SplitterSizeKeeper)

logger = logging.getLogger(__name__)

# Leaf items carry their refdes here; group items carry None — _collect_refs
# below tells the two apart by this, not by row-count/child-count guessing.
_REF_ROLE = Qt.ItemDataRole.UserRole + 1
# Every group item (flat top-level or hierarchical intermediate node) carries
# its own bare (flat)/full '/'-joined (hierarchical) value here — reading
# this directly avoids re-deriving it from displayed text, which has i18n
# "(none)"/"(count)" decoration baked in for flat groups.
_GROUP_VALUE_ROLE = Qt.ItemDataRole.UserRole + 2

_INVALID_REGEX_STYLE = "background-color: #ffcccc;"

# Clear all/Delete selected: how many skipped refs (missing Role/Cluster
# field) to list by name in the result message before collapsing the rest
# into "and N more" — a batch of a few hundred shouldn't dump every ref.
_MAX_SKIPPED_REFS_SHOWN = 10


def _pending_tab_text(count: int) -> str:
    """The "Pending changes" tab title (2026-09-17, plan
    plan_2026_09_17_spoke_s3_roles_reminder Р1). The title doubles as the
    reminder that Role/Cluster values are sitting on the BOARD only: the next
    F8 (Update PCB from Schematic) would overwrite them, and only Apply moves
    them into the schematic. `count` is what Apply can transfer (see
    pending.pending_reminder_state). Zero keeps a plain, unbracketed title, so
    a clean state shows nothing at all — nothing extra appears on the small
    screen (Ф5)."""
    if count <= 0:
        return _("Pending changes")
    return _("Pending changes ({count})").format(count=count)


@dataclass
class _Row:
    ref: str
    role: Optional[str]
    cluster: Optional[str]
    divergent: bool = field(default=False)


class RoleClusterTreeDock(QWidget):
    """Master-detail "Components" dock — since 2026-09-05 (plan
    components_fieldstool_master_detail) the components tree lives in a left
    QTabWidget (tabs on top: "Components" + the shared Pending page) with the
    embedded fieldstool window as the right QView, mirroring Config/Trees.
    Direct construction without the master-detail collaborators (unit tests)
    still yields the plain single-tree page the dock used to be."""

    # Fired when a Cluster GROUP node is clicked while grouped by Cluster,
    # in LIVE mode only (see _on_clicked) — PlacerDock listens, so picking
    # a cluster here fills its Cluster field the same way picking a cell
    # elsewhere fills the Cell field. Not fired for Role-mode or leaf
    # clicks, and not fired at all in schematic mode (that mode routes
    # into fieldstool instead — see module docstring).
    cluster_picked = pyqtSignal(str)

    def __init__(self, main_window, connection=None,
                 pending_panel=None, fieldstool_window=None):
        super().__init__(main_window)
        # Stable widget identity for diagnostics/findChild. This is NO LONGER a
        # QDockWidget (2026-09-10, task T): it is one page of DockHub's central
        # QTabWidget, so saveState()/restoreState() never sees it.
        self.setObjectName("tree_dock")
        self._main_window = main_window
        # Injected BoardConnection — falls back to the owning window's when
        # not passed explicitly (keeps direct-construction callers, e.g.
        # tests that mutate main_window.connection.board, working).
        self._connection = connection if connection is not None else main_window.connection
        self._selected: List[Selected] = []
        # ref -> leaf QStandardItem, rebuilt alongside the model on every
        # _rebuild() — lets highlight_board_selection() jump straight to a
        # matched ref instead of walking the whole tree on every selection
        # tick (found live 2026-08-07: that walk, O(total rows) on every
        # ~400ms board-selection tick, was part of a rare GIL/Qt-mutex
        # deadlock window — see handoff_2026_08_07_worker_thread_gil_deadlock.md).
        self._ref_to_item: Dict[str, QStandardItem] = {}
        # Distinguishes "first build with actual data" (auto-expand top
        # level so the tree isn't a single flat blob) from "user just
        # collapsed everything via the button" (both leave
        # _capture_view_state's expanded_paths empty, but only the former
        # should re-expand) — _rebuild() runs on every ~2s poll tick, so
        # without this flag Collapse all would snap back open on the very
        # next tick. Only consumed once a rebuild actually has rows: the
        # group_by combo's persisted setting can itself trigger an empty
        # _rebuild() during __init__ (before the first set_footprints()),
        # and that empty build must not burn the flag before real data
        # ever gets a chance to auto-expand.
        self._auto_expand_pending = True
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run (same pattern
        # as PlacerDock/ThermalViaArrayDock).
        self._active_op: Optional[Any] = None
        # Set by gui/dock_hub.py to the main window's request_refresh() —
        # called right after Clear all/Delete selected successfully write to
        # the live board, so Pending changes' diff picks up the write
        # immediately instead of waiting for a manual Refresh click (the
        # automatic poll tick never refreshes on its own once already
        # connected, see MainWindow._poll's docstring).
        self.on_board_written: Optional[Callable[[], None]] = None
        # ... and the STORE half of the same news (Т5б): fired when this dock
        # records into (Tag selected) or drops records from (Delete selected/
        # Clear all) the project's override store, so the other holder re-reads
        # the FILE and the three-sided diff is rebuilt from the truth.
        # Deliberately separate from on_board_written — dock_hub wires both, and
        # neither is allowed to stand in for the other.
        self.on_overrides_written: Optional[Callable[[], None]] = None
        # Master-detail collaborators + state (2026-09-05, plan
        # components_fieldstool_master_detail): pending_panel is the shared
        # Pending page (a QWidget) and fieldstool_window is the embedded
        # fieldstool MainWindow. Direct-construction tests pass neither -> the
        # dock stays the plain single-tree page it always was.
        self._pending_panel = pending_panel
        self._fieldstool_window = fieldstool_window
        self._left_tabs: Optional[QTabWidget] = None
        self.splitter: Optional[QSplitter] = None
        self._splitter_restored = False

        # ── "Components" tab page — the tree and its controls ──────────────
        self._tree_page = QWidget()
        layout = QVBoxLayout(self._tree_page)
        layout.setContentsMargins(4, 4, 4, 4)

        top_row = QHBoxLayout()
        self.group_by = QComboBox()
        self.group_by.addItems([_("Role"), _("Cluster")])
        self.group_by.setCurrentIndex(settings.state.get("tree_group_by", 0))
        self.group_by.currentIndexChanged.connect(self._on_group_by_changed)
        top_row.addWidget(self.group_by)
        self.collapse_all_button = QPushButton(_("Collapse all"))
        self.collapse_all_button.clicked.connect(self.tree_collapse_all)
        top_row.addWidget(self.collapse_all_button)
        layout.addLayout(top_row)

        write_row = QHBoxLayout()
        self.delete_selected_button = QPushButton(_("Delete selected"))
        self.delete_selected_button.clicked.connect(self._on_delete_selected)
        write_row.addWidget(self.delete_selected_button)
        self.clear_all_button = QPushButton(_("Clear all"))
        self.clear_all_button.clicked.connect(self._on_clear_all)
        write_row.addWidget(self.clear_all_button)
        layout.addLayout(write_row)

        # Tag selected (2026-09-08, plan role_cluster_selection_tagging) — the
        # SET-side sibling of Delete selected/Clear all above: type a Role
        # and/or Cluster value (either independently optional — empty means
        # "don't touch this field", NOT "erase it") and write it onto every
        # footprint in the current selection in ONE commit. Editable combos
        # because Role/Cluster are open-ended per Cell/profile — a fixed enum
        # would be wrong; suggestions are auto-filled from values already on
        # the board (see _refresh_tag_combo_suggestions).
        tag_row = QHBoxLayout()
        self.tag_role_combo = QComboBox()
        self.tag_role_combo.setEditable(True)
        self.tag_role_combo.setPlaceholderText(_("Role (leave empty to skip)"))
        tag_row.addWidget(self.tag_role_combo)
        self.tag_cluster_combo = QComboBox()
        self.tag_cluster_combo.setEditable(True)
        self.tag_cluster_combo.setPlaceholderText(_("Cluster (leave empty to skip)"))
        tag_row.addWidget(self.tag_cluster_combo)
        self.tag_selected_button = QPushButton(_("Tag selected"))
        self.tag_selected_button.clicked.connect(self._on_tag_selected)
        tag_row.addWidget(self.tag_selected_button)
        layout.addLayout(tag_row)

        # NOT restored from settings here — see restore_mode_from_settings()
        # below for why (main_window.fieldstool_dock doesn't exist yet at
        # this point in gui/main_window.py's __init__).
        self.mode_checkbox = QCheckBox(_("Not yet applied"))
        self.mode_checkbox.toggled.connect(self._on_mode_changed)
        layout.addWidget(self.mode_checkbox)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_("Filter (ref/role/cluster)..."))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._rebuild)
        search_row.addWidget(self.search_edit)
        self.regex_checkbox = QCheckBox(_("regex"))
        self.regex_checkbox.toggled.connect(self._rebuild)
        search_row.addWidget(self.regex_checkbox)
        layout.addLayout(search_row)

        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_clicked)
        layout.addWidget(self.tree)
        # Highlight for the selected item (2026-08-15, plan
        # configurator_panel) — a bare QTreeView had near-invisible
        # selection on Windows (found during the same discussion that added
        # the Config tree's identical fix); applied at startup and
        # re-applied live by DockHub when the Settings tab's highlight
        # changes.
        self.tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))

        # Master-detail wrap (only when DockHub supplied the collaborators).
        self._splitter_sizes: Optional[SplitterSizeKeeper] = None
        if self._fieldstool_window is not None:
            self._build_master_detail()
        else:
            self._install_content(self._tree_page)
        # No size policy to set here: this widget is a page of DockHub's central
        # QTabWidget, which is the elastic centre (task T). S.1's
        # make_dock_grow_vertically was measured inert and removed.

    def _install_content(self, widget: QWidget) -> None:
        """Fill this central-TAB page with `widget`. It used to be a
        QDockWidget owned by QMainWindow, so QDockWidget.setWidget() was the
        install path; now it is a plain QWidget page (task T)."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)

    def _build_master_detail(self) -> None:
        """Wrap the components-tree page (plus the shared Pending page as a
        second left tab) around the embedded fieldstool window as the right
        QView: QSplitter { QTabWidget(tabs on TOP) { "Components",
        "Pending changes" } | fieldstool_window } — the same master-detail
        organisation Config/Trees use (2026-09-05, plan
        components_fieldstool_master_detail). Tabs on top is an explicit Denis
        requirement: unlike the left dock GROUP's bottom tab bar, this inner
        tab bar sits above its content."""
        self._left_tabs = QTabWidget()
        self._left_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._left_tabs.addTab(self._tree_page, _("Components"))
        if self._pending_panel is not None:
            self._left_tabs.addTab(self._pending_panel, _pending_tab_text(0))
            # Р6 (plan_2026_09_17_spoke_s3_roles_reminder): the panel reports
            # its own count (Р2 — computed from the SAME list its set_edits()
            # already receives, no second diff). getattr, not a hard type
            # check: callers may inject any QWidget as the page.
            count_signal = getattr(self._pending_panel,
                                   "pending_count_changed", None)
            if count_signal is not None:
                count_signal.connect(self._on_pending_count_changed)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._left_tabs)
        self.splitter.addWidget(self._fieldstool_window)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self._install_content(self.splitter)
        # S.3: remember the last good handle position while visible, so a quit
        # with this dock hidden (tabbed behind Config/Trees) cannot persist the
        # [0, 0] Qt reports for a hidden splitter.
        self._splitter_sizes = SplitterSizeKeeper(self.splitter)

    def _on_pending_count_changed(self, count: int) -> None:
        """Render the panel's pending count into the left tab's title (Р1,
        plan_2026_09_17_spoke_s3_roles_reminder). Only the TITLE changes — no
        widget is added, nothing moves or grows (Ф5)."""
        if self._left_tabs is None or self._pending_panel is None:
            return
        index = self._left_tabs.indexOf(self._pending_panel)
        if index >= 0:
            self._left_tabs.setTabText(index, _pending_tab_text(count))

    # ── Master-detail UI-state persistence (2026-09-05, plan
    #    components_fieldstool_master_detail) ───────────────────────────────
    #
    # The splitter handle position (tree|pending tabs vs the fieldstool pane)
    # and the active LEFT tab are remembered between runs like the dock layout
    # itself: flushed on quit as gui_state.json["components_splitter_sizes"]
    # (a plain two-int pixel list, the same human-readable key style as
    # Config's "config_splitter_sizes") + gui_state.json["components_left_tab"],
    # and re-applied once the dock is first shown (only then does the splitter
    # have a real, laid-out width) — mirrors ConfigTreeDock exactly.

    def persist_ui_state(self) -> None:
        """Flush the last GOOD splitter handle position + the active left tab to
        gui_state.json. Runs unconditionally on quit (MainWindow.
        _persist_settings). No-op when not in master-detail mode (plain
        standalone tree page).

        The handle position goes through SplitterSizeKeeper, never straight
        `sizes()`: a HIDDEN splitter reports [0, 0], and writing that restored
        the divider to the left on the next start (S.3 of
        techdocs/me/scroll.md). An earlier docstring here claimed
        sizes()/setSizes() round-trip cleanly before the widget is realized —
        that claim was WRONG, and it is exactly the defect: the values are only
        meaningful while the splitter is laid out."""
        if self._splitter_sizes is not None:
            sizes = self._splitter_sizes.capture()
            if sizes is not None:
                settings.state.set("components_splitter_sizes", list(sizes))
        if self._left_tabs is not None:
            settings.state.set("components_left_tab", self._left_tabs.currentIndex())

    def restore_ui_state(self) -> None:
        """Apply the persisted splitter handle + left-tab selection (if any).
        Best-effort: a missing / wrong-arity / non-numeric value is ignored,
        same "saved state must never crash startup" discipline as
        ConfigTreeDock. Public so tests can invoke it synchronously."""
        if self.splitter is not None and self.splitter.count() == 2:
            raw = settings.state.get("components_splitter_sizes")
            if isinstance(raw, list) and len(raw) == 2:
                try:
                    sizes = [int(x) for x in raw]
                except (TypeError, ValueError):
                    logger.warning("Ignoring invalid saved components splitter sizes: %r", raw)
                else:
                    self.splitter.setSizes(sizes)
        if self._left_tabs is not None:
            idx = settings.state.get("components_left_tab")
            if isinstance(idx, int) and 0 <= idx < self._left_tabs.count():
                self._left_tabs.setCurrentIndex(idx)

    def showEvent(self, event) -> None:
        """Restore the persisted splitter/tab UI state once the dock is first
        shown — setSizes() before the widget is realized would target a
        zero-size splitter (same first-show pattern as ConfigTreeDock)."""
        super().showEvent(event)
        if self._splitter_restored:
            return
        self._splitter_restored = True
        if self.splitter is not None:
            self.restore_ui_state()

    def reveal_ref(self, ref: str) -> None:
        """Select + expand to the tree node for a ref (no board IO) — used by
        a Pending-table row click so the Components tab shows the same
        component (plan components_fieldstool_master_detail). No-op when the
        ref isn't in the currently built model (e.g. a different mode)."""
        model = self.tree.model()
        if model is None:
            return
        item = self._ref_to_item.get(ref)
        if item is None:
            return
        index = model.indexFromItem(item)
        selection_model = self.tree.selectionModel()
        selection_model.clearSelection()
        selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
        parent = index.parent()
        while parent.isValid():
            self.tree.setExpanded(parent, True)
            parent = parent.parent()
        self.tree.scrollTo(index)

    def apply_highlight(self) -> None:
        """Re-apply the highlight stylesheet to this tree's selected item —
        one of the three highlight consumers (see gui/docks/configurator.py).
        Called at construction (reads the current settings.state) and by
        DockHub whenever the Settings tab's highlight_changed fires."""
        self.tree.setStyleSheet(highlight_stylesheet_for("QTreeView::item:selected"))

    def restore_mode_from_settings(self) -> None:
        """Call once, from gui/main_window.py, only after self._main_window's
        fieldstool_dock has been constructed — restoring "schematic mode"
        triggers a rebuild that reads main_window.fieldstool_dock.window,
        which doesn't exist yet during this dock's own __init__ (tree_dock
        is built before fieldstool_dock there)."""
        if settings.state.get("tree_schematic_mode"):
            self.mode_checkbox.setChecked(True)  # triggers _on_mode_changed via its signal

    def set_footprints(self, selected: List[Selected]) -> None:
        """Called by MainWindow after every successful poll/refresh with the
        full current snapshot (Board.select() with no filters). Only
        rebuilds the tree if currently showing live data — must not
        clobber an active schematic view on every ~2s poll tick. In live mode
        also refreshes the Tag selected combos' suggestion lists (their values
        come from whatever is currently on the board — no separate
        vocabulary, see _refresh_tag_combo_suggestions)."""
        self._selected = selected
        if not self.mode_checkbox.isChecked():
            self._refresh_tag_combo_suggestions()
            self._rebuild()

    def refresh_known_lists(self) -> None:
        """S.3.2 (plan_2026_09_11_stale_snapshot_role_lists.md): update the
        "Tag selected" Role/Cluster SUGGESTION lists from the CURRENT
        BoardConnection.snapshot WITHOUT rebuilding the tree model.
        Deliberately NOT routed through set_footprints: the model rebuild is
        exactly the churn commit 431bcef removed (see gui/main_window.py's
        module docstring — the visible flash/scroll-jump on an idle, unchanged
        board), so a navigation-triggered list refresh must not pay for it.
        Called by DockHub.push_known_lists (i.e. only AFTER the cache has been
        rebuilt), which is why it takes no snapshot argument — it reads the
        same live cache the other docks are fed from. Live mode only, like
        set_footprints: an active schematic view keeps its own values."""
        self._selected = list(getattr(self._connection, "snapshot", None) or [])
        if not self.mode_checkbox.isChecked():
            self._refresh_tag_combo_suggestions()

    def refresh_schematic_view(self) -> None:
        """Wired to fieldstool's on_components_changed hook (see
        gui/docks/fieldstool_dock.py) — an explicit Rescan/Apply-triggered
        schematic refresh updates this tree immediately if it's currently
        showing schematic data, instead of going stale until an unrelated
        event (search keystroke, mode toggle) happens to rebuild it."""
        if self.mode_checkbox.isChecked():
            self._rebuild()

    def highlight_board_selection(self, refs) -> None:
        """Reflects the live KiCad GUI selection into the tree — the reverse
        direction of _on_clicked (tree click -> board selection). Called
        frequently (see MainWindow's selection-watch timer), so unlike
        _rebuild() this never touches the model itself, only the selection
        (cheap) — and bails out early if the target refs already match
        what's currently selected, so an unchanged board selection doesn't
        cause any visible churn on every tick. Mode-agnostic: just matches
        against whatever _REF_ROLE data is in the currently active model.

        Looks matched refs up in self._ref_to_item (O(1) per ref) instead of
        walking the whole tree — found live 2026-08-07: on a 1139-footprint
        board, the old recursive walk visited every single row on every
        ~400ms tick regardless of how few refs actually matched, which
        widened the window for a rare GIL/Qt-connection-mutex deadlock (see
        handoff_2026_08_07_worker_thread_gil_deadlock.md) as well as being
        needlessly expensive on its own. scrollTo() is called once, for the
        last match, not once per match — each call fully overrides the
        previous scroll position anyway, so calling it per-match only wasted
        work without any visible difference."""
        model = self.tree.model()
        if model is None:
            return
        _, current_refs = self._capture_view_state()
        if current_refs == refs:
            return
        selection_model = self.tree.selectionModel()
        selection_model.clearSelection()
        if not refs:
            return

        last_index = None
        for ref in refs:
            item = self._ref_to_item.get(ref)
            if item is None:
                continue
            index = model.indexFromItem(item)
            selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
            parent = index.parent()
            while parent.isValid():
                self.tree.setExpanded(parent, True)
                parent = parent.parent()
            last_index = index

        if last_index is not None:
            self.tree.scrollTo(last_index)

    def tree_collapse_all(self) -> None:
        self.tree.collapseAll()

    def _on_group_by_changed(self) -> None:
        settings.state.set("tree_group_by", self.group_by.currentIndex())
        self._rebuild()

    def _on_mode_changed(self, checked: bool) -> None:
        settings.state.set("tree_schematic_mode", checked)
        # Schematic mode has no live footprint to write Role/Cluster onto
        # (its rows come from fieldstool's parsed-schematic list, not real
        # board footprints) — see module docstring.
        self.delete_selected_button.setEnabled(not checked)
        self.clear_all_button.setEnabled(not checked)
        self.tag_selected_button.setEnabled(not checked)
        self.tag_role_combo.setEnabled(not checked)
        self.tag_cluster_combo.setEnabled(not checked)
        self._rebuild()

    def _current_rows(self) -> List[_Row]:
        if not self.mode_checkbox.isChecked():
            return [_Row(s.ref, s.role, s.cluster) for s in self._selected]
        # Public accessors on fieldstool's MainWindow (see gui/fieldstool_
        # window.py's components/pending_refs properties) — not the private
        # `_components`/`_pending_edits`, which are refreshed wholesale and
        # owned by that window.
        dock = self._fieldstool()
        if dock is None:
            return []
        # Filtered to refs with an actual schematic-vs-board discrepancy —
        # this mode used to list every schematic component unconditionally
        # (its original job was just "pick a target without a live board
        # selection"), which read as a real bug once Pending changes existed
        # alongside it: components stayed listed here after a successful
        # Apply even though nothing was left to apply (Denis, live,
        # 2026-08-03). No live snapshot at all (never connected this
        # session) means pending_refs is empty and this mode shows nothing —
        # accepted tradeoff, see the fieldstool_window.pending_refs docstring.
        pending_refs = dock.pending_refs
        return [_Row(c.ref, c.role, c.cluster, divergent=c.divergent)
                for c in dock.components if c.ref in pending_refs]

    def _fieldstool(self):
        """The embedded fieldstool dock, resolved lazily — this dock is
        constructed BEFORE fieldstool_dock in gui/main_window.py, and
        direct-construction tests build it with no fieldstool_dock at all,
        so the reference must be looked up at use-time (never at __init__)
        and may legitimately be absent."""
        return getattr(self._main_window, "fieldstool_dock", None)

    def _overrides_window(self):
        """The window that HOLDS this project's override store — the embedded
        fieldstool MainWindow, resolved lazily exactly like _fieldstool() (this
        dock is built before fieldstool_dock, and direct-construction tests have
        no such window at all)."""
        if self._fieldstool_window is not None:
            return self._fieldstool_window
        return getattr(self._fieldstool(), "window", None)

    def _overrides(self):
        """The CURRENT project's override store, or None (Т5б).

        Taken THROUGH that window on purpose: it is the one holder the project's
        root_changed already pushes the store into (FieldsToolDock.set_root_path,
        Т4), so a project switch can never leave a stale store here and dock_hub
        needs no second wiring. None simply means "no project / no store yet" —
        there is then nothing to record into and nothing to forget from."""
        window = self._overrides_window()
        return getattr(window, "overrides", None) if window is not None else None

    def _reload_overrides(self):
        """Re-read the store from ITS FILE and answer with the fresh object (Т5б).

        Called BEFORE every write: the cell editor's Refs table is the other
        holder of the same file and may have recorded since this dock last
        looked, and "the file is the truth" must not depend on who wrote last.
        The window's reload also recomputes the three-sided Pending diff."""
        window = self._overrides_window()
        if window is None:
            return None
        window.reload_overrides()
        return self._overrides()

    def _rebuild(self) -> None:
        """Called on every poll tick while in live mode (via set_footprints),
        on group-by/mode toggle, and on every search-box keystroke — a
        brand new QStandardItemModel is built and swapped in each time
        (simplest way to reflect additions/removals/renames, and to drop
        now-empty groups after filtering), which by itself would silently
        clear the tree's own selection/expansion state even though nothing
        the user did changed. Snapshot both before the swap, by refdes/path
        (stable across rebuilds as long as the underlying grouping didn't
        change), and restore them after."""
        expanded_paths, selected_refs = self._capture_view_state()
        visible = self._filtered(self._current_rows())
        self._ref_to_item = {}
        model = QStandardItemModel()
        if self.group_by.currentIndex() == 0:  # Role
            self._build_flat(model, visible, key=lambda r: r.role)
        else:  # Cluster
            self._build_hierarchical(model, visible, key=lambda r: r.cluster)
        self.tree.setModel(model)
        self._restore_view_state(expanded_paths, selected_refs)

    def _filtered(self, rows: List[_Row]) -> List[_Row]:
        """Search box matches against ref/role/cluster (OR — typing a role
        name and typing a refdes are both "find the thing" the same way).
        Empty query -> everything, no filter. Regex mode is case-insensitive
        for the same reason plain-text mode is: this is a quick "find it",
        not a precise pattern tool."""
        query = self.search_edit.text()
        if not query:
            self.search_edit.setStyleSheet("")
            return rows

        if self.regex_checkbox.isChecked():
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error:
                # Invalid/incomplete regex while typing — flag it, don't
                # crash and don't hide everything mid-keystroke.
                self.search_edit.setStyleSheet(_INVALID_REGEX_STYLE)
                return rows
            self.search_edit.setStyleSheet("")
            return [r for r in rows if self._regex_matches(r, pattern)]

        self.search_edit.setStyleSheet("")
        needle = query.lower()
        return [r for r in rows if self._substring_matches(r, needle)]

    @staticmethod
    def _regex_matches(r: _Row, pattern: "re.Pattern") -> bool:
        return bool(pattern.search(r.ref) or (r.role and pattern.search(r.role))
                    or (r.cluster and pattern.search(r.cluster)))

    @staticmethod
    def _substring_matches(r: _Row, needle: str) -> bool:
        return (needle in r.ref.lower() or (r.role is not None and needle in r.role.lower())
                or (r.cluster is not None and needle in r.cluster.lower()))

    def _capture_view_state(self):
        model = self.tree.model()
        if model is None:
            return set(), set()
        selection_model = self.tree.selectionModel()
        expanded_paths = set()
        selected_refs = set()

        def walk(item: QStandardItem, path):
            index = model.indexFromItem(item)
            if self.tree.isExpanded(index):
                expanded_paths.add(path)
            if selection_model.isSelected(index):
                ref = item.data(_REF_ROLE)
                if ref is not None:
                    selected_refs.add(ref)
            for row in range(item.rowCount()):
                child = item.child(row)
                walk(child, path + (child.text(),))

        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            child = root.child(row)
            walk(child, (child.text(),))
        return expanded_paths, selected_refs

    def _restore_view_state(self, expanded_paths, selected_refs) -> None:
        model = self.tree.model()
        selection_model = self.tree.selectionModel()

        def walk(item: QStandardItem, path):
            index = model.indexFromItem(item)
            if path in expanded_paths:
                self.tree.setExpanded(index, True)
            ref = item.data(_REF_ROLE)
            if ref is not None and ref in selected_refs:
                selection_model.select(index, QItemSelectionModel.SelectionFlag.Select)
            for row in range(item.rowCount()):
                child = item.child(row)
                walk(child, path + (child.text(),))

        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            child = root.child(row)
            walk(child, (child.text(),))
        has_rows = model.invisibleRootItem().rowCount() > 0
        if not expanded_paths and self._auto_expand_pending and has_rows:
            # First build with actual data — start with top-level groups
            # visible instead of a single flat blob. Once the user has
            # interacted with expansion (including collapsing everything
            # on purpose via the Collapse all button), later rebuilds must
            # respect that instead of forcing depth-0 back open every poll
            # tick.
            self.tree.expandToDepth(0)
        if has_rows:
            self._auto_expand_pending = False

    @staticmethod
    def _leaf_item(r: _Row, show_role: bool = False) -> QStandardItem:
        # Role shown next to the ref ONLY in Cluster grouping (2026-08-13,
        # plan components_tree_show_role, Denis: "в дереве Components
        # дописывать кроме Рефа — роль (если есть)"): in Role grouping the
        # role is already the parent group, repeating it per leaf would be
        # noise without new information. Only when non-empty (a row without a
        # role stays just `ref`, no empty parens). Warning icon stays the
        # string's suffix.
        text = r.ref
        if show_role and r.role:
            text += f" ({r.role})"
        text += " ⚠" if r.divergent else ""  # warn on multi-unit divergence (schematic mode)
        item = QStandardItem(text)
        item.setEditable(False)
        item.setData(r.ref, _REF_ROLE)
        if r.divergent:
            item.setToolTip(_("This refdes' units disagree on Role/Cluster — edit carefully."))
        return item

    def _build_flat(self, model: QStandardItemModel, items: List[_Row],
                     key: Callable[[_Row], Optional[str]]) -> None:
        groups = {}
        for r in items:
            groups.setdefault(key(r) or _("(none)"), []).append(r)
        root = model.invisibleRootItem()
        for name in sorted(groups):
            members = groups[name]
            group_item = QStandardItem(f"{name} ({len(members)})")
            group_item.setEditable(False)
            group_item.setData(None, _REF_ROLE)
            group_item.setData(name, _GROUP_VALUE_ROLE)
            for r in sorted(members, key=lambda r: r.ref):
                # Role grouping — the role is the parent group already, don't
                # repeat it per leaf.
                leaf = self._leaf_item(r, show_role=False)
                self._ref_to_item[r.ref] = leaf
                group_item.appendRow(leaf)
            root.appendRow(group_item)

    def _build_hierarchical(self, model: QStandardItemModel, items: List[_Row],
                             key: Callable[[_Row], Optional[str]]) -> None:
        root = model.invisibleRootItem()
        nodes = {(): root}  # path tuple (segments so far) -> QStandardItem
        for r in sorted(items, key=lambda r: (key(r) or "", r.ref)):
            cluster = key(r)
            segments = tuple(cluster.split("/")) if cluster else (_("(none)"),)
            for depth in range(1, len(segments) + 1):
                path = segments[:depth]
                if path in nodes:
                    continue
                parent = nodes[segments[:depth - 1]]
                node = QStandardItem(segments[depth - 1])
                node.setEditable(False)
                node.setData(None, _REF_ROLE)
                node.setData("/".join(path), _GROUP_VALUE_ROLE)
                parent.appendRow(node)
                nodes[path] = node
            # Cluster grouping — the role isn't visible anywhere else in this
            # mode, so it goes next to the ref.
            leaf = self._leaf_item(r, show_role=True)
            self._ref_to_item[r.ref] = leaf
            nodes[segments].appendRow(leaf)

    def _on_clicked(self, index) -> None:
        item = self.tree.model().itemFromIndex(index)
        refs = set(self._collect_refs(item))
        is_group = item.data(_REF_ROLE) is None

        if not self.mode_checkbox.isChecked():
            if (self.group_by.currentIndex() == 1  # Cluster grouping
                    and is_group):
                self.cluster_picked.emit(item.data(_GROUP_VALUE_ROLE))

            board = self._connection.board
            if board is None or not refs:
                return
            # A background long op (poll, Extract/Redraw, or — since
            # 2026-08-03 — the refresh MainWindow.request_refresh() fires
            # right after a Stage/Clear all write) holds the shared kipy
            # socket; a synchronous select_items() call here would
            # interleave into its in-flight REQ transaction. Found live:
            # "ConnectionError: Error receiving reply from KiCad: Operation
            # canceled" on a tree click that happened to land mid-poll — the
            # request_refresh fix made that window much easier to hit.
            # fieldstool_window.py's _push_selection_to_board() already
            # guards the identical call the same way.
            if self._connection.long_op_active:
                return
            footprints = [s.fp for s in self._selected if s.ref in refs]
            board.adapter.select_items(footprints)
        else:
            dock = self._fieldstool()
            if dock is None:
                return
            if is_group:
                field_name = "Role" if self.group_by.currentIndex() == 0 else "Cluster"
                dock.pick_group(field_name, item.data(_GROUP_VALUE_ROLE), sorted(refs))
            else:
                dock.pick_leaf(sorted(refs))
            self._main_window.open_fieldstool()

    def _collect_refs(self, item: QStandardItem) -> List[str]:
        """Leaf items answer with their own refdes; group items answer with
        every refdes under them, so clicking a group (e.g. a whole Cluster
        branch) highlights all of it on the board, not just one component."""
        ref = item.data(_REF_ROLE)
        if ref is not None:
            return [ref]
        refs: List[str] = []
        for row in range(item.rowCount()):
            refs.extend(self._collect_refs(item.child(row)))
        return refs

    # ── Delete selected / Clear all (2026-08-03, live mode only) ─────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    def _selected_tree_refs(self) -> List[str]:
        """Every refdes reachable from the tree's CURRENT selection — a
        selected leaf answers with itself, a selected group with every
        refdes under it (via _collect_refs, same as a click's board-
        highlight already does)."""
        model = self.tree.model()
        if model is None:
            return []
        refs: List[str] = []
        for index in self.tree.selectionModel().selectedIndexes():
            refs.extend(self._collect_refs(model.itemFromIndex(index)))
        return sorted(set(refs))

    def _on_delete_selected(self) -> None:
        self._show_message("")
        if self._connection.board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return
        refs = self._selected_tree_refs()
        if not refs:
            self._show_message(_("Nothing selected."), _ERROR_STYLE)
            return
        footprints = [s.fp for s in self._selected if s.ref in refs]
        self._start_clear_op(footprints, _("Cleared Role/Cluster on {count} component(s)."))

    def _on_clear_all(self) -> None:
        self._show_message("")
        if self._connection.board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return
        if not self._selected:
            self._show_message(_("Nothing to clear."), _ERROR_STYLE)
            return
        reply = QMessageBox.question(
            self, _("Clear all"),
            _("Clear Role and Cluster on ALL {count} component(s) currently on the board? "
              "This is a single commit — undo-able in KiCad with Ctrl+Z.")
            .format(count=len(self._selected)))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_clear_op([s.fp for s in self._selected],
                             _("Cleared Role/Cluster on all {count} component(s)."))

    def _start_clear_op(self, footprints: List[Any], message_template: str) -> None:
        payload = {"footprints": footprints, "message_template": message_template}
        self._active_op = start_long_op(
            self._connection, (self.delete_selected_button, self.clear_all_button),
            self._run_clear, self._finish_clear, self._on_clear_failed, payload)

    def _do_clear(self, footprints: List[Any], message_template: str) -> None:
        """Synchronous composition of run + finish — the same behaviour the
        async button path would produce, kept for tests and any caller that
        must not return until the clear is complete (same shape as
        PlacerDock's _do_redraw)."""
        result = self._run_clear({"footprints": footprints, "message_template": message_template})
        self._finish_clear(result)

    def _run_clear(self, payload: dict) -> dict:
        """Worker thread: board IPC only — never touches a widget. Blanks
        Role AND Cluster (not just the field the tree happens to be
        grouped by right now) in ONE commit via set_field_values_bulk —
        same "batch undo in one Ctrl+Z" reasoning as PlacerDock's Cluster
        tagging.

        Footprints missing either field entirely are skipped BEFORE the
        batch is built, not sent — set_field_value is fatal on a missing
        field, and set_field_values_bulk wraps the whole batch in one
        commit, so a single such footprint would otherwise roll back
        every other footprint in the batch too (found live: 287
        components, one missing Cluster, the entire Clear all rolled
        back with nothing written)."""
        footprints = payload["footprints"]
        adapter = self._connection.board.adapter
        usable = []
        skipped_refs = []
        for fp in footprints:
            if adapter.has_field(fp, ROLE_FIELD_NAME) and adapter.has_field(fp, CLUSTER_FIELD_NAME):
                usable.append(fp)
            else:
                skipped_refs.append(fp.ref if fp.ref else "?")

        updates = []
        for fp in usable:
            updates.append((fp, ROLE_FIELD_NAME, ""))
            updates.append((fp, CLUSTER_FIELD_NAME, ""))
        if updates:
            try:
                adapter.set_field_values_bulk(
                    updates, _("Clear Role/Cluster on {count} component(s)").format(count=len(usable)))
            except ValidationError as e:
                return {"error": str(e)}
        return {"count": len(usable), "message_template": payload["message_template"],
                "skipped_refs": skipped_refs,
                # EVERY ref we were asked about, skipped ones included — the
                # store half must cover them too (Т5б/С22): a footprint the
                # board cannot take the write for is exactly the one whose
                # record would otherwise resurrect the erased value.
                "refs": [fp.ref for fp in footprints if fp.ref]}

    def _finish_clear(self, result: dict) -> None:
        """UI thread: reflect the worker's result into the message label, then
        drop OUR records for the same components (Т5б/С22)."""
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        message = result["message_template"].format(count=result["count"])
        skipped = result.get("skipped_refs") or []
        if skipped:
            shown = ", ".join(skipped[:_MAX_SKIPPED_REFS_SHOWN])
            if len(skipped) > _MAX_SKIPPED_REFS_SHOWN:
                shown += _(" and {more} more").format(more=len(skipped) - _MAX_SKIPPED_REFS_SHOWN)
            message += " " + _("Skipped {count} without Role/Cluster field: {refs}").format(
                count=len(skipped), refs=shown)
        dropped, store_error = self._forget_store_records(result.get("refs") or [])
        if store_error:
            # The board part DID happen; say what did not, in the same line, so
            # the user is never left with a message that hides half the truth.
            message += " " + store_error
        elif dropped:
            message += " " + _("Dropped {count} stored override(s) too — a record left "
                              "behind would bring the value straight back.").format(count=dropped)
        self._show_message(message, _WARN_STYLE if store_error else _SUCCESS_STYLE)
        if self.on_board_written:
            self.on_board_written()
        if dropped and self.on_overrides_written:
            # The window holds the store in memory; without this its copy (and
            # the three-sided diff built from it) would still show the records
            # we just dropped — the diff, not the board, is what the user reads.
            self.on_overrides_written()

    def _on_clear_failed(self, message: str) -> None:
        self._show_message(_("Clear failed: {error}").format(error=message), _ERROR_STYLE)

    def _forget_store_records(self, refs: Iterable[str]) -> tuple:
        """Drop OUR stored records for these components (Т5б/С22).

        Answers (how many records went, error message or ""). Keys come from the
        live snapshot — the store is keyed by symbol uuid, so a ref with no
        footprint there has no key and is left ALONE: its record, if any, is
        Т6's "forget" business, named in the Log rather than guessed at (the
        invention С12 forbids). No store (no project open) means there is
        nothing to clean — there are no records to resurrect anything.

        A record belongs to a component and a FIELD; Role and Cluster are
        forgotten explicitly, matching exactly the two fields the board half
        blanks, so this cannot grow a surprising blast radius later."""
        store = self._overrides()
        if store is None or not refs:
            return (0, "")
        by_ref = {s.ref: s for s in self._selected}
        dropped = 0
        for ref in sorted(set(refs)):
            selected = by_ref.get(ref)
            symbol_uuid = symbol_uuid_of(selected.fp) if selected is not None else None
            if not symbol_uuid:
                continue
            for field in (ROLE_FIELD_NAME, CLUSTER_FIELD_NAME):
                dropped += store.forget(symbol_uuid, field)
        if not dropped:
            return (0, "")
        try:
            store.save()
        except (OSError, ValidationError) as e:
            # The in-memory copy is now ahead of the file; hand the truth back
            # to its owner (which re-reads the FILE) instead of leaving the two
            # disagreeing behind an error line.
            if self.on_overrides_written:
                self.on_overrides_written()
            return (0, _("Could not save the override store: {error}").format(error=e))
        return (dropped, "")

    # ── Tag selected (2026-09-08; the ADDRESS moved to the store in Т5б) ──
    #
    # The SET-side mirror of Delete selected/Clear all above: instead of
    # blanking Role/Cluster, SET the typed Role and/or Cluster value(s) on every
    # component in the current selection. Both fields are independently optional
    # (empty = "don't touch this field", NOT "erase it"), which covers both "one
    # Cluster for a whole group" and "narrowed subgroup, one Role".
    #
    # Since Т5б it RECORDS INTO THE PROJECT'S OVERRIDE STORE instead of writing
    # the board. A board tag would be invisible the moment a record exists (our
    # value outranks the board, Т2) — so for exactly the components the user had
    # already noted, "tagged" would silently mean nothing, and this one button
    # would work or not depending on state the user cannot see. No worker and no
    # socket either: the store is a file, and the only thing taken from the board
    # is the LAST READ, where the symbol uuids (the store's key) come from. The
    # board write itself has not gone away — Т5а gives it an explicit button and
    # a CLI key of its own.

    @staticmethod
    def _add_combo_item_if_missing(combo: QComboBox, value: str) -> None:
        """Ensure `value` is offered as a combo item — used right after a
        successful Tag write, so the just-written value shows up in the
        suggestions immediately instead of waiting for the next poll tick to
        refresh the snapshot (mirror of fieldstool_window's same helper)."""
        if value and combo.findText(value) < 0:
            combo.addItem(value)

    def _refresh_tag_combo_suggestions(self) -> None:
        """Re-populate the Role/Cluster combo suggestion lists from whatever
        values are currently visible in the live board snapshot — no separate
        fixed vocabulary to maintain, the board itself is the source of known
        values. Preserves whatever the user is currently typing (editable
        combo's current text survives the rebuild, so a poll tick never eats
        a half-typed value)."""
        roles = sorted({s.role for s in self._selected if s.role})
        clusters = sorted({s.cluster for s in self._selected if s.cluster})
        for combo, values in ((self.tag_role_combo, roles),
                              (self.tag_cluster_combo, clusters)):
            current_text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(values)
            combo.setCurrentText(current_text)
            combo.blockSignals(False)

    def _on_tag_selected(self) -> None:
        """Collect the targets on the UI thread and hand them with the typed
        value(s) to _record_tag — no start_long_op: nothing here touches the
        socket, so there is nothing to keep off it."""
        self._show_message("")
        role_value = self.tag_role_combo.currentText().strip()
        cluster_value = self.tag_cluster_combo.currentText().strip()
        if not role_value and not cluster_value:
            self._show_message(_("Enter a Role and/or a Cluster value first."), _ERROR_STYLE)
            return
        refs = self._selected_tree_refs()
        if not refs:
            self._show_message(_("Nothing selected."), _ERROR_STYLE)
            return
        self._record_tag(refs, role_value, cluster_value)

    def _do_tag(self, refs: Iterable[str], role_value: str, cluster_value: str) -> None:
        """Synchronous convenience for the ONE path the button takes — kept for
        tests and any caller that must not return until the record is written
        (same shape as _do_clear). Takes REFDES, not footprints: a tag needs a
        KEY, and the key comes from the snapshot's footprint for that refdes, so
        accepting footprints would only invite keying by something else."""
        self._record_tag(refs, role_value, cluster_value)

    def _record_tag(self, refs: Iterable[str], role_value: str,
                    cluster_value: str) -> None:
        """Record the typed value(s) for every ref that HAS a key, then report.

        The store is re-read from its FILE first ("the file is the truth": the
        Refs table is the other holder and may have recorded since this dock last
        looked), and refused BY NAME when the project has no store — the old
        fallback, silently writing the board instead, is exactly what Т5б
        removes. A ref with no footprint in the last board read has no symbol
        uuid, so no key: it is reported by name (С12), never recorded under an
        invented one."""
        store = self._reload_overrides()
        if store is None:
            self._show_message(_("Open a project first — the override store lives next "
                                 "to its profile config."), _ERROR_STYLE)
            return
        by_ref = {s.ref: s for s in self._selected}
        records = []
        skipped = []
        for ref in sorted(set(refs)):
            selected = by_ref.get(ref)
            symbol_uuid = symbol_uuid_of(selected.fp) if selected is not None else None
            if not symbol_uuid:
                skipped.append(ref)
                continue
            if role_value:
                records.append((symbol_uuid, ref, ROLE_FIELD_NAME, role_value))
            if cluster_value:
                records.append((symbol_uuid, ref, CLUSTER_FIELD_NAME, cluster_value))
        for symbol_uuid, ref, field, value in records:
            store.set(symbol_uuid, ref, field, value, SOURCE_ROLE_CLUSTER_TREE)
        if records:
            try:
                store.save()
            except (OSError, ValidationError) as e:
                self._show_message(_("Could not save the override store: {error}").format(
                    error=e), _ERROR_STYLE)
                if self.on_overrides_written:
                    # Hand the truth back to the file's owner instead of leaving
                    # its in-memory copy ahead of the file behind an error line.
                    self.on_overrides_written()
                return
        self._finish_tag({"count": len({ref for _, ref, _, _ in records}),
                          "records": len(records), "skipped": skipped,
                          "role_value": role_value, "cluster_value": cluster_value})

    def _finish_tag(self, result: dict) -> None:
        """UI thread: report, keep the typed value among the combo suggestions
        (the snapshot that feeds them will not know about the record until Т5г
        makes the snapshot store-aware), and let the store's other holder re-read
        the file."""
        if result["count"]:
            if result["role_value"] and result["cluster_value"]:
                message = _("{count} component(s): Role and Cluster noted for this project — "
                            "they win over the board and survive an F8").format(
                                count=result["count"])
            elif result["role_value"]:
                message = _("{count} component(s): Role noted for this project — it wins "
                            "over the board and survives an F8").format(count=result["count"])
            else:
                message = _("{count} component(s): Cluster noted for this project — it wins "
                            "over the board and survives an F8").format(count=result["count"])
        else:
            message = _("nothing was recorded — none of the selected components is in the "
                        "last board read")
        skipped = result.get("skipped") or []
        if skipped:
            shown = ", ".join(skipped[:_MAX_SKIPPED_REFS_SHOWN])
            if len(skipped) > _MAX_SKIPPED_REFS_SHOWN:
                shown += _(" and {more} more").format(more=len(skipped) - _MAX_SKIPPED_REFS_SHOWN)
            message += " " + _("Skipped {count} — no symbol uuid in the last board read: "
                              "{refs}").format(count=len(skipped), refs=shown)
        self._show_message(message, _SUCCESS_STYLE if result["count"] else _WARN_STYLE)
        # The just-recorded value is not in the CURRENT snapshot (the snapshot
        # still reflects the board), so add it straight to the combo item lists;
        # the next _refresh_tag_combo_suggestions() keeps the board as the source
        # of suggestions until Т5г makes the snapshot store-aware too.
        self._add_combo_item_if_missing(self.tag_role_combo, result["role_value"])
        self._add_combo_item_if_missing(self.tag_cluster_combo, result["cluster_value"])
        # NOT on_board_written: the board did not change here (the same line Т5
        # drew for Stage). The STORE hook is what makes the other holder re-read
        # the file — and the window's own reload recomputes the three-sided
        # Pending diff, which is what the user must see next.
        if self.on_overrides_written:
            self.on_overrides_written()
