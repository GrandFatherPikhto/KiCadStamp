# gui/docks/detail_panel.py
"""
DetailDock — one shared right-hand form area for Placer/Root/Thermal via
(2026-08-03, GUI tree roadmap: Denis — "Панели: Экстракт, Пласер, Рут —
становятся контекстными (общая область формы)"). Used to be separate
QDockWidgets tabified together; PlacerDock/RootMetadataDock/
ThermalViaArrayDock/... are plain QWidgets (see each module's own docstring)
living as pages of one QStackedWidget here, switched by a QTabBar that drives
the stack directly (the standard "tabs without their own page-widgets" Qt
pattern — a QTabWidget would insist on owning/parenting the pages itself).
Extract is no longer a page: 2026-08-31 (plan extract_dialog_and_hide_existing
.md) it moved to a standalone non-modal dialog (gui/docks/extract_dialog.py)
launched from the Config tree context menu — see gui/dock_hub.py's
_open_extract_dialog(). Thermal via is no longer a page either: 2026-09-01
(plan plan_2026_09_01_thermal_via_dialog.md) it moved to a standalone
non-modal dialog (gui/docks/thermal_via_dialog.py) launched from the Tools
menu ("Place thermal vias...") and the Config tree context menu — see
gui/dock_hub.py's _open_thermal_via_dialog().

Switching is BOTH automatic (Config-tree context) and manual (the tab bar
itself) — Denis picked this over auto-only when asked live 2026-08-03,
specifically so a panel stays reachable even when the tree click that
would normally select it hasn't happened (e.g. checking Root while
Placer is what's currently showing). gui/dock_hub.py wires the automatic
half: cell_picked/placement_picked/add_placer_requested -> show_placer(),
file_selected
(fires on EVERY click, including leaf clicks, always BEFORE the more specific
signal — see config_tree.py's _on_clicked) -> show_root() as the fallback
for a plain file/category click; the more specific signal's handler (if any)
then runs right after and wins.

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
loaded on it right now — e.g. "Detail — Cells: composite" — read fresh
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
from .cell_editor import CellDock
from .configurator import ConfiguratorDock
from .net_trace import NetTraceDock
from .placer import PlacerDock
from .points import PointsDock
from .root_metadata import RootMetadataDock
from .rules import RuleDock
from .tools import ToolsDock

_ROOT, _PLACER, _POINTS, _RULES, _NET_TRACE, _CELLS = range(6)
# Tools (2026-08-30, Entity/Placement split phase 5.2 stage 3): the Entity's
# electrical fields (Nets/Net overrides/Refs), moved out of PlacerDock.
# Indexes shifted -2 on 2026-09-01 (plan extract_dialog_and_hide_existing.md /
# plan_2026_09_01_thermal_via_dialog.md): the Extract page (2026-08-31) and
# the Thermal via page (2026-09-01) were both removed from this dock.
_TOOLS = 6
_SETTINGS = 7


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
        # Project first (2026-08-11, Denis: "сделай док Проект первым. А то
        # он не понятно, где стоит") — it's the control tower now (root
        # ownership + Working file combobox, see gui/docks/root_metadata.py's
        # module docstring), so it's also the tab shown by default on
        # startup (QTabBar's own currentIndex defaults to whatever was
        # added first). Displayed as "Project" (2026-08-05, Denis: "давай не
        # root, а project") — the underlying panel is still
        # RootMetadataDock/root_metadata_dock/show_root() (root_panel here
        # below): "root" remains the correct internal term (it edits the
        # project's ROOT config file), only the user-facing label changed.
        self.tab_bar.addTab(_("Project"))
        self.tab_bar.addTab(_("Placer"))
        self.tab_bar.addTab(_("Points"))
        self.tab_bar.addTab(_("Rules"))
        self.tab_bar.addTab(_("Net traces"))
        self.tab_bar.addTab(_("Cells"))
        # Tools (2026-08-30, phase 5.2 stage 3): the Entity's electrical
        # fields (Nets/Net overrides/Refs) — see gui/docks/tools.py. Inserted
        # BEFORE Settings so Settings stays last (its own "added last"
        # convention is preserved).
        self.tab_bar.addTab(_("Tools"))
        # Settings (2026-08-15, plan configurator_panel) — GUI/app settings
        # for this machine (always-on-top, tray, highlight color, connection
        # timeout), deliberately NOT project config — see
        # gui/docks/configurator.py's module docstring for the "what this is
        # NOT" section. Added LAST so every existing tab's index is unchanged.
        self.tab_bar.addTab(_("Settings"))
        layout.addWidget(self.tab_bar)

        # _StackedPages, not a stock QStackedWidget (2026-08-30): the size
        # hints follow the CURRENT page, so the dock sizes to the page you are
        # actually on — see _StackedPages.
        self.stack = _StackedPages()
        self.root_panel = RootMetadataDock(main_window)
        self.placer_panel = PlacerDock(main_window)
        self.points_panel = PointsDock(main_window, connection=connection)
        self.rules_panel = RuleDock(main_window)
        self.net_trace_panel = NetTraceDock(main_window, connection=connection)
        self.cells_panel = CellDock(main_window)
        self.configurator_panel = ConfiguratorDock(main_window, connection=connection)
        self.tools_panel = ToolsDock(main_window)
        self.stack.addWidget(self.root_panel)
        self.stack.addWidget(self.placer_panel)
        self.stack.addWidget(self.points_panel)
        self.stack.addWidget(self.rules_panel)
        self.stack.addWidget(self.net_trace_panel)
        self.stack.addWidget(self.cells_panel)
        # Tools BEFORE Settings — the stack order must match the tab-bar
        # order exactly (setCurrentIndex drives stack.setCurrentIndex).
        self.stack.addWidget(self.tools_panel)
        self.stack.addWidget(self.configurator_panel)
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
        # tab's highlight_changed drives this live afterwards (see
        # gui/dock_hub.py).
        self.apply_highlight()

    # ── Page labels / current entity name (for the window title) ─────────

    _PAGE_LABELS = {
        _PLACER: _("Placer"),
        _ROOT: _("Project"),
        _POINTS: _("Points"),
        _RULES: _("Rules"),
        _NET_TRACE: _("Net traces"),
        _CELLS: _("Cells"),
        _TOOLS: _("Tools"),
        _SETTINGS: _("Settings"),
    }

    def _current_entity_name(self) -> str:
        """Best-effort "what's loaded on the current page right now",
        read fresh from that page's own name field — no page-agnostic
        concept of "current entity" exists, each dock owns its own name/net
        widget (see each module's __init__), so this just knows where to
        look for each one. Project/Coordinate placer have no single
        current entity (Project edits a whole file, Coordinate placer edits
        a whole TABLE of rows at once) — empty string for both, title
        falls back to just the page label."""
        index = self.tab_bar.currentIndex()
        if index == _PLACER:
            return self.placer_panel.current_entity_name
        if index == _POINTS:
            return self.points_panel.name_edit.text().strip()
        if index == _RULES:
            return self.rules_panel.name_edit.text().strip() or self.rules_panel.net_edit.currentText().strip()
        if index == _NET_TRACE:
            return self.net_trace_panel.net_edit.currentText().strip()
        if index == _CELLS:
            return self.cells_panel.name_edit.text().strip()
        if index == _TOOLS:
            return self.tools_panel.target_combo.currentText().strip()
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
        current settings.state) and by DockHub whenever the Settings tab's
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

    def show_root(self) -> None:
        self._show(_ROOT)

    def show_coordinate_placer(self) -> None:
        """Alias for show_placer (2026-08-12, Group 1): the merged PlacerDock
        hosts the coordinate mode now — there is no separate Coordinate
        placer tab anymore, the Placer tab switches its field set instead."""
        self._show(_PLACER)

    def show_points(self) -> None:
        self._show(_POINTS)

    def show_rules(self) -> None:
        self._show(_RULES)

    def show_net_trace(self) -> None:
        self._show(_NET_TRACE)

    def show_cells(self) -> None:
        self._show(_CELLS)

    def show_tools(self) -> None:
        """Same pattern as the other show_X() pages — the Tools tab (ToolsDock,
        the Entity's electrical fields, 2026-08-30 phase 5.2 stage 3)."""
        self._show(_TOOLS)

    def show_settings(self) -> None:
        """Same pattern as the other show_X() pages — the Settings tab
        (ConfiguratorDock) is reachable by clicking the tab bar directly; a
        programmatic entry point keeps it consistent with every other page."""
        self._show(_SETTINGS)
