# gui/docks/detail_panel.py
"""
DetailDock — one shared right-hand form area for Placer/Points/Chains/Net
traces/Cells/Tools (2026-08-03, GUI tree roadmap: Denis — "Панели: Экстракт,
Пласер, Рут — становятся контекстными (общая область формы)"). Used to be
separate QDockWidgets tabified together; PlacerDock/RuleDock/... are plain
QWidgets (see each module's own docstring) living as pages of one
QStackedWidget here, switched by a QTabBar that drives the stack directly (the
standard "tabs without their own page-widgets" Qt pattern — a QTabWidget would
insist on owning/parenting the pages itself).

Pages that LEFT this dock (2026-08-31 .. 2026-09-01) into standalone dialogs,
so they are no longer tabs here (the Extract page was removed entirely in
Phase F 2026-09-01 — "Extract tree..." in DockHub is the single capture path):
- Thermal via (2026-09-01, plan plan_2026_09_01_thermal_via_dialog.md) — the
  non-modal ThermalViaDialog (gui/docks/thermal_via_dialog.py);
- Points (2026-09-01, plan plan_2026_09_01_points_dialog.md) — the non-modal
  PointsDialog (gui/docks/points_dialog.py);
- Tools (2026-09-01, plan plan_2026_09_01_tools_dialog_and_entity_roles.md) —
  the non-modal ToolsDialog (gui/docks/tools_dialog.py) — the Entity's
  electrical fields (Nets/Net overrides/Refs) are no longer a tab here;
- Cells (2026-09-04, plan plan_2026_09_04_celldock_to_dialog.md) — the
  non-modal CellDialog (gui/docks/cell_dialog.py) — editing a cell's
  Components/Vias/Tracks/Nested is no longer a tab here;
- Project (RootMetadataDock) and Settings (ConfiguratorDock) — 2026-09-01,
  plan project_settings_dialogs — moved into the non-modal ProjectDialog
  (File > "Project...", gui/docks/project_dialog.py) and the modal
  SettingsDialog (Tools > "Settings...", gui/docks/settings_dialog.py).
  RootMetadataDock keeps its root-changed broadcast / Working-file combobox
  from inside the dialog — see gui/dock_hub.py.

Switching is BOTH automatic (Config-tree context) and manual (the tab bar
itself) — Denis picked this over auto-only when asked live 2026-08-03,
specifically so a panel stays reachable even when the tree click that
would normally select it hasn't happened (e.g. checking Rules while
Placer is what's currently showing). gui/dock_hub.py wires the automatic
half for the TWO pages that remain here:
cell_picked/placement_picked/add_placer_requested -> show_placer(),
net_trace_picked -> show_net_trace(). The pages that moved to standalone
dialogs (Points/Chains/Tools/Cells) no longer switch this dock at all —
their own context entries/double-clicks open the matching dialog instead
(see gui/dock_hub.py). The old file_selected -> show_root() fallback
(2026-08-03) was
REMOVED 2026-09-01 together with the Project tab — a plain file/category
click in the Config tree no longer switches this dock (it still feeds
RootMetadataDock's Working-file combo display via set_working_file_from_tree,
see gui/dock_hub.py).

Raise-on-switch (2026-08-06, found live — Denis: "неплохо бы подсвечивать,
какой док сейчас активен. А то вообще, не видно, кто и что"): every
show_X() below now ALSO calls setVisible(True)/raise_() on itself. Before
this, only the "Add .../Edit cell..." context-menu delegates in
gui/dock_hub.py (_start_new_placement etc.) did that explicitly — a plain
tree left-click (cell_picked/points_picked/rule_picked/...) switched the
internal QTabBar but never brought DetailDock itself to the front of its
OWN tabified group (it shares the right-hand dock area with fieldstool) —
so clicking a Cell while looking at fieldstool silently switched Placer
"under the hood" with nothing visible happening. Putting raise/show INSIDE
show_X() itself (rather than duplicated at each dock_hub.py call site, as
it used to be for the Add/Edit actions) fixes every caller at once and
can't drift out of sync again.

Window title (same request) reflects which page AND which entity is
loaded on it right now — e.g. "Detail — Net traces: GND" — read fresh
from each page's own name field via _current_entity_name() rather than
threaded through every show_X() call, so it stays correct even when a
DIFFERENT entity is loaded while already on the same tab (leaf click ->
load_entry() -> show_X() where the tab index doesn't change, so
QTabBar.currentChanged never fires — _update_title() is therefore called
unconditionally by show_X(), not only via that signal).
"""
from PyQt6.QtWidgets import (QDockWidget, QStackedWidget, QTabBar, QVBoxLayout,
                             QWidget)

from kicadstamp.i18n import _

from ._common import highlight_stylesheet_for
from .net_trace import NetTraceDock
from .placer import PlacerDock

# Rules is NOT a tab here anymore (2026-09-01, plan rules_to_chains): the
# Chain form moved out into the standalone non-modal ChainDialog (like Points/
# Tools/Thermal via/Extract before it) — a chains: node is edited via a DOUBLE
# click in the Config tree, not a Detail dock page.
_PLACER, _NET_TRACE = range(2)


