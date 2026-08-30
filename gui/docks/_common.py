# gui/docks/_common.py
"""Shared utilities for the kicadstamp GUI docks.

Two groups live here:

* read-merge-write config helpers (merge_write / add_list_entry /
  upsert_clone_placement / non_includable_keys / display_path / ...) — these
  are PURE file operations with no Qt dependency, so they moved to
  kicadstamp/config_writer.py in Phase 2 of the gui god-file decomposition
  (see techdocs/handoff/handoff_2026_08_05_architecture_fixes_roadmap.md).
  This module is a thin facade re-exporting them, so every existing importer
  (the docks below and tests/gui/test_dock_common.py) keeps working unchanged.

* Qt widget helpers (set_combo_items / configure_searchable / show_message)
  plus the message style constants — genuinely GUI-only, still defined here:
  one definition instead of each dock declaring its own copy. Since 2026-08-13
  the inline message_label is gone from every dock — show_message only routes
  to the Log dock, and the style constants double as the log-level selectors.
"""
import logging
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QCompleter, QLineEdit

from kicadstamp.config_writer import (
    read_data, write_data, add_include, add_list_entry, disable_include,
    display_path, merge_write, non_includable_keys, upsert_clone_placement,
    upsert_entity, upsert_entity_placement, upsert_list_entry)

from .. import settings

logger = logging.getLogger(__name__)

# Message-label styles — one definition shared by every dock's status
# label, instead of each dock declaring the same three CSS color strings.
ERROR_STYLE = "color: #a00;"
WARN_STYLE = "color: #a60;"
SUCCESS_STYLE = "color: #070;"


def set_combo_items(combo: QComboBox, items: List[str]) -> None:
    """Replace a combo's items while preserving the current text and
    blocking selection signals around the repopulation (blockSignals) —
    so an in-progress typed value survives a refresh instead of being
    wiped, the same reason the tree/bulk-edit docks guard against
    resetting user input."""
    current_text = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    combo.setCurrentText(current_text)
    combo.blockSignals(False)


