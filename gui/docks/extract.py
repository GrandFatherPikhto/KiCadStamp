# gui/docks/extract.py
"""
ExtractDock — build a Cell template from whatever's currently selected on
the board and write it into whichever file is currently selected in the
Config tree (gui/docks/config_tree.py). Wraps
kicadstamp.template_extraction.extract_template_from_selection() plus
kicadstamp_cli.py's cmd_extract merge-into-existing-file behaviour — NOT
kicadstamp.author.dump_template(), which always overwrites the whole file
(fine for a script regenerating its own dedicated file, wrong here: the
currently selected file is very likely already home to other cells).

Fed by the same selection-watch timer as the tree/bulk-edit docks (see
MainWindow._poll_board_selection) — but unlike those, which only need
FootprintInstance refs, extraction needs the FULL raw selection (vias and
tracks too — a thermal via array or a decoupling-cap+via pattern has both),
so MainWindow passes this dock the raw get_selected_items() result
alongside the Selected-wrapped footprint list the other docks use.

Optionally also writes an extract_profiles: entry (the --profile mechanism
in kicadstamp_cli.py's extract command) — a replayable recipe (name/output/
params) so the same extraction can be re-run from the CLI later without
retyping the alias mapping by hand. This CAN now go in the same file as the
cell output (cells_file:/cell_files: were folded into include: 2026-08-02 —
see handoff_2026_08_02_cells_include_unification.md — cells: and
extract_profiles: are both just include:-mergeable dict sections of the
same "structured root config" shape now), but a dedicated Extractor file is
still the default two-role split below — nothing requires merging them.

Net aliases feed params, NOT net_template_map, directly — the alias field
next to a net IS its params key (e.g. net '+2V5' aliased 'PWR_IN' becomes
params={'PWR_IN': '+2V5'}). extract_template_from_selection()'s own
auto-inference then derives net_template_map from params on its own
(net_resolution.py: any literal net equal to a param VALUE gets mapped to
that param's {key} automatically) — passing net_template_map directly
without matching params was an earlier bug here: parametrize_net() always
round-trip-checks pattern.format(**params) against the literal, and with
params=={} that check fails for every single alias (found live 2026-08-01,
"net '{X}' has a placeholder with no parameter" on every extract attempt
that used an alias).

"Rule net" checkbox next to each net row (2026-08-05, Denis: "давай
сделаем явный чекбокс [для null]. Это правильная фича. Она замыкает
использование rules") — a THIRD, mutually exclusive option alongside
"leave literal" (blank alias, unchecked) and "alias it" ({ALIAS}, for
ClonePlacement reuse): checking it writes via.net/track.net: null for that
net instead of the literal or an alias. At apply time a ManualSpoke-placed
cell's via/track with net: null inherits the enclosing Rule's own net
(kicadstamp/geometry/spoke_layout.py: `via.net or rule_net`) — the SAME
cell can then be reused, unmodified, across several Rules on different
power rails, which {ALIAS}/net_template CANNOT do here (ManualSpoke has no
params: field to resolve a template against at all — see
extract_template_from_selection()'s own docstring on rule_nets). Checking
the box clears+disables that row's alias edit (and vice versa); collected
into rule_nets, persisted as extract_profiles: rule_nets: [...] alongside
params/net_template_role.

Both the cell-output file and the extract_profiles file follow the SAME
currently-selected file in the Config tree (2026-08-03 — used to be two
independent FilePickerDock role slots, Cells/Extractor; collapsed into one
since browsing to a file already implies "write here" for both), pushed
here via set_target_file()/set_profile_file(). This dock used to
have its own separate "Change profile file..." QFileDialog button for the
profile file alone, which meant two different ways to pick a file for two
closely related purposes — reported as confusing live 2026-08-01.

There's a third role, Placer (set_placer_file()) — the root config a
placement run would actually be pointed at. After a successful extract,
if a Placer file is assigned, this dock also makes sure that file's own
include: list includes both the Cells file and the Extractor file
(deduped by resolved path, never duplicated, every other key in the file
left alone — see add_list_entry(), same read-merge-write shape as
merge_write() but for a list section instead of a dict one). Requested
live 2026-08-01 ("это всё собралось вместе, и placer — это точка сборки"):
extracting a cell is meant to leave the Placer file ready to use it, not
just leave the cell sitting in a file nothing points at yet. Skipped when
Cells/Extractor already IS the Placer file (self-reference is pointless —
the file already effectively "has itself").

Net template role (a component whose pads touch TWO aliased nets — a
ferrite bead/inductor bridging two rails, e.g. a pi-filter's feedback
element): extract_template_from_selection() can't auto-decide which of
the two aliased nets becomes that role's net_template (see
template_extraction.py, "N nets from --net-template on pads"), so it's
left EMPTY there unless net_template_role={role: literal} says which one
— a mechanism that already existed backend/CLI-side (--net-template-role)
but had no GUI surface until now. The "Net template role:" section only
ever shows a role once 2+ of ITS pads' nets have a non-empty alias typed
next to them (checked live: this project's own board has exactly this
shape — a PI_FILTER_FB ferrite bead with '-2V5' on one pad and
'-2V5_DIRTY' on the other, both meant to be templated) — reactive to
alias typing, not a fixed list. Extraction is blocked until every such
row has a pick; there is no safe default to guess.

The "Existing cells:"/"Existing profiles:" lists read straight from
whatever those two files currently contain (top-level keys / the
extract_profiles: section's keys) and let a click reuse an existing name
outright. They also drive a quiet auto-fill: when the current selection is
a single Cluster and a slugified form of it matches an existing key, that
key is filled into the Cell name / Profile key fields (only if the field
is still empty — never stomps something the user already typed) and
highlighted in its list. Matching is a plain slug comparison (e.g. Cluster
'PWR/DAC0' -> 'pwr_dac0'), not a stored Cluster->name mapping — nothing in
the file formats records that association today, so this is a heuristic
that helps when it hits.

Cell name specifically ALSO falls back to the Cluster's own slug when
nothing existing matches (Profile key does not — it already defaults to
the cell name at extraction time, see _on_extract()). Requested live
2026-08-01: "если есть имя кластера, зачем придумывать что-то" — a first-
time extraction has no existing key to match yet, but the Cluster name is
already right there, so Cell name is never left blank purely because
nothing's been extracted from it before.
"""
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kipy.board_types import Via
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
                              QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QListWidget, QPushButton, QTableWidget, QTableWidgetItem,
                              QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.explore import Selected
