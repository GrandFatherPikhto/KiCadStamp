# gui/docks/root_metadata.py
"""
RootMetadataDock — edits the root-config-only scalar keys of the project's
ONE root file: layer, schematic_dir/schematic_files/root_sheet, registry_path/
track_registry_path, log_file, operation_log_dir, place_components/
skip_existing_components, via_keepout_clearance_mm/via_search_step_mm/
via_search_max_radius_mm/via_search_n_directions — exactly the field set
config/models.py's Config dataclass carries OUTSIDE cells/points/
thermal_via_arrays/rules/clone_placements (those are entity sections,
already shown as tree leaves — see _common.py's non_includable_keys(), the
same set this dock edits).

root_sheet (added 2026-08-07, Denis: "рутовый шит надо перетащить хотя бы
в настройки проекта") — used to live only in the GUI's fieldstool dock as a
QSettings value, global rather than per-project, so switching projects
silently kept the previous project's root sheet and fieldstool's Pending
changes diff matched nothing (see config/models.py's Config.root_sheet
docstring). Wired to FieldsToolDock.set_root_path via ConfigTreeDock's
root_file_changed, same as rules_dock/placer_dock/etc.'s own set_root_path —
the project's value takes over as soon as a root file with it set is
opened; an empty/absent value leaves fieldstool's current root sheet (manual
Pick, or the old global QSettings one) alone, so existing profiles that
haven't adopted this field yet see no change in behavior.

Requested 2026-08-03 alongside the GUI tree roadmap's contextual-panel
direction ("Экстракт/Пласер/Рут становятся контекстными"). Originally
followed ConfigTreeDock's file_selected like Extract/Placer (whatever file
was currently browsed), but changed 2026-08-05 (Denis: "root-панель должна
читать/хранить/править настройки текущего проекта, независимо от того,
выбран узел root или нет. Он у нас единственный") to follow the dedicated
root_file_changed signal instead — it always targets the project's actual
root file, regardless of which included file is currently selected in the
tree. This also matches the field semantics better: these keys are only
meaningful on a file used as an actual root (an included file with any of
them set is fatal at load time — see config/includes.py), so tracking
"whatever is clicked" could point this dock at a file where writing these
fields would be invalid.

Restructured into tabs the same day ("решил сделать root табами") to cut
dock height, following ExtractDock's 2026-08-04 tabbing for the same
reason — Layer/place_components/skip_existing_components stay above the
tabs as general project settings; Files/Schematics/Via each get their own
tab.

Read-merge-write via _common.merge_write(section=None) — every other key
already in the file (cells:, include:, ...) is left untouched. A field is
only written if its value differs from Config's own dataclass default OR
the key was already present in the file when it was loaded (_present_keys)
— this keeps a freshly-created file free of default-value noise, while
never silently dropping a key the file already declared explicitly (even
one now typed back to its default). Actually clearing a key back to
"absent" is out of scope here — same "reachable by hand-editing the saved
YAML" spirit as PlacerDock's own documented scope limits.

Path fields get a "..." browse button (2026-08-03, Denis: "надо бы кнопки
с диалогом выбора пути/файла") — schematic_dir/operation_log_dir pick an
existing DIRECTORY (QFileDialog.getExistingDirectory); registry_path/
track_registry_path/log_file pick a FILE via a Save-mode dialog (these
files are routinely created BY `apply` on first run, not beforehand — same
"may not exist yet" reasoning ConfigTreeDock's own Add-included-file
dialog already uses). Whatever absolute path the dialog returns is
converted to a path relative to the target file's own directory before
being written into the field — every one of these fields is documented as
"relative to this YAML" (see config/models.py's Config docstring).

schematic_files (a real list, not a single scalar) gets a QListWidget +
Add.../Remove instead of a comma-separated QLineEdit (2026-08-03, Denis:
"диалоговое окошко со списком и кнопки добавить/удалить с диалогом выбора
файла") — Add opens an Open-mode dialog filtered to *.kicad_sch (these
files must already exist, unlike the path fields above), Remove deletes
whatever's currently selected.
"""
import dataclasses
import logging
import os
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QListWidgetItem, QMessageBox, QPushButton,
                              QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.config.models import Config
