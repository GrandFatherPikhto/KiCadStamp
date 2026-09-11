# tests/gui/test_root_metadata.py
import gui.docks.root_metadata as root_metadata_mod
from PyQt6.QtGui import QKeySequence

from gui import settings
from gui.docks.root_metadata import (ACTION_NEW, ACTION_OPEN,
                                     ACTION_RELOAD_SHEETS, RootMetadataDock)
from gui.hotkeys import registered_hotkeys
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


def test_only_the_via_tab_remains(main_window):
    """2026-09-11 (plan project_settings_single_source): the Files (Этап 1)
    and Schematics (Этап 2) tabs are both gone — only Via is left. Layer /
    place_components / skip_existing_components, the KiCad project field and
    the read-only sheet list live in the common form ABOVE the tabs."""
    dock = RootMetadataDock(main_window)
    labels = [dock._tabs.tabText(i) for i in range(dock._tabs.count())]
    assert labels == ["Via"]

    via_page = dock._tabs.widget(0)
    assert via_page.isAncestorOf(dock._float_edits["via_keepout_clearance_mm"])
    assert via_page.isAncestorOf(dock._int_edits["via_search_n_directions"])
    # the KiCad project field and the sheet list are NOT inside any tab
    assert not dock._tabs.isAncestorOf(dock.kicad_project_edit)
    assert not dock._tabs.isAncestorOf(dock.schematic_files_list)


def test_files_tab_and_its_fields_are_gone(main_window):
    """2026-09-11 (plan project_settings_single_source, Этап 1): the Files
    tab and its four path fields (registry_path/track_registry_path/
    log_file/operation_log_dir) are removed from the dock entirely — the
    keys stay part of the config FORMAT, but the GUI no longer edits them."""
    dock = RootMetadataDock(main_window)
    labels = [dock._tabs.tabText(i) for i in range(dock._tabs.count())]
    assert "Files" not in labels
    assert not hasattr(dock, "_text_edits")
    assert not hasattr(dock, "_DEFAULT_PLACEHOLDER_FOR")


def test_schematics_tab_and_schematic_dir_editor_are_gone(main_window):
    """2026-09-11 (plan project_settings_single_source, Этап 2): the
    Schematics tab and the schematic_dir field are removed — the KiCad
    project field replaced them, and the sheet list became read-only
    (Add.../Remove hotkeys and buttons are gone too)."""
    dock = RootMetadataDock(main_window)
    labels = [dock._tabs.tabText(i) for i in range(dock._tabs.count())]
    assert "Schematics" not in labels
    assert not hasattr(dock, "action_add_schematic_file")
    assert not hasattr(dock, "action_remove_schematic_file")


def test_removed_files_keys_survive_saving_another_field(main_window, tmp_path):
    """Regression for the Files-tab removal: a profile that explicitly
    declares the four removed keys must keep every one of them (and its
    other sections) byte-for-value when the dock saves an unrelated field.
    merge_write leaves keys it was not given untouched."""
    path = tmp_path / "root.sexp"
    _write(path, {
        "layer": "F.Cu",
        "registry_path": "custom/registry.json",
        "track_registry_path": "custom/tracks.json",
        "log_file": "custom/run.log",
        "operation_log_dir": "custom/operational",
        "cells": {"c1": {}},
    })
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    dock.layer_combo.setCurrentText("B.Cu")
    dock._on_save()

    data = _load(path)
    assert data["layer"] == "B.Cu"
    assert data["registry_path"] == "custom/registry.json"
    assert data["track_registry_path"] == "custom/tracks.json"
    assert data["log_file"] == "custom/run.log"
    assert data["operation_log_dir"] == "custom/operational"
    assert data["cells"] == {"c1": {}}