class _StackedPages(QStackedWidget):
    """QStackedWidget whose size hints track ONLY the current page.

    2026-08-30 (Denis, live): a stock QStackedWidget's size hints follow the
    LARGEST-ever page, so with the stack inside a QScrollArea the scroll
    overflowed even on a page that fit, and (since the scroll area was
    removed the same day — "убираем скроллы внутри доков") the dock itself
    would stay sized to the tallest page. Overriding BOTH hints to the
    current widget makes the dock follow the page you are actually on, never
    a historical maximum."""

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


class DetailDock(QDockWidget):
    def __init__(self, main_window, connection=None):
        super().__init__(_("Detail"), main_window)
        # Stable QDockWidget identity for QMainWindow.saveState()/restoreState()
        # (handoff sync_skip_message_and_view_menu §0) — without a unique
        # objectName Qt cannot reliably map a saved layout blob back to this
        # dock between runs.
        self.setObjectName("detail_dock")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tab_bar = QTabBar()
        # Project (2026-09-01, plan project_settings_dialogs) is no longer a
        # tab here — it moved out into the standalone non-modal ProjectDialog
        # (File > "Project...", see gui/docks/project_dialog.py). Settings
        # (ConfiguratorDock) moved out too — into the modal SettingsDialog
        # (Tools > "Settings...", see gui/docks/settings_dialog.py). This dock
        # keeps only the entity edit forms.
        self.tab_bar.addTab(_("Placer"))
        self.tab_bar.addTab(_("Net traces"))
        layout.addWidget(self.tab_bar)

        # _StackedPages, not a stock QStackedWidget (2026-08-30): the size
        # hints follow the CURRENT page, so the dock sizes to the page you are
        # actually on — see _StackedPages.
        self.stack = _StackedPages()
        self.placer_panel = PlacerDock(main_window)
        self.net_trace_panel = NetTraceDock(main_window, connection=connection)
        # Stack order must match the tab-bar order exactly (setCurrentIndex
        # drives stack.setCurrentIndex).
        self.stack.addWidget(self.placer_panel)
        self.stack.addWidget(self.net_trace_panel)
        # The stack sits DIRECTLY in the dock layout (2026-08-30, Denis:
        # "убираем скроллы внутри доков"). The 2026-08-27 QScrollArea wrap was
        # removed: _StackedPages already makes the dock follow the CURRENT page
        # (not the tallest one), and the app-wide `* { min-width: 0 }` stylesheet
        # (see _common.apply_compact_field_minimums) lets every page/tab shrink
        # to its absolute minimum — so nothing overflows off-screen anymore and
        # no scrollbar is needed. self.stack keeps the same API
        # (count/currentWidget/setCurrentIndex/addWidget) for every call site.
        layout.addWidget(self.stack, 1)

        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)
        self.tab_bar.currentChanged.connect(self._update_title)

        self.setWidget(container)
        self._update_title()
        # Highlight scheme at startup (reads settings.state) — the Settings
        # dialog's highlight_changed drives this live afterwards (see
        # gui/dock_hub.py).
        self.apply_highlight()

    # ── Page labels / current entity name (for the window title) ─────────

    _PAGE_LABELS = {
        _PLACER: _("Placer"),
        _NET_TRACE: _("Net traces"),
    }

    def _current_entity_name(self) -> str:
        """Best-effort "what's loaded on the current page right now",
        read fresh from that page's own name field — no page-agnostic
        concept of "current entity" exists, each dock owns its own name/net
        widget (see each module's __init__), so this just knows where to
        look for each one. The Coordinate placer has no single current
        entity (it edits a whole TABLE of rows at once) — empty string,
        title falls back to just the page label."""
        index = self.tab_bar.currentIndex()
        if index == _PLACER:
            return self.placer_panel.current_entity_name
        if index == _NET_TRACE:
            return self.net_trace_panel.net_edit.currentText().strip()
        return ""

    def _update_title(self) -> None:
        label = self._PAGE_LABELS.get(self.tab_bar.currentIndex(), "")
        name = self._current_entity_name()
        self.setWindowTitle(
            _("Detail — {label}: {name}").format(label=label, name=name) if name
            else _("Detail — {label}").format(label=label))

    def apply_highlight(self) -> None:
        """Re-apply the highlight stylesheet to the active tab of this
        dock's QTabBar — one of the three highlight consumers (see
        gui/docks/configurator.py). Called at construction (reads the
        current settings.state) and by DockHub whenever the Settings dialog's
        highlight_changed fires."""
        self.tab_bar.setStyleSheet(highlight_stylesheet_for("QTabBar::tab:selected"))

    # ── Switching (always raises/shows itself — see module docstring) ────

    def _show(self, index: int) -> None:
        self.tab_bar.setCurrentIndex(index)
        self._update_title()  # unconditional — currentChanged doesn't fire when index is unchanged
        self.setVisible(True)
        self.raise_()

    def show_placer(self) -> None:
        self._show(_PLACER)

    def show_coordinate_placer(self) -> None:
        """Alias for show_placer (2026-08-12, Group 1): the merged PlacerDock
        hosts the coordinate mode now — there is no separate Coordinate
        placer tab anymore, the Placer tab switches its field set instead."""
        self._show(_PLACER)

    def show_net_trace(self) -> None:
        self._show(_NET_TRACE)
