# tests/gui/test_dock_common.py
"""Regression tests for gui/docks/_common.py — the shared dock helpers
(Phase 2 of the gui/ cleanup roadmap). The read-merge-write helpers
(merge_write / add_list_entry / upsert_clone_placement) are exercised
against BOTH dispatch paths the docks rely on: YAML by default and JSON by
file extension, since the existing dock tests only ever drive them through
YAML files (test_extract_dock / test_placer_dock write .yaml fixtures)."""

import json
import logging
from pathlib import Path

import pytest

from gui.docks._common import (ERROR_STYLE, SUCCESS_STYLE, WARN_STYLE,
                               add_include, add_list_entry, configure_searchable,
                               disable_include, display_path, merge_write,
                               non_includable_keys, refresh_file_combo_choices,
                               set_combo_items, set_file_combo_selection, show_message,
                               upsert_clone_placement, upsert_list_entry)
import gui.docks._common as common_mod
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _load(path: Path):
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return sexp_to_dict(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: dict) -> str:
    """Serialize a dict in the fixture's format (.json -> json, else .sexp)."""
    if path.suffix.lower() == ".json":
        return json.dumps(data)
    return dict_to_sexp(data)


@pytest.fixture(params=[".sexp", ".json"])
def config_path(tmp_path, request):
    """Same file path with either extension — exercises both the s-expr and
    the JSON dispatch of the merge-write helpers (YAML support was removed
    from the config graph, 2026-08-28, core_yaml_removal)."""
    return tmp_path / f"config{request.param}"


# ── merge_write ─────────────────────────────────────────────────────────

def test_merge_write_flat_preserves_other_keys(config_path):
    config_path.write_text(_dump(config_path, {"old_cell": {"x": 1}}), encoding="utf-8")
    overwritten = merge_write(config_path, {"new_cell": {"x": 2}})
    assert overwritten is False
    data = _load(config_path)
    assert data["old_cell"] == {"x": 1}  # untouched
    assert data["new_cell"] == {"x": 2}


def test_merge_write_flat_reports_overwrite(config_path):
    config_path.write_text(_dump(config_path, {"cell": {"x": 1}}), encoding="utf-8")
    assert merge_write(config_path, {"cell": {"x": 9}}) is True
    assert _load(config_path)["cell"] == {"x": 9}


def test_merge_write_section_merges_only_that_nested_dict(config_path):
    config_path.write_text(_dump(config_path, {
        "clone_placements": [{"name": "A"}],
        "extract_profiles": {"p1": {"a": 1}},
    }), encoding="utf-8")
    overwritten = merge_write(
        config_path, {"extract_profiles": {"p2": {"b": 2}}}, section="extract_profiles")
    assert overwritten is False
    data = _load(config_path)
    assert data["clone_placements"] == [{"name": "A"}]  # other top-level key untouched
    assert data["extract_profiles"] == {"p1": {"a": 1}, "p2": {"b": 2}}


def test_merge_write_creates_missing_file(config_path):
    assert merge_write(config_path, {"cell": {"x": 1}}) is False
    assert _load(config_path) == {"cell": {"x": 1}}


# ── _read_data on a malformed file ──────────────────────────────────────

def test_merge_write_raises_os_error_on_malformed_sexp(tmp_path):
    """The "broken file -> OSError" invariant (found live 2026-08-04, when a
    malformed target raised the raw parse error instead of OSError and
    escaped every write-path caller's `except OSError`, crashing the GUI)
    now holds for the s-expr format — YAML was removed from the config graph
    (2026-08-28), so a malformed .sexp is the current analog."""
    path = tmp_path / "config.sexp"
    path.write_text("(kicadstamp-config\n", encoding="utf-8")

    with pytest.raises(OSError):
        merge_write(path, {"cell": {"x": 1}})


def test_merge_write_raises_os_error_on_malformed_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(OSError):
        merge_write(path, {"cell": {"x": 1}})


def test_read_data_os_error_message_names_the_broken_file(tmp_path):
    path = tmp_path / "config.sexp"
    path.write_text("(kicadstamp-config\n", encoding="utf-8")

    with pytest.raises(OSError) as excinfo:
        common_mod.read_data(path)
    assert str(path) in str(excinfo.value)


# ── add_list_entry ──────────────────────────────────────────────────────

def test_add_list_entry_appends_and_dedupes(config_path):
    config_path.write_text(_dump(config_path, {"include": ["sub/a.sexp"]}), encoding="utf-8")
    # a different relative spelling resolving to the same file is a no-op
    assert add_list_entry(config_path, "include", "sub/./a.sexp") is False
    assert add_list_entry(config_path, "include", "other.sexp") is True
    assert _load(config_path)["include"] == ["sub/a.sexp", "other.sexp"]


