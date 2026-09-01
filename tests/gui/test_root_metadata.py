# tests/gui/test_root_metadata.py
import gui.docks.root_metadata as root_metadata_mod
from PyQt6.QtGui import QKeySequence

from gui import settings
from gui.docks.root_metadata import (ACTION_ADD_SCH, ACTION_NEW, ACTION_OPEN,
                                     ACTION_REMOVE_SCH, ACTION_SAVE,
                                     RootMetadataDock)
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_working_set import WORKING_SET

MINIMAL_CELL = {
    "cells": {
        "one_role": {
            "components": [
                {"role": "THE_ROLE", "offset_along_mm": 0.0,
                 "offset_across_mm": 0.0, "angle_deg": 0.0},
            ],
        },
    },
}


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _load(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8")) or {}


# ── Root ownership: Open/New/Recent/restore (moved here 2026-08-11 from
# ConfigTreeDock, see gui/docks/root_metadata.py's module docstring) ───────

def test_open_root_via_dialog_sets_root_and_remembers_it(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = RootMetadataDock(main_window)
    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(root), "")))
    dock._on_open_root()

    assert dock._path == root
    assert settings.state.get("last_root_file") == str(root)
    assert settings.state.get("recent_root_files") == [str(root)]


def test_open_root_dialog_cancelled_leaves_root_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    dock._on_open_root()

    assert dock._path == root


def test_new_root_creates_an_empty_file_and_opens_it(main_window, tmp_path, monkeypatch):
    new_root = tmp_path / "brand_new.sexp"
    assert not new_root.exists()

    dock = RootMetadataDock(main_window)
    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_root), "")))
    dock._on_new_root()

    assert new_root.exists()
    assert sexp_to_dict(new_root.read_text(encoding="utf-8")) == {}
    assert dock._path == new_root


def test_new_root_does_not_overwrite_an_existing_file(main_window, tmp_path, monkeypatch):
    existing = tmp_path / "already_here.sexp"
    _write(existing, MINIMAL_CELL)
    before = existing.read_text(encoding="utf-8")

    dock = RootMetadataDock(main_window)
    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(existing), "")))
    dock._on_new_root()

    assert existing.read_text(encoding="utf-8") == before
    assert dock._path == existing


def test_new_root_dialog_cancelled_leaves_root_untouched(main_window, tmp_path, monkeypatch):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    dock._on_new_root()

    assert dock._path == root


# .sexp root (parallel config format, 2026-08-27)

def test_new_root_sexp_creates_empty_kicadstamp_config(main_window, tmp_path, monkeypatch):
    """A brand-new .sexp root starts with the (kicadstamp-config) template —
    a perfectly valid empty config, the s-expr analog of YAML's '{}'."""
    new_root = tmp_path / "brand_new.sexp"
    assert not new_root.exists()

    dock = RootMetadataDock(main_window)
    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(new_root), "")))
    dock._on_new_root()

    assert new_root.exists()
    text = new_root.read_text(encoding="utf-8")
    assert text.strip().startswith("(kicadstamp-config")
    assert dock._path == new_root
    # and the template is a loadable empty config
    from kicadstamp.config.loader import load_config
    cfg, _ = load_config(str(new_root))
    assert cfg is not None


def test_open_root_sexp_via_dialog(main_window, tmp_path, monkeypatch):
    """Open Root accepts .sexp files (filter now includes them)."""
    root = tmp_path / "root.sexp"
    root.write_text("(kicadstamp-config\n  (layer \"B.Cu\"))\n", encoding="utf-8")

    dock = RootMetadataDock(main_window)
    monkeypatch.setattr(root_metadata_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(root), "")))
    dock._on_open_root()

    assert dock._path == root
    assert settings.state.get("last_root_file") == str(root)


def test_default_new_name_uses_root_stem_with_sexp_extension(main_window, tmp_path):
    dock = RootMetadataDock(main_window)
    dock._path = tmp_path / "3ch-awg-tia.yaml"
    assert dock._default_new_name() == "3ch-awg-tia.sexp"

    # no root open yet -> generic 'config.sexp'
    dock2 = RootMetadataDock(main_window)
    assert dock2._default_new_name() == "config.sexp"


