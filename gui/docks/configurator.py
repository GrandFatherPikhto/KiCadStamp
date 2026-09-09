# gui/docks/configurator.py
"""
ConfiguratorDock — the Settings browser: GUI/app settings for THIS MACHINE,
deliberately NOT project config (the Project settings live in RootMetadataDock,
now hosted in its own non-modal ProjectDialog, see gui/docks/project_dialog.py).
Hosted inside the modal SettingsDialog (gui/docks/settings_dialog.py), launched
from the Tools menu ("Settings...").

Since 2026-09-01 (plan project_settings_dialogs) this is no longer a Detail
dock tab: it is a two-pane browser — a QTreeWidget of categories on the left
(General / Appearance / KiCad / Config tree / Hotkeys / MCP server /
Board overlay) and the matching settings page on the right (QStackedWidget).
And settings are applied EXPLICITLY (OK/Cancel/Apply, modal), not live: every
widget holds the "draft"; ConfiguratorDock.apply() writes the draft to
gui_state.json and fires the side effects (window-flag / tray / highlight /
timeout / hotkeys / overlay geometry); cancel() / reload_from_state() re-seed
the widgets from the persisted state, discarding the draft.

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
from PyQt6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox,
                             QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QKeySequenceEdit, QLabel, QMessageBox, QPushButton,
                             QRadioButton, QSpinBox, QStackedWidget,
                             QStyleFactory, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.i18n import _

from .. import board_overlay, settings
from ..color_schemes import available_color_schemes, load_color_scheme
from ..hotkeys import get_shortcut, registered_hotkeys, set_shortcut
from ..worker import start_long_op
from ._common import DEFAULT_HIGHLIGHT_COLOR

# Sensible bounds for the connection timeout spinbox, in milliseconds.
TIMEOUT_MIN_MS = 1000
TIMEOUT_MAX_MS = 120000


# ── Overlay worker functions (run on the worker thread via start_long_op —
# pure IPC/file work, no widget access; the live board is only touched here,
# never on the UI thread). ────────────────────────────────────────────────

def _fetch_overlay_layers(adapter):
    """[(layer, display name)] of every enabled USER layer of the live board —
    what the Board-overlay page's layer combo is populated from."""
    return board_overlay.overlay_layers(adapter)