def test_add_list_entry_refuses_non_list_section(config_path):
    if config_path.suffix == ".sexp":
        pytest.skip("the s-expr writer normalizes include: to a list — a non-list "
                    "include: is only representable in JSON")
    config_path.write_text(_dump(config_path, {"include": "not-a-list"}), encoding="utf-8")
    with pytest.raises(OSError):
        add_list_entry(config_path, "include", "x.sexp")


# ── upsert_clone_placement ──────────────────────────────────────────────

def test_upsert_clone_placement_replaces_by_name_and_appends(config_path):
    config_path.write_text(
        _dump(config_path, {"clone_placements": [{"name": "A", "cell": "c1"}]}),
        encoding="utf-8")
    assert upsert_clone_placement(config_path, {"name": "A", "cell": "c2"}) is True
    assert upsert_clone_placement(config_path, {"name": "B", "cell": "c1"}) is False
    data = _load(config_path)
    assert [e["name"] for e in data["clone_placements"]] == ["A", "B"]
    assert data["clone_placements"][0]["cell"] == "c2"  # replaced in place, not appended


def test_upsert_clone_placement_refuses_non_list(config_path):
    if config_path.suffix == ".sexp":
        pytest.skip("the s-expr writer normalizes clone_placements: to a list — a "
                    "non-list section is only representable in JSON")
    config_path.write_text(_dump(config_path, {"clone_placements": "nope"}), encoding="utf-8")
    with pytest.raises(OSError):
        upsert_clone_placement(config_path, {"name": "A"})


# ── upsert_list_entry (general form, 2026-08-03 — ConfigTreeDock's Add ───
# thermal via pad; upsert_clone_placement above now delegates to this) ────

def test_upsert_list_entry_replaces_by_key_and_appends(config_path):
    config_path.write_text(
        _dump(config_path, {"thermal_via_arrays": [{"name": "A", "pad": "1"}]}),
        encoding="utf-8")
    assert upsert_list_entry(config_path, "thermal_via_arrays", {"name": "A", "pad": "2"}) is True
    assert upsert_list_entry(config_path, "thermal_via_arrays", {"name": "B", "pad": "1"}) is False
    data = _load(config_path)
    assert [e["name"] for e in data["thermal_via_arrays"]] == ["A", "B"]
    assert data["thermal_via_arrays"][0]["pad"] == "2"  # replaced in place, not appended


def test_upsert_list_entry_refuses_non_list(config_path):
    if config_path.suffix == ".sexp":
        pytest.skip("the s-expr writer normalizes thermal_via_arrays: to a list — "
                    "a non-list section is only representable in JSON")
    config_path.write_text(
        _dump(config_path, {"thermal_via_arrays": "nope"}), encoding="utf-8")
    with pytest.raises(OSError):
        upsert_list_entry(config_path, "thermal_via_arrays", {"name": "A"})


def test_upsert_list_entry_key_fn_matches_by_name_or_net(config_path):
    """rules: needs this (2026-08-05) — a Rule's identity falls back to
    net: when name: is absent (config/models.py's rule_effective_name()),
    unlike thermal_via_arrays:/clone_placements: which always require an
    explicit name:."""
    identity = lambda e: e.get("name") or e.get("net")  # noqa: E731
    config_path.write_text(
        _dump(config_path, {"rules": [{"net": "+3V3", "anchor_role": "FPGA"}]}),
        encoding="utf-8")

    overwritten = upsert_list_entry(
        config_path, "rules", {"net": "+3V3", "anchor_role": "FPGA_2"}, key_fn=identity)
    assert overwritten is True
    data = _load(config_path)
    assert data["rules"] == [{"net": "+3V3", "anchor_role": "FPGA_2"}]

    appended = upsert_list_entry(
        config_path, "rules", {"net": "+1V2", "name": "explicit", "anchor_role": "FPGA"},
        key_fn=identity)
    assert appended is False
    data = _load(config_path)
    assert len(data["rules"]) == 2


# ── add_include / disable_include (ConfigTreeDock's Add/Remove file, ─────
# 2026-08-03 — comment-toggle via enabled: false, not erasing the line) ───

def test_add_include_appends_new_entry(config_path):
    assert add_include(config_path, "sub.sexp") is True
    assert _load(config_path)["include"] == ["sub.sexp"]


def test_add_include_is_a_noop_when_already_enabled(config_path):
    config_path.write_text(_dump(config_path, {"include": ["sub.sexp"]}), encoding="utf-8")
    assert add_include(config_path, "sub.sexp") is False
    assert _load(config_path)["include"] == ["sub.sexp"]


def test_add_include_reenables_a_disabled_entry_instead_of_duplicating(config_path):
    config_path.write_text(
        _dump(config_path, {"include": [{"path": "sub.sexp", "enabled": False}]}),
        encoding="utf-8")
    assert add_include(config_path, "sub.sexp") is True
    assert _load(config_path)["include"] == ["sub.sexp"]  # back to plain form, not duplicated