def test_set_root_file_emits_root_changed(main_window, tmp_path):
    """root_changed (moved here 2026-08-11, was ConfigTreeDock's own
    root_file_changed) is the signal every other dock's set_root_path
    listens to instead of file_selected — set_root_file() is its only
    source, unlike file_selected which fires on every plain tree click too."""
    root = tmp_path / "root.sexp"
    _write(root, {"cells": {}})

    dock = RootMetadataDock(main_window)
    received = []
    dock.root_changed.connect(received.append)

    dock.set_root_file(root)
    assert received == [root]

    dock.set_root_file(None)
    assert received == [root, None]


def test_recent_list_most_recent_first_and_deduplicated(main_window, tmp_path):
    a = tmp_path / "a.sexp"
    b = tmp_path / "b.sexp"
    _write(a, {"cells": {}})
    _write(b, {"cells": {}})

    dock = RootMetadataDock(main_window)
    dock.set_root_file(a)
    dock.set_root_file(b)
    dock.set_root_file(a)  # re-opening a must move it back to front, not duplicate

    assert settings.state.get("recent_root_files") == [str(a), str(b)]
    assert dock.recent_combo.count() == 2
    assert dock.recent_combo.itemData(0) == str(a)
    assert dock.recent_combo.itemData(1) == str(b)


def test_selecting_a_recent_entry_reopens_it(main_window, tmp_path):
    a = tmp_path / "a.sexp"
    _write(a, MINIMAL_CELL)

    dock = RootMetadataDock(main_window)
    dock.set_root_file(a)
    dock.set_root_file(None)

    dock._on_recent_selected(0)  # only entry: a.sexp

    assert dock._path == a


def test_restores_last_root_file_on_construction(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    _write(root, MINIMAL_CELL)
    settings.state.set("last_root_file", str(root))

    dock = RootMetadataDock(main_window)

    assert dock._path == root


# ── Working file combobox (2026-08-11) — deliberately separate from Root,
# see module docstring ──────────────────────────────────────────────────

def test_working_file_choices_come_from_the_whole_include_graph(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    included = tmp_path / "power.sexp"
    _write(root, {"include": ["power.sexp"]})
    _write(included, {"cells": {}})

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)

    choices = {dock.working_file_combo.itemData(i)
               for i in range(dock.working_file_combo.count())}
    assert choices == {str(root), str(included)}


def test_picking_a_working_file_emits_working_file_changed(main_window, tmp_path):
    root = tmp_path / "root.sexp"
    included = tmp_path / "power.sexp"
    _write(root, {"include": ["power.sexp"]})
    _write(included, {"cells": {}})

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)
    received = []
    dock.working_file_changed.connect(received.append)

    idx = dock.working_file_combo.findData(str(included))
    dock.working_file_combo.setCurrentIndex(idx)
    dock._on_working_file_combo_changed(idx)

    assert received == [included]


def test_tree_click_updates_the_combo_without_emitting_working_file_changed(main_window, tmp_path):
    """set_working_file_from_tree (wired to ConfigTreeDock's file_selected,
    see gui/dock_hub.py) mirrors the tree's own selection into the combo's
    DISPLAY only — it must not re-emit working_file_changed, since the tree
    already drives every entity dock directly."""
    root = tmp_path / "root.sexp"
    included = tmp_path / "power.sexp"
    _write(root, {"include": ["power.sexp"]})
    _write(included, {"cells": {}})

    dock = RootMetadataDock(main_window)
    dock.set_root_file(root)
    received = []
    dock.working_file_changed.connect(received.append)

    dock.set_working_file_from_tree(included)

    assert received == []
    assert dock.working_file_combo.currentData() == str(included)


def test_no_file_picked_shows_placeholder_and_defaults(main_window):
    dock = RootMetadataDock(main_window)
    dock.set_target_file(None)
    assert "No project file open" in dock.target_label.text()
    assert dock.layer_combo.currentText() == "F.Cu"
    assert dock.schematic_files_list.count() == 0
    # 2026-09-01 (plan project_save_model): the per-dock Save button is gone —
    # saving is the global File > Save; the fields auto-stage on commit points.
    assert not hasattr(dock, "save_button")


