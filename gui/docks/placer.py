# gui/docks/placer.py
"""
PlacerDock — pick a Cell + a Cluster name, an origin (absolute point /
anchor ref-or-role+pad / named Point), rotation, layer/mirror, and the net
params a by-nets placement needs — then Redraw (place it for real on the
live board, see it, adjust, Redraw again) and Save (write the
clone_placement into the Placer file). Requested live 2026-08-01:
"Суть пласера в том, чтобы выбрать кластер задать опорную точку...
Поменял координату, нажал перерисовать, оно переехало. Посмотрел,
утвердил."

Cluster tagging: ClonePlacement itself has NO output "Cluster" field —
checked directly against the pipeline (kicadstamp/placement/,
apply_pipeline.py): Cluster is only ever READ during apply (to narrow
anchor/role search), never written. The only place Cluster gets written
anywhere in this codebase is KiCadBoardAdapter.set_field_values_bulk() —
placement and tagging were always two separate manual steps. Redraw here
closes that gap itself: after a successful ApplyPipeline run, it
independently replays the SAME item through a throwaway PlacementPlanner
(plan_item() is pure computation, doesn't move anything — the real move
already happened via the pipeline) to recover which refs this placement
actually touched, and tags Cluster=<name> on them via the same
set_field_values_bulk() call.

Redraw uses the REAL Placer file's full config (load_config), not a
synthetic single-placement one — critical for
PlacementRegistry.reconcile()'s known_anchor_ids protection
(kicadstamp/registry.py): built from a config missing every OTHER
clone_placement already on the board, a redraw preview would read as
"everything else is gone" and PRUNE their vias/tracks. Loading the real
file and only NARROWING execution via ApplyPipeline's own `only=` keeps
everyone else protected while still previewing just this one. The in-
progress (possibly unsaved) form state replaces-by-name whatever's
already in cfg.clone_placements for this name, so Redraw always previews
the CURRENT form, not last Save's.

config_path passed to ApplyPipeline is always the Placer file's own path
(even when preloaded_cfg is given) — it's still used to derive
registry_path/track_registry_path (registry_path_for_config) when the
config itself doesn't set them explicitly, which is what makes repeated
Redraws idempotent (a second click recognizes vias/tracks the first
click already created, via the SAME registry file a real
`kicadstamp_cli.py apply` on this file would also use).

Cell picking moved out to ConfigTreeDock (gui/docks/config_tree.py, its
Cells category — replaced the earlier standalone CellListDock 2026-08-03,
see handoff_2026_08_03_gui_tree_risks_resolved.md), tabified with the
Components tree — feeds set_selected_cell() here. Cluster name
similarly follows RoleClusterTreeDock's cluster_picked signal when a
Cluster GROUP node is clicked there (set_cluster_name()) — both requested
live
2026-08-01 ("где выбирать cell? ...к дереву компонент надо добавить
табик со списком cell", "раз уж у нас есть список Cluster то при выборе
кластера надо сразу автоматически заполнять поле кластер"). Anchor
Role/Cluster are editable QComboBoxes populated from the live board
snapshot (refresh_known_roles(), called by MainWindow at the same ~2s
poll cadence as the rest of the docks) — "если выбираем
по роли то надо и поле anchor cluster да и лист... якорить, так уж по
полной". Ref stays a plain, unassisted text field — this project
deliberately avoids relying on refdes elsewhere (Role survives
re-annotation, Ref doesn't), the user confirmed live it's fine to leave
as a minor/deprioritized option now that it exists, not worth the same
treatment.

Scope NOT covered by this first version (kept out deliberately, not by
oversight): anchor_sheet narrowing, refs: explicit role->ref override,
by_selection mode. All still reachable by hand-editing the saved YAML; add
UI for them if they turn out to be needed often.

anchor_point IS autocompleted (closed 2026-08-06, Denis: "думаю имена
Points тоже надо делать выпадашкой с именами") — set_root_path(), wired to
ConfigTreeDock's root_file_changed same as RuleDock's own (gui/docks/
rules.py), sources the combo from the WHOLE include graph via
collect_all_point_names(), not just this dock's own target file.

load_placement() (reverse of _build_entry_dict) lets ConfigTreeDock's Clone
placements category (gui/docks/config_tree.py — replaced the earlier
standalone PlacerListDock 2026-08-03) re-open an already-saved
clone_placement for editing/Redraw — requested live 2026-08-02 alongside a
"Placements" tab next to the Components tree/Cells list ("таб пласеров
(там где дерево
компонент и экстракторов)"), same "pick from a list you already browse"
pattern as Cell/Cluster picking.

Params comboboxes (placeholder -> literal net) are populated from the
live board's actual net names (refresh_known_nets(), same ~2s poll
cadence as refresh_known_roles()) and filter-as-you-type via
_configure_searchable() — plain literal text is still accepted (editable
combo, NoInsert policy), this is a picker, not a whitelist: "сети стоит
сделать выпадашками (комбобоксами с поиском)" (2026-08-02).

Source: Cell / Role / Cluster (added 2026-08-06, closing a real workflow
complaint — Denis: "путь потрясающе длинный: создать экстрактор, извлечь
шаблон, сделать cell и только потом, placement") — see
_on_cell_mode_changed's own docstring for the backend mechanisms this
surfaces (ClonePlacement.role/cluster, both already existed in the backend
but had no GUI path) and why Cluster exists as a THIRD option rather than
Role alone (same-day pushback, Denis: "Условие уникальности у нас касается
кластера, а не роли").
"""
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from kipy.errors import ApiError
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                              QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                              QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                              QTabWidget, QVBoxLayout, QWidget)