def test_disable_include_converts_string_entry_to_disabled_mapping(config_path):
    config_path.write_text(
        _dump(config_path, {"include": ["sub.sexp", "other.sexp"]}), encoding="utf-8")
    target = (config_path.parent / "sub.sexp").resolve()
    assert disable_include(config_path, target) is True
    data = _load(config_path)
    assert data["include"] == [{"path": "sub.sexp", "enabled": False}, "other.sexp"]


def test_disable_include_is_a_noop_when_already_disabled(config_path):
    config_path.write_text(
        _dump(config_path, {"include": [{"path": "sub.sexp", "enabled": False}]}),
        encoding="utf-8")
    target = (config_path.parent / "sub.sexp").resolve()
    assert disable_include(config_path, target) is False


def test_disable_include_returns_false_when_target_not_included(config_path):
    config_path.write_text(_dump(config_path, {"include": ["other.sexp"]}), encoding="utf-8")
    target = (config_path.parent / "sub.sexp").resolve()
    assert disable_include(config_path, target) is False


# ── non_includable_keys ──────────────────────────────────────────────────

def test_non_includable_keys_flags_root_only_scalars(config_path):
    config_path.write_text(
        _dump(config_path, {"cells": {}, "layer": "B.Cu", "schematic_dir": "sch"}),
        encoding="utf-8")
    assert non_includable_keys(config_path) == {"layer", "schematic_dir"}


def test_non_includable_keys_empty_for_a_clean_subsystem_file(config_path):
    config_path.write_text(
        _dump(config_path, {"cells": {}, "rules": []}), encoding="utf-8")
    assert non_includable_keys(config_path) == set()


# ── display_path ────────────────────────────────────────────────────────

def test_display_path_relative_inside_project_and_absolute_outside(tmp_path, monkeypatch):
    # display_path lives in core now (kicadstamp/config_writer.py, Phase 2 of
    # the god-file decomposition) — patch ITS module global, not the gui facade's
    # re-export (which the function never reads).
    from kicadstamp import config_writer
    monkeypatch.setattr(config_writer, "PROJECT_ROOT", tmp_path)
    inside = tmp_path / "boards" / "cell.yaml"
    inside.parent.mkdir()
    assert display_path(inside) == str(Path("boards/cell.yaml"))
    outside = tmp_path.parent / "elsewhere.yaml"
    assert display_path(outside) == str(outside)


# ── Qt widget helpers ───────────────────────────────────────────────────

def test_set_combo_items_preserves_current_text(qapp):
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.setEditable(True)  # docks configure combos as searchable/editable
    combo.addItems(["a", "b", "c"])
    combo.setCurrentText("b")
    set_combo_items(combo, ["x", "y"])
    assert combo.currentText() == "b"  # in-progress value survives the refresh
    assert [combo.itemText(i) for i in range(combo.count())] == ["x", "y"]