from kicadstamp.i18n import _

from .. import settings, yaml_io
from ..hotkeys import build_action
from kicadstamp.config_working_set import WORKING_SET
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      display_path, merge_write, show_message)
from .rename import collect_graph_files

logger = logging.getLogger(__name__)

# Recent root files, most-recent-first, capped at this many entries — moved
# here 2026-08-11 from ConfigTreeDock along with Open/New/Recent themselves
# (see this module's own docstring, "Root ownership moved here").
_RECENT_LIMIT = 10

# Stable QAction ids for this dock's hotkeys (2026-08-30, plan
# dock_toolbars_menus_hotkeys Этап 1) — deliberately NOT derived from the
# label text: i18n changes the text, the id must stay stable because it is the
# key under which the user's per-action override lives in
# gui_state.json["hotkeys"] and the id the Settings-tab reassignment UI lists.
ACTION_OPEN = "root_metadata.open"
ACTION_NEW = "root_metadata.new"
ACTION_SAVE = "root_metadata.save"
ACTION_ADD_SCH = "root_metadata.add_schematic_file"
ACTION_REMOVE_SCH = "root_metadata.remove_schematic_file"

# Config's own field defaults — single source of truth, read via
# dataclasses instead of duplicated literals here (default_factory fields,
# e.g. schematic_files, are called; this dock only uses the list ones for
# schematic_files below).
_DEFAULTS: Dict[str, object] = {
    f.name: (f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default)
    for f in dataclasses.fields(Config)
}

# Third element: "dir" -> browse button opens getExistingDirectory,
# "file" -> Save-mode getSaveFileName (these are routinely created BY apply
# on first run, so they may not exist yet — see module docstring), "sch" ->
# Open-mode getOpenFileName filtered to *.kicad_sch (this one must already
# exist, same reasoning as schematic_files' own Add... button below).
# Fourth element: where it lands — "common" -> the general-settings form
# above the tabs (root_sheet, 2026-08-07, Denis: "перенеси на Project" —
# it identifies the project as a whole, same standing as Layer, not
# specific to any one tab), "schematics"/"files" -> that tab.
_TEXT_FIELDS = [
    ("root_sheet", _("Root sheet:"), "sch", "common"),
    ("schematic_dir", _("Schematic dir:"), "dir", "schematics"),
    ("registry_path", _("Registry path:"), "file", "files"),
    ("track_registry_path", _("Track registry path:"), "file", "files"),
    ("log_file", _("Log file:"), "file", "files"),
    ("operation_log_dir", _("Operation log dir:"), "dir", "files"),
]
_BOOL_FIELDS = [
    ("place_components", _("Place components")),
    ("skip_existing_components", _("Skip existing components")),
]
_FLOAT_FIELDS = [
    ("via_keepout_clearance_mm", _("Via keepout clearance (mm):")),
    ("via_search_step_mm", _("Via search step (mm):")),
    ("via_search_max_radius_mm", _("Via search max radius (mm):")),
]
_INT_FIELDS = [
    ("via_search_n_directions", _("Via search directions:")),
]


class RootMetadataDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — used
    to be its own QDockWidget, merged 2026-08-03 (see ExtractDock's module
    docstring note for the same change). Layout builds directly on self
    instead of a wrapped QDockWidget-owned container; everything else is
    unchanged.

    Root ownership moved here 2026-08-11 (was ConfigTreeDock's — see
    gui/docks/config_tree.py's module docstring, now a subscriber instead)
    — Denis: "И 'Открыть корневой файл' и 'Новый корневой файл'... тоже на
    док проект", "И 'Недавние' туда же". This dock now OWNS the project's
    root path (Open/New/Recent + set_root_file(), root_changed replaces the
    old root_file_changed as the source every other dock's set_root_path
    listens to, see gui/dock_hub.py) instead of merely displaying whatever
    ConfigTreeDock told it. Reasoning: this panel is opened DELIBERATELY
    (a tab click, not a per-click tree jump — Denis: "панель открывается
    осознанно... а не листается вслед за деревом"), so it's the natural
    home for "which root am I even working on" as well as its own
    root-only settings below.

    Working file (also new 2026-08-11, Denis: "туда же напрашивается и
    выбор текущего рабочего файла") is a SEPARATE combobox/signal
    (working_file_changed), deliberately NOT sharing state with root_
    changed — visually two distinct rows (Root: toolbar at the top vs.
    "Working file:" at the bottom) so this doesn't repeat the anchor_
    cluster double-duty confusion (2026-08-10 handoff): root_changed always
    means "the project's one root file", working_file_changed always means
    "whichever file Points/Rules/Placer/ThermalVia/Cells currently target",
    and those are allowed to point at the same file by coincidence without
    the code treating that as special. The combobox is a second, direct
    entry point for the exact same thing ConfigTreeDock's own tree clicks
    already broadcast (file_selected) — set_working_file_from_tree() below
    mirrors the tree's current selection into this combobox's display
    without re-broadcasting (the tree already drives every entity dock
    directly, see dock_hub.py), so only an actual combobox pick emits
    working_file_changed.

    Hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1 — this is
    the PILOT dock for the whole mechanism): the Open/New/Save/Add.../Remove
    buttons got a parallel QAction each (build_action in gui/hotkeys.py) —
    the action carries a stable action_id ("root_metadata.*", see the ACTION_*
    constants), a default shortcut and the SAME slot the button already calls
    (the button itself is left untouched this step, per the plan). Shortcuts
    are active app-wide (actions are added on the main window), rebindable in
    the Settings tab (ConfiguratorDock), stored in gui_state.json["hotkeys"].
    The same actions are reused by MainWindow's File menu (Open/New — one
    action, two places).

    File > Close (Этап 1b) is a NEW root-dock operation this panel had no API
    for: close_project() drops the project via set_root_file(None), guarded by
    an unsaved-changes prompt (_confirm_discard_changes) built on a _dirty
    flag set by every field edit (_connect_dirty_signals). Only this dock's
    own edits are tracked so far — a project-wide "any dock dirty" guard is a
    follow-up."""

    # Fired only when the PROJECT'S root file itself changes (Open/New/
    # Recent/restore-on-startup — set_root_file()'s every caller). Replaces
    # ConfigTreeDock's old root_file_changed as the source every other
    # dock's set_root_path listens to (gui/dock_hub.py) — there is exactly
    # ONE root per project.
    root_changed = pyqtSignal(object)
    # Fired only when the user picks a NEW entry in the Working file
    # combobox below (see module docstring on why this is separate from
    # root_changed) — every entity dock's set_target_file listens to this
    # in ADDITION to ConfigTreeDock's own file_selected (dock_hub.py), a
    # second direct entry point for the same thing.
    working_file_changed = pyqtSignal(object)

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._path: Optional[Path] = None
        self._present_keys: set = set()
        # Unsaved-changes flag for the File > Close guard (2026-08-30, plan
        # dock_toolbars_menus_hotkeys Этап 1b) — set by _mark_dirty on any
        # field edit, cleared by set_target_file/_on_save. Wired to the
        # widgets AFTER the initial restore (see _connect_dirty_signals).
        self._dirty: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        open_row = QHBoxLayout()
        # QAction-based hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys
        # Этап 1): each action-bearing button gets a QAction (stable action_id,
        # default shortcut, the same slot the button already calls — the plan
        # explicitly allows "дублировать вызов callback'а", the button itself
        # is left untouched on this step). Actions are parented to / added on
        # the MAIN WINDOW so their shortcuts stay active from any dock tab.
        self.action_open = build_action(
            self._main_window, ACTION_OPEN, _("Open Root file..."), "Ctrl+O", self._on_open_root)
        open_button = QPushButton(_("Open Root file..."))
        open_button.clicked.connect(self._on_open_root)
        open_row.addWidget(open_button)
        self.action_new = build_action(
            self._main_window, ACTION_NEW, _("New Root file..."), "Ctrl+N", self._on_new_root)
        new_button = QPushButton(_("New Root file..."))
        new_button.clicked.connect(self._on_new_root)
        open_row.addWidget(new_button)
        self.recent_combo = QComboBox()
        self.recent_combo.setPlaceholderText(_("Recent..."))
        self.recent_combo.activated.connect(self._on_recent_selected)
        open_row.addWidget(self.recent_combo, 1)
        layout.addLayout(open_row)

        self.target_label = QLabel(_("No project file open"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        # General project settings — apply regardless of tab, shown above
        # them rather than forced into one of Files/Schematics/Via.
        common_form = QFormLayout()

        self.layer_combo = QComboBox()
        self.layer_combo.addItems(["F.Cu", "B.Cu"])
        common_form.addRow(_("Layer:"), self.layer_combo)

        self._bool_checks: Dict[str, QCheckBox] = {}
        for key, label in _BOOL_FIELDS:
            check = QCheckBox(label)
            common_form.addRow("", check)
            self._bool_checks[key] = check

        layout.addLayout(common_form)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        files_page = QWidget()
        files_form = QFormLayout(files_page)
        schematics_page = QWidget()
        schematics_form = QFormLayout(schematics_page)
        via_page = QWidget()
        via_form = QFormLayout(via_page)

        self._text_edits: Dict[str, QLineEdit] = {}
        for key, label, kind, group in _TEXT_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(_("(relative to this YAML)"))
            row = QHBoxLayout()
            row.addWidget(edit)
            browse_button = QPushButton("...")
            browse_button.setMaximumWidth(30)
            if kind == "dir":
                browse_button.clicked.connect(
                    lambda _checked=False, e=edit, l=label: self._browse_dir(e, l))
            elif kind == "sch":
                browse_button.clicked.connect(
                    lambda _checked=False, e=edit, l=label: self._browse_sch(e, l))
            else:
                browse_button.clicked.connect(
                    lambda _checked=False, e=edit, l=label: self._browse_file(e, l))
            row.addWidget(browse_button)
            target_form = (common_form if group == "common" else
                           schematics_form if group == "schematics" else files_form)
            target_form.addRow(label, row)
            self._text_edits[key] = edit

        schematic_files_container = QWidget()
        sf_layout = QVBoxLayout(schematic_files_container)
        sf_layout.setContentsMargins(0, 0, 0, 0)
        self.schematic_files_list = QListWidget()
        self.schematic_files_list.setMaximumHeight(80)
        sf_layout.addWidget(self.schematic_files_list)
        sf_buttons = QHBoxLayout()
        self.action_add_schematic_file = build_action(
            self._main_window, ACTION_ADD_SCH, _("Add..."), "Ctrl+Shift+A", self._add_schematic_file)
        sf_add_button = QPushButton(_("Add..."))
        sf_add_button.clicked.connect(self._add_schematic_file)
        self.action_remove_schematic_file = build_action(
            self._main_window, ACTION_REMOVE_SCH, _("Remove"), "Ctrl+Shift+R", self._remove_schematic_file)
        sf_remove_button = QPushButton(_("Remove"))
        sf_remove_button.clicked.connect(self._remove_schematic_file)
        sf_buttons.addWidget(sf_add_button)
        sf_buttons.addWidget(sf_remove_button)
        sf_layout.addLayout(sf_buttons)
        schematics_form.addRow(_("Schematic files:"), schematic_files_container)

        self._float_edits: Dict[str, QLineEdit] = {}
        for key, label in _FLOAT_FIELDS:
            edit = QLineEdit()
            via_form.addRow(label, edit)
            self._float_edits[key] = edit

        self._int_edits: Dict[str, QLineEdit] = {}
        for key, label in _INT_FIELDS:
            edit = QLineEdit()
            via_form.addRow(label, edit)
            self._int_edits[key] = edit

        self._tabs.addTab(files_page, _("Files"))
        self._tabs.addTab(schematics_page, _("Schematics"))
        self._tabs.addTab(via_page, _("Via"))

        # 2026-09-01 (plan project_save_model): the per-dock Save button is
        # GONE — Ctrl+S now belongs to the GLOBAL File > Save (project.save,
        # gui/main_window.py), which commits the whole working set. This
        # action survives without a default shortcut only so the Settings
        # hotkey list / existing tests can still find it; the fields auto-stage
        # on their commit points instead (see _stage_on_commit).
        self.action_save = build_action(
            self._main_window, ACTION_SAVE, _("Save"), "", self._on_save)

        # Working file (2026-08-11) — deliberately separated (own label,
        # own row, below Save) from the Root toolbar above — see module
        # docstring on why these two must stay visually and semantically
        # distinct.
        layout.addWidget(QLabel(
            _("Working file (Points/Rules/Placer/Thermal via/Cells write here):")))
        self.working_file_combo = QComboBox()
        self.working_file_combo.setPlaceholderText(
            _("pick a file (or browse it in the Config tree)"))
        self.working_file_combo.activated.connect(self._on_working_file_combo_changed)
        layout.addWidget(self.working_file_combo)

        self._reload_recent_combo()
        self._restore_last_root()

        # Unsaved-changes flag wiring LAST (after the initial restore, so
        # loading a project never marks it dirty) — see _connect_dirty_signals.
        self._connect_dirty_signals()

    # ── Root ownership (Open/New/Recent — moved here 2026-08-11 from
    # ConfigTreeDock, see module docstring) ─────────────────────────────

    @property
    def root_path(self) -> Optional[Path]:
        """Current root file, if any — lets a late-connecting listener
        (DockHub._wire(), see gui/dock_hub.py) pick up a value that was
        already set by _restore_last_root() during __init__, i.e. BEFORE
        root_changed had any listeners at all (same reasoning ConfigTreeDock's
        old root_path property had)."""
        return self._path

    def _default_new_name(self) -> str:
        """Default filename for the New Root dialog — the current root's stem
        with the parallel .sexp extension (2026-08-27: .sexp is now a first-
        class root format, so a fresh root defaults to it; the Open/New
        filters accept both YAML and s-expr)."""
        stem = self._path.stem if self._path else "config"
        return f"{stem}.sexp"

    def _on_open_root(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, _("Open Root file"), str(self._path.parent if self._path else ""),
            "Config files (*.sexp *.json)")
        if not chosen:
            return
        self.set_root_file(Path(chosen))

    def _on_new_root(self) -> None:
        """Save-mode dialog (not Open) lets a not-yet-existing filename be
        typed — a brand new root starts out as an empty, perfectly valid
        config (every Config field is optional/defaulted). The empty template
        is format-aware: YAML gets '{}\n', s-expr gets '(kicadstamp-config)'."""
        default_dir = str(self._path.parent if self._path else "")
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _("New Root file"), str(Path(default_dir) / self._default_new_name()),
            "Config files (*.sexp *.json)")
        if not chosen:
            return
        chosen_path = Path(chosen)
        if not chosen_path.exists():
            if chosen_path.suffix.lower() == ".sexp":
                chosen_path.write_text("(kicadstamp-config)\n", encoding="utf-8")
            else:
                chosen_path.write_text("{}\n", encoding="utf-8")
        self.set_root_file(chosen_path)

    def _on_recent_selected(self, index: int) -> None:
        path_str = self.recent_combo.itemData(index)
        if path_str:
            self.set_root_file(Path(path_str))

    def _remember_recent(self, path: Path) -> None:
        recent = [p for p in settings.state.get("recent_root_files", []) if p != str(path)]
        recent.insert(0, str(path))
        settings.state.set("recent_root_files", recent[:_RECENT_LIMIT])
        settings.state.set("last_root_file", str(path))
        self._reload_recent_combo()

    def _reload_recent_combo(self) -> None:
        self.recent_combo.blockSignals(True)
        self.recent_combo.clear()
        for path_str in settings.state.get("recent_root_files", []):
            self.recent_combo.addItem(display_path(Path(path_str)), path_str)
        self.recent_combo.setCurrentIndex(-1)
        self.recent_combo.blockSignals(False)

    def _restore_last_root(self) -> None:
        last = settings.state.get("last_root_file")
        if last and Path(last).is_file():
            self.set_root_file(Path(last))

    def set_root_file(self, path: Optional[Path]) -> None:
        """The project's root file changed (Open/New/Recent/restore-on-
        startup — every caller above). Persists it as most-recent,
        repopulates this panel's OWN fields (via set_target_file, unchanged
        from before the root-ownership move), refreshes the Working-file
        combobox's choices for the new include graph, and broadcasts
        root_changed to every other dock (see gui/dock_hub.py). The
        unsaved-changes guard (Save/Discard/Cancel on a dirty working set)
        runs here, so no root switch can silently drop staged edits."""
        if WORKING_SET.is_dirty() and not self._confirm_discard_changes():
            return
        if path is not None:
            self._remember_recent(path)
        try:
            self.set_target_file(path)
            self.refresh_working_file_choices()
        except Exception:  # noqa: BLE001 — a broken root must never crash the GUI
            logger.exception("GUI: could not load root config %s — root stays "
                             "set; fix the config or pick another via Open/New "
                             "(window stays open)", path)
        self.root_changed.emit(path)

    # ── Working file (2026-08-11, separate from Root — see module
    # docstring) ─────────────────────────────────────────────────────────

    def refresh_working_file_choices(self) -> None:
        """Repopulate the "Working file" combobox's choices from the current
        include: graph (collect_graph_files). Public since 2026-08-15 (plan
        graph_changed_broadcast) so DockHub can call it as the seventh target
        of the graph-changed broadcast — the same graph-derived choices class
        as every entity dock's file combo, and just as stale until this runs.
        Cheap to call repeatedly via the mtime file cache."""
        self.working_file_combo.blockSignals(True)
        self.working_file_combo.clear()
        if self._path is not None:
            for p in collect_graph_files(self._path):
                self.working_file_combo.addItem(display_path(p), str(p))
        self.working_file_combo.setCurrentIndex(-1)
        self.working_file_combo.blockSignals(False)

    def set_working_file_from_tree(self, path: Optional[Path]) -> None:
        """Mirrors the Config tree's own file_selected into this combobox's
        DISPLAY only — does NOT re-emit working_file_changed, since the
        tree already drives every entity dock's target file directly (see
        dock_hub.py); this combobox is a second, direct entry point for the
        exact same thing, not a relay in front of the tree."""
        self.refresh_working_file_choices()
        if path is None:
            return
        idx = self.working_file_combo.findData(str(path))
        if idx < 0:
            return
        self.working_file_combo.blockSignals(True)
        self.working_file_combo.setCurrentIndex(idx)
        self.working_file_combo.blockSignals(False)

    def _on_working_file_combo_changed(self, index: int) -> None:
        path_str = self.working_file_combo.itemData(index)
        if path_str:
            self.working_file_changed.emit(Path(path_str))

    # ── Populating this panel's own fields ───────────────────────────────

    def set_target_file(self, path: Optional[Path]) -> None:
        """Repopulates this panel's own root-only fields from `path`. Called
        internally by set_root_file() above on every root change (Open/New/
        Recent/restore) — kept as a separate public method since tests
        exercise it directly without going through a real QFileDialog.
        Ends with _dirty = False: a (re)loaded file is by definition saved,
        regardless of the field-edit signals _populate fired along the way."""
        self._show_message("")
        self._path = path
        # Clear _dirty BEFORE _populate: repopulation fires the same signals
        # _stage_on_commit listens to, and a stale dirty flag would wrongly
        # stage the freshly-loaded root as a user edit (2026-09-01).
        self._dirty = False
        if path is None:
            self.target_label.setText(_("No project file open"))
            self._present_keys = set()
            self._populate({})
        else:
            self.target_label.setText(display_path(path))
            data = yaml_io.load_data(path)
            self._present_keys = set(data.keys())
            self._populate(data)
        self._dirty = False

    def _populate(self, data: dict) -> None:
        self.layer_combo.setCurrentText(data.get("layer", _DEFAULTS["layer"]))
        for key, _label, _kind, _group in _TEXT_FIELDS:
            self._text_edits[key].setText(data.get(key) or "")
        self.schematic_files_list.clear()
        self.schematic_files_list.addItems(data.get("schematic_files") or [])
        self._make_schematic_items_editable()
        for key, _label in _BOOL_FIELDS:
            self._bool_checks[key].setChecked(bool(data.get(key, _DEFAULTS[key])))
        for key, _label in _FLOAT_FIELDS:
            self._float_edits[key].setText(str(data.get(key, _DEFAULTS[key])))
        for key, _label in _INT_FIELDS:
            self._int_edits[key].setText(str(data.get(key, _DEFAULTS[key])))

    # ── Browse buttons for the path fields ──────────────────────────────

    def _relative_to_target(self, absolute: str) -> str:
        return Path(os.path.relpath(absolute, self._path.parent)).as_posix()

    def _browse_dir(self, edit: QLineEdit, label: str) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        start = (self._path.parent / edit.text()) if edit.text().strip() else self._path.parent
        chosen = QFileDialog.getExistingDirectory(self, label, str(start))
        if not chosen:
            return
        edit.setText(self._relative_to_target(chosen))

    def _browse_sch(self, edit: QLineEdit, label: str) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        start = (self._path.parent / edit.text()) if edit.text().strip() else self._path.parent
        chosen, _filter = QFileDialog.getOpenFileName(
            self, label, str(start), "KiCad Schematic (*.kicad_sch)")
        if not chosen:
            return
        edit.setText(self._relative_to_target(chosen))

    def _browse_file(self, edit: QLineEdit, label: str) -> None:
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        start = (self._path.parent / edit.text()) if edit.text().strip() else self._path.parent
        chosen, _filter = QFileDialog.getSaveFileName(self, label, str(start))
        if not chosen:
            return
        edit.setText(self._relative_to_target(chosen))

    # ── Add/Remove for the schematic_files list ─────────────────────────

    def _add_schematic_file(self) -> None:
        """Multiselect Add... (task 2026-08-30): getOpenFileNames lets the user
        pick several .kicad_sch at once; each is added with the same relative-
        path, no-duplicates rule as the old single-file picker."""
        if self._path is None:
            self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, _("Add schematic file(s)"), str(self._path.parent),
            "KiCad Schematic (*.kicad_sch);;All files (*)")
        for path in chosen:
            rel = self._relative_to_target(path)
            if not self.schematic_files_list.findItems(rel, Qt.MatchFlag.MatchExactly):
                item = QListWidgetItem(rel)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.schematic_files_list.addItem(item)

    def _remove_schematic_file(self) -> None:
        for item in self.schematic_files_list.selectedItems():
            self.schematic_files_list.takeItem(self.schematic_files_list.row(item))

    def _make_schematic_items_editable(self) -> None:
        """Inline-editable list items (task 2026-08-30): the user can type/
        paste a schematic path directly into the list, not only pick one via
        the Add... dialog."""
        for i in range(self.schematic_files_list.count()):
            item = self.schematic_files_list.item(i)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)

    # ── Save ──────────────────────────────────────────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

    def _on_save(self, quiet: bool = False) -> None:
        """Collect the panel's root-settings fields and write them — in the
        staged model this STAGES into the working set (File > Save commits).
        `quiet=True` (the auto-stage commit points) suppresses the success/
        "nothing to save" log chatter that a manual Save should still show."""
        if self._path is None:
            if not quiet:
                self._show_message(_("Open or create a project (root) file first."), _ERROR_STYLE)
            return

        updates: Dict[str, object] = {}

        layer = self.layer_combo.currentText()
        if layer != _DEFAULTS["layer"] or "layer" in self._present_keys:
            updates["layer"] = layer

        for key, _label, _kind, _group in _TEXT_FIELDS:
            text = self._text_edits[key].text().strip()
            if text or key in self._present_keys:
                updates[key] = text or None

        files = [self.schematic_files_list.item(i).text()
                 for i in range(self.schematic_files_list.count())]
        if files or "schematic_files" in self._present_keys:
            updates["schematic_files"] = files

        for key, _label in _BOOL_FIELDS:
            value = self._bool_checks[key].isChecked()
            if value != _DEFAULTS[key] or key in self._present_keys:
                updates[key] = value

        for key, label in _FLOAT_FIELDS:
            text = self._float_edits[key].text().strip()
            try:
                value = float(text) if text else _DEFAULTS[key]
            except ValueError:
                self._show_message(_("{label} {text!r} is not a number.")
                                    .format(label=label, text=text), _ERROR_STYLE)
                return
            if value != _DEFAULTS[key] or key in self._present_keys:
                updates[key] = value

        for key, label in _INT_FIELDS:
            text = self._int_edits[key].text().strip()
            try:
                value = int(text) if text else _DEFAULTS[key]
            except ValueError:
                self._show_message(_("{label} {text!r} is not an integer.")
                                    .format(label=label, text=text), _ERROR_STYLE)
                return
            if value != _DEFAULTS[key] or key in self._present_keys:
                updates[key] = value

        if not updates:
            if not quiet:
                self._show_message(_("Nothing to save — every field is still at its default."), "")
            return

        try:
            merge_write(self._path, updates)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._present_keys |= set(updates)
        # A successful write is by definition saved — clears the File > Close
        # unsaved-changes flag (2026-08-30, plan Этап 1b).
        self._dirty = False
        if not quiet:
            self._show_message(
                _("Saved root metadata to {path}").format(path=display_path(self._path)),
                _SUCCESS_STYLE)

    # ── Unsaved-changes guard + File > Close (2026-08-30, plan
    # dock_toolbars_menus_hotkeys Этап 1b) ───────────────────────────────

    def _mark_dirty(self) -> None:
        """Central dirty setter — every field-edit signal calls this (see
        _connect_dirty_signals), so the File > Close guard can never miss an
        unsaved change. Same pattern as TreesDock._mark_dirty."""
        self._dirty = True

    def _stage_on_commit(self) -> None:
        """Auto-stage (2026-09-01, plan project_save_model): a field edit's
        commit point (blur/Enter for line edits, a combo pick, a checkbox
        toggle, a schematic-list change) stages the current root-settings form
        into the working set. Replaces the old per-dock Save button — only the
        global File > Save commits to disk."""
        if self._path is None or not self._dirty:
            return
        self._on_save(quiet=True)

    def _connect_dirty_signals(self) -> None:
        """Wire every editable field to _mark_dirty (and, on its commit point,
        to _stage_on_commit). Called LAST in __init__ (after
        _restore_last_root), so loading a project never marks it dirty; any
        later set_target_file() repopulation clears _dirty BEFORE _populate
        (see set_target_file) so the repopulation signals never stage."""
        self.layer_combo.currentTextChanged.connect(self._stage_on_commit)
        for edit in self._text_edits.values():
            edit.textChanged.connect(self._mark_dirty)
            edit.editingFinished.connect(self._stage_on_commit)
        for check in self._bool_checks.values():
            check.toggled.connect(self._stage_on_commit)
        for edit in self._float_edits.values():
            edit.textChanged.connect(self._mark_dirty)
            edit.editingFinished.connect(self._stage_on_commit)
        for edit in self._int_edits.values():
            edit.textChanged.connect(self._mark_dirty)
            edit.editingFinished.connect(self._stage_on_commit)
        model = self.schematic_files_list.model()
        model.dataChanged.connect(self._stage_on_commit)
        model.rowsInserted.connect(self._stage_on_commit)
        model.rowsRemoved.connect(self._stage_on_commit)

    def _confirm_discard_changes(self) -> bool:
        """True to proceed (nothing to lose, or the user confirmed) — the
        single guard for the WHOLE project's staged edits (2026-09-01, plan
        project_save_model): WORKING_SET holds every dock's unsaved config
        state, so switching/closing a project asks once about all of it. Save
        commits the working set to disk via the global File > Save; Discard
        drops it (the caller's root switch clears it via root_changed)."""
        if not WORKING_SET.is_dirty():
            return True
        ret = QMessageBox.question(
            self, _("Unsaved changes"),
            _("Save the current project's unsaved changes?"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if ret == QMessageBox.StandardButton.Cancel:
            return False
        if ret == QMessageBox.StandardButton.Save:
            saver = getattr(self._main_window, "_save_project", None)
            if saver is not None:
                saver()
            elif self._path is not None:  # plain window in tests: direct flush
                WORKING_SET.flush(self._path)
            return not WORKING_SET.is_dirty()  # failed save -> keep current state
        return True  # Discard — the caller's root switch clears the working set

    def close_project(self) -> None:
        """File > Close (2026-08-30, plan Этап 1b) — drop the current project
        root via set_root_file(None); the guard (see _confirm_discard_changes)
        now lives inside set_root_file, so Open/New/Recent share it too and no
        path can silently discard staged edits. Every other dock follows
        through root_changed (gui/dock_hub.py)."""
        self.set_root_file(None)
