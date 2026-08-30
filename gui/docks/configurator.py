# gui/docks/configurator.py
"""
ConfiguratorDock — the "Settings" tab of the Detail dock (2026-08-15, plan
techdocs/handoff/plan_2026_08_15_configurator_panel.md). Hosts pure GUI/app
settings for THIS MACHINE, deliberately NOT project config: unlike
RootMetadataDock (the "Project" tab), this page never touches the project
YAML at all. All state lives in gui/settings.py's flat gui_state.json — the
same storage last_root_file/window_geometry/always_on_top/tray_enabled
already use — so this dock is just a GUI facade over that store plus a few
new keys (highlight_mode/highlight_color/kicad_timeout_ms), no new storage
to invent.

Always-on-top / Tray are a MOVE, not a copy: the two checkboxes used to sit
directly in MainWindow's status bar (gui/main_window.py); the actual
window-flag / QSystemTrayIcon logic still lives in MainWindow's
_set_always_on_top/_set_tray_enabled. This dock only owns the UI and
re-emits toggles via always_on_top_toggled/tray_enabled_toggled signals,
which DockHub wires back onto MainWindow (gui/dock_hub.py) — the same
dock->window wiring pattern every other dock uses. Persistence of these two
keys also stays in MainWindow._persist_settings (unchanged behavior); the
checkboxes are read back through this dock's attributes.

Highlight color — a single scheme applied to all three highlight consumers
(DetailDock's active tab, ConfigTreeDock's and RoleClusterTreeDock's
selected tree item), picked via "System palette" (palette(highlight)) or a
custom color (QColorDialog). highlight_changed() fires on any change; DockHub
re-applies the stylesheet to all three target widgets (the helper itself is
gui/docks/_common.py's highlight_stylesheet_for).

KiCad connection timeout — the ONE user-facing timeout (DEFAULT_TIMEOUT_MS,
see kicadstamp/constants.py). The internal protective timings
(_CONNECT_TIMEOUT_GRACE_S, _CLOSE_TIMEOUT_S, single-instance ping) are
deliberately NOT exposed here — one of them literally just closed a live GUI
freeze (see handoff_2026_08_15_pynng_close_timeout.md), so letting the user
set it to 0/huge would reopen that bug class. The value is written straight
into connection.timeout_ms, which BoardConnection reads by reference on every
connect(), so it takes effect on the NEXT connection without disturbing any
open one.
"""
from functools import partial
from typing import Dict

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QGroupBox, QHBoxLayout,
                             QKeySequenceEdit, QLabel, QPushButton, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _

from .. import settings
from ..hotkeys import get_shortcut, registered_hotkeys, set_shortcut
from ._common import DEFAULT_HIGHLIGHT_COLOR

# Sensible bounds for the connection timeout spinbox, in milliseconds.
TIMEOUT_MIN_MS = 1000
TIMEOUT_MAX_MS = 120000


class ConfiguratorDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py), tab
    labelled "Settings" — see module docstring for what lives here and,
    just as importantly, what deliberately does NOT."""

    # Re-emitted from the checkbox toggles — DockHub connects these back to
    # MainWindow._set_always_on_top/_set_tray_enabled (the logic that
    # actually flips the window flag / builds the tray icon stays there).
    always_on_top_toggled = pyqtSignal(bool)
    tray_enabled_toggled = pyqtSignal(bool)
    # Fired whenever the highlight scheme changed (mode radio or custom
    # color picked) — DockHub listens and re-applies the stylesheet to all
    # three highlight consumers (see gui/dock_hub.py).
    highlight_changed = pyqtSignal()

    def __init__(self, main_window, connection=None):
        # main_window is accepted for consistency with the other Detail-dock
        # pages, but this panel needs no window back-reference — everything
        # it does is either self-contained (highlight/timeout) or re-emitted
        # as a signal for DockHub to wire.
        super().__init__()
        self._connection = connection

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── Window: always on top / tray ─────────────────────────────────
        window_group = QGroupBox(_("Window"))
        window_layout = QVBoxLayout(window_group)
        self.always_on_top_checkbox = QCheckBox(_("Always on top"))
        self.always_on_top_checkbox.toggled.connect(self.always_on_top_toggled)
        window_layout.addWidget(self.always_on_top_checkbox)
        self.tray_checkbox = QCheckBox(_("Tray icon"))
        self.tray_checkbox.toggled.connect(self.tray_enabled_toggled)
        window_layout.addWidget(self.tray_checkbox)
        layout.addWidget(window_group)

        # ── Highlight color ──────────────────────────────────────────────
        highlight_group = QGroupBox(_("Highlight color"))
        highlight_layout = QVBoxLayout(highlight_group)
        self.system_radio = QRadioButton(_("System palette"))
        self.custom_radio = QRadioButton(_("Custom"))
        mode = settings.state.get("highlight_mode", "system")
        if mode == "custom":
            self.custom_radio.setChecked(True)
        else:
            self.system_radio.setChecked(True)
        highlight_layout.addWidget(self.system_radio)
        highlight_layout.addWidget(self.custom_radio)

        pick_row = QHBoxLayout()
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(16, 16)
        self._update_color_preview()
        pick_row.addWidget(self.color_preview)
        self.pick_color_button = QPushButton(_("Pick color..."))
        self.pick_color_button.clicked.connect(self._pick_color)
        self.pick_color_button.setEnabled(mode == "custom")
        pick_row.addWidget(self.pick_color_button)
        pick_row.addStretch(1)
        highlight_layout.addLayout(pick_row)
        layout.addWidget(highlight_group)

        # custom_radio alone determines the mode (checked -> custom,
        # unchecked -> system), and the two radios are auto-exclusive — so a
        # SINGLE toggled connection covers both directions and emits
        # highlight_changed exactly once per change (connecting both radios
        # would fire the handler twice per toggle: custom.on and
        # system.off on the same switch). Connected AFTER the initial
        # setChecked above so construction never emits.
        self.custom_radio.toggled.connect(self._on_highlight_mode_changed)

        # ── KiCad connection timeout ─────────────────────────────────────
        timeout_group = QGroupBox(_("KiCad connection"))
        timeout_layout = QVBoxLayout(timeout_group)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(TIMEOUT_MIN_MS, TIMEOUT_MAX_MS)
        self.timeout_spin.setSuffix(" ms")
        # Apply the persisted value on startup (blocks the valueChanged
        # signal during setValue so the constructor can't clobber a
        # --timeout-ms passed on the CLI with DEFAULT_TIMEOUT_MS before a
        # value was ever stored in gui_state.json — see _on_timeout_changed).
        persisted_timeout = settings.state.get("kicad_timeout_ms", None)
        value = persisted_timeout if persisted_timeout is not None else DEFAULT_TIMEOUT_MS
        self.timeout_spin.blockSignals(True)
        self.timeout_spin.setValue(value)
        self.timeout_spin.blockSignals(False)
        self.timeout_spin.valueChanged.connect(self._on_timeout_changed)
        timeout_layout.addWidget(self.timeout_spin)
        # A stored value takes over on the NEXT connection too (the spinbox
        # edit path already does this via _on_timeout_changed; this covers
        # the startup restore, where the connection was built before this
        # panel existed and got the CLI/default timeout).
        if persisted_timeout is not None and self._connection is not None:
            self._connection.timeout_ms = value
        layout.addWidget(timeout_group)

        # ── Config tree ──────────────────────────────────────────────────
        # First (and so far only) ConfigTreeDock setting — the confirmation
        # dialog that pops after a Rename (2026-08-25, Denis: "оно нафиг не
        # надо"). Default ON — this only adds the OPTION to silence it, the
        # default behavior is unchanged.
        config_tree_group = QGroupBox(_("Config tree"))
        config_tree_layout = QVBoxLayout(config_tree_group)
        self.rename_confirmation_checkbox = QCheckBox(_("Show confirmation after rename"))
        self.rename_confirmation_checkbox.setToolTip(
            _("When checked, Rename on the Config tree shows a confirmation "
              "dialog after the entry is renamed. Uncheck to rename silently — "
              "the summary line still goes to the Log."))
        self.rename_confirmation_checkbox.setChecked(
            settings.state.get("rename_confirmation_enabled", True))
        self.rename_confirmation_checkbox.toggled.connect(
            self._on_rename_confirmation_toggled)
        config_tree_layout.addWidget(self.rename_confirmation_checkbox)
        layout.addWidget(config_tree_group)

        # ── Hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ──
        # One QKeySequenceEdit per registered QAction-based hotkey (see
        # gui/hotkeys.py): rebinding writes gui_state.json["hotkeys"] and
        # re-applies to the live action immediately. The list is populated by
        # refresh_hotkeys() — called here (whatever is registered so far) AND
        # by DockHub AFTER every dock is constructed, so dock construction
        # order NEVER drops an action from this list: a dock built late (e.g.
        # LogDock, created after ConfiguratorDock — see gui/dock_hub.py) would
        # otherwise work as a hotkey but silently stay unrebindable here
        # (found in review of handoff 2026_08_30_hotkeys_pilot_and_file_menu).
        hotkeys_group = QGroupBox(_("Hotkeys"))
        hotkeys_layout = QVBoxLayout(hotkeys_group)
        self.hotkey_edits: Dict[str, QKeySequenceEdit] = {}
        self._hotkeys_layout = hotkeys_layout
        layout.addWidget(hotkeys_group)
        self.refresh_hotkeys()

        # ── MCP server ─────────────────────────────────────────────────────
        # The headless MCP server (kicadstamp-mcp, stdio) reads its raw-write
        # gate from gui_state.json (this checkbox) OR the
        # KICADSTAMP_MCP_ALLOW_RAW_WRITE=1 env var — so the Settings tab can
        # control the spawned server without an env var.
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
        self.raw_write_checkbox.setChecked(
            settings.state.get("mcp_allow_raw_write", False))
        self.raw_write_checkbox.toggled.connect(self._on_raw_write_toggled)
        mcp_layout.addWidget(self.raw_write_checkbox)
        mcp_info = QLabel(
            _("MCP server: kicadstamp-mcp over stdio. Register it in the "
              "client's Settings tab, or via the repo's .mcp.json."))
        mcp_info.setWordWrap(True)
        mcp_layout.addWidget(mcp_info)
        layout.addWidget(mcp_group)

        layout.addStretch(1)

    # ── Highlight ────────────────────────────────────────────────────────

    def _update_color_preview(self) -> None:
        color = settings.state.get("highlight_color", DEFAULT_HIGHLIGHT_COLOR)
        self.color_preview.setStyleSheet(
            f"background: {color}; border: 1px solid #888888;")

    def _on_highlight_mode_changed(self) -> None:
        mode = "custom" if self.custom_radio.isChecked() else "system"
        settings.state.set("highlight_mode", mode)
        self.pick_color_button.setEnabled(mode == "custom")
        self.highlight_changed.emit()

    def _pick_color(self) -> None:
        """QColorDialog is a first use in this project (see
        techdocs/handoff/plan_2026_08_15_configurator_panel.md) — standard
        Qt pattern: getColor(initial, parent, title), only act if
        color.isValid() (the user pressed Cancel)."""
        current = QColor(settings.state.get("highlight_color", DEFAULT_HIGHLIGHT_COLOR))
        color = QColorDialog.getColor(current, self, _("Pick highlight color"))
        if not color.isValid():
            return
        hex_color = color.name()
        settings.state.set("highlight_color", hex_color)
        self._update_color_preview()
        self.highlight_changed.emit()

    # ── Config tree ──────────────────────────────────────────────────────

    def _on_rename_confirmation_toggled(self, checked: bool) -> None:
        settings.state.set("rename_confirmation_enabled", checked)

    # ── MCP server ────────────────────────────────────────────────────────

    def _on_raw_write_toggled(self, checked: bool) -> None:
        settings.state.set("mcp_allow_raw_write", checked)

    # ── Connection timeout ───────────────────────────────────────────────

    def _on_timeout_changed(self, value: int) -> None:
        settings.state.set("kicad_timeout_ms", value)
        # BoardConnection reads self.timeout_ms by reference on every
        # connect() (gui/connection.py), so writing it here takes effect on
        # the next connection attempt without touching any open one.
        if self._connection is not None:
            self._connection.timeout_ms = value

    # ── Hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ──────

    def refresh_hotkeys(self) -> None:
        """Rebuild the Hotkeys group from the current gui.hotkeys registry —
        idempotent. Called at construction (whatever is registered so far) and
        AGAIN by DockHub once EVERY dock is built, so dock construction order
        never decides which actions are rebindable here (a dock created after
        this panel — e.g. LogDock — must still appear; its hotkey works via
        parent.addAction regardless, but without this refresh it would silently
        be missing from the rebinding UI)."""
        self.hotkey_edits = {}
        while self._hotkeys_layout.count():
            item = self._hotkeys_layout.takeAt(0)
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
            edit.editingFinished.connect(partial(self._on_hotkey_edited, action_id, edit))
            self.hotkey_edits[action_id] = edit
            row.addWidget(edit)
            self._hotkeys_layout.addLayout(row)

    def _on_hotkey_edited(self, action_id: str, edit: QKeySequenceEdit) -> None:
        """A QKeySequenceEdit's editingFinished handler — persists the new
        binding to gui_state.json["hotkeys"] and re-applies it to the live
        QAction via gui.hotkeys.set_shortcut (an empty sequence clears the
        override, back to the code default)."""
        set_shortcut(action_id, edit.keySequence().toString())
