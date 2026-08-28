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

Net template role (a component whose pads touch TWO nets — a ferrite
bead/inductor bridging two rails, e.g. a pi-filter's feedback element):
extract_template_from_selection() can't auto-decide which of the two nets
becomes that role's net_template (see template_extraction.py, "N nets
from --net-template on pads"), so it's left EMPTY there unless
net_template_role={role: literal} says which one — a mechanism that
already existed backend/CLI-side (--net-template-role) but had no GUI
surface until now. The "Net template role:" section shows a role once 2+
of ITS pads' DISTINCT nets THEMSELVES classify by role (lemma2/pad, see
_classify_selection_nets) — driven by the preview classification, NOT by
alias typing (2026-08-13, plan net_alias_optional_gui step 5: a
classified net's Alias edit is disabled, so the old "2+ aliases typed"
trigger could no longer fire at all). Checked live on this project's own
board: a PI_FILTER_FB ferrite bead with '-2V5' on one pad and
'-2V5_DIRTY' on the other, both resolving by role. Extraction is blocked
until every such row has a pick; there is no safe default to guess.

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

Cluster filter (2026-08-12): an area-select in KiCad is purely geometric —
if new, not-yet-extracted components (Denis's real case: resistors tagged
Cluster=FPGA_PERIPH, placed close to the FPGA on purpose) sit right next to
an already-placed Rule/Pi-filter cluster, the selection sweeps up both. See
cluster_filter_checkbox's own comment in __init__ and _filtered_selection()
for the mechanics — every selection-derived read in this dock (label,
warning, net aliases, Origin choices, cluster auto-fill, the extract payload
itself) goes through _filtered_selection() so the filter is never
half-applied.