def test_populates_widgets_from_existing_scalar_keys(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {
        "layer": "B.Cu",
        "schematic_dir": "../sch",
        "root_sheet": "board.kicad_sch",
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
    # the config stores root_sheet; the field shows the sibling .kicad_pro
    assert dock.kicad_project_edit.text() == "board.kicad_pro"
    assert [dock.schematic_files_list.item(i).text() for i in range(dock.schematic_files_list.count())] \
        == ["extra1.kicad_sch", "extra2.kicad_sch"]
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

    dock.kicad_project_edit.setText("../../schematics/board.kicad_pro")
    dock._on_save()

    data = _load(path)
    # the field is shown as .kicad_pro but STORED as root_sheet (.kicad_sch)
    assert data["root_sheet"] == "../../schematics/board.kicad_sch"
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


def test_schematic_sheets_list_is_read_only(main_window, tmp_path):
    """2026-09-11 (plan project_settings_single_source, Этап 2): the old
    Add.../Remove buttons and inline editing are gone — the list only
    displays Config.schematic_files and is refreshed by the Reload button."""
    from PyQt6.QtWidgets import QListWidget

    path = tmp_path / "root.sexp"
    _write(path, {"schematic_files": ["a.kicad_sch", "b.kicad_sch"]})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(path)

    assert dock.schematic_files_list.selectionMode() \
        == QListWidget.SelectionMode.NoSelection
    assert not hasattr(dock, "_add_schematic_file")
    assert not hasattr(dock, "_remove_schematic_file")
    assert not hasattr(dock, "_make_schematic_items_editable")
    assert [dock.schematic_files_list.item(i).text()
            for i in range(dock.schematic_files_list.count())] \
        == ["a.kicad_sch", "b.kicad_sch"]


def test_browse_kicad_project_derives_root_sheet_relative_to_config(
        main_window, tmp_path, monkeypatch):
    """Picking a .kicad_pro sets the field and, on save, stores Config.root_sheet
    (same basename + .kicad_sch) relative to the config — the store-on-save half
    of the derivation (plan project_settings_single_source, Этап 2)."""
    target = tmp_path / "sub" / "root.sexp"
    target.parent.mkdir()
    _write(target, {})
    (tmp_path / "sub" / "board.kicad_sch").write_text("(kicad_sch\n)\n",
                                                      encoding="utf-8")
    picked = tmp_path / "sub" / "board.kicad_pro"

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(picked), "")))

    dock._browse_kicad_project()
    dock._on_save()

    assert dock.kicad_project_edit.text() == "board.kicad_pro"
    assert _load(target)["root_sheet"] == "board.kicad_sch"