from kicadstamp.apply_pipeline import ApplyPipeline
from kicadstamp.config import Config, RuntimeContext, load_clone_placement, load_config
from kicadstamp.constants import CLUSTER_FIELD_NAME
from kicadstamp.exceptions import PlacerError, ValidationError
from kicadstamp.i18n import _
from kicadstamp.placement.planner import PlacementPlanner

from .. import yaml_io
from ..ui_utils import busy
from ..worker import start_long_op
from ._anchor_origin import AnchorOriginWidget
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE, configure_searchable, display_path,
                      set_combo_items, show_message, upsert_clone_placement)
from .rename import collect_all_point_names

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class _KeyValueTableEditor(QWidget):
    """One small dict[str, str]-editing block — read-only table + a
    key/value row with Add/update + Remove selected, same "table below,
    editing goes through the row" discipline as RuleDock's spokes editor/
    CellDock's per-tab editors, just for a plain string->string mapping
    instead of a richer dataclass. Used three times in PlacerDock's Nets
    tab (2026-08-06, Denis: "в пласере точно надо... таблицей (может быть
    даже с изменяемыми полями)") — ClonePlacement.nets/net_overrides/refs
    had NO GUI at all before this (explicitly flagged "Scope NOT covered"
    in this module's own docstring); one reusable class instead of
    tripling the same table+row+Add/Remove wiring three times over.
    Key/value combos are searchable and editable (configure_searchable) —
    set_key_choices()/set_value_choices() feed them known roles/nets, same
    picker-not-whitelist convention as every other combo here."""

    def __init__(self, key_label: str, value_label: str,
                key_placeholder: str = "", value_placeholder: str = "",
                parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._data: Dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([key_label, value_label])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.key_edit = QComboBox()
        configure_searchable(self.key_edit)
        self.key_edit.lineEdit().setPlaceholderText(key_placeholder)
        row.addWidget(self.key_edit)
        self.value_edit = QComboBox()
        configure_searchable(self.value_edit)
        self.value_edit.lineEdit().setPlaceholderText(value_placeholder)
        row.addWidget(self.value_edit)
        self.add_button = QPushButton(_("Add / update"))
        self.add_button.clicked.connect(self._on_add_or_update)
        row.addWidget(self.add_button)
        self.remove_button = QPushButton(_("Remove selected"))
        self.remove_button.clicked.connect(self._on_remove)
        row.addWidget(self.remove_button)
        layout.addLayout(row)

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.key_edit.setCurrentText(self.table.item(rows[0].row(), 0).text())
        self.value_edit.setCurrentText(self.table.item(rows[0].row(), 1).text())

    def _on_add_or_update(self) -> None:
        key = self.key_edit.currentText().strip()
        value = self.value_edit.currentText().strip()
        if not key or not value:
            return
        self._data[key] = value
        self._refresh()

    def _on_remove(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        key = self.table.item(rows[0].row(), 0).text()
        self._data.pop(key, None)
        self._refresh()
        self.key_edit.setCurrentText("")
        self.value_edit.setCurrentText("")

    def _refresh(self) -> None:
        self.table.setRowCount(len(self._data))
        for row, (key, value) in enumerate(sorted(self._data.items())):
            self.table.setItem(row, 0, QTableWidgetItem(key))
            self.table.setItem(row, 1, QTableWidgetItem(value))

    def to_dict(self) -> Dict[str, str]:
        return dict(self._data)

    def load_dict(self, data: Optional[Dict[str, str]]) -> None:
        self._data = dict(data or {})
        self._refresh()
        self.key_edit.setCurrentText("")
        self.value_edit.setCurrentText("")

    def set_key_choices(self, items: List[str]) -> None:
        set_combo_items(self.key_edit, items)

    def set_value_choices(self, items: List[str]) -> None:
        set_combo_items(self.value_edit, items)


class PlacerDock(QWidget):
    """A page inside DetailDock's stack (gui/docks/detail_panel.py) — used
    to be its own QDockWidget, merged 2026-08-03 (see ExtractDock's module
    docstring note for the same change). Layout builds directly on self
    instead of a wrapped QDockWidget-owned container; everything else is
    unchanged."""

    # Fired after a successful Save — ConfigTreeDock listens to refresh its
    # Clone placements category (see gui/dock_hub.py).
    saved = pyqtSignal()

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._cells_path: Optional[Path] = None
        self._placer_path: Optional[Path] = None
        self._root_path: Optional[Path] = None
        self._selected_cell: Optional[str] = None
        self._param_edits: Dict[str, QComboBox] = {}
        self._known_nets: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Tabbed (2026-08-06, Denis: "в пласере точно надо табом. Он может
        # быть длинный!") — same "a stacked QVBoxLayout's minimum height is
        # the SUM of every section's own" fix Extract/Root/Rules/Cells
        # already got. Buttons/message stay OUTSIDE the tabs — they act on
        # the whole placement, not one tab.
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        source_page = QWidget()
        source_page_layout = QVBoxLayout(source_page)
        source_form = QFormLayout()
        self.cell_mode_combo = QComboBox()
        self.cell_mode_combo.addItems([_("Cell"), _("Role (single component, no cell)"),
                                       _("Cluster (existing tag, single component)")])
        self.cell_mode_combo.currentIndexChanged.connect(self._on_cell_mode_changed)
        source_form.addRow(_("Source:"), self.cell_mode_combo)
        source_page_layout.addLayout(source_form)

        self._cell_row = QWidget()
        cell_form = QFormLayout(self._cell_row)
        cell_form.setContentsMargins(0, 0, 0, 0)
        self.cell_combo = QComboBox()
        # Deliberately NOT configure_searchable() (2026-08-06, live freeze in
        # CellDock's own anchor_role_combo taught this: an editable combo +
        # QCompleter on a field whose value space is a CLOSED SET — must
        # match an existing cells: key — is both a plausible freeze risk and
        # semantically wrong; see CellDock's own anchor_role_combo for the
        # same fix). Cell picking still also works from the Config tree's
        # Cells category (set_selected_cell) — this combo is a second,
        # faster way to do the exact same thing in place, requested live
        # 2026-08-06 (Denis: "в пласере давай сделаем имя целла по
        # выпадающему комбо-боксу... не удобно" ходить в дерево конфига
        # за каждым пиком).
        self.cell_combo.setPlaceholderText(_("pick a cell"))
        self.cell_combo.currentTextChanged.connect(self.set_selected_cell)
        cell_form.addRow(_("Cell:"), self.cell_combo)
        source_page_layout.addWidget(self._cell_row)

        self._role_only_row = QWidget()
        role_only_form = QFormLayout(self._role_only_row)
        role_only_form.setContentsMargins(0, 0, 0, 0)
        self.place_role_edit = QComboBox()
        configure_searchable(self.place_role_edit)
        role_only_form.addRow(_("Role:"), self.place_role_edit)
        source_page_layout.addWidget(self._role_only_row)

        self._cluster_only_row = QWidget()
        cluster_only_form = QFormLayout(self._cluster_only_row)
        cluster_only_form.setContentsMargins(0, 0, 0, 0)
        self.place_cluster_edit = QComboBox()
        configure_searchable(self.place_cluster_edit)
        # Deliberately NOT labelled "Cluster:" — that label is already taken
        # by self.cluster_edit below (the placement's own NAME, which is
        # what a successful Redraw tags components Cluster=<name> WITH —
        # see module docstring's Cluster-tagging note). This field is the
        # opposite direction: an ALREADY-EXISTING Cluster tag to search FOR.
        cluster_only_form.addRow(_("Existing Cluster:"), self.place_cluster_edit)
        source_page_layout.addWidget(self._cluster_only_row)

        self._name_row = QWidget()
        form = QFormLayout(self._name_row)
        form.setContentsMargins(0, 0, 0, 0)
        self.cluster_edit = QComboBox()
        configure_searchable(self.cluster_edit)
        self.cluster_edit.lineEdit().setPlaceholderText(_("Cluster / clone_placement name"))
        form.addRow(_("Cluster:"), self.cluster_edit)
        source_page_layout.addWidget(self._name_row)
        source_page_layout.addStretch(1)
        self._tabs.addTab(source_page, _("Source"))

        # Nets/Net overrides/Refs tabs (2026-08-06, split into three sibling
        # tabs same day they were introduced — Denis, live: stacked as
        # sections of one "Nets" page, all four (Params+Nets+Net overrides+
        # Refs) at once didn't fit the screen. Params already existed
        # (cell-driven, auto-discovered placeholders); nets:/net_overrides:/
        # refs: had NO GUI at all before this (see module docstring +
        # _KeyValueTableEditor above) — all four only apply to Cell mode's
        # by-nets role resolution, hidden for Role/Cluster mode same as
        # Params already was. Params stays paired with Nets (role -> literal
        # net) rather than getting its own tab — both feed the same by-nets
        # resolution step and Denis explicitly liked that pairing as-is
        # ("отличное решение"); Net overrides and Refs are separate/rarer
        # enough to earn their own tabs instead of competing for the same
        # vertical space.
        nets_page = QWidget()
        nets_page_layout = QVBoxLayout(nets_page)
        self._params_label = QLabel(_("Params (placeholder -> literal net, for by-nets role resolution):"))
        nets_page_layout.addWidget(self._params_label)
        self._params_container = QWidget()
        self._params_layout = QGridLayout(self._params_container)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        nets_page_layout.addWidget(self._params_container)
        nets_page_layout.addWidget(QLabel(_("Nets (role -> literal net, priority over the cell's own net_template):")))
        self.nets_table = _KeyValueTableEditor(_("Role"), _("Net"), _("ROLE"), _("net name"))
        nets_page_layout.addWidget(self.nets_table)
        self._nets_tab_index = self._tabs.addTab(nets_page, _("Nets"))

        net_overrides_page = QWidget()
        net_overrides_page_layout = QVBoxLayout(net_overrides_page)
        net_overrides_page_layout.addWidget(
            QLabel(_("Net overrides (resolved net -> final override):")))
        self.net_overrides_table = _KeyValueTableEditor(
            _("Resolved net"), _("Override"), _("resolved net name"), _("override net name"))
        net_overrides_page_layout.addWidget(self.net_overrides_table)
        self._net_overrides_tab_index = self._tabs.addTab(net_overrides_page, _("Net overrides"))

        refs_page = QWidget()
        refs_page_layout = QVBoxLayout(refs_page)
        refs_page_layout.addWidget(
            QLabel(_("Refs (role -> explicit ref, bypasses search entirely — last resort):")))
        self.refs_table = _KeyValueTableEditor(_("Role"), _("Ref"), _("ROLE"), _("e.g. C12"))
        refs_page_layout.addWidget(self.refs_table)
        self._refs_tab_index = self._tabs.addTab(refs_page, _("Refs"))

        origin_page = QWidget()
        origin_page_layout = QVBoxLayout(origin_page)
        # shift=True here even though ClonePlacement has no separate shift
        # fields of its own — Anchor/Point mode reuse entry["xy"] itself to
        # carry the shift (see _build_entry_dict below), a ClonePlacement-
        # only quirk the shared widget deliberately stays ignorant of (see
        # gui/docks/_anchor_origin.py's module docstring).
        self.origin_widget = AnchorOriginWidget(
            modes=["xy", "anchor", "point"], anchor_fields=["pad", "cluster"], shift=True)
        origin_page_layout.addWidget(self.origin_widget)
        # Aliases onto the shared widget's own sub-widgets — kept so
        # existing tests/call sites that poke fields directly keep working.
        self.origin_mode_combo = self.origin_widget.origin_mode_combo
        self.x_edit = self.origin_widget.x_edit
        self.y_edit = self.origin_widget.y_edit
        self.anchor_ref_edit = self.origin_widget.anchor_ref_edit
        self.anchor_role_edit = self.origin_widget.anchor_role_edit
        self.anchor_pad_edit = self.origin_widget.anchor_pad_edit
        self.anchor_cluster_edit = self.origin_widget.anchor_cluster_edit
        self.point_edit = self.origin_widget.point_edit
        self.shift_x_edit = self.origin_widget.shift_x_edit
        self.shift_y_edit = self.origin_widget.shift_y_edit

        extra_form = QFormLayout()
        self.rotation_edit = QLineEdit()
        self.rotation_edit.setPlaceholderText("0")
        extra_form.addRow(_("Rotation (deg):"), self.rotation_edit)
        self.layer_combo = QComboBox()
        self.layer_combo.addItems([_("(cell default)"), "F.Cu", "B.Cu"])
        extra_form.addRow(_("Layer:"), self.layer_combo)
        origin_page_layout.addLayout(extra_form)
        self.mirror_checkbox = QCheckBox(_("Mirror"))
        origin_page_layout.addWidget(self.mirror_checkbox)
        origin_page_layout.addStretch(1)
        self._tabs.addTab(origin_page, _("Origin"))

        button_row = QHBoxLayout()
        self.redraw_button = QPushButton(_("Redraw"))
        self.redraw_button.clicked.connect(self._on_redraw)
        button_row.addWidget(self.redraw_button)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self._on_cell_mode_changed()

    # ── Cell/Role source toggle ──────────────────────────────────────────

    def _on_cell_mode_changed(self) -> None:
        """Cell (default) vs Role vs Cluster (2026-08-06). Role: Denis, "мы
        не можем как-то упростить процедуру размещения отдельных компонент?
        ...путь потрясающе длинный: создать экстрактор, извлечь шаблон,
        сделать cell и только потом, placement" — ClonePlacement.role
        already existed in the backend (config/models.py: "for a
        ONE-COMPONENT placement without a single via/track — creating a
        separate cell file just for one role is cumbersome",
        ClonePositionCalculator synthesises a temporary Cell on the fly,
        cells: is never touched), this toggle is just its first GUI
        surface. Cluster: same day, Denis pushed back on Role specifically
        — "Условие уникальности у нас касается кластера, а не роли...
        ОДНУ деталь надо размещать просто по кластеру. Роль там не при
        делах" — Role is a CATEGORY (many components legitimately share
        one), Cluster is meant to stay unique per instance, so Cluster mode
        resolves by an exact, unconditional Cluster-tag match instead
        (resolve_by_cluster_tag) — no selection/nets ambiguity to narrow at
        all. Params never apply to Role or Cluster mode (a synthetic
        one-component cell has no via/track net fields to template in the
        first place), so the whole Params section hides for either.

        The top "Cluster:" name row (self._name_row/self.cluster_edit) also
        hides in Cluster mode (found live 2026-08-06, Denis: "Зачем нам два
        поля Existing Cluster и Cluster?") — _build_entry_dict() reuses the
        picked Existing-Cluster value as the placement's own name too in
        that mode, so there is nothing left for this row to ask for.

        Nets/Net overrides/Refs (2026-08-06, own tabs as of the same day —
        see their addTab() calls above) hide for the same reason Params
        does: resolve_roles_by_selection (the default resolution unless
        nets:/params: are ALSO set — see clone_uses_selection_mode) never
        reads clone.refs at all, only resolve_roles_by_nets's step 0 does —
        setting Refs without also setting Nets/Params in Role/Cluster mode
        would silently do nothing, so hiding all three tabs together avoids
        that trap. setTabVisible(), not setVisible() on their page widgets —
        each now IS a whole tab page on its own, so hiding just the content
        would leave an empty, confusingly-clickable tab behind instead of
        removing it from the tab bar entirely."""
        mode = self.cell_mode_combo.currentIndex()
        self._cell_row.setVisible(mode == 0)
        self._role_only_row.setVisible(mode == 1)
        self._cluster_only_row.setVisible(mode == 2)
        self._name_row.setVisible(mode != 2)
        self._tabs.setTabVisible(self._nets_tab_index, mode == 0)
        self._tabs.setTabVisible(self._net_overrides_tab_index, mode == 0)
        self._tabs.setTabVisible(self._refs_tab_index, mode == 0)

    # ── Wiring from the Config tree / Components tree ─────────────────────

    def set_cells_file(self, path: Optional[Path]) -> None:
        self._cells_path = path
        self._refresh_cell_choices()

    def _refresh_cell_choices(self) -> None:
        cells = yaml_io.load_data(self._cells_path).get("cells", {}) if self._cells_path else {}
        set_combo_items(self.cell_combo, sorted(cells.keys()))

    def set_placer_file(self, path: Optional[Path]) -> None:
        self._placer_path = path

    def set_root_path(self, path: Optional[Path]) -> None:
        """Wired to ConfigTreeDock's root_file_changed — the Point combo is
        sourced from the WHOLE include graph (a Point routinely lives in a
        different file than the clone_placement referencing it), same
        reasoning/pattern as RuleDock's own set_root_path (gui/docks/rules.py).
        Closes the "anchor_point Point-name autocomplete" gap this dock's own
        module docstring had deliberately deferred until now (2026-08-06,
        Denis: "думаю имена Points тоже надо делать выпадашкой с именами")."""
        self._root_path = path
        self._refresh_point_names()

    def _refresh_point_names(self) -> None:
        names = collect_all_point_names(self._root_path) if self._root_path is not None else []
        self.origin_widget.set_point_names(names)

    def set_selected_cell(self, name: str) -> None:
        """Shared entry point for picking a Cell — called both by
        ConfigTreeDock's Cells category (see gui/docks/config_tree.py) when
        a Cell is clicked there (Cell picking used to live inside this dock,
        but the user expected it alongside the Components tree instead,
        2026-08-01: "где выбирать cell? ...к дереву компонент надо
        добавить табик со списком cell") AND by cell_combo's own
        currentTextChanged (2026-08-06: a second, in-place way to pick the
        same thing — see cell_combo's own comment). blockSignals around the
        combo update either way: called externally, it must reflect the new
        text into the combo without re-entering this same method through
        its own signal; called from the combo itself, the text already
        matches, so this is a no-op — either way, no double rebuild."""
        if not name:
            return
        self._selected_cell = name
        self._refresh_cell_choices()
        self.cell_combo.blockSignals(True)
        if self.cell_combo.findText(name) < 0:
            self.cell_combo.addItem(name)
        self.cell_combo.setCurrentText(name)
        self.cell_combo.blockSignals(False)
        self._rebuild_param_rows()
        self._rebuild_cell_role_choices()

    def set_cluster_name(self, name: str) -> None:
        """Called by RoleClusterTreeDock's cluster_picked signal when a
        Cluster group node is clicked there — requested alongside the
        Cell-list
        move (2026-08-01: "раз уж у нас есть список Cluster то при выборе
        кластера надо сразу автоматически заполнять поле кластер")."""
        self.cluster_edit.setCurrentText(name)

    def refresh_known_roles(self, snapshot) -> None:
        """Populates the Cluster/anchor Role/anchor Cluster combos with
        distinct values already used on the board — "если выбираем по
        роли то надо и поле anchor cluster да и лист" (2026-08-01);
        Cluster itself made a searchable dropdown too, same reasoning,
        2026-08-04 ("а мы можем сделать кластер в пласере выпадающим?").
        Called by MainWindow at the same ~2s full-poll cadence as the rest
        of the docks (not the 400ms selection-watch tick — the known-value
        list barely changes tick to tick). `snapshot` is the cached
        BoardConnection.snapshot the caller already built — this used to
        call board.select() itself, a second full snapshot build per
        refresh (1.2 in techdocs/handoff/)."""
        roles = sorted({s.role for s in snapshot if s.role})
        clusters = sorted({s.cluster for s in snapshot if s.cluster})
        set_combo_items(self.cluster_edit, clusters)
        self.origin_widget.set_known_roles(roles, clusters)
        set_combo_items(self.place_role_edit, roles)
        set_combo_items(self.place_cluster_edit, clusters)

    def refresh_known_nets(self, board) -> None:
        """Populates the Params comboboxes (placeholder -> literal net) with
        the live board's actual net names — "сети стоит сделать выпадашками
        (комбобоксами с поиском)" (2026-08-02). Same ~2s poll cadence as
        refresh_known_roles(); cached on self so newly-discovered param
        rows (_rebuild_param_rows, triggered by picking a different Cell)
        don't have to wait for the next poll tick to be populated. Nets/Net
        overrides' own value combos (2026-08-06) share the same list."""
        self._known_nets = sorted({n.name for n in board.adapter.get_all_nets() if n.name})
        for combo in self._param_edits.values():
            set_combo_items(combo, self._known_nets)
        self.nets_table.set_value_choices(self._known_nets)
        self.net_overrides_table.set_key_choices(self._known_nets)
        self.net_overrides_table.set_value_choices(self._known_nets)

    def _rebuild_param_rows(self) -> None:
        cell_data = yaml_io.load_data(self._cells_path).get("cells", {}).get(self._selected_cell, {})
        placeholders = sorted(self._discover_placeholders(cell_data))
        previous = {name: edit.currentText() for name, edit in self._param_edits.items()}

        while self._params_layout.count():
            item = self._params_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._param_edits = {}
        for row, name in enumerate(placeholders):
            self._params_layout.addWidget(QLabel(name), row, 0)
            edit = QComboBox()
            configure_searchable(edit)
            edit.lineEdit().setPlaceholderText(_("literal net for {{{name}}}").format(name=name))
            edit.addItems(self._known_nets)
            edit.setCurrentText(previous.get(name, ""))
            self._params_layout.addWidget(edit, row, 1)
            self._param_edits[name] = edit

    def _rebuild_cell_role_choices(self) -> None:
        """Nets/Refs' own Role key choices — scoped to the PICKED CELL's own
        components: roles, not every role on the live board (found live
        2026-08-06, Denis: "зачем в выпадашках ВСЕ доступные на плате
        роли? Нас же интересуют только роли относящиеся к Pi_Filter_p5v?"
        — right: nets:/refs: are only ever consulted for a role that's
        actually one of cell.components (see resolve_roles_by_nets), a
        board-wide role list was misleadingly broad. Same "scope to the
        owning cell, not the whole board" fix as CellDock's own
        anchor_role_combo (2026-08-06)."""
        cell_data = yaml_io.load_data(self._cells_path).get("cells", {}).get(self._selected_cell, {})
        roles = sorted({c.get("role") for c in cell_data.get("components", []) if c.get("role")})
        self.nets_table.set_key_choices(roles)
        self.refs_table.set_key_choices(roles)

    @staticmethod
    def _discover_placeholders(node: Any) -> set:
        found = set()
        if isinstance(node, dict):
            for value in node.values():
                found |= PlacerDock._discover_placeholders(value)
        elif isinstance(node, list):
            for value in node:
                found |= PlacerDock._discover_placeholders(value)
        elif isinstance(node, str):
            found |= set(_PLACEHOLDER_RE.findall(node))
        return found

    # ── Message helper (same shape as ExtractDock's) ────────────────────────

    def _show_message(self, text: str, style: str = "") -> None:
        show_message(self.message_label, text, style, logger)

    # ── Building the clone_placement dict (shared by Redraw and Save) ──────

    def _parse_float(self, edit: QLineEdit, label: str, default: Optional[float] = None) -> Optional[float]:
        text = edit.text().strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            self._show_message(_("{label}: {text!r} is not a number.").format(label=label, text=text), _ERROR_STYLE)
            return None

    def _build_entry_dict(self) -> Optional[Dict[str, Any]]:
        source_mode = self.cell_mode_combo.currentIndex()
        is_role_mode = source_mode == 1
        is_cluster_mode = source_mode == 2

        if is_cluster_mode:
            # No separate name field here (2026-08-06, found live — Denis:
            # "Зачем нам два поля Existing Cluster и Cluster?") — Cluster is
            # meant to already be unique per instance, and it's the exact
            # value Redraw re-tags the component with afterwards anyway,
            # so a second, independently-typed name risks silently
            # retagging the component to something else. self._name_row
            # (self.cluster_edit) is hidden in this mode — see
            # _on_cell_mode_changed.
            cluster = self.place_cluster_edit.currentText().strip()
            if not cluster:
                self._show_message(_("Pick an existing Cluster first."), _ERROR_STYLE)
                return None
            entry: Dict[str, Any] = {"name": cluster, "cluster": cluster}
        else:
            name = self.cluster_edit.currentText().strip()
            if not name:
                self._show_message(_("Cluster name is required."), _ERROR_STYLE)
                return None
            if is_role_mode:
                role = self.place_role_edit.currentText().strip()
                if not role:
                    self._show_message(_("Pick a Role first."), _ERROR_STYLE)
                    return None
                entry: Dict[str, Any] = {"name": name, "role": role}
            else:
                if not self._selected_cell:
                    self._show_message(_("Pick a Cell first."), _ERROR_STYLE)
                    return None
                entry: Dict[str, Any] = {"name": name, "cell": self._selected_cell}

        origin_fields, err = self.origin_widget.build()
        if err:
            self._show_message(err, _ERROR_STYLE)
            return None
        mode = origin_fields["mode"]
        if mode == "xy":
            entry["xy"] = [origin_fields["x"], origin_fields["y"]]
        else:
            # ClonePlacement has no separate shift field — Anchor/Point mode
            # reuse xy: itself to carry the shift (see _anchor_origin.py's
            # module docstring on why the shared widget doesn't know this).
            entry["xy"] = [origin_fields["shift_x"], origin_fields["shift_y"]]
            if mode == "anchor":
                if "ref" in origin_fields:
                    entry["anchor_ref"] = origin_fields["ref"]
                else:
                    entry["anchor_role"] = origin_fields["role"]
                if "pad" in origin_fields:
                    entry["anchor_pad"] = origin_fields["pad"]
                if "cluster" in origin_fields:
                    entry["anchor_cluster"] = origin_fields["cluster"]
            else:  # Point
                entry["anchor_point"] = origin_fields["point"]

        rotation = self._parse_float(self.rotation_edit, _("Rotation"), default=0.0)
        if rotation is None:
            return None
        if rotation:
            entry["rotation_deg"] = rotation

        layer_idx = self.layer_combo.currentIndex()
        if layer_idx == 1:
            entry["layer"] = "F.Cu"
        elif layer_idx == 2:
            entry["layer"] = "B.Cu"

        if self.mirror_checkbox.isChecked():
            entry["mirror"] = True

        if not (is_role_mode or is_cluster_mode):
            params = {name: edit.currentText().strip() for name, edit in self._param_edits.items()
                      if edit.currentText().strip()}
            if params:
                entry["params"] = params
            nets = self.nets_table.to_dict()
            if nets:
                entry["nets"] = nets
            net_overrides = self.net_overrides_table.to_dict()
            if net_overrides:
                entry["net_overrides"] = net_overrides
            refs = self.refs_table.to_dict()
            if refs:
                entry["refs"] = refs

        return entry

    # ── Redraw ────────────────────────────────────────────────────────────

    def _on_redraw(self) -> None:
        """Redraw button handler — form collection (validation + widget
        reads) runs on the UI thread; the ApplyPipeline run + cluster
        tagging run on a worker thread (see gui/worker.py). The pipeline
        opens its OWN kipy socket, but we still gate the shared connection
        (long_op_active) for the duration so the GUI's polling timers don't
        fire a second concurrent socket request — the same "one socket in
        flight" the old blocked-UI behaviour guaranteed implicitly."""
        self._show_message("")
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        self._start_redraw_op(payload)

    def _collect_redraw_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: read every widget + run every validation that can
        reject the request up front (including loading + mutating the Placer
        config). Returns a plain-data payload for the worker, or None after
        showing the error."""
        entry = self._build_entry_dict()
        if entry is None:
            return None
        if self._placer_path is None:
            self._show_message(_("Pick a Placer file in Files first."), _ERROR_STYLE)
            return None
        # Role mode needs no cells.yaml at all — ClonePositionCalculator
        # synthesises its one-component Cell on the fly (see
        # _on_cell_mode_changed's docstring), cells: is never read.
        if "cell" in entry and self._cells_path is None:
            self._show_message(_("Pick a Cells file in Files first."), _ERROR_STYLE)
            return None

        try:
            clone_placement = load_clone_placement(entry)
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return None

        try:
            if self._placer_path.exists():
                cfg, ctx = load_config(str(self._placer_path))
            else:
                cfg, ctx = Config(), RuntimeContext()
        except (ValidationError, OSError, yaml.YAMLError) as e:
            self._show_message(_("Failed to load Placer file: {error}").format(error=e), _ERROR_STYLE)
            return None

        if "cell" in entry and entry["cell"] not in cfg.cells:
            self._show_message(
                _("Cell {cell!r} isn't reachable from the Placer file's include: — "
                  "extract/save it and make sure include: is wired (see Extract).")
                .format(cell=entry["cell"]), _ERROR_STYLE)
            return None

        # Replace-by-name: previewing an already-saved placement's edits
        # must not create a second copy alongside the saved one.
        cfg.clone_placements = [c for c in cfg.clone_placements if c.name != clone_placement.name]
        cfg.clone_placements.append(clone_placement)

        return {
            "placer_path": self._placer_path,
            "cfg": cfg,
            "ctx": ctx,
            "name": clone_placement.name,
        }

    def _run_redraw(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: ApplyPipeline run + cluster tagging — never touches
        a widget. Returns {"name": ..., "tagged": ...} on success,
        {"error": str} for placement failure, or {"warn": str} when the
        placement itself succeeded but tagging didn't."""
        pipeline = ApplyPipeline(config_path=str(payload["placer_path"]),
                                 preloaded_cfg=payload["cfg"], preloaded_ctx=payload["ctx"],
                                 only=[payload["name"]], dry_run=False)
        try:
            pipeline.run()
        except (PlacerError, ValidationError, ApiError) as e:
            return {"error": _("Placement failed: {error}").format(error=e)}
        except Exception as e:
            logger.exception("Placer redraw failed")
            return {"error": _("Placement failed: {error}").format(error=e)}

        try:
            tagged = self._tag_cluster(pipeline, payload["cfg"], payload["ctx"], payload["name"])
        except Exception as e:
            logger.exception("Cluster tagging after placement failed")
            return {"warn": _("Placed, but tagging Cluster failed: {error}").format(error=e)}

        return {"name": payload["name"], "tagged": tagged}

    def _finish_redraw(self, result: Dict[str, Any]) -> None:
        """UI thread: reflect the worker's result into the message label."""
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        if result.get("warn"):
            self._show_message(result["warn"], _WARN_STYLE)
            return
        self._show_message(
            _("Placed {name!r} ({count} component(s) tagged Cluster={name!r}).")
            .format(name=result["name"], count=result["tagged"]), _SUCCESS_STYLE)

    def _start_redraw_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._main_window.connection, (self.redraw_button, self.save_button),
            self._run_redraw, self._finish_redraw, self._on_redraw_failed, payload)

    def _on_redraw_failed(self, message: str) -> None:
        self._show_message(_("Placement failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_redraw(self) -> None:
        """Synchronous composition of collect + run + finish — the same
        behaviour the async button path would produce, kept for tests and
        any caller that must not return until the redraw is complete."""
        payload = self._collect_redraw_inputs()
        if payload is None:
            return
        result = self._run_redraw(payload)
        self._finish_redraw(result)

    def _tag_cluster(self, pipeline: ApplyPipeline, cfg: Config, ctx: RuntimeContext, name: str) -> int:
        """Recovers which refs this specific clone_placement touched (see
        module docstring) and tags them Cluster=name. Returns how many
        got tagged (0 if the item couldn't be found — shouldn't happen
        given `only=[name]` just ran successfully, but not fatal either
        way, since the board is already correctly placed regardless)."""
        my_item = next((it for it in pipeline.items
                         if it.kind == 'clone' and it.obj.name == name), None)
        if my_item is None:
            return 0

        planner = PlacementPlanner(pipeline.adapter, cfg, sheet_names=ctx.sheet_names if ctx else {})
        planner.begin_planning()
        refs: List[str] = []
        for item in pipeline.items:
            moves = planner.plan_item(item)
            if item is my_item:
                refs = [m.ref for m in moves]
                break

        updates = []
        for ref in refs:
            fp = pipeline.adapter.get_footprint(ref)
            if fp is not None:
                updates.append((fp, CLUSTER_FIELD_NAME, name))
        if updates:
            pipeline.adapter.set_field_values_bulk(updates, _("Placer: tag Cluster={name}").format(name=name))
        return len(updates)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        with busy((self.redraw_button, self.save_button)):
            self._do_save()

    def _do_save(self) -> None:
        entry = self._build_entry_dict()
        if entry is None:
            return
        if self._placer_path is None:
            self._show_message(_("Pick a Placer file in Files first."), _ERROR_STYLE)
            return
        try:
            load_clone_placement(entry)  # validate before writing anything
        except ValidationError as e:
            self._show_message(str(e), _ERROR_STYLE)
            return

        try:
            overwritten = self._upsert_clone_placement(self._placer_path, entry)
        except OSError as e:
            self._show_message(_("Write failed: {error}").format(error=e), _ERROR_STYLE)
            return

        self._show_message(
            _("{action} {name!r} in {path}").format(
                action=_("Overwrote") if overwritten else _("Wrote"),
                name=entry["name"], path=display_path(self._placer_path)),
            _SUCCESS_STYLE)
        self.saved.emit()

    # ── Starting a brand new placement (ConfigTreeDock's Add placer) ───────

    def new_placement(self, placer_path: Path) -> None:
        """Resets the form to its initial (blank) state and targets
        placer_path — ConfigTreeDock's "Add placer" context-menu action
        (2026-08-03) opens this form empty rather than writing a raw stub
        straight to YAML, so the existing validated Save path
        (_do_save -> load_clone_placement) is what actually creates the
        entry, same as every other way a placement gets saved."""
        self._placer_path = placer_path
        self._selected_cell = None
        self.cell_combo.setCurrentIndex(-1)
        self.cell_mode_combo.setCurrentIndex(0)
        self.place_role_edit.setCurrentText("")
        self.place_cluster_edit.setCurrentText("")
        self._on_cell_mode_changed()
        self.cluster_edit.setCurrentText("")
        self.origin_widget.clear()
        self.rotation_edit.setText("")
        self.layer_combo.setCurrentIndex(0)
        self.mirror_checkbox.setChecked(False)
        self._rebuild_param_rows()
        self._rebuild_cell_role_choices()
        self.nets_table.load_dict({})
        self.net_overrides_table.load_dict({})
        self.refs_table.load_dict({})
        self._show_message("")

    # ── Loading an already-saved placement back into the form ──────────────

    def load_placement(self, entry: Dict[str, Any]) -> None:
        """Reverse of _build_entry_dict() — called by ConfigTreeDock's Clone
        placements category (via its placement_picked signal) when the
        user clicks an already-saved
        clone_placement in the new "Placements" tab, so it can be edited
        and Redrawn/re-Saved instead of only ever building placements from
        scratch (2026-08-02: "таб пласеров... там где дерево компонент и
        экстракторов")."""
        self._show_message("")
        self.cluster_edit.setCurrentText(str(entry.get("name", "")))
        if "role" in entry:
            self.cell_mode_combo.setCurrentIndex(1)
            self.place_role_edit.setCurrentText(str(entry["role"]))
        elif "cluster" in entry:
            self.cell_mode_combo.setCurrentIndex(2)
            self.place_cluster_edit.setCurrentText(str(entry["cluster"]))
        else:
            self.cell_mode_combo.setCurrentIndex(0)
            if "cell" in entry:
                self.set_selected_cell(entry["cell"])
        self._on_cell_mode_changed()  # setCurrentIndex above is a no-op signal-wise when unchanged

        xy = entry.get("xy") or [0.0, 0.0]
        if "anchor_point" in entry:
            self.origin_widget.load(mode="point", point=str(entry["anchor_point"]),
                                    shift_x=xy[0], shift_y=xy[1])
        elif "anchor_ref" in entry or "anchor_role" in entry:
            self.origin_widget.load(
                mode="anchor", ref=str(entry.get("anchor_ref", "")),
                role=str(entry.get("anchor_role", "")),
                pad=str(entry.get("anchor_pad", "")),
                cluster=str(entry.get("anchor_cluster", "")),
                shift_x=xy[0], shift_y=xy[1])
        else:
            self.origin_widget.load(mode="xy", x=xy[0], y=xy[1])

        self.rotation_edit.setText(str(entry.get("rotation_deg", 0)))
        self.layer_combo.setCurrentIndex({"F.Cu": 1, "B.Cu": 2}.get(entry.get("layer"), 0))
        self.mirror_checkbox.setChecked(bool(entry.get("mirror", False)))

        params = entry.get("params") or {}
        for name, edit in self._param_edits.items():
            edit.setCurrentText(str(params.get(name, "")))
        self.nets_table.load_dict(entry.get("nets"))
        self.net_overrides_table.load_dict(entry.get("net_overrides"))
        self.refs_table.load_dict(entry.get("refs"))

    @staticmethod
    def _upsert_clone_placement(path: Path, entry: Dict[str, Any]) -> bool:
        """Read-merge-write like merge_write()/add_list_entry(), but for
        clone_placements: — a list of dicts matched by their own 'name' key,
        not by list membership: an entry whose name already exists gets
        REPLACED in place (same position), a new name gets appended. Every
        other key in the file (cells:, include:, extract_profiles:, ...) is
        left untouched. Delegates to
        gui/docks/_common.upsert_clone_placement (kept as a thin wrapper
        because the GUI tests call dock._upsert_clone_placement directly)."""
        return upsert_clone_placement(path, entry)