from kicadstamp.extract_writer import run_extract_to_file
from kicadstamp.i18n import _
from kicadstamp.template_extraction import extract_template_from_selection

from .. import yaml_io
from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE, display_path, set_combo_items, show_message)
from .rename import collect_graph_files

logger = logging.getLogger(__name__)


class ExtractDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) —
    used to be its own QDockWidget, merged 2026-08-03 (Denis: "Панели:
    Экстракт, Пласер, Рут — становятся контекстными (общая область
    формы)"). Building the layout directly on self instead of a wrapped
    QDockWidget-owned container is the only change from that; every widget
    attribute/method below is unchanged."""

    # Fired after a successful Extract that wrote a cell (and/or a profile)
    # to disk — ConfigTreeDock listens to refresh (see gui/dock_hub.py),
    # same pattern as PlacerDock.saved/ThermalViaDock.saved. FIXED
    # (2026-08-04, Denis live: "создал новый экстракт и cell, список в
    # конфиге не обновляется" — ExtractDock never had this signal at all,
    # unlike the other two write-capable docks, so a newly extracted cell
    # never showed up in the Config tree until an unrelated action
    # happened to trigger ConfigTreeDock.refresh()).
    saved = pyqtSignal()

    def __init__(self, main_window, connection=None):
        super().__init__(main_window)
        self._main_window = main_window
        # Injected BoardConnection — falls back to the owning window's when
        # not passed explicitly (keeps direct-construction callers, e.g.
        # tests that mutate main_window.connection.board, working).
        self._connection = connection if connection is not None else main_window.connection
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._raw_items: List[Any] = []
        self._selected_footprints: List[Selected] = []
        self._root_path: Optional[Path] = None
        self._target_path: Optional[Path] = None
        self._profile_path: Optional[Path] = None
        self._placer_path: Optional[Path] = None
        self._net_alias_edits: Dict[str, QLineEdit] = {}
        self._rule_net_checkboxes: Dict[str, QCheckBox] = {}
        self._net_template_role_edits: Dict[str, QComboBox] = {}
        self._last_autofill_key: Optional[Tuple[frozenset, Optional[Path], Optional[Path]]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.selection_label = QLabel(_("Nothing selected"))
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        self.cluster_warning_label = QLabel("")
        self.cluster_warning_label.setWordWrap(True)
        self.cluster_warning_label.setStyleSheet(_WARN_STYLE)
        layout.addWidget(self.cluster_warning_label)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("cell name (key under cells:)"))
        form.addRow(_("Cell name:"), self.name_edit)
        layout.addLayout(form)

        # Cell/Profile file pickers as independent dropdowns (2026-08-06,
        # Denis: "имя файла, куда пишем extract и cell... тоже, выпадашками
        # (комбобоксами со списком доступных файлов)" — until now, both
        # ALWAYS followed the SAME ConfigTreeDock click (file_selected fires
        # set_target_file/set_profile_file together, see dock_hub.py and
        # this dock's own module docstring's "collapsed into one" note from
        # 2026-08-03) — asked live whether they could even be different:
        # yes, the backend already supports it (run_extract_to_file takes
        # them independently, extract_profiles: entries have their own
        # output:), the GUI just never surfaced it. These combos genuinely
        # un-couple them: set_target_file()/set_profile_file() stay the
        # SAME shared entry points ConfigTreeDock's click still calls (so
        # that path keeps working unchanged), but each combo can now ALSO
        # independently override its own path. Closed-set, non-editable —
        # same "an editable combo whose value must match something real is
        # a freeze risk" lesson as CellDock's anchor_role_combo/PlacerDock's
        # cell_combo — populated from every file reachable via include:
        # from the current project root (collect_graph_files, same helper
        # gui/docks/rename.py's own renaming already relies on).
        target_file_row = QHBoxLayout()
        target_file_row.addWidget(QLabel(_("Cell file:")))
        self.target_file_combo = QComboBox()
        self.target_file_combo.setPlaceholderText(_("pick a file (or browse it in the Config tree)"))
        self.target_file_combo.currentIndexChanged.connect(self._on_target_file_combo_changed)
        target_file_row.addWidget(self.target_file_combo, 1)
        layout.addLayout(target_file_row)

        # Tabbed instead of stacked (2026-08-04, Denis: "плашка отказывается
        # переразмериваться" — a QVBoxLayout's minimum height is the SUM of
        # every section's own minimum, Origin+Net aliases+Net template
        # role+Existing all stacked at once forced the window taller than
        # it could ever shrink to. A QTabWidget only sizes for the CURRENT
        # page, not the sum of all of them, so the dock can actually be
        # resized down now. Every widget attribute below keeps its old
        # name — only which layout it's added to changed, nothing that
        # reads/writes them elsewhere needed touching.
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        origin_page = QWidget()
        origin_page_layout = QVBoxLayout(origin_page)
        origin_form = QFormLayout()
        self.origin_mode_combo = QComboBox()
        self.origin_mode_combo.addItems(
            [_("Bounding box (default)"), _("Component role"), _("Via net")])
        self.origin_mode_combo.currentIndexChanged.connect(self._on_origin_mode_changed)
        origin_form.addRow(_("Origin:"), self.origin_mode_combo)
        origin_page_layout.addLayout(origin_form)

        self._origin_role_row = QWidget()
        role_row = QHBoxLayout(self._origin_role_row)
        role_row.setContentsMargins(0, 0, 0, 0)
        self.origin_role_combo = QComboBox()
        self.origin_role_combo.setEditable(True)
        role_row.addWidget(QLabel(_("Role:")))
        role_row.addWidget(self.origin_role_combo, 1)
        self.origin_pad_edit = QLineEdit()
        self.origin_pad_edit.setPlaceholderText(_("pad (optional)"))
        role_row.addWidget(QLabel(_("Pad:")))
        role_row.addWidget(self.origin_pad_edit)
        origin_page_layout.addWidget(self._origin_role_row)
        self._origin_role_row.setVisible(False)

        self._origin_via_row = QWidget()
        via_row = QHBoxLayout(self._origin_via_row)
        via_row.setContentsMargins(0, 0, 0, 0)
        self.origin_via_net_combo = QComboBox()
        self.origin_via_net_combo.setEditable(True)
        via_row.addWidget(QLabel(_("Net:")))
        via_row.addWidget(self.origin_via_net_combo, 1)
        origin_page_layout.addWidget(self._origin_via_row)
        self._origin_via_row.setVisible(False)
        origin_page_layout.addStretch(1)
        self._tabs.addTab(origin_page, _("Origin"))

        aliases_page = QWidget()
        aliases_page_layout = QVBoxLayout(aliases_page)
        aliases_page_layout.addWidget(QLabel(_("Net aliases (blank = keep literal):")))
        # A real QTableWidget (2026-08-06, Denis: "у нас в экстракторе
        # net-aliases, не таблица" — was a hand-rolled QGridLayout+
        # QScrollArea, one Label/QLineEdit/QCheckBox row per net). Rows
        # themselves are NOT user-added/removed here, unlike PlacerDock's
        # _KeyValueTableEditor (gui/docks/placer.py) — the set of nets is
        # dictated entirely by what's on the current selection's pads
        # (_rebuild_net_aliases, called every ~400ms selection-watch tick);
        # only the Alias/Rule-net CELLS within each row are user-editable,
        # via setCellWidget — the table just replaces the grid as the
        # layout mechanism, the data flow (_net_alias_edits/
        # _rule_net_checkboxes keyed by net) is unchanged.
        self.nets_table = QTableWidget(0, 3)
        self.nets_table.setHorizontalHeaderLabels([_("Net"), _("Alias"), _("Rule net (null)")])
        self.nets_table.verticalHeader().setVisible(False)
        self.nets_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.nets_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.nets_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        # NoEditTriggers, not because nothing here is editable (Alias/Rule
        # net ARE, via their own cell widgets) but because the Net column
        # itself is a plain read-only QTableWidgetItem — this only stops Qt
        # from opening an inline text editor on top of it.
        self.nets_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        aliases_page_layout.addWidget(self.nets_table, 1)
        self._tabs.addTab(aliases_page, _("Net aliases"))

        self._role_net_section = QWidget()
        role_net_section_layout = QVBoxLayout(self._role_net_section)
        role_net_section_layout.addWidget(
            QLabel(_("Net template role (bridging component — pick which aliased net is the template):")))
        self._role_net_layout = QGridLayout()
        self._role_net_layout.setContentsMargins(0, 0, 0, 0)
        role_net_section_layout.addLayout(self._role_net_layout)
        role_net_section_layout.addStretch(1)
        self._role_net_tab_index = self._tabs.addTab(self._role_net_section, _("Net template role"))
        # Hidden until _update_cluster_warning() finds an ambiguous bridging
        # component — a whole hidden TAB now (setTabVisible), replacing the
        # old setVisible(False) on the section widget itself (see the sole
        # other call site of this, near _role_net_tab_index below).
        self._tabs.setTabVisible(self._role_net_tab_index, False)

        existing_page = QWidget()
        existing_row = QHBoxLayout(existing_page)
        cells_col = QVBoxLayout()
        cells_col.addWidget(QLabel(_("Cells:")))
        self.cells_list = QListWidget()
        self.cells_list.itemClicked.connect(self._on_cell_item_clicked)
        cells_col.addWidget(self.cells_list)
        existing_row.addLayout(cells_col)

        profiles_col = QVBoxLayout()
        profiles_col.addWidget(QLabel(_("Profiles:")))
        self.profiles_list = QListWidget()
        self.profiles_list.itemClicked.connect(self._on_profile_item_clicked)
        profiles_col.addWidget(self.profiles_list)
        existing_row.addLayout(profiles_col)
        self._tabs.addTab(existing_page, _("Existing"))

        self.save_profile_checkbox = QCheckBox(_("Also save as extract_profile"))
        layout.addWidget(self.save_profile_checkbox)

        profile_form = QFormLayout()
        self.profile_key_edit = QLineEdit()
        self.profile_key_edit.setPlaceholderText(_("profile key (defaults to cell name)"))
        # Tooltip (2026-08-06, Denis: "я постоянно забываю" what this field
        # is even for) — the key this extraction gets saved under in
        # extract_profiles: (separate from Cell name — the two CAN differ,
        # this project's own data has exactly that: profile key
        # 'n2v5_adj_pi_filter', cell name '2v5_adj_pi_filter', see
        # _find_profile_key_for_cell's own docstring). Only matters when
        # "Also save as extract_profile" is checked — a replayable recipe
        # (params/net_template_role/rule_nets/origin) that a later
        # `kicadstamp_cli.py extract --profile <key>` or a click in this
        # dock's own Existing -> Profiles list can reuse without retyping
        # the alias mapping by hand.
        profile_key_tooltip = _(
            "Key this extraction is saved under in extract_profiles: (only used if 'Also save as "
            "extract_profile' is checked) — separate from Cell name, the two can differ. Saves a "
            "replayable recipe (net aliases, origin, ...) you can reuse later via "
            "'kicadstamp_cli.py extract --profile <key>' or by clicking it in Existing -> Profiles, "
            "instead of retyping the alias mapping by hand. Defaults to the Cell name if left blank.")
        self.profile_key_edit.setToolTip(profile_key_tooltip)
        profile_key_label = QLabel(_("Profile key:"))
        profile_key_label.setToolTip(profile_key_tooltip)
        profile_form.addRow(profile_key_label, self.profile_key_edit)
        layout.addLayout(profile_form)

        profile_file_row = QHBoxLayout()
        profile_file_row.addWidget(QLabel(_("Profile file:")))
        self.profile_file_combo = QComboBox()
        self.profile_file_combo.setPlaceholderText(_("pick a file (or browse it in the Config tree)"))
        self.profile_file_combo.currentIndexChanged.connect(self._on_profile_file_combo_changed)
        profile_file_row.addWidget(self.profile_file_combo, 1)
        layout.addLayout(profile_file_row)

        self.placer_target_label = QLabel(_("No placer file picked (pick one in the Config tree, optional)"))
        self.placer_target_label.setWordWrap(True)
        layout.addWidget(self.placer_target_label)

        self.extract_button = QPushButton(_("Extract to file"))
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self._on_extract)
        layout.addWidget(self.extract_button)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

    def set_board_selection(self, raw_items: List[Any], selected_footprints: List[Selected]) -> None:
        """Called every selection-watch tick — see module docstring for why
        this needs the raw mixed list, not just the Selected-footprint one
        the tree/bulk-edit docks use."""
        self._raw_items = raw_items
        self._selected_footprints = selected_footprints
        self._update_selection_label()
        self._update_cluster_warning()
        self._rebuild_net_aliases()
        self._update_origin_choices()
        self._autofill_from_cluster()
        self._update_button_state()

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._origin_role_row.setVisible(mode == 1)
        self._origin_via_row.setVisible(mode == 2)

    def _update_origin_choices(self) -> None:
        """Populates the Role/Via-net combos from what's actually in the
        current selection — picking an origin from outside the selection
        makes no sense (extract_template_from_selection fatals on it
        anyway: 'role not found in selection' / 'no such via in selection'),
        so there's no point offering it."""
        roles = sorted({s.role for s in self._selected_footprints if s.role})
        set_combo_items(self.origin_role_combo, roles)

        via_nets = sorted({item.net.name for item in self._raw_items
                            if isinstance(item, Via) and item.net and item.net.name})
        set_combo_items(self.origin_via_net_combo, via_nets)

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — populates
        target_file_combo/profile_file_combo from every file reachable via
        include: from the project root (same collect_graph_files()
        gui/docks/rename.py's own renaming uses), same pattern as
        RulesDock/PlacerDock/CellDock's own set_root_path (2026-08-06,
        closing the "Cell/Profile file also as a dropdown" gap — see
        target_file_combo's own comment)."""
        self._root_path = path
        self._refresh_file_choices()

    def _refresh_file_choices(self) -> None:
        files = collect_graph_files(self._root_path) if self._root_path is not None else []
        items = sorted(((display_path(p), p) for p in files), key=lambda t: t[0])
        for combo in (self.target_file_combo, self.profile_file_combo):
            combo.blockSignals(True)
            combo.clear()
            for text, path in items:
                combo.addItem(text, path)
            combo.blockSignals(False)
        self._set_file_combo_selection(self.target_file_combo, self._target_path)
        self._set_file_combo_selection(self.profile_file_combo, self._profile_path)

    @staticmethod
    def _set_file_combo_selection(combo: QComboBox, path: Optional[Path]) -> None:
        """Reflects `path` into `combo`'s current selection without
        re-firing currentIndexChanged (blockSignals) — used both after a
        full repopulate (_refresh_file_choices) and whenever
        set_target_file/set_profile_file is called directly (e.g. from
        ConfigTreeDock's click, or before the root/include graph is even
        known yet). Adds `path` as an extra item if the combo's current
        list doesn't have it (root not set yet, or a file outside the
        include graph) — same "still show/select it anyway" fallback
        PlacerDock's cell_combo uses for the same reason."""
        combo.blockSignals(True)
        if path is None:
            combo.setCurrentIndex(-1)
        else:
            idx = combo.findData(path)
            if idx < 0:
                combo.addItem(display_path(path), path)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _on_target_file_combo_changed(self, index: int) -> None:
        path = self.target_file_combo.itemData(index)
        if path is not None:
            self.set_target_file(path)

    def _on_profile_file_combo_changed(self, index: int) -> None:
        path = self.profile_file_combo.itemData(index)
        if path is not None:
            self.set_profile_file(path)

    def set_target_file(self, path: Optional[Path]) -> None:
        """Shared entry point for picking the Cell output file — called both
        by ConfigTreeDock's file_selected signal (2026-08-03 — replaced
        FilePickerDock's Cells-role slot; see gui/docks/config_tree.py's
        module docstring) AND by target_file_combo's own selection
        (2026-08-06 — see its comment). Either path keeps the other in
        sync, same shared-setter pattern as PlacerDock's
        set_selected_cell()."""
        self._target_path = path
        self._set_file_combo_selection(self.target_file_combo, path)
        self._refresh_existing_lists()
        self._update_button_state()

    def set_profile_file(self, path: Optional[Path]) -> None:
        """Shared entry point for picking the Extract-profile output file —
        called both by ConfigTreeDock's file_selected signal (2026-08-03 —
        replaced FilePickerDock's Extractor-role slot; Cell/Profile files
        used to always follow the SAME currently-browsed file, collapsed
        into one back then) AND by profile_file_combo's own selection
        (2026-08-06 — genuinely un-couples them again, see
        target_file_combo's own comment for why)."""
        self._profile_path = path
        self._set_file_combo_selection(self.profile_file_combo, path)
        self._refresh_existing_lists()

    def set_placer_file(self, path: Optional[Path]) -> None:
        """Called whenever the Config tree's current file changes (wired to
        ConfigTreeDock's file_selected signal, 2026-08-03 — replaced
        FilePickerDock's Placer-role slot). Optional — extraction works the
        same without one, it just skips the include: wiring described in
        the module docstring. Not a dropdown (unlike Cell/Profile above) —
        kept scoped to ConfigTreeDock's click, see the 2026-08-06 request
        this followed."""
        self._placer_path = path
        self.placer_target_label.setText(
            _("Placer file: {path}").format(path=path) if path is not None
            else _("No placer file picked (pick one in the Config tree, optional)"))

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")

    _load_data = staticmethod(yaml_io.load_data)
    _existing_keys = staticmethod(yaml_io.existing_keys)

    def _refresh_existing_lists(self) -> None:
        self.cells_list.clear()
        self.cells_list.addItems(sorted(self._existing_keys(self._target_path, section="cells")))
        self.profiles_list.clear()
        self.profiles_list.addItems(sorted(self._existing_keys(self._profile_path, section="extract_profiles")))
        self._last_autofill_key = None  # force _autofill_from_cluster to re-check against the new content

    @staticmethod
    def _select_list_item(list_widget: QListWidget, text: Optional[str]) -> None:
        list_widget.clearSelection()
        if text is None:
            return
        items = list_widget.findItems(text, Qt.MatchFlag.MatchExactly)
        if items:
            list_widget.setCurrentItem(items[0])

    def _autofill_from_cluster(self) -> None:
        """If the selection is a single Cluster and a slugified form of it
        (or its last '/'-segment) matches an existing Cells/Extractor key,
        fill that key into Cell name / Profile key — but only into a field
        that's still empty, so this never overwrites something already
        typed. Also highlights the match in its list either way, so a hit
        is visible even when the field was left untouched. No match ->
        silently does nothing (see module docstring).

        A matched profile's own params: (alias -> net literal, the same
        shape _on_extract() writes) are pulled into the net-alias fields
        too — reported live 2026-08-01 as missing ("алиасы сетей не
        подтянул"): reusing a profile's name without its aliases just
        means retyping them by hand every time. Tries an exact net-literal
        match first (the profile is being re-run on the SAME nets); a
        param whose literal isn't present in the current selection falls
        back to filling the next still-empty row, in declared order — the
        common case this covers is reusing a profile for an analogous
        Cluster on a different rail (found live 2026-08-01: a '-2V5'/
        '-2V5_DIRTY' selection matching a profile whose params were
        recorded against '+2V5'/'+2V5_DIRTY' — no literal in common, but
        the two aliases still belong in the same two rows). It's a
        heuristic, not a guarantee — same empty-field-only rule as the
        name fields, so a bad guess is just as easy to overtype as a
        blank field would have been."""
        clusters = frozenset(s.cluster for s in self._selected_footprints if s.cluster)
        key = (clusters, self._target_path, self._profile_path)
        if key == self._last_autofill_key:
            return
        self._last_autofill_key = key

        matched_cell = matched_profile = None
        if len(clusters) == 1:
            cluster = next(iter(clusters))
            candidates = [self._slugify(cluster)]
            if "/" in cluster:
                candidates.append(self._slugify(cluster.rsplit("/", 1)[-1]))

            cell_keys = self._existing_keys(self._target_path, section="cells")
            matched_cell = next((c for c in candidates if c in cell_keys), None)
            if not self.name_edit.text().strip():
                # An existing key wins if there is one (it reflects
                # whatever naming was actually chosen last time); otherwise
                # the Cluster's own slug is still a perfectly good default
                # — no reason to leave the field blank just because nothing
                # was extracted under that name yet (2026-08-01: "если есть
                # имя кластера, зачем придумывать что-то").
                self.name_edit.setText(matched_cell or candidates[0])

            profile_keys = self._existing_keys(self._profile_path, section="extract_profiles")
            matched_profile = next((c for c in candidates if c in profile_keys), None)
            if matched_profile:
                if not self.profile_key_edit.text().strip():
                    self.profile_key_edit.setText(matched_profile)
                self._apply_profile_entry(matched_profile)

        self._select_list_item(self.cells_list, matched_cell)
        self._select_list_item(self.profiles_list, matched_profile)

    def _apply_profile_entry(self, profile_key: str) -> None:
        """Pulls one extract_profiles entry's params/net_template_role/
        rule_nets/origin_by_* into the alias/role/Origin fields — shared by
        the cluster auto-match above and by explicitly clicking a profile in
        the "Existing" list (see __init__): a manual pick deserves exactly
        the same pull an automatic match gets, not just the name (reported
        live 2026-08-01: names were "picked up" — via clicking, since this
        board's real Cluster names don't slugify to match its cell/profile
        keys at all, so the auto-match path above never actually fires here
        — but the alias fields, and then the Origin combo too, stayed
        untouched, because back then this pull only lived inside the
        auto-match branch and didn't cover Origin at all)."""
        profile_entry = self._load_data(self._profile_path).get("extract_profiles", {}).get(profile_key, {})
        if not profile_entry:
            return

        unmatched_aliases = []
        for alias, net_literal in (profile_entry.get("params") or {}).items():
            edit = self._net_alias_edits.get(net_literal)
            if edit is None:
                unmatched_aliases.append(alias)
            elif not edit.text().strip():
                edit.setText(alias)
        if unmatched_aliases:
            empty_edits = [e for e in self._net_alias_edits.values() if not e.text().strip()]
            for alias, edit in zip(unmatched_aliases, empty_edits):
                edit.setText(alias)

        # rule_nets — direct literal match only (no rail-swap heuristic like
        # aliases get above: a rule net has no alias name to bridge through,
        # it's just "this net = null", so it either matches today's net
        # exactly or it doesn't).
        for net_literal in (profile_entry.get("rule_nets") or []):
            checkbox = self._rule_net_checkboxes.get(net_literal)
            if checkbox is not None and not checkbox.isChecked():
                checkbox.setChecked(True)

        # net_template_role is role -> literal, and role IS stable across a
        # rail swap (unlike the literal itself) — so rather than reusing
        # the old literal directly, look up which ALIAS it had back then
        # and find today's candidate net carrying that same alias (just
        # filled in above).
        old_params = profile_entry.get("params") or {}
        alias_for_old_literal = {v: k for k, v in old_params.items()}
        for role, old_literal in (profile_entry.get("net_template_role") or {}).items():
            combo = self._net_template_role_edits.get(role)
            wanted_alias = alias_for_old_literal.get(old_literal)
            if combo is None or combo.currentText().strip() or not wanted_alias:
                continue
            for i in range(combo.count()):
                candidate_net = combo.itemText(i)
                edit = self._net_alias_edits.get(candidate_net)
                if edit is not None and edit.text().strip() == wanted_alias:
                    combo.setCurrentText(candidate_net)
                    break

        # Origin — only applied while still at the untouched default (index
        # 0, bbox): same empty-field-only spirit as the alias/role pulls
        # above, so this never yanks the Origin selection out from under
        # something the user (or an earlier pull) already set.
        if self.origin_mode_combo.currentIndex() == 0:
            origin_role = profile_entry.get("origin_by_component_role")
            origin_via = profile_entry.get("origin_by_via_net")
            if origin_role:
                self.origin_mode_combo.setCurrentIndex(1)
                self.origin_role_combo.setCurrentText(origin_role)
                origin_pad = profile_entry.get("origin_by_component_pad")
                if origin_pad:
                    self.origin_pad_edit.setText(str(origin_pad))
            elif origin_via:
                self.origin_mode_combo.setCurrentIndex(2)
                self.origin_via_net_combo.setCurrentText(origin_via)

    def _find_profile_key_for_cell(self, cell_name: str) -> Optional[str]:
        """A profile entry's own key and the cell name it writes can differ
        (profile_key defaults to the cell name but can be overridden — see
        _on_extract()'s entry['name'] — this project's own real data has
        exactly this: profile key 'n2v5_adj_pi_filter', cell name
        '2v5_adj_pi_filter'). Used when the Cells list is clicked, so that
        click can find and pull the matching profile's aliases too, not
        just the ones the Profiles list itself was clicked for."""
        profiles = self._load_data(self._profile_path).get("extract_profiles", {}) or {}
        return next((key for key, entry in profiles.items()
                     if (entry.get("name") or key) == cell_name), None)

    def _on_cell_item_clicked(self, item) -> None:
        cell_name = item.text()
        self.name_edit.setText(cell_name)
        profile_key = self._find_profile_key_for_cell(cell_name)
        if profile_key is not None:
            if not self.profile_key_edit.text().strip():
                self.profile_key_edit.setText(profile_key)
            self._apply_profile_entry(profile_key)

    def _on_profile_item_clicked(self, item) -> None:
        self.pick_profile(item.text())

    def pick_profile(self, profile_key: str) -> None:
        """Public entry point for picking an extract_profiles: entry —
        same effect as clicking it in this dock's own "Existing Profiles"
        list, exposed so ConfigTreeDock's Extract-profiles category (2026-
        08-03, GUI tree roadmap Этап 1) can route into the same behavior
        without duplicating it."""
        self.profile_key_edit.setText(profile_key)
        self._apply_profile_entry(profile_key)

    def _update_net_template_role_rows(self) -> None:
        """A role needs an explicit net_template_role pick exactly when 2+
        of ITS pads' nets currently have a non-empty alias next to them
        (see module docstring) — recomputed live as alias text changes
        (connected to each net-alias edit's textChanged), not just on
        selection changes, since typing an alias is exactly what turns a
        previously-unambiguous role into an ambiguous one."""
        ambiguous: Dict[str, List[str]] = {}
        for s in self._selected_footprints:
            if not s.role:
                continue
            distinct_nets = set(s.nets.values())
            aliased = sorted(n for n in distinct_nets
                              if self._net_alias_edits.get(n) and self._net_alias_edits[n].text().strip())
            if len(aliased) >= 2:
                ambiguous[s.role] = aliased

        if set(ambiguous) == set(self._net_template_role_edits):
            return

        previous = {role: combo.currentText() for role, combo in self._net_template_role_edits.items()}
        while self._role_net_layout.count():
            item = self._role_net_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._net_template_role_edits = {}
        for row, (role, nets) in enumerate(sorted(ambiguous.items())):
            self._role_net_layout.addWidget(QLabel(role), row, 0)
            combo = QComboBox()
            combo.addItem("")
            combo.addItems(nets)
            combo.setCurrentText(previous.get(role, ""))
            self._role_net_layout.addWidget(combo, row, 1)
            self._net_template_role_edits[role] = combo

        self._tabs.setTabVisible(self._role_net_tab_index, bool(ambiguous))

    def _update_selection_label(self) -> None:
        if not self._raw_items:
            self.selection_label.setText(_("Nothing selected"))
            return
        fp_count = len(self._selected_footprints)
        other_count = len(self._raw_items) - fp_count
        if other_count:
            self.selection_label.setText(
                _("{fp} component(s), {other} via/track(s) selected")
                .format(fp=fp_count, other=other_count))
        else:
            self.selection_label.setText(_("{fp} component(s) selected").format(fp=fp_count))

    def _update_cluster_warning(self) -> None:
        clusters = {s.cluster for s in self._selected_footprints}
        if len(clusters) > 1:
            shown = ", ".join(repr(c) for c in sorted(clusters, key=lambda c: c or ""))
            self.cluster_warning_label.setText(
                _("Selection spans multiple Clusters: {clusters}").format(clusters=shown))
        else:
            self.cluster_warning_label.setText("")

    def _rebuild_net_aliases(self) -> None:
        """One row per distinct net found on the selected components' pads.
        Preserves whatever the user already typed/checked for a net that's
        still present — the selection-watch tick fires every ~400ms, so
        without this, in-progress typing would be wiped just like the
        tree/bulk-edit docks had to guard against."""
        nets = sorted({net for s in self._selected_footprints for net in s.nets.values()})
        previous_alias = {net: edit.text() for net, edit in self._net_alias_edits.items()}
        previous_rule_net = {net: cb.isChecked() for net, cb in self._rule_net_checkboxes.items()}
        if set(nets) == set(previous_alias):
            return

        self.nets_table.setRowCount(0)  # also deletes every row's cell widgets

        self._net_alias_edits = {}
        self._rule_net_checkboxes = {}
        for row, net in enumerate(nets):
            self.nets_table.insertRow(row)
            net_item = QTableWidgetItem(net)
            net_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # read-only, not editable/selectable
            self.nets_table.setItem(row, 0, net_item)

            edit = QLineEdit()
            edit.setPlaceholderText(_("alias, e.g. PWR_IN"))
            edit.setText(previous_alias.get(net, ""))
            edit.textChanged.connect(self._update_net_template_role_rows)
            self.nets_table.setCellWidget(row, 1, edit)
            self._net_alias_edits[net] = edit

            checkbox = QCheckBox(_("Rule net (null)"))
            checkbox.setToolTip(
                _("Write this via/track net as null instead of a literal — at apply time a "
                  "ManualSpoke-placed cell inherits the enclosing Rule's own net for it, so the "
                  "cell can be reused across Rules on different nets."))
            checkbox.setChecked(previous_rule_net.get(net, False))
            checkbox.toggled.connect(lambda checked, e=edit: self._on_rule_net_toggled(e, checked))
            self.nets_table.setCellWidget(row, 2, checkbox)
            self._rule_net_checkboxes[net] = checkbox
            self._on_rule_net_toggled(edit, checkbox.isChecked())
        self._update_net_template_role_rows()

    @staticmethod
    def _on_rule_net_toggled(edit: QLineEdit, checked: bool) -> None:
        """Rule net and alias are mutually exclusive for one net (see module
        docstring) — checking the box clears+disables the alias edit rather
        than just leaving a stale, now-ignored alias typed next to it."""
        if checked:
            edit.setText("")
        edit.setDisabled(checked)

    def _update_button_state(self) -> None:
        self.extract_button.setEnabled(bool(self._raw_items) and self._target_path is not None)

    def _show_message(self, text: str, style: str = "") -> None:
        """Sets the inline status label AND mirrors it into the Log dock
        (see gui/docks/log_panel.py) at the matching level, so error/
        warning messages survive after the label itself gets overwritten
        by the next action — requested live 2026-08-01 ("для списка
        ошибок сделать внизу отдельное окошко")."""
        show_message(self.message_label, text, style, logger)

    def _on_extract(self) -> None:
        """Extract button handler — form collection (validation + widget
        reads) runs on the UI thread; the board IPC + file writes run on a
        worker thread (see gui/worker.py), so the GUI stays responsive while
        the shared socket stays exclusively ours (connection.long_op_active
        pauses the polling timers for the duration)."""
        self._show_message("")
        payload = self._collect_extract_inputs()
        if payload is None:
            return
        self._start_extract_op(payload)

    def _collect_extract_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: read every widget + run every validation that can
        reject the request up front. Returns a plain-data payload for the
        worker (no widget references), or None after showing the error."""
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Cell name is required."), _ERROR_STYLE)
            return None
        if not self._raw_items or self._target_path is None:
            return None
        save_profile = self.save_profile_checkbox.isChecked()
        if save_profile and self._profile_path is None:
            self._show_message(
                _("'Also save as extract_profile' is checked, but no profile file is picked."),
                _ERROR_STYLE)
            return None

        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None

        rule_nets = {net for net, cb in self._rule_net_checkboxes.items() if cb.isChecked()}

        params: Dict[str, str] = {}
        for net_literal, edit in self._net_alias_edits.items():
            alias = edit.text().strip()
            if not alias:
                continue
            if alias in params:
                self._show_message(
                    _("Alias {alias!r} used for both {a!r} and {b!r} — each alias needs a "
                      "distinct net.").format(alias=alias, a=params[alias], b=net_literal),
                    _ERROR_STYLE)
                return None
            params[alias] = net_literal

        origin_kwargs: Dict[str, str] = {}
        origin_mode = self.origin_mode_combo.currentIndex()
        if origin_mode == 1:  # component role (+ optional pad)
            role = self.origin_role_combo.currentText().strip()
            if not role:
                self._show_message(_("Origin: pick a component role."), _ERROR_STYLE)
                return None
            origin_kwargs["origin_component_role"] = role
            pad = self.origin_pad_edit.text().strip()
            if pad:
                origin_kwargs["origin_component_pad"] = pad
        elif origin_mode == 2:  # via net
            net = self.origin_via_net_combo.currentText().strip()
            if not net:
                self._show_message(_("Origin: pick a via net."), _ERROR_STYLE)
                return None
            origin_kwargs["origin_via_net"] = net

        net_template_role: Dict[str, str] = {}
        for role, combo in self._net_template_role_edits.items():
            literal = combo.currentText().strip()
            if not literal:
                self._show_message(
                    _("Net template role: role {role!r} bridges 2+ aliased nets — pick which one "
                      "is the template.").format(role=role),
                    _ERROR_STYLE)
                return None
            net_template_role[role] = literal

        return {
            "name": name,
            "raw_items": self._raw_items,
            "target_path": self._target_path,
            "save_profile": save_profile,
            "profile_key": self.profile_key_edit.text().strip() or name,
            "profile_path": self._profile_path,
            "placer_path": self._placer_path,
            "params": params,
            "rule_nets": rule_nets,
            "origin_kwargs": origin_kwargs,
            "net_template_role": net_template_role,
            "board": board,
        }

    def _run_extract(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC + file writes only — never touches a
        widget. Thin wrapper over kicadstamp.extract_writer.
        run_extract_to_file() — the transformative/profile/placer-wiring
        logic moved to core (Phase 2 of the gui god-file decomposition);
        extract_template_from_selection is passed in at call time so tests
        can monkeypatch this module's copy. Returns {"messages": [...],
        "annotations": [...], "template_dict": {...}} on success, or
        {"error": str} for the expected failure modes (an unexpected
        exception is caught by _LongOpWorker and reported through the failed
        signal instead)."""
        return run_extract_to_file(
            payload["board"].adapter,
            name=payload["name"],
            params=payload["params"],
            items=payload["raw_items"],
            net_template_role=payload["net_template_role"],
            rule_nets=payload["rule_nets"],
            origin_kwargs=payload["origin_kwargs"],
            target_path=payload["target_path"],
            save_profile=payload["save_profile"],
            profile_key=payload["profile_key"],
            profile_path=payload["profile_path"],
            placer_path=payload["placer_path"],
            extract_fn=extract_template_from_selection)

    @staticmethod
    def _summarize_net_from_role(template_dict: Dict[str, Any]) -> Optional[str]:
        """One-line summary of which extracted via/track nets got written as
        net_from_role (instead of a literal/parametrised net) — the ONLY
        surface where this is visible to the user, since
        extract_template_from_selection()'s auto-suggestion (template_
        extraction.py's _suggest_net_from_role, plan step 4) otherwise
        happens silently inside the extractor. template_dict is the raw
        {name: {vias/components/tracks/...}} the worker got back (see
        _run_extract's docstring) — its only key is the just-extracted
        cell's name, so no separate name parameter is needed here. Guards
        against a non-dict cell value (some older test fakes return a
        minimal/unrealistic shape, e.g. {"ref": "C1"}, when they only care
        about proving something unrelated, like adapter forwarding) —
        nothing to summarize there, not an error."""
        if not template_dict:
            return None
        cell = next(iter(template_dict.values()))
        if not isinstance(cell, dict):
            return None
        items = list(cell.get("vias") or []) + list(cell.get("tracks") or [])
        roles = [entry["net_from_role"] + (f"/pad:{entry['net_from_role_pad']}"
                                           if entry.get("net_from_role_pad") else "")
                 for entry in items if entry.get("net_from_role")]
        if not roles:
            return None
        return _("{count} via/track net(s) auto-classified by role: {roles}").format(
            count=len(roles), roles=", ".join(roles))

    def _finish_extract(self, result: Dict[str, Any]) -> None:
        """UI thread: reflect the worker's result into the message label and
        refresh the existing-lists widgets."""
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        messages = result["messages"]
        annotations = result["annotations"]
        net_from_role_summary = self._summarize_net_from_role(result.get("template_dict") or {})
        if net_from_role_summary:
            messages.append(net_from_role_summary)
        if annotations:
            messages.append(_("{count} field(s) could not be determined automatically: {details}")
                             .format(count=len(annotations),
                                     details="; ".join(f"{role}/{field}: {hint}"
                                                        for role, field, hint in annotations)))
            self._show_message("; ".join(messages), _WARN_STYLE)
        else:
            self._show_message("; ".join(messages), _SUCCESS_STYLE)
        self._refresh_existing_lists()
        self.saved.emit()

    def _start_extract_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._connection, (self.extract_button,),
            self._run_extract, self._finish_extract, self._on_extract_failed, payload)

    def _on_extract_failed(self, message: str) -> None:
        self._show_message(_("Extract failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_extract(self) -> None:
        """Synchronous composition of collect + run + finish — the same
        behaviour the async button path would produce, kept for tests and
        any caller that must not return until the extract is complete."""
        payload = self._collect_extract_inputs()
        if payload is None:
            return
        result = self._run_extract(payload)
        self._finish_extract(result)

