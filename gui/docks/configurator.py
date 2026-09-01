# gui/docks/configurator.py
"""
ConfiguratorDock — the Settings browser: GUI/app settings for THIS MACHINE,
deliberately NOT project config (the Project settings live in RootMetadataDock,
now hosted in its own non-modal ProjectDialog, see gui/docks/project_dialog.py).
Hosted inside the modal SettingsDialog (gui/docks/settings_dialog.py), launched
from the Tools menu ("Settings...").

Since 2026-09-01 (plan project_settings_dialogs) this is no longer a Detail
dock tab: it is a two-pane browser — a QTreeWidget of categories on the left
(General / Appearance / KiCad / Config tree / Hotkeys / MCP server) and the
matching settings page on the right (QStackedWidget). And settings are applied
EXPLICITLY (OK/Cancel/Apply, modal), not live: every widget holds the "draft";
ConfiguratorDock.apply() writes the draft to gui_state.json and fires the side
effects (window-flag / tray / highlight / timeout / hotkeys); cancel() /
reload_from_state() re-seed the widgets from the persisted state, discarding
the draft.

All state lives in gui/settings.py's flat gui_state.json — the same storage
last_root_file/window_geometry/always_on_top/tray_enabled already use — so this
widget is just a GUI facade over that store, no new storage to invent.

Always-on-top / Tray are a MOVE, not a copy: the two checkboxes used to sit
directly in MainWindow's status bar (gui/main_window.py); the actual
window-flag / QSystemTrayIcon logic still lives in MainWindow's
_set_always_on_top/_set_tray_enabled. This widget only owns the UI and re-emits
toggles via always_on_top_toggled/tray_enabled_toggled signals — now EMITTED
FROM apply() (the modal OK/Cancel/Apply contract: a checkbox toggle alone no
longer flips the window), which DockHub wires back onto MainWindow
(gui/dock_hub.py). MainWindow._restore_window_state applies the persisted flags
explicitly at startup (see gui/main_window.py).

Highlight color — a single scheme applied to all three highlight consumers
(DetailDock's active tab, ConfigTreeDock's and RoleClusterTreeDock's selected
tree item), picked via "System palette" (palette(highlight)) or a custom color
(QColorDialog). highlight_changed() fires from apply(); DockHub re-applies the
stylesheet to all three target widgets (the helper itself is
gui/docks/_common.py's highlight_stylesheet_for).

KiCad connection timeout — the ONE user-facing timeout (DEFAULT_TIMEOUT_MS, see
kicadstamp/constants.py). The internal protective timings (_CONNECT_TIMEOUT_GRACE_S,
_CLOSE_TIMEOUT_S, single-instance ping) are deliberately NOT exposed here — one of
them literally just closed a live GUI freeze (see handoff_2026_08_15_pynng_close_
timeout.md), so letting the user set it to 0/huge would reopen that bug class. The
value is written into connection.timeout_ms on apply(), which BoardConnection reads
by reference on every connect() — so it takes effect on the NEXT connection without
disturbing any open one.
"""
from functools import partial
from typing import Dict

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QGroupBox, QHBoxLayout,
                             QKeySequenceEdit, QLabel, QPushButton, QRadioButton,
                             QSpinBox, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _

from .. import settings
from ..hotkeys import get_shortcut, registered_hotkeys, set_shortcut
from ._common import DEFAULT_HIGHLIGHT_COLOR

# Sensible bounds for the connection timeout spinbox, in milliseconds.
TIMEOUT_MIN_MS = 1000
TIMEOUT_MAX_MS = 120000