def _sweep_overlay_layer(adapter, layer_name: str) -> int:
    """Delete EVERY graphic shape on the configured overlay user layer
    (display name resolved to the live layer enum inside the worker)."""
    return board_overlay.sweep_layer_by_name(adapter, layer_name)


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
        self.overlay_page = self._build_overlay_page()
        # The last-dispatched overlay sweep op (worker.py keeps its own
        # keep-alive too — this is for inspection/idempotency, the same shape
        # as the docks' _active_op).
        self._active_overlay_op = None

        for label, page in (
            (_("General"), self.general_page),
            (_("Appearance"), self.appearance_page),
            (_("KiCad"), self.kicad_page),
            (_("Config tree"), self.config_tree_page),
            (_("Hotkeys"), self.hotkeys_page),
            (_("MCP server"), self.mcp_page),
            (_("Board overlay"), self.overlay_page),
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
        """Qt style (QStyleFactory name) on top, highlight color below."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        # Style — the more fundamental look-and-feel knob, so it sits ABOVE
        # the highlight group (2026-09-03, plan qt_style_setting). The first
        # item "System default" is special (not a style name): it means "never
        # call setStyle()" — today's behaviour. The rest come from
        # QStyleFactory.keys(), i.e. what THIS machine/Qt build offers — not a
        # hardcoded list (the set differs per OS/build, and a name saved on one
        # machine must degrade safely on another).
        style_group = QGroupBox(_("Style"))
        style_layout = QVBoxLayout(style_group)
        self.style_combo = QComboBox()
        self.style_combo.addItem(_("System default"))
        self.style_combo.addItems(sorted(QStyleFactory.keys()))
        style_layout.addWidget(self.style_combo)
        style_hint = QLabel(_("Applies immediately; restart KiCadStamp for a "
                              "fully clean result if you switch styles more "
                              "than once in one session."))
        style_hint.setWordWrap(True)
        style_layout.addWidget(style_hint)

        # Color scheme (2026-09-03, plan color_scheme_setting) — an optional
        # built-in QPalette override, INDEPENDENT from Style (any combination
        # works; the hint below just notes that Fusion follows a custom
        # palette most faithfully). First item "None" is special: it means
        # "no palette override" (the system/theme palette). Scheme names are
        # proper names (like Fusion/Windows for Style), NOT translated — only
        # the surrounding labels are.
        scheme_label = QLabel(_("Color scheme:"))
        style_layout.addWidget(scheme_label)
        self.color_scheme_combo = QComboBox()
        self.color_scheme_combo.addItem(_("None"))
        self.color_scheme_combo.addItems(available_color_schemes())
        style_layout.addWidget(self.color_scheme_combo)
        scheme_hint = QLabel(_("Custom colors are most reliable with the "
                               "Fusion style."))
        scheme_hint.setWordWrap(True)
        style_layout.addWidget(scheme_hint)
        layout.addWidget(style_group)

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

    # ── Board overlay page (Phase D of plan_2026_09_09_cell_anchor_v2_ ─────
    # declarative_and_board_overlay.md)
    #
    # The cell-anchor editor's bbox/marker geometry (layer, line widths,
    # marker radius) is persisted here via settings.state (gui_state.json)
    # with the board_overlay module constants as DEFAULTS — the drawing code
    # reads the same keys, so the dialog and the drawing share one source of
    # truth. The layer combo is filled LIVE from the board
    # (board_overlay.overlay_layers — only USER layers) when KiCad is
    # connected; without a board the remembered value is shown and nothing
    # crashes. There is NO colour setting (§0.6): graphics take their layer's
    # colour, so the only knob is "which layer" — the hint says so and
    # recommends a dedicated user layer.

    def _build_overlay_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        geometry_group = QGroupBox(_("Overlay geometry"))
        geometry_form = QFormLayout(geometry_group)
        self.overlay_layer_combo = QComboBox()
        self.overlay_layer_combo.setToolTip(
            _("The KiCad user layer the anchor bbox and marker are drawn on. "
              "Filled LIVE from the open board; without KiCad the remembered "
              "layer is shown."))
        geometry_form.addRow(_("Overlay layer:"), self.overlay_layer_combo)
        self.overlay_bbox_stroke_spin = QDoubleSpinBox()
        self.overlay_bbox_stroke_spin.setRange(0.01, 10.0)
        self.overlay_bbox_stroke_spin.setDecimals(2)
        self.overlay_bbox_stroke_spin.setSingleStep(0.05)
        self.overlay_bbox_stroke_spin.setSuffix(" mm")
        geometry_form.addRow(_("Bbox line width:"), self.overlay_bbox_stroke_spin)
        self.overlay_marker_radius_spin = QDoubleSpinBox()
        self.overlay_marker_radius_spin.setRange(0.05, 50.0)
        self.overlay_marker_radius_spin.setDecimals(2)
        self.overlay_marker_radius_spin.setSingleStep(0.1)
        self.overlay_marker_radius_spin.setSuffix(" mm")
        geometry_form.addRow(_("Marker radius:"), self.overlay_marker_radius_spin)
        self.overlay_marker_stroke_spin = QDoubleSpinBox()
        self.overlay_marker_stroke_spin.setRange(0.01, 10.0)
        self.overlay_marker_stroke_spin.setDecimals(2)
        self.overlay_marker_stroke_spin.setSingleStep(0.05)
        self.overlay_marker_stroke_spin.setSuffix(" mm")
        geometry_form.addRow(_("Marker line width:"), self.overlay_marker_stroke_spin)
        geometry_hint = QLabel(
            _("There is no colour setting — overlay graphics take their "
              "layer's colour, managed in KiCad. Tip: dedicate a separate "
              "user layer (e.g. User.KiCadStamp) to the overlay — its colour "
              "and visibility are then configured once in KiCad, and "
              "whole-layer cleanup is safe."))
        geometry_hint.setWordWrap(True)
        geometry_form.addRow(geometry_hint)
        layout.addWidget(geometry_group)

        cleanup_group = QGroupBox(_("Cleanup"))
        cleanup_layout = QVBoxLayout(cleanup_group)
        self.overlay_sweep_button = QPushButton(_("Remove entire overlay layer"))
        self.overlay_sweep_button.setToolTip(
            _("Deletes EVERY graphic shape on the overlay layer chosen above — "
              "including shapes you drew in KiCad yourself. Only that user "
              "layer is affected."))
        self.overlay_sweep_button.clicked.connect(self._on_sweep_overlay)
        cleanup_layout.addWidget(self.overlay_sweep_button)
        cleanup_hint = QLabel(
            _("The guaranteed cleanup against lost overlay shapes: it sweeps "
              "the whole chosen layer rather than individual marker/bbox "
              "shapes."))
        cleanup_hint.setWordWrap(True)
        cleanup_layout.addWidget(cleanup_hint)
        layout.addWidget(cleanup_group)
        layout.addStretch(1)
        return page

    def _overlay_adapter(self):
        """The live board adapter the overlay page's IPC goes through, or
        None when not connected."""
        connection = getattr(self, "_connection", None)
        board = getattr(connection, "board", None) if connection is not None \
            else None
        if board is None:
            return None
        return getattr(board, "adapter", None)

    def _seed_overlay_layer_combo(self) -> None:
        """Offline-safe seed of the overlay-layer combo: the remembered layer
        (a single item). The live refresh (refresh_overlay_layers) replaces
        the items with the board's user layers once KiCad is connected."""
        layer = board_overlay.overlay_layer_name()
        self.overlay_layer_combo.blockSignals(True)
        self.overlay_layer_combo.clear()
        self.overlay_layer_combo.addItem(layer)
        self.overlay_layer_combo.blockSignals(False)

    def _apply_overlay_layers(self, layers) -> None:
        """Fill the overlay-layer combo from [(layer, display name)] — the
        LIVE board's enabled USER layers (never a hardcoded list, §0.7).
        Keeps the remembered layer when it is on the board; otherwise falls
        back to the default, then the first offered layer. An empty live set
        falls back to the remembered value."""
        names = [display for _layer, display in (layers or [])]
        remembered = board_overlay.overlay_layer_name()
        if not names:
            self._seed_overlay_layer_combo()
            return
        if remembered in names:
            target = remembered
        elif board_overlay.OVERLAY_DEFAULT_LAYER in names:
            target = board_overlay.OVERLAY_DEFAULT_LAYER
        else:
            target = names[0]
        self.overlay_layer_combo.blockSignals(True)
        self.overlay_layer_combo.clear()
        self.overlay_layer_combo.addItems(names)
        self.overlay_layer_combo.setCurrentText(target)
        self.overlay_layer_combo.blockSignals(False)

    def refresh_overlay_layers(self) -> None:
        """(Re)populate the overlay-layer combo from the LIVE board — called
        when the Settings dialog opens (SettingsDialog.open_modal). Without a
        board connection it just shows the remembered value (never crashes,
        never an empty combo)."""
        self._seed_overlay_layer_combo()
        adapter = self._overlay_adapter()
        if adapter is None:
            return
        self._active_overlay_op = start_long_op(
            self._connection, [], _fetch_overlay_layers,
            self._apply_overlay_layers, lambda _msg: None, adapter)

    def _on_sweep_overlay(self) -> None:
        """'Remove entire overlay layer' — the guaranteed whole-layer cleanup.
        Confirms first (it deletes EVERY graphic on the chosen layer), then
        sweeps it on the worker thread. The sweep_layer user-layer guard is
        NOT weakened — a layer that is not an enabled user layer is a fatal
        and nothing is deleted."""
        adapter = self._overlay_adapter()
        if adapter is None:
            QMessageBox.warning(
                self, _("Board overlay"),
                _("No live board — sweeping the overlay layer needs a KiCad "
                  "connection."))
            return
        layer_name = self.overlay_layer_combo.currentText().strip()
        if not layer_name:
            QMessageBox.warning(self, _("Board overlay"),
                                _("Pick an overlay layer first."))
            return
        reply = QMessageBox.question(
            self, _("Remove entire overlay layer"),
            _("This deletes EVERY graphic shape on the layer “{layer}” — "
              "including shapes you added in KiCad yourself. Other layers are "
              "untouched. Continue?").format(layer=layer_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._active_overlay_op = start_long_op(
            self._connection, [self.overlay_sweep_button],
            _sweep_overlay_layer,
            self._on_overlay_sweep_done, self._on_overlay_sweep_failed,
            adapter, layer_name)

    def _on_overlay_sweep_done(self, count) -> None:
        # The whole layer is gone, so the persisted by-uuid map is stale.
        board_overlay.clear_persisted_overlay()
        layer_name = self.overlay_layer_combo.currentText().strip() \
            or board_overlay.OVERLAY_DEFAULT_LAYER
        QMessageBox.information(
            self, _("Board overlay"),
            _("Removed {count} shape(s) from the layer “{layer}”.")
            .format(count=count, layer=layer_name))

    def _on_overlay_sweep_failed(self, message: str) -> None:
        QMessageBox.warning(
            self, _("Board overlay"),
            _("Sweeping the overlay layer failed: {message}")
            .format(message=message))

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
        saved_style = settings.state.get("qt_style")
        if isinstance(saved_style, str) and saved_style in QStyleFactory.keys():
            self.style_combo.setCurrentText(saved_style)
        else:
            # Quiet fallback to "System default" — a stored non-None value
            # that names no style on THIS machine (e.g. gui_state.json synced
            # from another OS) must not break the dialog.
            self.style_combo.setCurrentIndex(0)
        saved_scheme = settings.state.get("color_scheme")
        if isinstance(saved_scheme, str) and saved_scheme in available_color_schemes():
            self.color_scheme_combo.setCurrentText(saved_scheme)
        else:
            # Quiet fallback to "None" — a stored value that names no built-in
            # scheme (e.g. gui_state.json synced from another machine/version
            # where the set of schemes differs) must not break the dialog.
            self.color_scheme_combo.setCurrentIndex(0)
        self.timeout_spin.setValue(settings.state.get("kicad_timeout_ms",
                                                      DEFAULT_TIMEOUT_MS))
        self.rename_confirmation_checkbox.setChecked(
            bool(settings.state.get("rename_confirmation_enabled", True)))
        self.raw_write_checkbox.setChecked(
            bool(settings.state.get("mcp_allow_raw_write", False)))
        # Board overlay (Phase D): re-seed the geometry spinboxes from the
        # persisted values (board_overlay accessors fall back to the module
        # constants = the defaults) and show the remembered layer offline —
        # the LIVE layer list is (re)filled by refresh_overlay_layers() when
        # the Settings dialog opens with a board.
        self.overlay_bbox_stroke_spin.setValue(board_overlay.overlay_bbox_stroke_mm())
        self.overlay_marker_radius_spin.setValue(board_overlay.overlay_marker_radius_mm())
        self.overlay_marker_stroke_spin.setValue(board_overlay.overlay_marker_stroke_mm())
        self._seed_overlay_layer_combo()
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

        # Qt style (2026-09-03, plan qt_style_setting): index 0 is the special
        # "System default" entry (its text is translated, so compare by index,
        # not text). System default -> clear the key and DO NOT call setStyle():
        # Qt has no guaranteed "restore original default style" after a live
        # switch in this session, so the running style is left untouched (see
        # design §2.4). A concrete style -> persist + apply live via
        # QApplication.instance().setStyle() (the same immediate-apply contract
        # every other setting on this dialog honours).
        if self.style_combo.currentIndex() == 0:
            settings.state.set("qt_style", None)
        else:
            chosen_style = self.style_combo.currentText()
            settings.state.set("qt_style", chosen_style)
            app = QApplication.instance()
            if app is not None:
                app.setStyle(chosen_style)

        # Color scheme (2026-09-03, plan color_scheme_setting): index 0 is the
        # special "None" entry (its text is translated, so compare by index,
        # not text). Unlike qt_style, a CLEAN rollback to the pre-override
        # palette is achievable here: QApplication.setPalette() accepts any
        # QPalette, and gui_main.main() snapshots the pristine palette as the
        # app's "original_palette" dynamic property before any override —
        # restoring exactly that object undoes every earlier palette override
        # this session (fatal-safe fallback to style().standardPalette()).
        if self.color_scheme_combo.currentIndex() == 0:  # "None"
            settings.state.set("color_scheme", None)
            app = QApplication.instance()
            if app is not None:
                original = app.property("original_palette")
                app.setPalette(original if original is not None
                               else app.style().standardPalette())
        else:
            chosen_scheme = self.color_scheme_combo.currentText()
            settings.state.set("color_scheme", chosen_scheme)
            app = QApplication.instance()
            if app is not None:
                palette = load_color_scheme(chosen_scheme)
                if palette is not None:
                    app.setPalette(palette)

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

        # Board overlay (Phase D): persist the overlay geometry. The layer is
        # stored as its KiCad display name; the combo always carries a value
        # (the live board's user layers, or the remembered layer when offline),
        # so the stored value is never empty.
        settings.state.set(board_overlay.OVERLAY_LAYER_KEY,
                           self.overlay_layer_combo.currentText().strip()
                           or board_overlay.OVERLAY_DEFAULT_LAYER)
        settings.state.set(board_overlay.OVERLAY_BBOX_STROKE_KEY,
                           self.overlay_bbox_stroke_spin.value())
        settings.state.set(board_overlay.OVERLAY_MARKER_RADIUS_KEY,
                           self.overlay_marker_radius_spin.value())
        settings.state.set(board_overlay.OVERLAY_MARKER_STROKE_KEY,
                           self.overlay_marker_stroke_spin.value())

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