def test_configure_searchable_makes_combo_editable_noinsert(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QComboBox, QCompleter
    combo = QComboBox()
    configure_searchable(combo)
    assert combo.isEditable() is True
    assert combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    completer = combo.completer()
    assert completer is not None
    assert completer.completionMode() == QCompleter.CompletionMode.PopupCompletion
    # CaseSensitive (2026-08-04): every actual Role/Cluster/Net comparison
    # elsewhere in the project is a plain case-sensitive `==`/dict key — a
    # case-insensitive completer used to silently rewrite a differently-
    # cased typed value (e.g. "C_Out_Bulk") to an existing item's stored
    # casing ("C_OUT_BULK") on Enter/focus-out, before the caller ever read
    # currentText().
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseSensitive


def test_configure_searchable_does_not_silently_rewrite_a_differently_cased_value(qapp):
    """Regression for the exact bug found live: typing a new value that
    differs only in case from an existing item must survive Enter/focus-
    out verbatim, not snap back to the existing item's casing."""
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.addItem("C_OUT_BULK")
    configure_searchable(combo)
    combo.setCurrentText("C_Out_Bulk")

    combo.lineEdit().returnPressed.emit()

    assert combo.currentText() == "C_Out_Bulk"


def test_show_message_logs_by_style(caplog):
    """show_message() no longer touches any label (2026-08-13 — the inline
    message_label was removed from every dock, see
    plan_2026_08_13_remove_dock_message_label.md); it only routes the message
    to the Log dock at the level matching `style`."""
    dock_logger = logging.getLogger("gui.docks.test_dock_common")
    with caplog.at_level(logging.DEBUG, logger="gui.docks.test_dock_common"):
        show_message("boom", ERROR_STYLE, dock_logger)
        assert caplog.records[-1].levelname == "ERROR"
        show_message("careful", WARN_STYLE, dock_logger)
        assert caplog.records[-1].levelname == "WARNING"
        show_message("done", SUCCESS_STYLE, dock_logger)
        assert caplog.records[-1].levelname == "INFO"
        before = len(caplog.records)
        show_message("", "", dock_logger)  # empty text -> no log record
        assert len(caplog.records) == before


# ── file-combo helpers (plan 2026-08-13 tree_to_combo_file_pickers) ──────

def test_set_file_combo_selection_adds_a_path_not_in_the_list(qapp, tmp_path):
    """The cheap half's fallback: a file outside the include graph (root
    not set yet, or not reachable via include:) must still be selected and
    shown as an extra item, never silently dropped."""
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.addItem("something else", Path("somewhere.yaml"))
    outside = tmp_path / "outside.yaml"

    set_file_combo_selection(combo, outside)

    assert combo.currentData() == outside
    assert combo.count() == 2


def test_set_file_combo_selection_selects_an_existing_item_without_duplicating(qapp, tmp_path):
    """Regression for a REAL latent bug found 2026-08-13 (diagnostics/
    diag_combo_finddata_path.py): QComboBox.findData() does NOT match a
    pathlib.Path stored as itemData (Qt's QVariant comparison never runs
    Path.__eq__ — itemData == root is True in Python, findData(root) is
    -1), so the old ExtractDock helper re-added an already-listed path as a
    duplicate. The shared helper must match by Python == instead."""
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.addItem("root.yaml", tmp_path / "root.yaml")
    combo.addItem("sub.sexp", tmp_path / "sub.sexp")

    set_file_combo_selection(combo, tmp_path / "root.yaml")

    assert combo.currentIndex() == 0
    assert combo.count() == 2  # matched, NOT duplicated
    assert combo.currentData() == tmp_path / "root.yaml"


def test_set_file_combo_selection_none_clears_the_selection(qapp):
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.addItem("a", Path("a.yaml"))
    combo.setCurrentIndex(0)

    set_file_combo_selection(combo, None)

    assert combo.currentIndex() == -1


def test_set_file_combo_selection_blocks_current_index_changed(qapp, tmp_path):
    """Regression guard — a tree click calls set_*_file -> this helper; if
    it re-fired currentIndexChanged, the dock's own combo handler would call
    the setter again (harmless but noisy), and during set_root_path's
    repopulate it would re-enter per-item."""
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.addItem("a", Path("a.yaml"))  # first item added BEFORE the connect
    fired = []
    combo.currentIndexChanged.connect(fired.append)

    set_file_combo_selection(combo, tmp_path / "x.sexp")

    assert fired == []


def test_refresh_file_combo_choices_populates_and_preserves_current(qapp, tmp_path):
    """The expensive half: repopulates from the WHOLE include graph and
    then reflects each dock's current path back — without adding a
    duplicate of an already-listed path (findData match, not blind add)."""
    from PyQt6.QtWidgets import QComboBox
    (tmp_path / "sub.sexp").write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["sub.sexp"]}), encoding="utf-8")
    combo = QComboBox()

    refresh_file_combo_choices((combo,), root, (root,))

    names = {combo.itemData(i).name for i in range(combo.count())}
    assert names == {"root.sexp", "sub.sexp"}
    assert combo.count() == 2  # no duplicate of the already-listed root
    assert combo.currentData() == root


def test_refresh_file_combo_choices_none_root_leaves_combos_empty(qapp, tmp_path):
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    combo.addItem("stale", Path("stale.yaml"))

    refresh_file_combo_choices((combo,), None, (None,))

    assert combo.count() == 0


def test_refresh_file_combo_choices_drops_path_outside_new_graph(qapp, tmp_path):
    """2026-08-16 evening (Denis live): switching the open root project must
    drop a dock's selected file when it's not in the NEW project's graph.
    refresh_file_combo_choices used to reflect the pre-switch path back via
    set_file_combo_selection, whose "add as an extra item even if outside
    the graph" fallback silently re-added and re-selected the OLD project's
    file — and the dock's own path attribute was never told the project
    changed (PlacerDock kept reading the old project's components file).
    Now the helper validates each current path against the new root's file
    set and returns None for anything no longer reachable."""
    from PyQt6.QtWidgets import QComboBox
    (tmp_path / "sub.sexp").write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"include": ["sub.sexp"]}), encoding="utf-8")
    # A file in a SIBLING directory of the root — not reachable via any
    # include: under root.sexp, exactly Denis's "old project's file".
    stale = tmp_path / "other_project" / "components.sexp"
    stale.parent.mkdir()
    stale.write_text(dict_to_sexp({"cells": {}}), encoding="utf-8")
    combo = QComboBox()

    corrected = refresh_file_combo_choices((combo,), root, (stale,))

    assert corrected == [None]
    assert combo.count() == 2  # only the graph's own files — no phantom item
    assert combo.currentData() is None