def test_fields_are_grouped_into_files_schematics_via_tabs(main_window):
    """Restructured into tabs 2026-08-05 (Denis: "решил сделать root
    табами") to cut dock height, same reasoning as ExtractDock's 2026-08-04
    tabbing — Layer/place_components/skip_existing_components are general
    project settings and stay above the tabs instead of in any one of
    them."""
    dock = RootMetadataDock(main_window)
    labels = [dock._tabs.tabText(i) for i in range(dock._tabs.count())]
    assert labels == ["Files", "Schematics", "Via"]

    files_page = dock._tabs.widget(0)
    schematics_page = dock._tabs.widget(1)
    via_page = dock._tabs.widget(2)

    assert files_page.isAncestorOf(dock._text_edits["registry_path"])
    assert files_page.isAncestorOf(dock._text_edits["track_registry_path"])
    assert files_page.isAncestorOf(dock._text_edits["log_file"])
    assert files_page.isAncestorOf(dock._text_edits["operation_log_dir"])

    assert schematics_page.isAncestorOf(dock._text_edits["schematic_dir"])
    assert schematics_page.isAncestorOf(dock.schematic_files_list)

    assert via_page.isAncestorOf(dock._float_edits["via_keepout_clearance_mm"])
    assert via_page.isAncestorOf(dock._int_edits["via_search_n_directions"])


def test_populates_widgets_from_existing_scalar_keys(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {
        "layer": "B.Cu",
        "schematic_dir": "../sch",
        "schematic_files": ["extra1.kicad_sch", "extra2.kicad_sch"],
        "registry_path": "registries/fpga.json",
        "place_components": False,
        "skip_existing_components": True,
        "via_search_n_directions": 4,
        "cells": {"some_cell": {}},
    })
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    assert dock.layer_combo.currentText() == "B.Cu"
    assert dock._text_edits["schematic_dir"].text() == "../sch"
    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["extra1.kicad_sch", "extra2.kicad_sch"]
    assert dock._text_edits["registry_path"].text() == "registries/fpga.json"
    assert dock._bool_checks["place_components"].isChecked() is False
    assert dock._bool_checks["skip_existing_components"].isChecked() is True
    assert dock._int_edits["via_search_n_directions"].text() == "4"


def test_missing_keys_show_config_defaults(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    assert dock.layer_combo.currentText() == "F.Cu"
    assert dock._bool_checks["place_components"].isChecked() is True
    assert dock._bool_checks["skip_existing_components"].isChecked() is False
    assert dock._float_edits["via_keepout_clearance_mm"].text() == "0.2"
    assert dock._int_edits["via_search_n_directions"].text() == "8"


def test_save_with_nothing_changed_and_nothing_present_writes_nothing(main_window, tmp_path, caplog):
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {"c1": {}}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)
    dock._on_save()

    assert _load(path) == {"cells": {"c1": {}}}
    assert any("default" in r.message for r in caplog.records)


def test_save_writes_only_changed_field_and_preserves_other_keys(main_window, tmp_path, caplog):
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {"c1": {}}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._text_edits["schematic_dir"].setText("../../schematics")
    dock._on_save()

    data = _load(path)
    assert data["schematic_dir"] == "../../schematics"
    assert data["cells"] == {"c1": {}}
    assert any("Saved" in r.message for r in caplog.records)


def test_save_writes_already_present_key_back(main_window, tmp_path):
    """An already-present scalar key must be written back on Save — pinned on
    a NON-default value: s-expr omits default-valued fields on serialize
    (the YAML-era "even at default" assertion can't be observed in .sexp —
    dict_to_sexp drops e.g. via_search_n_directions: 8 outright), so this
    asserts the same _present_keys write-back logic at a round-trippable
    value."""
    path = tmp_path / "root.sexp"
    _write(path, {"via_search_n_directions": 4})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._int_edits["via_search_n_directions"].setText("6")
    dock._on_save()

    assert _load(path)["via_search_n_directions"] == 6


def test_save_rejects_non_numeric_float_field(main_window, tmp_path, caplog):
    path = tmp_path / "root.sexp"
    _write(path, {})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._float_edits["via_keepout_clearance_mm"].setText("not-a-number")
    dock._on_save()

    assert _load(path) == {}
    assert any("not a number" in r.message for r in caplog.records)


def test_save_rejects_non_integer_int_field(main_window, tmp_path, caplog):
    path = tmp_path / "root.sexp"
    _write(path, {})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock._int_edits["via_search_n_directions"].setText("not-an-int")
    dock._on_save()

    assert _load(path) == {}
    assert any("not an integer" in r.message for r in caplog.records)