def configure_searchable(combo: QComboBox) -> None:
    """Turns a plain editable QComboBox into a filter-as-you-type search
    box. Qt's own default completer for an editable combo only matches
    from the start of the string, which isn't enough once there are
    dozens of nets/roles on a real board (2026-08-02: "сети стоит
    сделать выпадашками (комбобоксами с поиском)"). NoInsert keeps this
    a picker, not a whitelist — typed text that isn't in the list is
    still accepted as the field's value, it just doesn't get added as a
    new permanent entry.

    CaseSensitive (2026-08-04, Denis live: typed "C_Out_Bulk" for a new
    Role, fieldstool silently turned it back into the existing "C_OUT_
    BULK") — every actual comparison of these values elsewhere (config
    matching, the schematic-vs-board diff, tree grouping, rename) is a
    plain case-sensitive `==`/dict key, so a case-INsensitive completer
    was the odd one out: on Enter/focus-out, Qt's own combo box logic
    snaps whatever you typed to an existing item's stored casing the
    moment it matches case-insensitively — silently substituting a
    different value than the one you actually typed, before the caller
    ever reads currentText()."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    completer = combo.completer()
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)


def set_mode_pair_enabled(is_polar: bool, cartesian_widgets, polar_widgets) -> None:
    """Common Cartesian/Polar field-toggle shared by every dock that edits a
    position in either mode (_anchor_origin.py::_update_polar_mode,
    coordinate_placer.py::_update_row_mode, rules.py::_update_spoke_mode —
    three near-identical copies consolidated 2026-08-12, Group 3): Cartesian
    fields enabled only in Cartesian mode, polar fields only in Polar.
    Disabled (not hidden) keeps the row/editor layout stable. Either sequence
    may contain None entries (docks that don't build a given field) — skipped.
    Accepts plain widgets or table cell widgets alike (anything with
    setEnabled)."""
    for w in cartesian_widgets:
        if w is not None:
            w.setEnabled(not is_polar)
    for w in polar_widgets:
        if w is not None:
            w.setEnabled(is_polar)


def parse_float_field(edit: QLineEdit) -> Tuple[bool, Optional[float]]:
    """(ok, value) from a QLineEdit's text — blank is a valid None ("not
    set", left for the loader/YAML validation to decide whether it's
    required), only genuinely unparsable text is (False, None). This
    (ok, value) convention replaces the overloaded-None convention the other
    docks used (where None meant both "empty" AND "invalid" — the cause of
    the RulesDock "Polar mode needs both Radius and Angle" clobbering the
    real "not a number" error, see plan Group 2 item 5): callers can now
    tell "empty" from "invalid"."""
    text = edit.text().strip()
    if not text:
        return True, None
    try:
        return True, float(text)
    except ValueError:
        return False, None


def show_message(text: str, style: str = "",
                 log: Optional[logging.Logger] = None) -> None:
    """Mirrors a message into the Log dock (see gui/docks/log_panel.py) at
    the level matching `style` — since 2026-08-13 the docks have NO inline
    message_label at all (Denis: "Нам вообще на плашке не надо выводов
    лога. Пусть всё валится в окошко лога"), so the Log dock is the single
    place every dock status ends up. Requested live 2026-08-01 ("для
    списка ошибок сделать внизу отдельное окошко"); `style` is one of
    ERROR_STYLE/WARN_STYLE/SUCCESS_STYLE ('' -> plain info); the caller's
    logger is passed through so log records keep the source dock's own
    logger name."""
    if not text:
        return
    record_log = log if log is not None else logger
    if style == ERROR_STYLE:
        record_log.error(text)
    elif style == WARN_STYLE:
        record_log.warning(text)
    else:
        record_log.info(text)


def set_file_combo_selection(combo: QComboBox, path: Optional[Path]) -> None:
    """Reflects `path` into `combo`'s current selection without re-firing
    currentIndexChanged (blockSignals) — the CHEAP half of the file-combo
    pair, used both after a full repopulate (refresh_file_combo_choices)
    and whenever a dock's set_target_file/set_cells_file/set_placer_file is
    called directly (e.g. from ConfigTreeDock's click, or before the root/
    include graph is even known yet). Adds `path` as an extra item if the
    combo's current list doesn't have it (root not set yet, or a file
    outside the include graph) — same "still show/select it anyway"
    fallback PlacerDock's cell_combo/set_selected_cell already relies on.

    Consolidated 2026-08-13 from ExtractDock's private copy (see plan
    tree_to_combo_file_pickers) — every dock's file combo now shares ONE
    implementation instead of six near-identical _set_file_combo_selection
    methods.

    Matching is a manual itemData()==path loop, NOT combo.findData(path):
    findData compares through Qt's QVariant machinery, which does not run
    pathlib.Path's Python __eq__ (verified 2026-08-13, diag_combo_finddata_
    path.py: itemData == root is True in Python, findData(root) is -1) — so
    findData would re-add an already-listed path as a duplicate instead of
    selecting it."""
    combo.blockSignals(True)
    if path is None:
        combo.setCurrentIndex(-1)
    else:
        idx = -1
        for i in range(combo.count()):
            if combo.itemData(i) == path:
                idx = i
                break
        if idx < 0:
            combo.addItem(display_path(path), path)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
    combo.blockSignals(False)


def refresh_file_combo_choices(combos: Sequence[QComboBox], root_path: Optional[Path],
                               current_paths: Sequence[Optional[Path]]) -> List[Optional[Path]]:
    """Repopulates every combo in `combos` from every file reachable via
    include: from `root_path` (collect_graph_files — walks the whole graph
    on disk), then reflects each dock's current path back into its own combo
    via set_file_combo_selection. `current_paths` is aligned with `combos`.

    This is the EXPENSIVE half of the file-combo pair (it actually goes to
    disk and parses the entire include graph): call it ONLY from a dock's
    set_root_path(), never from a per-click set_target_file/set_placer_file
    — those fire on EVERY ConfigTreeDock click, and doing this on each one
    would re-parse the whole graph in every dock for no reason (see plan
    tree_to_combo_file_pickers, step 1). `collect_graph_files` is imported
    lazily to keep this module free of a circular import — rename.py imports
    _common at module level, so a top-level `from .rename import
    collect_graph_files` here would break whichever of the two loaded first.

    Returns the CORRECTED current path for each combo, aligned with
    `current_paths`: unchanged if it's still reachable from `root_path`'s
    include graph, else None (2026-08-16 evening — a dock's remembered file
    from the PREVIOUS root must not silently keep being read as if it still
    belonged to the new project; found live — PlacerDock kept reading the
    old project's components file after switching root_path entirely). This
    is exactly where the old behavior went wrong: it reflected the
    PRE-switch path back through set_file_combo_selection, whose "add as an
    extra item even if outside the graph" fallback (meant for direct
    ConfigTreeDock clicks) silently re-added and re-selected the now-stale
    file without ever telling the dock's own path attribute that the
    project changed. Callers MUST assign the return value back onto their
    own path attribute(s) — this function only owns the combo widgets,
    never a dock's own state.
    """
    from .rename import collect_graph_files
    files = collect_graph_files(root_path) if root_path is not None else []
    file_set = set(files)
    items = sorted(((display_path(p), p) for p in files), key=lambda t: t[0])
    for combo in combos:
        combo.blockSignals(True)
        combo.clear()
        for text, path in items:
            combo.addItem(text, path)
        combo.blockSignals(False)
    corrected = [p if (p is not None and p in file_set) else None for p in current_paths]
    for combo, path in zip(combos, corrected):
        set_file_combo_selection(combo, path)
    return corrected


# --- Highlight (active tab / selected tree item) color -------------------

# Custom-mode fallback (and the initial value shown in the Settings tab)
# when gui_state.json has no highlight_color yet.
DEFAULT_HIGHLIGHT_COLOR = "#3daee9"


def highlight_stylesheet_for(selector: str) -> str:
    """Build a QSS rule for `selector` that highlights the active/selected
    widget using either the system palette's highlight color or the user's
    custom color — per the GUI settings' highlight_mode/highlight_color keys
    (edited in the Settings tab, see gui/docks/configurator.py).

    The three consumers (DetailDock's QTabBar active tab,
    ConfigTreeDock's QTreeWidget and RoleClusterTreeDock's QTreeView
    selected item — all callers pass their own QSS selector, written
    against the base class: QTreeView::item:selected for both trees) all
    call this instead of each copy-pasting a QSS string, so a change to the
    highlight scheme lands in ONE place.

    Text color stays palette(highlighted-text) in both modes — deliberately
    not inventing a custom contrast rule from scratch (see
    techdocs/handoff/plan_2026_08_15_configurator_panel.md)."""
    mode = settings.state.get("highlight_mode", "system")
    if mode == "custom":
        color = settings.state.get("highlight_color", DEFAULT_HIGHLIGHT_COLOR)
        return (f"{selector} {{ background: {color}; "
                "color: palette(highlighted-text); }")
    return (f"{selector} {{ background: palette(highlight); "
            "color: palette(highlighted-text); }")


# 2026-08-30 (Denis, live): a dock's widgets floored its minimum width —
# QComboBox's minimum equaled its WIDEST item, tables/trees carried their
# column content widths, QTabWidget its pages, buttons their text — so a
# dock (and a tab inside it) could not be shrunk, and in the old QScrollArea
# wrap the content "улетало за край экрана". Follow-up: "пусть пользователь
# решает, какую длину дока ставить" — the dock must shrink to the ABSOLUTE
# minimum. A `* { min-width: 0 }` stylesheet zeroes the width floor of EVERY
# widget for the LAYOUT (Qt still reports content-based minimumSizeHints,
# but layouts honour the stylesheet min-width — verified: PlacerDock's
# minimum dropped 596px -> 393px, TreesDock -> 135px, ConfigTree -> 52px),
# while growth on widening is untouched. The user sizes the dock by hand.
FIELD_MIN_WIDTH_PX = 0
FIELD_MIN_WIDTH_QSS = "* { min-width: 0; }"


def apply_compact_field_minimums(app) -> None:
    """Apply the app-wide "everything may shrink to the absolute minimum"
    stylesheet (2026-08-30): `* { min-width: 0 }` removes the width floor of
    every widget, so every dock/tab can be compressed to whatever the user
    wants; growth on widening is untouched. Idempotent — an existing app
    stylesheet is preserved and the rule is appended once. `app` is anything
    with styleSheet()/setStyleSheet() (QApplication at startup; a QWidget
    works too — used by tests to avoid global state)."""
    existing = app.styleSheet()
    if FIELD_MIN_WIDTH_QSS not in existing:
        app.setStyleSheet((existing + "\n" + FIELD_MIN_WIDTH_QSS).strip())