class ConfiguratorDock(QWidget):
    """Two-pane settings browser (QTreeWidget of categories on the left, the
    matching settings page on the right) hosting this machine's GUI/app
    settings — see module docstring. Deliberately NOT project config. Hosted
    in the modal SettingsDialog; settings apply explicitly via apply()
    (OK/Apply), never live — cancel()/reload_from_state() discard the draft."""

    # Emitted from apply() — DockHub connects these back to
    # MainWindow._set_always_on_top/_set_tray_enabled (the logic that actually
    # flips the window flag / builds the tray icon stays there).
    always_on_top_toggled = pyqtSignal(bool)
    tray_enabled_toggled = pyqtSignal(bool)
    # Emitted from apply() whenever the highlight scheme changed — DockHub
    # listens and re-applies the stylesheet to all highlight consumers (see
    # gui/dock_hub.py).
    highlight_changed = pyqtSignal()

    def __init__(self, main_window, connection=None):
        # main_window is accepted for consistency with the other Detail-dock
        # pages, but this panel needs no window back-reference — everything it
        # does is either self-contained (highlight/timeout) or re-emitted as a
        # signal for DockHub to wire.
        super().__init__()
        self._connection = connection
        # Draft highlight color — updated by the picker/preview, written to
        # settings.state only in apply() (OK/Cancel/Apply contract).
        self._draft_highlight_color = settings.state.get(
            "highlight_color", DEFAULT_HIGHLIGHT_COLOR)

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # ── Left: category tree ──────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFixedWidth(170)
        self.tree.setObjectName("settings_category_tree")
        root.addWidget(self.tree)

        # ── Right: one settings page per category ────────────────────────
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # Build pages in category order; the tree insertion order must match
        # the stack order (rows link the two). Pages are kept as attributes so
        # tests can assert which page the tree switched to.
        self.general_page = self._build_general_page()
        self.appearance_page = self._build_appearance_page()
        self.kicad_page = self._build_kicad_page()
        self.config_tree_page = self._build_config_tree_page()
        self.hotkeys_page = self._build_hotkeys_page()
        self.mcp_page = self._build_mcp_page()

        for label, page in (
            (_("General"), self.general_page),
            (_("Appearance"), self.appearance_page),
            (_("KiCad"), self.kicad_page),
            (_("Config tree"), self.config_tree_page),
            (_("Hotkeys"), self.hotkeys_page),
            (_("MCP server"), self.mcp_page),
        ):
            self.tree.addTopLevelItem(QTreeWidgetItem([label]))
            self.stack.addWidget(page)

        self.tree.currentItemChanged.connect(self._on_category_changed)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

        # Seed every widget from the persisted state (no draft leaks into a
        # fresh construction).
        self.reload_from_state()

    # ── Category tree -> page switching ──────────────────────────────────

    def _on_category_changed(self, current, _previous) -> None:
        if current is None:
            return
        self.stack.setCurrentIndex(self.tree.indexOfTopLevelItem(current))

    # ── Pages (built in the same order as the tree) ──────────────────────

    def _build_general_page(self) -> QWidget:
        """Window: always on top / tray icon."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        window_group = QGroupBox(_("Window"))
        window_layout = QVBoxLayout(window_group)
        self.always_on_top_checkbox = QCheckBox(_("Always on top"))
        window_layout.addWidget(self.always_on_top_checkbox)
        self.tray_checkbox = QCheckBox(_("Tray icon"))
        window_layout.addWidget(self.tray_checkbox)
        layout.addWidget(window_group)
        layout.addStretch(1)
        return page

    def _build_appearance_page(self) -> QWidget:
        """Highlight color: system palette vs custom."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        highlight_group = QGroupBox(_("Highlight color"))
        highlight_layout = QVBoxLayout(highlight_group)
        self.system_radio = QRadioButton(_("System palette"))
        self.custom_radio = QRadioButton(_("Custom"))
        highlight_layout.addWidget(self.system_radio)
        highlight_layout.addWidget(self.custom_radio)

        pick_row = QHBoxLayout()
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(16, 16)
        pick_row.addWidget(self.color_preview)
        self.pick_color_button = QPushButton(_("Pick color..."))
        self.pick_color_button.clicked.connect(self._pick_color)
        pick_row.addWidget(self.pick_color_button)
        pick_row.addStretch(1)
        highlight_layout.addLayout(pick_row)
        layout.addWidget(highlight_group)
        layout.addStretch(1)
        # UI-only enable (a custom color can only be picked in Custom mode);
        # no persistence here — that happens in apply().
        self.custom_radio.toggled.connect(self.pick_color_button.setEnabled)
        return page

    def _build_kicad_page(self) -> QWidget:
        """KiCad connection timeout."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        timeout_group = QGroupBox(_("KiCad connection"))
        timeout_layout = QVBoxLayout(timeout_group)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(TIMEOUT_MIN_MS, TIMEOUT_MAX_MS)
        self.timeout_spin.setSuffix(" ms")
        timeout_layout.addWidget(self.timeout_spin)
        layout.addWidget(timeout_group)
        layout.addStretch(1)
        return page

    def _build_config_tree_page(self) -> QWidget:
        """Config-tree setting: rename confirmation."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        config_tree_group = QGroupBox(_("Config tree"))
        config_tree_layout = QVBoxLayout(config_tree_group)
        self.rename_confirmation_checkbox = QCheckBox(_("Show confirmation after rename"))
        self.rename_confirmation_checkbox.setToolTip(
            _("When checked, Rename on the Config tree shows a confirmation "
              "dialog after the entry is renamed. Uncheck to rename silently — "
              "the summary line still goes to the Log."))
        config_tree_layout.addWidget(self.rename_confirmation_checkbox)
        layout.addWidget(config_tree_group)
        layout.addStretch(1)
        return page

    def _build_hotkeys_page(self) -> QWidget:
        """One QKeySequenceEdit per registered QAction-based hotkey (see
        gui/hotkeys.py). Rebound values stay in the widget (the draft) until
        apply() -> set_shortcut() writes gui_state.json["hotkeys"] and
        re-applies to the live QAction."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        hotkeys_group = QGroupBox(_("Hotkeys"))
        self._hotkeys_layout = QVBoxLayout(hotkeys_group)
        self.hotkey_edits: Dict[str, QKeySequenceEdit] = {}
        layout.addWidget(hotkeys_group)
        layout.addStretch(1)
        self.refresh_hotkeys()
        return page

    def _build_mcp_page(self) -> QWidget:
        """MCP server: raw-write gate. The headless MCP server (kicadstamp-mcp,
        stdio) reads its raw-write gate from gui_state.json (this checkbox) OR
        the KICADSTAMP_MCP_ALLOW_RAW_WRITE=1 env var — so the Settings dialog
        can control the spawned server without an env var."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        mcp_group = QGroupBox(_("MCP server"))
        mcp_layout = QVBoxLayout(mcp_group)
        self.raw_write_checkbox = QCheckBox(
            _("Allow raw MCP write tools (kicad_raw_move_footprint)"))
        self.raw_write_checkbox.setToolTip(
            _("When checked, the MCP server (kicadstamp-mcp) registers the raw, "
              "high-risk kicad_raw_move_footprint tool — direct kipy writes "
              "bypassing the validated config layer. Every call requires the "
              "expected board name (expected_board_name) and refuses to write "
              "when a different board is open in KiCad. Takes effect when the "
              "MCP server next starts. Same effect as the "
              "KICADSTAMP_MCP_ALLOW_RAW_WRITE=1 environment variable."))
        mcp_layout.addWidget(self.raw_write_checkbox)
        mcp_info = QLabel(
            _("MCP server: kicadstamp-mcp over stdio. Register it in the "
              "client's Settings tab, or via the repo's .mcp.json."))
        mcp_info.setWordWrap(True)
        mcp_layout.addWidget(mcp_info)
        layout.addWidget(mcp_group)
        layout.addStretch(1)
        return page

    # ── Draft / apply / cancel (OK/Cancel/Apply contract) ────────────────

    def reload_from_state(self) -> None:
        """Re-seed every widget from the persisted gui_state.json — discards
        any unsaved draft. Called on construction, on Cancel, and by
        SettingsDialog before each modal open (so a draft that was never
        applied is never what the next open shows)."""
        self.always_on_top_checkbox.setChecked(
            bool(settings.state.get("always_on_top", False)))
        self.tray_checkbox.setChecked(bool(settings.state.get("tray_enabled", False)))
        self._draft_highlight_color = settings.state.get(
            "highlight_color", DEFAULT_HIGHLIGHT_COLOR)
        mode = settings.state.get("highlight_mode", "system")
        self.custom_radio.setChecked(mode == "custom")
        self.system_radio.setChecked(mode != "custom")
        self.pick_color_button.setEnabled(mode == "custom")
        self._update_color_preview()
        self.timeout_spin.setValue(settings.state.get("kicad_timeout_ms",
                                                      DEFAULT_TIMEOUT_MS))
        self.rename_confirmation_checkbox.setChecked(
            bool(settings.state.get("rename_confirmation_enabled", True)))
        self.raw_write_checkbox.setChecked(
            bool(settings.state.get("mcp_allow_raw_write", False)))
        for action_id, edit in self.hotkey_edits.items():
            edit.setKeySequence(get_shortcut(action_id))

    def apply(self) -> None:
        """Commit the current widget state (the draft) to gui_state.json and
        fire the side effects — the OK/Apply half of the modal Settings dialog.
        Called by SettingsDialog on OK/Apply. Side effects are emitted LAST so
        a listener sees a fully persisted snapshot."""
        always_on_top = self.always_on_top_checkbox.isChecked()
        tray_enabled = self.tray_checkbox.isChecked()
        settings.state.set("always_on_top", always_on_top)
        settings.state.set("tray_enabled", tray_enabled)

        mode = "custom" if self.custom_radio.isChecked() else "system"
        settings.state.set("highlight_mode", mode)
        settings.state.set("highlight_color", self._draft_highlight_color)

        timeout_ms = self.timeout_spin.value()
        settings.state.set("kicad_timeout_ms", timeout_ms)
        # BoardConnection reads self.timeout_ms by reference on every connect()
        # (gui/connection.py), so writing it here takes effect on the next
        # connection attempt without touching any open one.
        if self._connection is not None:
            self._connection.timeout_ms = timeout_ms

        settings.state.set("rename_confirmation_enabled",
                           self.rename_confirmation_checkbox.isChecked())
        settings.state.set("mcp_allow_raw_write", self.raw_write_checkbox.isChecked())

        for action_id, edit in self.hotkey_edits.items():
            # Only persist hotkeys that actually CHANGED: set_shortcut writes a
            # gui_state.json["hotkeys"] override, and the codebase's rule is
            # "absent entry == code default" — re-writing every unchanged
            # default on each Apply would dirty the state for no reason.
            if edit.keySequence() != get_shortcut(action_id):
                set_shortcut(action_id, edit.keySequence().toString())

        self.always_on_top_toggled.emit(always_on_top)
        self.tray_enabled_toggled.emit(tray_enabled)
        self.highlight_changed.emit()

    def cancel(self) -> None:
        """Discard the draft — re-seed widgets from the persisted state (the
        Cancel half of the modal dialog)."""
        self.reload_from_state()

    # ── Highlight ────────────────────────────────────────────────────────

    def _update_color_preview(self) -> None:
        self.color_preview.setStyleSheet(
            f"background: {self._draft_highlight_color}; border: 1px solid #888888;")

    def _pick_color(self) -> None:
        """QColorDialog is a first use in this project (see
        techdocs/handoff/plan_2026_08_15_configurator_panel.md) — standard
        Qt pattern: getColor(initial, parent, title), only act if
        color.isValid() (the user pressed Cancel). The picked color updates
        the draft (and the preview); it is written to settings.state only in
        apply()."""
        current = QColor(self._draft_highlight_color)
        color = QColorDialog.getColor(current, self, _("Pick highlight color"))
        if not color.isValid():
            return
        self._draft_highlight_color = color.name()
        self._update_color_preview()

    # ── Hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ──────

    def refresh_hotkeys(self) -> None:
        """Rebuild the Hotkeys page's edits from the current gui.hotkeys
        registry — idempotent. Called at construction (whatever is registered
        so far) and AGAIN by DockHub once EVERY dock is built, so dock
        construction order never decides which actions are rebindable here (a
        dock created after this panel — e.g. LogDock — must still appear; its
        hotkey works via parent.addAction regardless, but without this refresh
        it would silently be missing from the rebinding UI)."""
        self.hotkey_edits = {}
        # Each row is added via addLayout(row) (see the build loop below), so a
        # taken item wraps a SUB-layout, not a widget — item.widget() is None
        # for it and a naive `widget.deleteLater()` silently never fires,
        # leaving the old QLabel/QKeySequenceEdit alive as orphans that
        # re-appear stacked over the rebuilt rows (found in review — this
        # method runs TWICE per startup: ConfiguratorDock.__init__ + DockHub).
        # So: descend into the sub-layout, delete its widgets, then delete the
        # sub-layout itself; a direct child widget (if any) is deleted too.
        while self._hotkeys_layout.count():
            item = self._hotkeys_layout.takeAt(0)
            sub_layout = item.layout()
            if sub_layout is not None:
                while sub_layout.count():
                    sub_item = sub_layout.takeAt(0)
                    sub_widget = sub_item.widget()
                    if sub_widget is not None:
                        sub_widget.deleteLater()
                sub_layout.deleteLater()
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for action_id, label, _default in registered_hotkeys():
            row = QHBoxLayout()
            label_widget = QLabel(label)
            label_widget.setWordWrap(True)
            row.addWidget(label_widget, 1)
            edit = QKeySequenceEdit(get_shortcut(action_id))
            edit.setMaximumWidth(160)
            self.hotkey_edits[action_id] = edit
            row.addWidget(edit)
            self._hotkeys_layout.addLayout(row)