def test_browse_kicad_project_without_a_root_sheet_leaves_value(
        main_window, tmp_path, monkeypatch, caplog):
    """A picked .kicad_pro whose sibling .kicad_sch does not exist must leave
    the current root_sheet untouched and say so in the log (plan Этап 2:
    "если такого файла нет — сообщить в логе и не трогать текущее значение")."""
    target = tmp_path / "root.sexp"
    _write(target, {"root_sheet": "keep.kicad_sch"})
    picked = tmp_path / "missing.kicad_pro"  # no missing.kicad_sch on disk

    dock = RootMetadataDock(main_window)
    dock.set_target_file(target)
    before = dock.kicad_project_edit.text()
    monkeypatch.setattr(
        "gui.docks.root_metadata.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(picked), "")))

    dock._browse_kicad_project()
    dock._on_save()

    assert before == "keep.kicad_pro"  # sanity: derived from root_sheet
    assert dock.kicad_project_edit.text() == before
    assert _load(target)["root_sheet"] == "keep.kicad_sch"
    assert any("No root sheet" in r.message for r in caplog.records)


def _write_sch(path, sheet_files=()):
    """Minimal .kicad_sch text good enough for walk_schematic_hierarchy: it
    only needs (sheet blocks (newline right after the tag) carrying a
    Sheetfile property (see schematic_discovery._SHEETFILE_RE)."""
    body = "".join(
        '(sheet\n'
        '    (property "Sheetname" "{name}")\n'
        '    (property "Sheetfile" "{f}")\n'
        '  )\n'.format(name=f.rsplit(".", 1)[0], f=f)
        for f in sheet_files)
    path.write_text("(kicad_sch\n" + body + ")\n", encoding="utf-8")


def test_reload_schematic_sheets_writes_reachable_and_clears_schematic_dir(
        main_window, tmp_path):
    """The Reload button walks the hierarchy from root_sheet, REPLACES
    schematic_files with the reachable files (relative, including the root),
    and CLEARS schematic_dir. A sibling .kicad_sch that is not reachable from
    the root must NOT be picked up — the old schematic_dir: "." would have
    globbed it (plan project_settings_single_source, Этап 2)."""
    config = tmp_path / "root.sexp"
    _write(config, {"schematic_dir": ".", "root_sheet": "board.kicad_sch",
                    "schematic_files": ["stale.kicad_sch"]})
    _write_sch(tmp_path / "board.kicad_sch", sheet_files=["child.kicad_sch"])
    _write_sch(tmp_path / "child.kicad_sch")
    _write_sch(tmp_path / "orphan.kicad_sch")  # unreachable from the root

    dock = RootMetadataDock(main_window)
    dock.set_target_file(config)
    dock._reload_schematic_sheets()

    data = _load(config)
    assert data["schematic_files"] == ["board.kicad_sch", "child.kicad_sch"]
    assert "orphan.kicad_sch" not in data["schematic_files"]
    # schematic_dir cleared: None serializes away for s-expr
    assert "schematic_dir" not in data
    # the read-only widget mirrors the new list
    assert [dock.schematic_files_list.item(i).text()
            for i in range(dock.schematic_files_list.count())] \
        == ["board.kicad_sch", "child.kicad_sch"]


def test_reload_schematic_sheets_requires_a_project(main_window, tmp_path, caplog):
    """No root_sheet in the config -> the button refuses with a log message
    and writes nothing."""
    config = tmp_path / "root.sexp"
    _write(config, {"cells": {"c1": {}}})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(config)

    dock._reload_schematic_sheets()

    assert _load(config) == {"cells": {"c1": {}}}
    assert any("Pick a KiCad project" in r.message for r in caplog.records)


def test_reload_schematic_sheets_missing_root_file_leaves_list(
        main_window, tmp_path, caplog):
    """root_sheet points at a file that does not exist -> log + no write."""
    config = tmp_path / "root.sexp"
    _write(config, {"root_sheet": "gone.kicad_sch",
                    "schematic_files": ["keep.kicad_sch"]})
    dock = RootMetadataDock(main_window)
    dock.set_target_file(config)

    dock._reload_schematic_sheets()

    assert _load(config)["schematic_files"] == ["keep.kicad_sch"]
    assert any("not found" in r.message for r in caplog.records)


def test_browse_without_a_file_picked_shows_error(main_window, caplog):
    dock = RootMetadataDock(main_window)
    dock._browse_kicad_project()
    assert any("Open or create a project" in r.message for r in caplog.records)


# ── QAction hotkeys (2026-08-30, plan dock_toolbars_menus_hotkeys Этап 1) ──

def test_creates_actions_with_stable_ids_and_defaults(main_window):
    """Every action-bearing button (Open/New/Reload schematic sheets) got a
    QAction with the stable action_id + default shortcut. No
    root_metadata.save: the per-dock Save button was retired 2026-09-01
    (Ctrl+S = the GLOBAL File > Save, project.save — see
    test_vestigial_save_hotkey_removed). The Add.../Remove hotkeys are gone
    with the Schematics tab (2026-09-11)."""
    dock = RootMetadataDock(main_window)
    expected = {
        ACTION_OPEN: ("Open Root file...", "Ctrl+O"),
        ACTION_NEW: ("New Root file...", "Ctrl+N"),
        ACTION_RELOAD_SHEETS: ("Reload schematic sheets", "Ctrl+Shift+R"),
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
    monkeypatch.setattr(root_metadata_mod.RootMetadataDock, "_reload_schematic_sheets",
                        lambda self: calls.append("reload"))

    dock = RootMetadataDock(main_window)
    dock.action_open.trigger()
    dock.action_new.trigger()
    dock.action_reload_sheets.trigger()
    assert calls == ["open", "new", "reload"]


def test_custom_binding_from_settings_applies_on_next_open(main_window):
    """A stored override in gui_state.json["hotkeys"] is applied when the dock
    is next constructed (plan gate: "кастомный биндинг из settings.state
    применяется при следующем открытии"). Anchored on ACTION_NEW now that the
    vestigial ACTION_SAVE is gone (2026-09-04)."""
    settings.state.set("hotkeys", {ACTION_NEW: "Ctrl+Alt+S"})
    dock = RootMetadataDock(main_window)
    assert dock.action_new.shortcut() == QKeySequence("Ctrl+Alt+S")
    # other actions are untouched
    assert dock.action_open.shortcut() == QKeySequence("Ctrl+O")


def test_vestigial_save_hotkey_removed(main_window):
    """Bug B regression (plan staged_delete_stale_tree_and_save_hotkey): the
    per-dock root_metadata.save hotkey action is GONE — the Save button was
    retired 2026-09-01 (Ctrl+S is the GLOBAL File > Save = project.save,
    gui/main_window.py). It must not be registered anywhere, so Settings can no
    longer offer a second, misleading "Save" row, and a Ctrl+S binding can
    never land on the wrong action and make the real Ctrl+S ambiguous/dead."""
    dock = RootMetadataDock(main_window)
    window_actions = {a.objectName() for a in main_window.actions()}
    assert "root_metadata.save" not in window_actions
    assert not any(action_id == "root_metadata.save"
                   for action_id, _label, _default in registered_hotkeys())


# ── Unsaved-changes guard + File > Close (plan Этап 1b) ──────────────────

def test_editing_a_field_marks_dirty(main_window, tmp_path):
    path = tmp_path / "root.sexp"
    _write(path, {"cells": {}})
    dock = RootMetadataDock(main_window)
    dock.set_root_file(path)
    assert not dock._dirty
    dock.kicad_project_edit.setText("../../sch/board.kicad_pro")
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