def test_save_without_a_file_picked_shows_error(main_window, caplog):
    dock = RootMetadataDock(main_window)
    dock._on_save()
    assert any("Open or create a project" in r.message for r in caplog.records)


def test_schematic_files_round_trips_as_a_list(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock.schematic_files_list.addItems(["a.kicad_sch", "b.kicad_sch"])
    dock._on_save()

    assert _load(path)["schematic_files"] == ["a.kicad_sch", "b.kicad_sch"]


def test_remove_schematic_file_removes_selected_item(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"schematic_files": ["a.kicad_sch", "b.kicad_sch"]})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock.schematic_files_list.item(0).setSelected(True)
    dock._remove_schematic_file()

    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["b.kicad_sch"]


def test_browse_dir_writes_path_relative_to_target_file(main_window, tmp_path, monkeypatch):
    target = tmp_path / "sub" / "root.sexp"
    target.parent.mkdir()
    _write(target, {})
    picked_dir = tmp_path / "sub" / "schematics"
    picked_dir.mkdir()

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(picked_dir)))

    dock._browse_dir(dock._text_edits["schematic_dir"], "Schematic dir")

    assert dock._text_edits["schematic_dir"].text() == "schematics"


def test_browse_file_writes_path_relative_to_target_file(main_window, tmp_path, monkeypatch):
    target = tmp_path / "sub" / "root.sexp"
    target.parent.mkdir()
    _write(target, {})
    picked_file = tmp_path / "sub" / "registries" / "fpga.json"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(picked_file), "")))

    dock._browse_file(dock._text_edits["registry_path"], "Registry path")

    assert dock._text_edits["registry_path"].text() == "registries/fpga.json"


def test_add_schematic_file_writes_path_relative_to_target_file(main_window, tmp_path, monkeypatch):
    target = tmp_path / "sub" / "root.sexp"
    target.parent.mkdir()
    _write(target, {})
    picked_file = tmp_path / "sub" / "extra.kicad_sch"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(picked_file)], "")))

    dock._add_schematic_file()

    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["extra.kicad_sch"]


def test_add_schematic_file_does_not_duplicate(main_window, tmp_path, monkeypatch):
    target = tmp_path / "root.sexp"
    _write(target, {"schematic_files": ["extra.kicad_sch"]})
    picked_file = tmp_path / "extra.kicad_sch"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(picked_file)], "")))

    dock._add_schematic_file()

    assert dock.schematic_files_list.count() == 1


def test_add_schematic_file_multiselect_adds_all_no_duplicates(main_window, tmp_path, monkeypatch):
    """Task 2026-08-30: Add... uses getOpenFileNames, so several .kicad_sch
    can be picked in one dialog; each is added relative to the target, and a
    path already in the list is skipped (no duplicates)."""
    target = tmp_path / "root.sexp"
    _write(target, {"schematic_files": ["a.kicad_sch"]})
    picked = [str(tmp_path / "b.kicad_sch"), str(tmp_path / "a.kicad_sch"),
              str(tmp_path / "c.kicad_sch")]

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileNames",
        staticmethod(lambda *a, **k: (picked, "")))

    dock._add_schematic_file()

    assert [dock.schematic_files_list.item(i).text()
            for i in range(dock.schematic_files_list.count())] \
        == ["a.kicad_sch", "b.kicad_sch", "c.kicad_sch"]


def test_browse_without_a_file_picked_shows_error(main_window, caplog):
    dock = RootMetadataDock(main_window)
    dock._browse_dir(dock._text_edits["schematic_dir"], "Schematic dir")
    assert any("Open or create a project" in r.message for r in caplog.records)


# ── QAction hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ──