One checkbox does both halves of "extract strictly this Cluster" — talked
through live with Denis, who explicitly rejected a second toggle/an
"exclude these other Clusters" list as unneeded complexity once the target
Cluster is picked explicitly: (1) footprints of any OTHER Cluster are
dropped (as above); (2) any selected Via/Track whose live UUID is already
recorded in the Placer file's registry.json/tracks.registry.json — i.e. it
was created by an EARLIER apply run for some OTHER already-existing
clone_placement/Rule — is dropped too (_registry_uuids()). Net-name
matching was considered and rejected first: a shared net (GND, present in
both Clusters) can't be told apart that way, whereas the registry's UUID
is an exact, unambiguous "this belongs to placement X" fact. Silently a
no-op without a Placer file assigned (nothing to check the registry
against) — footprint filtering alone still applies.
"""
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kicadstamp.domain.board import Footprint, Track, Via
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
                              QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QListWidget, QPushButton, QTableWidget, QTableWidgetItem,
                              QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.config import clone_placement_effective_name, load_config
from kicadstamp.exceptions import ValidationError
from kicadstamp.explore import Selected
from kicadstamp.extract_writer import run_extract_to_file
from kicadstamp.i18n import _
from kicadstamp.utils.units import MM
from kicadstamp.net_resolution import RULE_NETS
from kicadstamp.template_extraction import (
    extract_template_from_selection,
    # Private helpers, imported by alias: the preview classification in this
    # dock (see _classify_selection_nets) reuses the extractor's own role->net
    # machinery verbatim instead of reimplementing classify_net in the GUI
    # (plan 2026-08-13, steps 1-2). Direct private cross-module imports have
    # precedents in the project (e.g. tests/test_author.py's _prune_defaults);
    # keeping the core module's names untouched avoids touching the extract
    # path for what is a GUI-only change.
    _selection_role_nets as selection_role_nets,
    _suggest_net_from_role as suggest_net_from_role,
)

from .. import yaml_io
from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE, set_combo_items, show_message)
from .rename import collect_section_entries

logger = logging.getLogger(__name__)


@dataclass
class SubPlacementCandidate:
    """One existing top-level ClonePlacement ENTIRELY covered by the current
    selection — a candidate for the new cell's clone_placements: section
    (2026-08-25, handoff composite_cell_autodetect_and_cycle_guard, Задание 1).
    Instead of copying its geometry flat into the new cell, Extract can
    reference the placement by name, so the two stay in sync on every apply.

    clone — the ClonePlacement; items — its live board items (Footprint/Via/
    Track), resolved via resolve_clone_board_items (cached per Placer path);
    item_keys — the items' stable identity keys (see ExtractDock._item_key),
    reused both for the subset check and for excluding the items from the
    new cell's flat lists at extract time."""
    clone: Any
    items: List[Any]
    item_keys: frozenset


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
        # net -> (category, role) preview classification of the current
        # selection's distinct nets (see _classify_selection_nets) — computed
        # every selection-watch tick, drives both the Auto-role column and the
        # net-template-role ambiguity trigger (plan 2026-08-13).
        self._net_auto_roles: Dict[str, Tuple[str, Optional[str]]] = {}
        self._net_template_role_edits: Dict[str, QComboBox] = {}
        self._last_autofill_key: Optional[Tuple[frozenset, Optional[Path], Optional[Path]]] = None
        # (via_uuids, track_uuids) already in the Placer file's registry —
        # see _registry_uuids()'s own docstring for why this is cached
        # instead of re-reading the Placer file's whole include: graph on
        # every ~400ms selection-watch tick. Reset (to None, meaning "stale,
        # recompute on next need") only by set_placer_file().
        self._registry_uuids_cache: Optional[Tuple[set, set]] = None
        # Re-extract target (2026-08-25, handoff clone_item_resolver_select_and_
        # reextract): the picked existing profile/Cell plus the placement the
        # user chose in the re-extract combo — see _refresh_re_extract_placements.
        self._re_extract_cell_name: Optional[str] = None
        self._re_extract_profile_key: Optional[str] = None
        self._re_extract_profile_entry: Dict[str, Any] = {}
        # Sub-placements (2026-08-25, handoff composite_cell_autodetect_and_
        # cycle_guard, Задание 1): existing top-level ClonePlacements fully
        # covered by the current selection — see _update_sub_placement_candidates.
        self._sub_placement_candidates: List[SubPlacementCandidate] = []
        # placement effective name -> checkbox (preserved across preview ticks).
        self._sub_placement_checkboxes: Dict[str, QCheckBox] = {}
        # [(clone, resolved_board_items)] per Placer path — CACHED like
        # _registry_uuids_cache (resolve_clone_board_items is live-board work,
        # far too heavy for the ~400ms selection-watch tick); reset to None by
        # set_root_path().
        self._sub_placements_cache: Optional[List[Tuple[Any, List[Any]]]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.selection_label = QLabel(_("Nothing selected"))
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        self.cluster_warning_label = QLabel("")
        self.cluster_warning_label.setWordWrap(True)
        self.cluster_warning_label.setStyleSheet(_WARN_STYLE)
        layout.addWidget(self.cluster_warning_label)

        # Cluster filter (2026-08-12, Denis: a box-select around new
        # components placed close to an existing structure — e.g. resistors
        # tagged Cluster=FPGA_PERIPH sitting right next to an already-placed
        # Rule/Pi-filter cluster — sweeps up the neighbours' components too.
        # Only shown once _update_cluster_filter_choices() finds 2+ distinct
        # Clusters in the raw selection (same trigger as cluster_warning_label
        # above); picking one and checking the box restricts extraction to
        # that Cluster: footprints of any other Cluster are excluded (as
        # before), AND — 2026-08-12, second pass, see module docstring's
        # "One checkbox does both halves" note — any Via/Track whose UUID is
        # already recorded in the Placer file's registry.json (i.e. it
        # belongs to some OTHER already-existing clone_placement/Rule) is
        # excluded too, via _registry_uuids(). Tracks that only touched an
        # excluded component/via are then ALSO dropped for free by the
        # existing _filter_tracks_within_selection (its ends no longer match
        # anything in the reduced selection).
        cluster_filter_row = QHBoxLayout()
        self.cluster_filter_checkbox = QCheckBox(_("Keep only one Cluster:"))
        self.cluster_filter_checkbox.setToolTip(
            _("Selection spans multiple Clusters (e.g. it swept up nearby "
              "Rules/other cells too) — check this and pick a Cluster on the "
              "right to extract only ITS components. Tracks that only "
              "touched an excluded component are dropped automatically. "
              "Vias/tracks already recorded in the Placer file's registry "
              "(i.e. belonging to some OTHER already-existing placement) are "
              "excluded too — requires a Placer file to be picked; without "
              "one, only the Cluster/footprint part of the filter applies."))
        self.cluster_filter_checkbox.toggled.connect(self._on_cluster_filter_changed)
        cluster_filter_row.addWidget(self.cluster_filter_checkbox)
        self.cluster_filter_combo = QComboBox()
        self.cluster_filter_combo.currentIndexChanged.connect(self._on_cluster_filter_changed)
        cluster_filter_row.addWidget(self.cluster_filter_combo, 1)
        layout.addLayout(cluster_filter_row)
        self.cluster_filter_checkbox.setVisible(False)
        self.cluster_filter_combo.setVisible(False)

        # Raw-selection bypass (2026-08-24, handoff_2026_08_24_extract_raw_
        # selection_flag): by default extract keeps only tracks/vias whose
        # connected copper reaches a pad of a kept footprint (the connectivity
        # filter in template_selection.py). Checking this takes the selection
        # EXACTLY as selected — no pad-connectivity check at all. Opt-in only,
        # the filter remains the default; useful for via/copper arrays with no
        # anchor component in the selection, or a quick draft capture.
        self.raw_selection_checkbox = QCheckBox(_("Take selection as-is (skip connectivity filter)"))
        self.raw_selection_checkbox.setToolTip(
            _("By default extract keeps only tracks/vias whose connected copper reaches a pad of "
              "a kept footprint (the connectivity filter). Check this to take the selection "
              "exactly as selected — every selected track/via goes into the cell with no "
              "pad-connectivity check (useful for via arrays / copper with no anchor component "
              "in the selection, or a quick draft capture)."))
        layout.addWidget(self.raw_selection_checkbox)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("cell name (key under cells:)"))
        form.addRow(_("Cell name:"), self.name_edit)
        layout.addLayout(form)

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
        #
        # 4th column "Auto-role" (2026-08-13, plan net_alias_optional_gui):
        # read-only per-net preview of what the extractor's net_from_role
        # auto-suggestion would do with this net ("role: <ROLE>" for a net
        # that already resolves by role — lemma2 or pad — empty for a
        # fallback net that genuinely needs an alias/literal). Lets the
        # table stop looking like every net needs a typed alias, and the
        # Alias edit of an auto-role net is disabled (see _rebuild_net_aliases).
        self.nets_table = QTableWidget(0, 4)
        self.nets_table.setHorizontalHeaderLabels(
            [_("Net"), _("Alias"), _("Rule net (null)"), _("Auto-role")])
        self.nets_table.verticalHeader().setVisible(False)
        self.nets_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.nets_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.nets_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.nets_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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

        # Sub-placements (2026-08-25, handoff composite_cell_autodetect_and_
        # cycle_guard, Задание 1): existing top-level ClonePlacements whose
        # ENTIRE board-item set is covered by the current selection. Checking
        # a row means "reference it via clone_placements: instead of copying
        # its geometry flat into the new cell" — see
        # _update_sub_placement_candidates. Same hidden-tab pattern as the
        # "Net template role" section above: invisible until there is at
        # least one candidate.
        self._sub_placement_section = QWidget()
        sub_placement_section_layout = QVBoxLayout(self._sub_placement_section)
        sub_placement_section_layout.addWidget(QLabel(
            _("Sub-placements (existing placements fully covered by this "
              "selection — referenced via clone_placements, not copied):")))
        self._sub_placements_table = QTableWidget(0, 4)
        self._sub_placements_table.setHorizontalHeaderLabels(
            [_("Include"), _("Placement"), _("Cell"), _("Matched")])
        self._sub_placements_table.verticalHeader().setVisible(False)
        self._sub_placements_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._sub_placements_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._sub_placements_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._sub_placements_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self._sub_placements_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        sub_placement_section_layout.addWidget(self._sub_placements_table, 1)
        sub_placement_section_layout.addStretch(1)
        self._sub_placement_tab_index = self._tabs.addTab(
            self._sub_placement_section, _("Sub-placements"))
        self._tabs.setTabVisible(self._sub_placement_tab_index, False)

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

        self.extract_button = QPushButton(_("Extract to file"))
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self._on_extract)
        layout.addWidget(self.extract_button)

        # Re-extract from current board state (2026-08-25, handoff
        # clone_item_resolver_select_and_reextract): pick an already-saved
        # extract profile (or Cell) in the Existing tab, then re-capture its
        # cell from the live board via the clone_placement that currently owns
        # it — no manual re-selection in pcbnew. The combo lists every
        # clone_placement whose cell: references the picked cell; the button is
        # enabled only once a concrete placement is chosen.
        re_extract_row = QHBoxLayout()
        re_extract_row.addWidget(QLabel(_("Placement:")))
        self.re_extract_placement_combo = QComboBox()
        self.re_extract_placement_combo.setToolTip(
            _("The clone_placement that currently places the picked Cell on the board "
              "(populated when you pick an existing profile/Cell). Re-extract re-captures "
              "that placement's live components/vias/tracks."))
        re_extract_row.addWidget(self.re_extract_placement_combo, 1)
        layout.addLayout(re_extract_row)
        self.re_extract_button = QPushButton(_("Re-extract from current board state"))
        self.re_extract_button.setEnabled(False)
        self.re_extract_button.clicked.connect(self._on_re_extract)
        layout.addWidget(self.re_extract_button)

    def set_board_selection(self, raw_items: List[Any], selected_footprints: List[Selected]) -> None:
        """Called every selection-watch tick — see module docstring for why
        this needs the raw mixed list, not just the Selected-footprint one
        the tree/bulk-edit docks use."""
        self._raw_items = raw_items
        self._selected_footprints = selected_footprints
        self._update_cluster_filter_choices()
        self._refresh_derived_selection_state()

    def _refresh_derived_selection_state(self) -> None:
        """Everything downstream of "what's selected" — shared between a
        fresh selection tick (set_board_selection) and the Cluster filter
        checkbox/combo changing on an otherwise-unchanged selection."""
        # 2026-08-12, Group 4: every consumer below used to call
        # _filtered_selection() itself — 5 redundant recomputations of the
        # same (possibly registry-filtering) scan per ~400ms selection-watch
        # tick. Compute it ONCE here and share the result.
        filtered = self._filtered_selection()
        self._update_selection_label(filtered)
        self._update_cluster_warning()
        self._rebuild_net_aliases(filtered)
        self._update_origin_choices(filtered)
        self._autofill_from_cluster(filtered)
        self._update_button_state(filtered)

    def _on_cluster_filter_changed(self, *_args: Any) -> None:
        self._refresh_derived_selection_state()

    def _update_cluster_filter_choices(self) -> None:
        """Populates cluster_filter_combo from the Clusters actually present
        in the RAW selection (never the already-filtered one — the whole
        point is to keep every option choosable even while one is applied).
        Hidden whenever there's nothing to filter (0 or 1 distinct Cluster),
        which also resets an active filter rather than leaving a stale one
        silently in effect on a selection that no longer spans Clusters."""
        clusters = sorted({s.cluster for s in self._selected_footprints if s.cluster})
        multi = len(clusters) > 1
        self.cluster_filter_checkbox.setVisible(multi)
        self.cluster_filter_combo.setVisible(multi)
        if not multi:
            self.cluster_filter_checkbox.setChecked(False)
            self.cluster_filter_combo.clear()
            return
        previous = self.cluster_filter_combo.currentData()
        self.cluster_filter_combo.blockSignals(True)
        self.cluster_filter_combo.clear()
        for cluster in clusters:
            self.cluster_filter_combo.addItem(cluster, cluster)
        if previous in clusters:
            self.cluster_filter_combo.setCurrentIndex(clusters.index(previous))
        else:
            # Default to the Cluster with the most components in the
            # selection — usually "mine", since the neighbours swept in by
            # an area-select are typically a smaller fraction of it.
            counts = Counter(s.cluster for s in self._selected_footprints if s.cluster)
            majority = counts.most_common(1)[0][0]
            self.cluster_filter_combo.setCurrentIndex(clusters.index(majority))
        self.cluster_filter_combo.blockSignals(False)

    def _cluster_filter_target(self) -> Optional[str]:
        if not self.cluster_filter_checkbox.isChecked():
            return None
        return self.cluster_filter_combo.currentData()

    def _registry_uuids(self) -> Tuple[set, set]:
        """(via_uuids, track_uuids) already recorded in the Placer file's
        registry.json/tracks.registry.json — i.e. Via/Track objects some
        EARLIER `apply` run of THIS Placer file already created for an
        existing clone_placement/Rule (see registry.py's own module
        docstring: registry.json is an index key->live-board-UUID, written
        by record_created() as each item is actually created on the board).
        A raw Via/Track's own `.id.value` matching one of these UUIDs is an
        exact fact ("this belongs to placement X already"), unlike net-name
        matching (rejected live 2026-08-12 — a shared net like GND can't be
        told apart between two Clusters that way).

        Cached per Placer path (invalidated only by set_placer_file(), see
        its own docstring) rather than reloaded on every ~400ms
        selection-watch tick — load_config() walks the whole include: graph,
        too heavy to repeat that often; every other dock that reads a config
        file this way (Rules/ThermalVia/Placer previews) only does so on an
        explicit user action too, never on a fast timer, so a staleness
        window bounded by "until the Placer file combo/browse changes" is
        consistent with the rest of the app, not a new risk.

        Empty sets — no Via/Track excluded by this half of the filter — when
        there's no Placer file assigned (nothing to check against) or it
        can't be read (reported via the log, never fatal: footprint
        filtering by Cluster still applies on its own)."""
        if self._placer_path is None:
            return set(), set()
        if self._registry_uuids_cache is not None:
            return self._registry_uuids_cache
        # Local import — kicadstamp.registry's own top-level `from
        # .placement.commands import ...` transitively runs the WHOLE
        # kicadstamp.placement package's __init__ chain (BatchExecutor etc.),
        # part of which imports back `from ...registry import
        # make_registry_key` — a real circular dependency that only bites
        # when kicadstamp.registry is the FIRST thing to touch it in a given
        # process. Every other current importer of kicadstamp.registry
        # (apply_pipeline.py etc.) happens to already have kicadstamp.placement
        # loaded by the time it gets there; this GUI dock is loaded much
        # earlier (gui.main_window -> dock_hub -> detail_panel -> extract),
        # before anything else in that chain has touched kicadstamp.placement
        # at all — a module-level import here hit "partially initialized
        # module" on startup. Deferring to call time (well after the app's
        # other docks, e.g. PlacerDock, have already imported
        # kicadstamp.placement for real) avoids it without having to
        # restructure registry.py itself.
        from kicadstamp.registry import (load_registry, load_track_registry,
                                         registry_path_for_config, track_registry_path_for_config)
        via_uuids: set = set()
        track_uuids: set = set()
        if self._placer_path.exists():
            try:
                _cfg, _ctx = load_config(str(self._placer_path))
                registry_path = _ctx.registry_path or registry_path_for_config(str(self._placer_path))
                track_registry_path = (_ctx.track_registry_path
                                       or track_registry_path_for_config(str(self._placer_path)))
                via_uuids = {entry.uuid for entry in load_registry(registry_path).values()}
                track_uuids = {entry.uuid for entry in load_track_registry(track_registry_path).values()}
            except (ValidationError, OSError) as e:
                logger.warning(_("Cluster filter: failed to read the Placer file's registry "
                                 "({placer}): {type}: {error} — Via/Track exclusion by registry "
                                 "skipped, footprint filtering by Cluster still applies")
                               .format(placer=self._placer_path, type=type(e).__name__, error=e))
        self._registry_uuids_cache = (via_uuids, track_uuids)
        return self._registry_uuids_cache

    def _filtered_selection(self) -> Tuple[List[Any], List[Selected]]:
        """(raw_items, selected_footprints) narrowed to the Cluster filter's
        target, if one is active — otherwise the untouched full selection.
        Excludes the matching FootprintInstance from raw_items by ref, and
        any Via/Track already known to belong to another existing placement
        by registry UUID (see _registry_uuids()).

        Sub-placements (2026-08-25, Задание 1/1б): candidates are detected on
        the footprint-narrowed selection BEFORE the registry drop — a
        fully-covered existing placement must not be disqualified by the very
        drop that would strip its own copper. Its via/tracks are then exempt
        from the drop (kept in the selection; they become a reference or stay
        flat per the user's Sub-placements checkbox)."""
        target = self._cluster_filter_target()
        if target is None:
            footprints = self._selected_footprints
            raw_items = self._raw_items
        else:
            footprints = [s for s in self._selected_footprints if s.cluster == target]
            kept_refs = {s.ref for s in footprints}
            raw_items = [i for i in self._raw_items
                         if not (isinstance(i, Footprint) and i.ref not in kept_refs)]
        # Candidate detection (also rebuilds the hidden Sub-placements tab) —
        # always on the footprint-narrowed selection with ALL via/tracks still
        # present, so a wholly-covered placement's own copper cannot disqualify
        # it from becoming a candidate.
        self._update_sub_placement_candidates(raw_items, footprints)
        if target is None:
            return raw_items, footprints
        via_uuids, track_uuids = self._registry_uuids()
        exempt_via, exempt_track = self._sub_placement_owned_uuids()
        final_items = []
        for item in raw_items:
            if isinstance(item, Via):
                if item.uuid not in via_uuids or item.uuid in exempt_via:
                    final_items.append(item)
            elif isinstance(item, Track):
                if item.uuid not in track_uuids or item.uuid in exempt_track:
                    final_items.append(item)
            else:
                final_items.append(item)
        return final_items, footprints

    @staticmethod
    def _item_key(item: Any) -> Tuple[str, str]:
        """Stable identity of a board item for the subset check / flat-list
        exclusion: a Footprint by its ref (the selection and resolve_clone_
        board_items both see the SAME footprint index, so ref is exact), a
        Via/Track by its UUID. The kind prefix keeps the three namespaces
        from ever colliding."""
        if isinstance(item, Footprint):
            return ("fp", item.ref)
        if isinstance(item, Via):
            return ("via", item.uuid)
        if isinstance(item, Track):
            return ("track", item.uuid)
        return (type(item).__name__, getattr(item, "uuid", str(id(item))))

    def _sub_placement_is_self_reference(self, clone) -> bool:
        """True when `clone` references the cell currently being extracted
        (clone.cell == the Cell-name field) — a literal self-reference (cell
        `dac_buf` containing clone_placements: cell: dac_buf), which the
        resolver's cycle guard would only catch AFTER it was already written.
        Compared exactly as-is, matching the project's other cell-name
        comparisons (e.g. _refresh_re_extract_placements' `c.cell ==
        cell_name`). An empty target name never matches — on a selection-watch
        tick that fires before the name was typed there is nothing to compare
        against, so nothing is filtered."""
        target = self.name_edit.text().strip()
        return bool(target) and getattr(clone, "cell", None) == target

    def _sub_placement_catalog(self) -> List[Tuple[Any, List[Any]]]:
        """[(clone, resolved_board_items)] for every top-level ClonePlacement
        in the Placer file's config — CACHED per Placer path (invalidated by
        set_root_path, the same staleness window as _registry_uuids):
        resolve_clone_board_items is live-board work (role resolution +
        registry -> UUID -> live item), far too heavy for the ~400ms
        selection-watch tick. Returns [] when there's no Placer file /
        config / adapter (nothing to match against — the Sub-placements
        feature just stays off)."""
        if self._sub_placements_cache is not None:
            return self._sub_placements_cache
        catalog: List[Tuple[Any, List[Any]]] = []
        if self._placer_path is not None and self._placer_path.exists():
            board = self._connection.board
            adapter = getattr(board, "adapter", None)
            if adapter is not None:
                try:
                    cfg, ctx = load_config(str(self._placer_path))
                except Exception as e:
                    logger.warning(_("Sub-placements: failed to load config "
                                     "({placer}): {type}: {error}")
                                   .format(placer=self._placer_path,
                                           type=type(e).__name__, error=e))
                    cfg = None
                if cfg is not None:
                    from kicadstamp.registry import (registry_path_for_config,
                                                     track_registry_path_for_config)
                    from kicadstamp.placement.services.board_items_resolver import (
                        resolve_clone_board_items)
                    registry_path = (ctx.registry_path
                                     or registry_path_for_config(str(self._placer_path)))
                    track_registry_path = (ctx.track_registry_path
                                           or track_registry_path_for_config(
                                               str(self._placer_path)))
                    for clone in cfg.clone_placements:
                        try:
                            items = resolve_clone_board_items(
                                adapter, cfg, ctx, clone,
                                registry_path=registry_path,
                                track_registry_path=track_registry_path)
                        except ValidationError as e:
                            # Unresolvable on this board — no candidate (falls
                            # back to the old flat path), never fatal.
                            logger.warning(_("Sub-placements: {name}: {error}")
                                           .format(name=clone_placement_effective_name(clone),
                                                   error=e))
                            items = []
                        catalog.append((clone, items))
        self._sub_placements_cache = catalog
        return catalog

    def _sub_placement_owned_uuids(self) -> Tuple[set, set]:
        """(via_uuids, track_uuids) owned by the CURRENT sub-placement
        candidates (their via/track items). Used by _filtered_selection to NOT
        registry-drop a fully-covered placement's own copper (Задание 1б): a
        wholly-covered placement either becomes a referenced sub-placement or
        stays flat per the user's checkbox, but its copper must never be
        silently stripped by the registry filter in between."""
        via_uuids: set = set()
        track_uuids: set = set()
        for cand in self._sub_placement_candidates:
            for item in cand.items:
                if isinstance(item, Via):
                    via_uuids.add(item.uuid)
                elif isinstance(item, Track):
                    track_uuids.add(item.uuid)
        return via_uuids, track_uuids

    def _update_sub_placement_candidates(self, raw_items: List[Any],
                                         footprints: List[Selected]) -> None:
        """Detect existing top-level ClonePlacements whose ENTIRE board-item
        set is a subset of the current (Cluster-narrowed) selection — those
        become "Sub-placements" candidates (Задание 1): instead of copying
        their geometry flat into the new cell, Extract can reference them via
        clone_placements:. Only a FULLY covered placement is a candidate — a
        partial overlap is probably a geometric coincidence and stays on the
        old path (no surprises). Nested CellPlacements are deliberately not
        considered (they have no .mirror and their rotation_deg is not a world
        angle — a separate, later task). Rebuilds the hidden tab + visibility.

        Called from _filtered_selection (once per selection-watch tick, plus
        at extract time) — the resolved items come from the cached catalog."""
        candidates: List[SubPlacementCandidate] = []
        if raw_items:
            selection_keys = {self._item_key(i) for i in raw_items}
            for clone, items in self._sub_placement_catalog():
                if not items:
                    continue
                item_keys = {self._item_key(i) for i in items}
                if item_keys <= selection_keys:
                    # Self-reference guard (2026-08-25, handoff sub_placements_
                    # self_reference_guard): a candidate whose cell: IS the
                    # target cell would write `dac_buf -> dac_buf`. Excluded
                    # outright — its items stay on the flat path instead.
                    if self._sub_placement_is_self_reference(clone):
                        continue
                    candidates.append(SubPlacementCandidate(
                        clone=clone, items=items, item_keys=frozenset(item_keys)))
        self._sub_placement_candidates = candidates
        self._rebuild_sub_placements_table()

    @staticmethod
    def _sub_placement_counts_text(cand: SubPlacementCandidate) -> str:
        """Trust evidence for the table: how many components/vias/tracks of the
        placement are present in the selection — "this is really the whole
        placement, not a coincidence". Counts are per-kind, not a raw item
        total, so the number reads naturally."""
        n_fp = sum(1 for i in cand.items if isinstance(i, Footprint))
        n_cu = sum(1 for i in cand.items if isinstance(i, (Via, Track)))
        return _("{fp} component(s), {cu} via/track(s)").format(fp=n_fp, cu=n_cu)

    def _rebuild_sub_placements_table(self) -> None:
        """One row per candidate: checkbox (default on) | placement name |
        its cell | matched count. Preserves checkbox state across preview ticks
        by placement name (same spirit as _rebuild_net_aliases preserving
        typed aliases). Tab stays hidden while there are no candidates."""
        previous = {name: cb.isChecked()
                    for name, cb in self._sub_placement_checkboxes.items()}
        self._sub_placements_table.setRowCount(0)
        self._sub_placement_checkboxes = {}
        for cand in self._sub_placement_candidates:
            name = clone_placement_effective_name(cand.clone)
            row = self._sub_placements_table.rowCount()
            self._sub_placements_table.insertRow(row)
            cb = QCheckBox()
            cb.setChecked(previous.get(name, True))
            self._sub_placement_checkboxes[name] = cb
            self._sub_placements_table.setCellWidget(row, 0, cb)
            self._sub_placements_table.setItem(row, 1, QTableWidgetItem(name))
            self._sub_placements_table.setItem(row, 2,
                                               QTableWidgetItem(cand.clone.cell or ""))
            self._sub_placements_table.setItem(
                row, 3, QTableWidgetItem(self._sub_placement_counts_text(cand)))
        self._tabs.setTabVisible(self._sub_placement_tab_index,
                                 bool(self._sub_placement_candidates))

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._origin_role_row.setVisible(mode == 1)
        self._origin_via_row.setVisible(mode == 2)

    def _update_origin_choices(self, filtered_selection=None) -> None:
        """Populates the Role/Via-net combos from what's actually in the
        current selection — picking an origin from outside the selection
        makes no sense (extract_template_from_selection fatals on it
        anyway: 'role not found in selection' / 'no such via in selection'),
        so there's no point offering it. filtered_selection — precomputed
        (raw_items, footprints) passed by _refresh_derived_selection_state
        (2026-08-12, Group 4); computed here when None (direct callers)."""
        raw_items, footprints = (filtered_selection if filtered_selection is not None
                                 else self._filtered_selection())
        roles = sorted({s.role for s in footprints if s.role})
        set_combo_items(self.origin_role_combo, roles)

        via_nets = sorted({item.net_name for item in raw_items
                            if isinstance(item, Via) and item.net_name})
        set_combo_items(self.origin_via_net_combo, via_nets)

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to RootMetadataDock's root_changed — new extracted cells:/
        extract_profiles: entries are always written to the project root
        file (2026-08-21, plan flatten_and_single_file_gui), so all three
        output targets (Cell file / Profile file / Placer file) ARE the
        root. The Existing lists and auto-fill read the WHOLE include graph
        (see _graph_section_keys/_graph_section_entry)."""
        self._root_path = path
        self._target_path = path
        self._profile_path = path
        self._placer_path = path
        self._registry_uuids_cache = None
        self._sub_placements_cache = None
        self._refresh_existing_lists()
        self._update_button_state()

    def _graph_section_keys(self, section: str):
        """Every key of a DICT section across the whole include graph."""
        if self._root_path is None:
            return []
        return sorted(collect_section_entries(self._root_path, section).keys())

    def _graph_section_entry(self, section: str, key: str) -> dict:
        """One DICT-section entry by name, read from the whole include graph."""
        if self._root_path is None:
            return {}
        return collect_section_entries(self._root_path, section).get(key, {})

    def prepare_new_profile(self, file_path: Path) -> None:
        """ConfigTreeDock's "Add extract profile..." delegate (2026-08-13,
        plan context_menu_by_section). Deliberately NOT a blank form ready
        to Save like the other five Add-actions: an extract_profiles: entry's
        params/net_template_role/rule_nets physically come from a REAL board
        selection at extraction time (see module docstring), so there is
        nothing meaningful a user could type into an empty form. Instead this
        prepares the existing Extract flow for a profile save:
        - points this dock at `file_path` (set_profile_file, which also keeps
          profile_file_combo in sync);
        - pre-checks "Also save as extract_profile" and clears the profile-key
          field (defaults to the Cell name at extraction time), focusing it;
        - leaves cells_list / the picked Cell untouched — the profile key is
          independent of what's selected for extraction.
        The visible cue is the checked checkbox + focused empty key field (and
        that field's existing tooltip); the hint message goes only to the Log
        dock (show_message — no inline label since 2026-08-13, deliberately
        not reintroducing one for this single hint)."""
        self._profile_path = self._root_path
        self._refresh_existing_lists()
        self.save_profile_checkbox.setChecked(True)
        self.profile_key_edit.clear()
        self.profile_key_edit.setFocus()
        self._show_message(
            _("Select footprints/vias/tracks on the board, type a profile key above, then "
              "Extract — the profile is saved as a side effect of a real extraction."),
            _WARN_STYLE)

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")

    _load_data = staticmethod(yaml_io.load_data)
    _existing_keys = staticmethod(yaml_io.existing_keys)

    def _refresh_existing_lists(self) -> None:
        self.cells_list.clear()
        self.cells_list.addItems(self._graph_section_keys("cells"))
        self.profiles_list.clear()
        self.profiles_list.addItems(self._graph_section_keys("extract_profiles"))
        self._last_autofill_key = None  # force _autofill_from_cluster to re-check against the new content

    @staticmethod
    def _select_list_item(list_widget: QListWidget, text: Optional[str]) -> None:
        list_widget.clearSelection()
        if text is None:
            return
        items = list_widget.findItems(text, Qt.MatchFlag.MatchExactly)
        if items:
            list_widget.setCurrentItem(items[0])

    def _autofill_from_cluster(self, filtered_selection=None) -> None:
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
        _raw_items, footprints = (filtered_selection if filtered_selection is not None
                                  else self._filtered_selection())
        clusters = frozenset(s.cluster for s in footprints if s.cluster)
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

            cell_keys = self._graph_section_keys("cells")
            matched_cell = next((c for c in candidates if c in cell_keys), None)
            if not self.name_edit.text().strip():
                # An existing key wins if there is one (it reflects
                # whatever naming was actually chosen last time); otherwise
                # the Cluster's own slug is still a perfectly good default
                # — no reason to leave the field blank just because nothing
                # was extracted under that name yet (2026-08-01: "если есть
                # имя кластера, зачем придумывать что-то").
                self.name_edit.setText(matched_cell or candidates[0])

            profile_keys = self._graph_section_keys("extract_profiles")
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
        profile_entry = self._graph_section_entry("extract_profiles", profile_key)
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
        profiles = (collect_section_entries(self._root_path, "extract_profiles")
                    if self._root_path is not None else {})
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
        entry = (self._graph_section_entry("extract_profiles", profile_key)
                 if profile_key is not None else {})
        self._set_re_extract_target(cell_name, profile_key, entry)

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
        entry = self._graph_section_entry("extract_profiles", profile_key)
        self._set_re_extract_target((entry.get("name") or profile_key) if entry else profile_key,
                                    profile_key, entry)

    def _update_net_template_role_rows(self, footprints=None) -> None:
        """A role needs an explicit net_template_role pick exactly when 2+ of
        ITS pads' DISTINCT nets themselves classify by role (lemma2/pad, see
        _classify_selection_nets) — driven by the preview classification, NOT
        by typed aliases (plan 2026-08-13, step 5). Since a classified net's
        Alias edit is disabled, the old "2+ manually aliased nets on its pads"
        trigger could no longer fire at all, so the ambiguity test moved onto
        the classification itself. Careful distinction (the plan's only
        non-trivial point): a role whose nets do NOT classify at all (all
        fallback) is NOT ambiguous — there is nothing to pick.

        footprints — the (possibly Cluster-filtered) selection, the same list
        _rebuild_net_aliases just classified; falls back to the unfiltered
        _selected_footprints for direct callers."""
        if footprints is None:
            footprints = self._selected_footprints
        ambiguous: Dict[str, List[str]] = {}
        for s in footprints:
            if not s.role:
                continue
            distinct_nets = set(s.nets.values())
            # Bug 3 (2026-08-13): a net marked "Rule net" is excluded — at
            # extraction it becomes net: null (template_extraction.py:
            # `if via_net in rule_nets: via_net = None` before
            # _suggest_net_from_role), so it takes NO part in the role
            # classification and must not make the role look ambiguous (or
            # the user would be forced into a net_template_role pick that
            # isn't actually needed, seeding a junk params entry).
            classifying = sorted(
                n for n in distinct_nets
                if self._net_auto_roles.get(n) and self._net_auto_roles[n][0] != "fallback"
                and not self._is_rule_net_checked(n))
            if len(classifying) >= 2:
                ambiguous[s.role] = classifying

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

    def _update_selection_label(self, filtered_selection=None) -> None:
        if not self._raw_items:
            self.selection_label.setText(_("Nothing selected"))
            return
        raw_items, footprints = (filtered_selection if filtered_selection is not None
                                 else self._filtered_selection())
        if not raw_items:
            self.selection_label.setText(_("Nothing left after the Cluster filter (see above)"))
            return
        fp_count = len(footprints)
        other_count = len(raw_items) - fp_count
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
            text = _("Selection spans multiple Clusters: {clusters}").format(clusters=shown)
            target = self._cluster_filter_target()
            if target is not None:
                kept = sum(1 for s in self._selected_footprints if s.cluster == target)
                text += " " + _("(filtered to {cluster!r}: keeping {kept} of {total} component(s))").format(
                    cluster=target, kept=kept, total=len(self._selected_footprints))
            self.cluster_warning_label.setText(text)
        else:
            self.cluster_warning_label.setText("")

    def _classify_selection_nets(self, footprints) -> Dict[str, Tuple[str, Optional[str]]]:
        """net -> (category, role) preview classification of the selection's
        distinct nets (plan 2026-08-13, step 2) — the same 3 buckets the
        extractor's net_from_role auto-suggestion produces per via/track:
          - ("lemma2", role)  — net is the only non-rule net of exactly one
            selected role (unambiguous; no pad needed);
          - ("pad", role)     — a role sits on this net but cannot be pinned
            down without geometry (a multi-net role, or a rail shared by
            several roles); a specific via/track would pick one by geometry,
            so the exact role can change per item, but SOME role exists;
          - ("fallback", None) — no selected role covers the net (the only
            real alias/literal candidate), or it is an intrinsic rule net
            (GND) that needs no role at all.
        No geometry: the preview is per-net, there is no specific via/track
        point yet, so the geometric tie-break (points/components) is not fed
        in — classify_net already accepts points=None. Rule nets are handled
        OUTSIDE classify_net: the user's "Rule net" checkbox set is a
        separate explicit opt-in mechanism (plan step 4) and the intrinsic
        RULE_NETS ("GND") always read fallback — a rule net is not "owned" by
        any role and must not count as a classifying net, or every rail+GND
        cap role would look like a "2+ classifying nets" bridging component
        (plan step 5's trap). classify_net is called with EMPTY rule_nets so
        a rail shared by several single-net roles still reads "pad": it DOES
        resolve by role at extract time (via geometry), so its alias must be
        disabled like any other auto-classified net."""
        board = self._connection.board
        if board is None:
            return {}
        raw_fps = [s.fp for s in footprints if getattr(s, "fp", None) is not None]
        if not raw_fps:
            return {}
        role_nets = selection_role_nets(board.adapter, raw_fps)
        if not role_nets:
            return {}
        nets = sorted({net for s in footprints for net in s.nets.values()})
        out: Dict[str, Tuple[str, Optional[str]]] = {}
        for net in nets:
            if net in RULE_NETS:
                # Intrinsic rule net (GND) — needs no role, not "owned" by any
                # role, not a classifying net; alias stays active (the "Rule
                # net (null)" checkbox is the way to null it).
                out[net] = ("fallback", None)
                continue
            role, pad = suggest_net_from_role(role_nets, net, set(), None, None)
            if role is None:
                out[net] = ("fallback", None)
            elif pad is None:
                out[net] = ("lemma2", role)
            else:
                out[net] = ("pad", role)
        return out

    def _is_rule_net_checked(self, net: str) -> bool:
        """Whether the net's "Rule net (null)" checkbox is currently checked —
        the one composite predicate shared by every place deciding whether a
        net still counts "by role": the net-template-role ambiguity trigger
        (bug 3), the Auto-role column/tooltip (bug 5) and the Alias edit's
        disabled state (pre-existing, 2026-08-05)."""
        checkbox = self._rule_net_checkboxes.get(net)
        return checkbox is not None and checkbox.isChecked()

    def _apply_auto_role_visuals(self, row: int, net: str) -> None:
        """The ONE place that decides what the Auto-role column and the Alias
        edit's tooltip/disabled state show for a net — shared by all THREE
        paths that build or update that column (tail of bug 5, handoff
        2026_08_13_autorole_rule_net_tail): the per-tick inline refresh
        (_refresh_auto_role_cells), a fresh table row (_rebuild_net_aliases)
        and the Rule-net checkbox toggle (_on_rule_net_toggled). A net counts
        "by role" only when it classifies AND isn't marked Rule net — with the
        checkbox set, extraction writes net: null and the role has nothing to
        do with it, so the column/tooltip must not claim otherwise. A net that
        went fallback (or got Rule net checked) also loses the stale by-role
        tooltip — the field is live again, the old "input ignored" tip was a
        lie (bug 4)."""
        category, role = self._net_auto_roles.get(net, ("fallback", None))
        rule_checked = self._is_rule_net_checked(net)
        by_role = category != "fallback" and not rule_checked
        role_item = self.nets_table.item(row, 3)
        if role_item is not None:
            role_item.setText(
                _("role: {role}").format(role=role) if by_role else "")
        edit = self._net_alias_edits.get(net)
        if edit is not None:
            if by_role:
                edit.setToolTip(
                    _("This net already resolves via Role {role!r} — a typed "
                      "alias here would be ignored at apply time").format(role=role))
            else:
                edit.setToolTip("")
            edit.setDisabled(rule_checked or category != "fallback")

    def _refresh_auto_role_cells(self) -> None:
        """Update the read-only Auto-role column text and the Alias edit
        disabled/tooltip state for the CURRENT rows, in place (no rebuild) —
        used when the net set is unchanged between selection-watch ticks but
        the role evidence may have changed (e.g. the user moved the selection
        to different components that happen to carry the same net names). All
        per-row state is applied by the single _apply_auto_role_visuals."""
        for row in range(self.nets_table.rowCount()):
            net_item = self.nets_table.item(row, 0)
            if net_item is None:
                continue
            self._apply_auto_role_visuals(row, net_item.text())

    def _rebuild_net_aliases(self, filtered_selection=None) -> None:
        """One row per distinct net found on the selected components' pads.
        Preserves whatever the user already typed/checked for a net that's
        still present — the selection-watch tick fires every ~400ms, so
        without this, in-progress typing would be wiped just like the
        tree/bulk-edit docks had to guard against. filtered_selection —
        precomputed by _refresh_derived_selection_state (2026-08-12, Group 4).

        Each row also carries a read-only "Auto-role" cell (plan 2026-08-13):
        which selected role the extractor's net_from_role auto-suggestion
        would resolve this net to (lemma2/pad) vs. no role at all (fallback —
        the only nets where a typed alias still means something). The Alias
        edit of a lemma2/pad net is disabled with an explanatory tooltip: the
        core resolves net_from_role BEFORE net_template_map, so a typed alias
        there would be silently ignored (no-override decision, plan §"Решение
        по спорному пункту")."""
        _raw_items, footprints = (filtered_selection if filtered_selection is not None
                                  else self._filtered_selection())
        nets = sorted({net for s in footprints for net in s.nets.values()})
        previous_alias = {net: edit.text() for net, edit in self._net_alias_edits.items()}
        previous_rule_net = {net: cb.isChecked() for net, cb in self._rule_net_checkboxes.items()}
        # Preview classification FIRST — drives both the Auto-role column and
        # the net-template-role ambiguity trigger. Recomputed even when the
        # net set is unchanged: the role evidence (selection composition) can
        # change on its own while the net names stay the same.
        self._net_auto_roles = self._classify_selection_nets(footprints)
        if set(nets) == set(previous_alias):
            # Same nets as last tick — the user's in-progress alias typing
            # must survive (no table rebuild), but the classification-driven
            # cells still refresh in place: the role evidence (selection
            # composition) can change even when the net names do not.
            self._refresh_auto_role_cells()
            self._update_net_template_role_rows(footprints)
            return

        self.nets_table.setRowCount(0)  # also deletes every row's cell widgets

        self._net_alias_edits = {}
        self._rule_net_checkboxes = {}
        for row, net in enumerate(nets):
            self.nets_table.insertRow(row)
            net_item = QTableWidgetItem(net)
            net_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # read-only, not editable/selectable
            self.nets_table.setItem(row, 0, net_item)

            # The Auto-role cell and the Alias tooltip/disabled state are all
            # applied by the single _apply_auto_role_visuals (tail of bug 5,
            # handoff 2026_08_13_autorole_rule_net_tail) — including the
            # Rule-net checkbox, so a fresh row whose box was restored checked
            # already comes in showing no "role: X" and no by-role tooltip.
            role_item = QTableWidgetItem("")
            role_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # read-only, not editable/selectable
            self.nets_table.setItem(row, 3, role_item)

            edit = QLineEdit()
            edit.setPlaceholderText(_("alias, e.g. PWR_IN"))
            edit.setText(previous_alias.get(net, ""))
            self.nets_table.setCellWidget(row, 1, edit)
            self._net_alias_edits[net] = edit

            checkbox = QCheckBox(_("Rule net (null)"))
            checkbox.setToolTip(
                _("Write this via/track net as null instead of a literal — at apply time a "
                  "ManualSpoke-placed cell inherits the enclosing Rule's own net for it, so the "
                  "cell can be reused across Rules on different nets."))
            checkbox.setChecked(previous_rule_net.get(net, False))
            checkbox.toggled.connect(lambda checked, r=row, n=net: self._on_rule_net_toggled(r, checked, n))
            self.nets_table.setCellWidget(row, 2, checkbox)
            self._rule_net_checkboxes[net] = checkbox
            self._on_rule_net_toggled(row, checkbox.isChecked(), net)
        self._update_net_template_role_rows(footprints)

    def _on_rule_net_toggled(self, row: int, checked: bool, net: str) -> None:
        """Rule-net checkbox handler — fires synchronously on every click (and
        once during a table rebuild). Clearing a checked box's alias text and
        refreshing the Auto-role column/tooltip/disabled state all flow through
        the single _apply_auto_role_visuals, so the visuals never lag a tick
        behind the click (tail of bug 5): checking "Rule net" on a
        role-classifying net immediately drops "role: X" from the column and
        clears the by-role tooltip."""
        if checked:
            edit = self._net_alias_edits.get(net)
            if edit is not None:
                edit.setText("")
        self._apply_auto_role_visuals(row, net)

    def _update_button_state(self, filtered_selection=None) -> None:
        raw_items, _footprints = (filtered_selection if filtered_selection is not None
                                  else self._filtered_selection())
        self.extract_button.setEnabled(bool(raw_items) and self._target_path is not None)

    def _show_message(self, text: str, style: str = "") -> None:
        """Mirror into the Log dock at the level matching `style` — the docks
        no longer have an inline message_label (2026-08-13), the Log dock is
        the single destination."""
        show_message(text, style, logger)

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
        full_raw_items, _footprints = self._filtered_selection()
        if not full_raw_items or self._target_path is None:
            return None
        # Sub-placements (Задание 1): a checked candidate's board-items are
        # EXCLUDED from what becomes the new cell's flat components/vias/tracks
        # — they are referenced via clone_placements: instead, so the same
        # geometry must not land in the cell twice (once flat, once by
        # reference). The CellPlacement entry itself (with xy) is built on the
        # worker. `full_raw_items` (BEFORE the exclusion) is carried in the
        # payload too: the worker derives the ONE origin from it, so an
        # anchor/origin component that gets excluded as a Sub-placement is
        # still found and the Sub-placement xy and the flat geometry share one
        # coordinate system (live bug 2026-08-25 — origin used to be derived
        # from the already-trimmed list).
        sub_placements: List[Dict[str, Any]] = []
        raw_items = full_raw_items  # trimmed below for the flat geometry
        if self._sub_placement_candidates:
            excluded_keys: set = set()
            for cand in self._sub_placement_candidates:
                cb = self._sub_placement_checkboxes.get(
                    clone_placement_effective_name(cand.clone))
                if cb is not None and cb.isChecked():
                    # Defense-in-depth self-reference guard (2026-08-25): a
                    # stale UI tick may have built the table before the Cell
                    # name was typed, so a self-referencing candidate could
                    # still be checked here. Skip it entirely — its items stay
                    # flat and nothing is lost.
                    if self._sub_placement_is_self_reference(cand.clone):
                        continue
                    excluded_keys |= cand.item_keys
                    sub_placements.append({
                        "name": clone_placement_effective_name(cand.clone),
                        "clone": cand.clone,
                    })
            if excluded_keys:
                raw_items = [i for i in full_raw_items
                             if self._item_key(i) not in excluded_keys]
        # A pure-composite cell (only clone_placements, no flat content) is a
        # legitimate extract — an empty `raw_items` is only an error when there
        # are no Sub-placements to reference either.
        if not raw_items and not sub_placements:
            self._show_message(_("Nothing left to extract — the checked "
                                 "Sub-placements cover the whole selection."),
                               _ERROR_STYLE)
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
                    _("Net template role: role {role!r} bridges 2+ nets that auto-classify by "
                      "role — pick which one is the template.").format(role=role),
                    _ERROR_STYLE)
                return None
            net_template_role[role] = literal
            # Seed the matching param (name = role, e.g. {PI_FILTER_FB}) so the
            # picked literal is actually resolvable as a net_template. A
            # bridging role's nets classify (lemma2/pad) -> their Alias edits
            # are disabled, so params for them can never be typed by hand, and
            # without this seed net_template_map stays empty and the extractor
            # fatals with "...not in net_template_map" — the from-scratch
            # bridging dead-end found by review on commit 9866869. The combo
            # pick IS the explicit opt-in (net_template_role is separate from
            # the no-override rule for ordinary lemma2/pad nets); setdefault so
            # an existing alias param with the same name is never clobbered.
            params.setdefault(role, literal)

        return {
            "name": name,
            "raw_items": raw_items,
            "full_raw_items": full_raw_items,
            "target_path": self._target_path,
            "save_profile": save_profile,
            "profile_key": self.profile_key_edit.text().strip() or name,
            "profile_path": self._profile_path,
            "placer_path": self._placer_path,
            "params": params,
            "rule_nets": rule_nets,
            "origin_kwargs": origin_kwargs,
            "net_template_role": net_template_role,
            "raw_selection": self.raw_selection_checkbox.isChecked(),
            "sub_placements": sub_placements,
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
        origin, origin_err = self._compute_extract_origin(payload)
        if origin_err is not None:
            return {"error": origin_err}
        clone_placements = None
        if payload.get("sub_placements"):
            clone_placements, sub_err = self._build_sub_placements(payload, origin)
            if clone_placements is None:
                return {"error": sub_err}
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
            raw_selection=payload["raw_selection"],
            extract_fn=extract_template_from_selection,
            origin=origin,
            clone_placements=clone_placements)

    def _compute_extract_origin(self, payload: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
        """Worker thread: the ONE origin for the whole Extract, derived from the
        FULL (pre-Sub-placements-exclusion) selection (`full_raw_items`) via the
        same `_find_origin` the extractor uses — then shared by the flat
        geometry (`run_extract_to_file(origin=...)` -> extract_fn) and the
        Sub-placement xy (`_build_sub_placements`). Fixes the live bug
        2026-08-25: origin used to be derived from the already-trimmed list, so
        an anchor/origin component that gets excluded as a Sub-placement
        vanished -> "--origin-by-component-role ... not found in selection".
        Returns (origin, None) or (None, error_message)."""
        adapter = payload["board"].adapter
        from kicadstamp.template_selection import _find_origin
        full = payload.get("full_raw_items", payload["raw_items"])
        footprints = [i for i in full if isinstance(i, Footprint)]
        vias = [i for i in full if isinstance(i, Via)]
        if not footprints and not vias:
            # No geometric items to derive an origin from (empty selection, or
            # only non-footprint/via/track items). Leave origin None — extract_fn
            # then handles it exactly as before (its own "nothing to extract"
            # fatal for a genuinely empty selection), and Sub-placements can't
            # be present (they require non-empty board items in the selection).
            return None, None
        origin_kwargs = payload.get("origin_kwargs") or {}
        try:
            origin = _find_origin(
                footprints, vias,
                origin_kwargs.get("origin_via_net"),
                origin_kwargs.get("origin_component_role"),
                origin_kwargs.get("origin_component_pad"),
                adapter)
        except ValidationError as e:
            return None, str(e)
        return origin, None

    @staticmethod
    def _templatize_sheet(value: str, sheet: str | None) -> str:
        """Replace a literal `sheet` path segment (as `/{sheet}/`) with the
        `{sheet}` placeholder, so the extracted nested CellPlacement's nets:/
        params: resolve against whichever sheet this reusable composite gets at
        apply time (see clone_position_calculator.py::_resolve_one_level, the
        same effective-sheet inheritance/injection cf1041a + handoff
        cell_placement_net_sheet_template). A no-op when `sheet` is falsy or
        doesn't appear in `value` as a full path segment (bounded by '/' on
        both sides) — e.g. a global rail like '+3V3' is left untouched."""
        if not sheet:
            return value
        return value.replace(f"/{sheet}/", "/{sheet}/")

    def _build_sub_placements(self, payload: Dict[str, Any], origin) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Worker thread: turn the checked Sub-placements (payload records
        carrying the clone) into CellPlacement-shaped dicts for the new cell's
        clone_placements: section.

        xy is the existing placement's world origin converted into the NEW
        cell's local frame via the SAME absolute->local conversion the
        extractor uses for every other point (then (world - origin) / MM) — the
        new cell is extracted "as-is" at rotation 0, so the existing
        placement's world rotation IS its local rotation (copied verbatim).
        `origin` is the ONE precomputed origin from _compute_extract_origin
        (derived from the FULL pre-exclusion selection) — the same origin the
        flat geometry and extract_fn use, so Sub-placement xy and the flat
        coordinates are guaranteed to share one coordinate system. The world
        origin is computed via clone_world_origin (board_items_resolver) — the
        same anchor + shift composition apply_clone_geometry uses. mirror/layer
        are copied one-to-one; params/nets/net_overrides/refs are carried over
        verbatim when non-empty (the source placement and the new nested
        CellPlacement reference the SAME cell, so their meaning is unchanged).
        sheet/cluster (own-identity for internal role narrowing) are carried
        over the same way (2026-08-26, handoff cell_placement_sheet_cluster).

        Returns (entries, None) or (None, error_message) — the caller returns
        the error verbatim, so a checked Sub-placement that cannot be resolved
        (missing anchor on the board, etc.) aborts the extract instead of
        silently dropping the referenced geometry."""
        adapter = payload["board"].adapter
        from kicadstamp.placement.services.board_items_resolver import clone_world_origin

        try:
            cfg, ctx = load_config(str(payload["placer_path"]))
        except Exception as e:
            return None, _("Failed to load config for Sub-placements: {error}") \
                .format(error=e)
        sheet_names = ctx.sheet_names or {}

        entries: List[Dict[str, Any]] = []
        # Pre-pass (2026-08-26, handoff extract_omit_uniform_sheet): are ALL
        # sub-placements of this extract batch on the SAME, non-None sheet? —
        # the common "reusable single-sheet composite" case (all five dac_buf
        # PI-filters were on Channel_1). When uniform, `sheet:` is omitted on
        # every node so cf1041a's inheritance supplies it per future channel
        # instead of baking THIS extract's channel in forever. Same
        # self-reference guard as the loop below (such a record would be
        # `continue`d anyway, so it must not skew the comparison). Mixed/None
        # values leave the current per-item literal behaviour unchanged — a
        # genuine cross-sheet composite must not be silently collapsed to one
        # inherited value.
        _sub_clones = [rec["clone"] for rec in payload["sub_placements"]
                       if getattr(rec["clone"], "cell", None) != payload.get("name")]
        _sheets = {c.sheet for c in _sub_clones}
        uniform_sheet = len(_sheets) == 1 and None not in _sheets

        for rec in payload["sub_placements"]:
            clone = rec["clone"]
            # Final self-reference guard (defense-in-depth, 2026-08-25): the
            # UI-thread filters above already exclude these, but never write a
            # cell -> itself reference even if a stale record slips through.
            if getattr(clone, "cell", None) == payload.get("name"):
                continue
            try:
                world_origin = clone_world_origin(adapter, cfg, clone,
                                                  sheet_names=sheet_names)
            except ValidationError as e:
                return None, _("Sub-placement {name!r}: {error}") \
                    .format(name=rec["name"], error=e)
            entry: Dict[str, Any] = {
                # Nested name — slug of the existing placement's name (the
                # same Cluster->slug rule the Cell-name quiet auto-fill uses),
                # not the raw effective name.
                "name": self._slugify(rec["name"]),
                "cell": clone.cell,
                "xy": [round((world_origin.x - origin.x) / MM, 4),
                       round((world_origin.y - origin.y) / MM, 4)],
            }
            # Defaults omitted from the written YAML (same style as the rest of
            # the config): rotation/mirror/layer only when they actually deviate.
            if clone.rotation_deg:
                entry["rotation_deg"] = clone.rotation_deg
            if clone.mirror:
                entry["mirror"] = True
            if clone.layer is not None:
                entry["layer"] = clone.layer
            # Own-identity sheet/cluster carried over (2026-08-26, handoff
            # cell_placement_sheet_cluster): the nested CellPlacement needs the
            # SAME (Sheet, Cluster) identity as the source ClonePlacement —
            # otherwise role_narrowing.py's sheet/cluster narrowing steps read
            # None (getattr) and a shared-net role (e.g. +3V3 on a PI-filter)
            # stays ambiguous among identical physical instances. sheet only
            # when set (like layer), cluster when truthy (required on
            # ClonePlacement, so always present in practice). sheet: is
            # additionally OMITTED when every sub-placement of this extract
            # batch is on the same sheet (uniform_sheet, see the pre-pass above).
            if clone.sheet is not None and not uniform_sheet:
                entry["sheet"] = clone.sheet
            if clone.cluster:
                entry["cluster"] = clone.cluster
            # Parametrisation carried over (2026-08-25, handoff
            # sub_placements_lost_params) with the literal `sheet` path segment
            # templatized to `{sheet}` (2026-08-26, handoff
            # cell_placement_net_sheet_template): the source ClonePlacement and
            # the new nested CellPlacement reference the SAME cell, so
            # params/nets keep their meaning — but a reusable composite cloned
            # per channel must NOT carry a hardcoded /Channel_1/ in its nets/
            # params (that would drag Channel_1 parts onto every other channel
            # at apply time). net_overrides/refs stay verbatim (net_overrides
            # is keyed by the already-resolved name — templatizing its keys is
            # out of scope). Written only when non-empty — no `params: {}`
            # noise on plain placements.
            if clone.params:
                entry["params"] = {
                    k: (self._templatize_sheet(v, clone.sheet) if isinstance(v, str) else v)
                    for k, v in clone.params.items()
                }
            if clone.nets:
                entry["nets"] = {k: self._templatize_sheet(v, clone.sheet) for k, v in clone.nets.items()}
            if clone.net_overrides:
                entry["net_overrides"] = dict(clone.net_overrides)
            if clone.refs:
                entry["refs"] = dict(clone.refs)
            entries.append(entry)
        return entries, None

    @staticmethod
    def _summarize_net_from_role(template_dict: Dict[str, Any]) -> Optional[str]:
        """One-line summary of which extracted via/track nets got written as
        net_from_role (instead of a literal/parametrised net) — the ONLY
        surface where this is visible to the user, since
        extract_template_from_selection()'s auto-suggestion (template_
        extraction.py's suggest_net_from_role, plan step 4) otherwise
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

    # ── Re-extract from current board state ───────────────────────────────

    def _set_re_extract_target(self, cell_name: Optional[str], profile_key: Optional[str],
                               profile_entry: Dict[str, Any]) -> None:
        """Record the picked existing profile/Cell as the re-extract target
        and repopulate the placement combo for it."""
        self._re_extract_cell_name = cell_name
        self._re_extract_profile_key = profile_key
        self._re_extract_profile_entry = dict(profile_entry) if profile_entry else {}
        self._refresh_re_extract_placements()

    def _refresh_re_extract_placements(self) -> None:
        """Populate the re-extract placement combo with every clone_placement
        in the include graph whose cell: references the picked re-extract cell
        (profile's name / picked Cell). The button is enabled exactly when a
        concrete placement is chosen. Read-only, on the UI thread — an explicit
        user action (profile/cell click), same load_config cost as
        _registry_uuids()."""
        cell_name = self._re_extract_cell_name
        self.re_extract_placement_combo.blockSignals(True)
        self.re_extract_placement_combo.clear()
        placements: List[Any] = []
        if cell_name and self._root_path is not None:
            try:
                cfg, _ctx = load_config(str(self._root_path))
            except Exception:
                cfg = None
            if cfg is not None:
                placements = [c for c in cfg.clone_placements if c.cell == cell_name]
        for c in placements:
            name = clone_placement_effective_name(c)
            self.re_extract_placement_combo.addItem(name, name)
        self.re_extract_placement_combo.blockSignals(False)
        self.re_extract_button.setEnabled(self.re_extract_placement_combo.count() > 0)

    def _resolve_re_extract_target(self, cell_name: str, profile_entry: Dict[str, Any]) -> Optional[Path]:
        """The cells file re-extract writes back into — the profile's stored
        output: (written by run_extract_to_file as display_path(), i.e.
        relative to the project root when possible), or the dock's current
        target path when re-extracting a bare Cell with no profile."""
        output = profile_entry.get("output")
        if output:
            from kicadstamp.config_writer import PROJECT_ROOT
            p = Path(output)
            return p if p.is_absolute() else PROJECT_ROOT / p
        if self._target_path is not None:
            return self._target_path
        self._show_message(_("Set the project root first."), _ERROR_STYLE)
        return None

    def _on_re_extract(self) -> None:
        """Re-extract button — collect the picked profile/cell + placement on
        the UI thread, then run the heavy work (load_config + resolver + file
        write) on a worker thread, same split as the normal Extract path."""
        board = self._connection.board
        if board is None or getattr(board, "adapter", None) is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return
        cell_name = self._re_extract_cell_name
        placement_name = self.re_extract_placement_combo.currentData()
        if not cell_name or not placement_name:
            self._show_message(_("Pick an existing profile/Cell and a placement first."), _ERROR_STYLE)
            return
        if self._root_path is None or self._placer_path is None:
            self._show_message(_("Set the project root first."), _ERROR_STYLE)
            return
        target_path = self._resolve_re_extract_target(cell_name, self._re_extract_profile_entry)
        if target_path is None:
            return
        payload = {
            "root_path": self._root_path,
            "placer_path": self._placer_path,
            "board": board,
            "cell_name": cell_name,
            "placement_name": placement_name,
            "profile_key": self._re_extract_profile_key,
            "profile_entry": self._re_extract_profile_entry,
            "target_path": target_path,
        }
        self._start_re_extract_op(payload)

    def _run_re_extract(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC + file writes only — never touches a
        widget. Resolves the chosen placement's live items and re-runs the
        SAME run_extract_to_file transform the normal path uses, with
        items= from the resolver instead of the GUI selection."""
        from kicadstamp.placement.services.board_items_resolver import resolve_clone_board_items
        from kicadstamp.registry import (registry_path_for_config,
                                         track_registry_path_for_config)

        adapter = payload["board"].adapter
        try:
            cfg, ctx = load_config(str(payload["root_path"]))
        except Exception as e:
            return {"error": _("Failed to load config: {error}").format(error=e)}

        placement_name = payload["placement_name"]
        clone = next((c for c in cfg.clone_placements
                      if clone_placement_effective_name(c) == placement_name), None)
        if clone is None:
            return {"error": _("Placement {name!r} not found in the config graph.")
                    .format(name=placement_name)}

        registry_path = ctx.registry_path or registry_path_for_config(str(payload["placer_path"]))
        track_registry_path = (ctx.track_registry_path
                               or track_registry_path_for_config(str(payload["placer_path"])))
        try:
            items = resolve_clone_board_items(
                adapter, cfg, ctx, clone,
                registry_path=registry_path, track_registry_path=track_registry_path)
        except ValidationError as e:
            return {"error": str(e)}
        if not items:
            return {"error": _("nothing found on the board for this placement — "
                               "has it been placed yet?")}

        entry = payload["profile_entry"] or {}
        origin_kwargs: Dict[str, str] = {}
        for profile_key, value in entry.items():
            if profile_key.startswith("origin_by_") and value:
                origin_kwargs["origin_" + profile_key[len("origin_by_"):]] = value

        return run_extract_to_file(
            adapter,
            name=payload["cell_name"],
            params=entry.get("params") or {},
            items=items,
            net_template_role=entry.get("net_template_role") or {},
            rule_nets=set(entry.get("rule_nets") or []),
            origin_kwargs=origin_kwargs,
            target_path=payload["target_path"],
            save_profile=False,
            profile_key=payload.get("profile_key") or payload["cell_name"],
            profile_path=None,
            placer_path=payload["placer_path"],
            raw_selection=bool(entry.get("raw_selection", False)),
            extract_fn=extract_template_from_selection)

    def _start_re_extract_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._connection, (self.re_extract_button,),
            self._run_re_extract, self._finish_extract, self._on_extract_failed, payload)

