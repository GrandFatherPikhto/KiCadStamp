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
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QCompleter, QLineEdit

from kicadstamp.config_writer import (
    read_data, write_data, add_include, add_list_entry, disable_include,
    display_path, merge_write, non_includable_keys, upsert_clone_placement,
    upsert_list_entry)

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