def test_creates_actions_with_stable_ids_and_defaults(main_window):
    """Every action-bearing button (Open/New/Save/Add.../Remove) got a QAction
    with the stable action_id + default shortcut — the buttons adopt them via
    setDefaultAction (one action = button + hotkey)."""
    dock = RootMetadataDock(main_window)
    expected = {
        ACTION_OPEN: ("Open Root file...", "Ctrl+O"),
        ACTION_NEW: ("New Root file...", "Ctrl+N"),
        # Ctrl+S belongs to the GLOBAL File > Save (project.save, 2026-09-01);
        # the root-dock action keeps no default shortcut.
        ACTION_SAVE: ("Save", ""),
        ACTION_ADD_SCH: ("Add...", "Ctrl+Shift+A"),
        ACTION_REMOVE_SCH: ("Remove", "Ctrl+Shift+R"),
    }
    window_actions = {a.objectName(): a for a in main_window.actions()}
    for action_id, (label, shortcut) in expected.items():
        action = window_actions[action_id]
        assert action.text() == label
        assert action.shortcut() == QKeySequence(shortcut)


def test_action_triggers_reach_the_same_slots(main_window, monkeypatch):
    """The QAction's triggered handler is the same slot the old button used to
    call — triggering the action must reach the dock's method (button and
    hotkey are two views of ONE action, not a duplicated copy).

    The slots are patched on the CLASS BEFORE constructing the dock: PyQt
    captures the bound method at connect() time, so patching the instance
    afterwards would leave the action wired to the real _on_open_root — which
    opens a modal QFileDialog and hangs the offscreen test."""
    calls = []
    monkeypatch.setattr(root_metadata_mod.RootMetadataDock, "_on_open_root",
                        lambda self: calls.append("open"))
    monkeypatch.setattr(root_metadata_mod.RootMetadataDock, "_on_new_root",
                        lambda self: calls.append("new"))
    monkeypatch.setattr(root_metadata_mod.RootMetadataDock, "_on_save",
                        lambda self: calls.append("save"))
    monkeypatch.setattr(root_metadata_mod.RootMetadataDock, "_add_schematic_file",
                        lambda self: calls.append("add"))
    monkeypatch.setattr(root_metadata_mod.RootMetadataDock, "_remove_schematic_file",
                        lambda self: calls.append("remove"))

    dock = RootMetadataDock(main_window)
    dock.action_open.trigger()
    dock.action_new.trigger()
    dock.action_save.trigger()
    dock.action_add_schematic_file.trigger()
    dock.action_remove_schematic_file.trigger()
    assert calls == ["open", "new", "save", "add", "remove"]


def test_custom_binding_from_settings_applies_on_next_open(main_window):
    """A stored override in gui_state.json["hotkeys"] is applied when the dock
    is next constructed (plan gate: "кастомный биндинг из settings.state
    применяется при следующем открытии")."""
    settings.state.set("hotkeys", {ACTION_SAVE: "Ctrl+Alt+S"})
    dock = RootMetadataDock(main_window)
    assert dock.action_save.shortcut() == QKeySequence("Ctrl+Alt+S")
    # other actions are untouched
    assert dock.action_open.shortcut() == QKeySequence("Ctrl+O")


# ── Unsaved-changes guard + File > Close (plan Этап 1b) ──────────────────

def test_editing_a_field_marks_dirty(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {}})
    dock = RootMetadataDock(main_window)
    dock.set_root_file(path)
    assert not dock._dirty
    dock._text_edits["schematic_dir"].setText("../../sch")
    assert dock._dirty
    dock._on_save()
    assert not dock._dirty  # a successful save clears it


def test_confirm_discard_passes_when_clean(main_window, tmp_path):
    """Not dirty -> no dialog, True immediately (the close guard)."""
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {}})
    dock = RootMetadataDock(main_window)
    dock.set_root_file(path)
    assert dock._confirm_discard_changes() is True


def test_close_project_respects_discard_guard(main_window, tmp_path, monkeypatch):
    """File > Close: a refused unsaved-changes guard keeps the project open; a
    confirmed one drops the root via set_root_file(None). The guard now covers
    the whole project's staged working set (2026-09-01)."""
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {}})
    dock = RootMetadataDock(main_window)
    dock.set_root_file(path)
    WORKING_SET.enabled = True
    WORKING_SET.stage_write(path, {"cells": {"c1": {}}})  # project is dirty

    monkeypatch.setattr(dock, "_confirm_discard_changes", lambda: False)
    dock.close_project()
    assert dock._path == path  # guard refused -> project stays open

    monkeypatch.setattr(dock, "_confirm_discard_changes", lambda: True)
    dock.close_project()
    assert dock._path is None  # confirmed -> project closed

    WORKING_SET.enabled = False
    WORKING_SET.clear()
