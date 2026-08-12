# tests/gui/test_extract_dock.py
from unittest.mock import MagicMock

import json

import yaml
from kipy.board_types import FootprintInstance, Track, Via
from PyQt6.QtCore import Qt

import gui.docks.extract as extract_mod
from gui.docks.extract import ExtractDock


class FakeSelected:
    def __init__(self, ref, role, cluster, nets):
        self.ref, self.role, self.cluster, self.nets = ref, role, cluster, nets


class FakeAdapter:
    pass


class FakeBoard:
    adapter = FakeAdapter()


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _fake_extract(adapter, name, params=None, items=None, annotations=None, **kwargs):
    return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}


def _fake_fp(ref):
    """A MagicMock(spec=FootprintInstance) so isinstance() checks (both in
    this dock's own _filtered_selection() and in kicadstamp code) treat it
    as a real footprint — same pattern used throughout tests/*.py for the
    same reason (see e.g. tests/test_clone_ignore_selection.py)."""
    fp = MagicMock(spec=FootprintInstance)
    fp.reference_field.text.value = ref
    return fp


def _fake_via(net_name, uuid="via-uuid-unregistered"):
    via = MagicMock(spec=Via)
    via.net.name = net_name
    via.id.value = uuid
    return via


def _fake_track(net_name="GND", uuid="track-uuid-unregistered"):
    track = MagicMock(spec=Track)
    track.net.name = net_name
    track.id.value = uuid
    return track


def _write_registry(path, entries) -> None:
    """entries: {key: {"uuid": ..., ...}} — same shape RegistryEntry/
    TrackRegistryEntry serialize to (see kicadstamp/registry.py's
    save_registry/save_track_registry) — only 'uuid' matters for these
    tests, the rest is filler to keep the dataclass happy on load."""
    path.write_text(json.dumps(entries), encoding="utf-8")


# ── Net aliases as a real QTableWidget (2026-08-06, Denis: "у нас в
# экстракторе net-aliases, не таблица") ──────────────────────────────────

def test_net_aliases_table_has_one_row_per_distinct_net(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("C1", "C_IN", "X", {"1": "+3V3", "2": "GND"})])

    assert dock.nets_table.rowCount() == 2
    net_names = {dock.nets_table.item(row, 0).text() for row in range(dock.nets_table.rowCount())}
    assert net_names == {"+3V3", "GND"}


def test_net_aliases_table_net_column_is_read_only(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("C1", "C_IN", "X", {"1": "+3V3"})])

    item = dock.nets_table.item(0, 0)
    assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)


def test_net_aliases_table_alias_and_checkbox_are_cell_widgets(main_window, tmp_path):
    """Alias/Rule-net stay reachable through the same _net_alias_edits/
    _rule_net_checkboxes dicts as before (unchanged data flow) — verify
    they're also actually the table's own cell widgets, in the right
    columns, not just tracked separately."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("C1", "C_IN", "X", {"1": "+3V3"})])

    row = next(r for r in range(dock.nets_table.rowCount()) if dock.nets_table.item(r, 0).text() == "+3V3")
    assert dock.nets_table.cellWidget(row, 1) is dock._net_alias_edits["+3V3"]
    assert dock.nets_table.cellWidget(row, 2) is dock._rule_net_checkboxes["+3V3"]


# ── Cell/Profile file pickers as independent combos (2026-08-06, Denis:
# "имя файла, куда пишем extract и cell... тоже, выпадашками" — un-couples
# them from always following the same ConfigTreeDock click) ─────────────

def _combo_index_for_filename(combo, filename):
    """display_path() shows a path relative to PROJECT_ROOT (falling back
    to absolute outside it) — under tmp_path (always outside the real
    PROJECT_ROOT) that's the full absolute path, not a bare filename, so
    tests match on itemData's own .name instead of item TEXT."""
    for i in range(combo.count()):
        if combo.itemData(i).name == filename:
            return i
    return -1


def test_set_root_path_populates_both_file_combos(tmp_path, main_window):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = ExtractDock(main_window)

    dock.set_root_path(root)

    target_names = {dock.target_file_combo.itemData(i).name for i in range(dock.target_file_combo.count())}
    profile_names = {dock.profile_file_combo.itemData(i).name for i in range(dock.profile_file_combo.count())}
    assert target_names == {"root.yaml", "sub.yaml"}
    assert profile_names == {"root.yaml", "sub.yaml"}


def test_picking_the_target_combo_calls_set_target_file(tmp_path, main_window):
    (tmp_path / "sub.yaml").write_text("cells: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - sub.yaml\n", encoding="utf-8")
    dock = ExtractDock(main_window)
    dock.set_root_path(root)

    dock.target_file_combo.setCurrentIndex(_combo_index_for_filename(dock.target_file_combo, "sub.yaml"))

    assert dock._target_path is not None
    assert dock._target_path.name == "sub.yaml"


def test_target_and_profile_files_can_genuinely_differ(tmp_path, main_window):
    """The whole point of the request — previously these two always
    followed the same ConfigTreeDock click and could never diverge."""
    (tmp_path / "cells_only.yaml").write_text("cells: {}\n", encoding="utf-8")
    (tmp_path / "profiles_only.yaml").write_text("extract_profiles: {}\n", encoding="utf-8")
    root = tmp_path / "root.yaml"
    root.write_text("include:\n  - cells_only.yaml\n  - profiles_only.yaml\n", encoding="utf-8")
    dock = ExtractDock(main_window)
    dock.set_root_path(root)

    dock.target_file_combo.setCurrentIndex(
        _combo_index_for_filename(dock.target_file_combo, "cells_only.yaml"))
    dock.profile_file_combo.setCurrentIndex(
        _combo_index_for_filename(dock.profile_file_combo, "profiles_only.yaml"))

    assert dock._target_path.name == "cells_only.yaml"
    assert dock._profile_path.name == "profiles_only.yaml"
    assert dock._target_path != dock._profile_path


def test_set_target_file_reflects_into_the_combo_even_before_root_is_known(tmp_path, main_window):
    """ConfigTreeDock's own file_selected click must keep working exactly
    as before, even for a dock that never got set_root_path() yet (or a
    file outside the include graph) — same fallback PlacerDock's
    cell_combo/set_selected_cell already relies on."""
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("cells: {}\n", encoding="utf-8")
    dock = ExtractDock(main_window)

    dock.set_target_file(cells_file)

    assert dock.target_file_combo.currentData() == cells_file
    assert dock._target_path == cells_file


def test_file_combos_are_closed_pickers_not_free_text_fields(main_window):
    dock = ExtractDock(main_window)
    assert not dock.target_file_combo.isEditable()
    assert not dock.profile_file_combo.isEditable()


def test_profile_key_field_has_an_explanatory_tooltip(main_window, tmp_path):
    """Denis, live 2026-08-06: "я постоянно забываю" what Profile key even
    is — a hover reminder beats asking again next time."""
    dock = ExtractDock(main_window)
    assert dock.profile_key_edit.toolTip()
    assert "extract_profiles" in dock.profile_key_edit.toolTip()


def test_cluster_slug_default_when_nothing_matches(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "PWR/DAC0", {"1": "+3V3"})])
    assert dock.name_edit.text() == "pwr_dac0"


def test_cluster_slug_does_not_stomp_manual_typing(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.name_edit.setText("my_custom_name")
    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "OTHER/CLUSTER", {"1": "+3V3"})])
    assert dock.name_edit.text() == "my_custom_name"


def test_existing_cell_key_beats_raw_cluster_slug(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "existing_manual_name": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"},
    }})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "Existing Manual Name", {"1": "+3V3"})])
    assert dock.name_edit.text() == "existing_manual_name"


def test_clicking_profile_pulls_aliases_role_and_origin(main_window, tmp_path):
    """Reproduces this project's own real data shape (profile key !=
    cell name, Cluster name that doesn't slugify to match either one) —
    found live 2026-08-01 that this is exactly why the cluster auto-match
    path never fires on the real board, and clicking is the path that
    actually matters."""
    cells_dir = tmp_path / "templates"
    cells_dir.mkdir()
    cells_file = cells_dir / "test.yaml"
    _write_yaml(cells_file, {"cells": {"2v5_adj_pi_filter": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}})

    extractor_file = tmp_path / "test_extract.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "templates/test.yaml",
                "name": "2v5_adj_pi_filter",
                "params": {"PWR_OUT": "-2V5", "PWR_IN": "-2V5_DIRTY"},
                "origin_by_component_role": "C_IN_BYPASS",
                "origin_by_component_pad": "1",
            }
        }
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)

    sel = [
        FakeSelected("C22", "C_OUT_BULK", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "GND"}),
        FakeSelected("C26", "C_OUT_BYPASS", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "GND"}),
        FakeSelected("C19", "C_IN_BYPASS", "Out_Pi_Filter_N2V5", {"1": "-2V5_DIRTY", "2": "GND"}),
        FakeSelected("FB6", "PI_FILTER_FB", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "-2V5_DIRTY"}),
    ]
    dock.set_board_selection([], sel)

    # Cluster slug ("out_pi_filter_n2v5") matches neither cell nor profile
    # key -> Cell name only got the raw-slug fallback, Profile key (which
    # has no such fallback) stayed empty; confirming the auto-match-by-key
    # path is a no-op here before the click.
    assert dock.name_edit.text() == "out_pi_filter_n2v5"
    assert dock.profile_key_edit.text() == ""

    item = dock.profiles_list.findItems("n2v5_adj_pi_filter", Qt.MatchFlag.MatchExactly)[0]
    dock.profiles_list.itemClicked.emit(item)

    assert dock.profile_key_edit.text() == "n2v5_adj_pi_filter"
    assert dock._net_alias_edits["-2V5"].text() == "PWR_OUT"
    assert dock._net_alias_edits["-2V5_DIRTY"].text() == "PWR_IN"
    assert dock.origin_mode_combo.currentIndex() == 1
    assert dock.origin_role_combo.currentText() == "C_IN_BYPASS"
    assert dock.origin_pad_edit.text() == "1"

    # FB6/PI_FILTER_FB sits on both aliased nets -> ambiguous row appears,
    # but this profile predates net_template_role, so nothing to pull:
    # stays unresolved, requiring one manual pick.
    assert "PI_FILTER_FB" in dock._net_template_role_edits
    assert dock._net_template_role_edits["PI_FILTER_FB"].currentText() == ""


def test_clicking_cell_cross_references_matching_profile(main_window, tmp_path):
    cells_dir = tmp_path / "templates"
    cells_dir.mkdir()
    cells_file = cells_dir / "test.yaml"
    _write_yaml(cells_file, {"cells": {"2v5_adj_pi_filter": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}})

    extractor_file = tmp_path / "test_extract.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "templates/test.yaml",
                "name": "2v5_adj_pi_filter",
                "params": {"PWR_OUT": "-2V5"},
            }
        }
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_board_selection([], [FakeSelected("C22", "C_OUT_BULK", "Anything", {"1": "-2V5"})])

    item = dock.cells_list.findItems("2v5_adj_pi_filter", Qt.MatchFlag.MatchExactly)[0]
    dock.cells_list.itemClicked.emit(item)

    assert dock.name_edit.text() == "2v5_adj_pi_filter"
    assert dock.profile_key_edit.text() == "n2v5_adj_pi_filter"
    assert dock._net_alias_edits["-2V5"].text() == "PWR_OUT"


def test_net_alias_positional_fallback_on_rail_swap(main_window, tmp_path):
    """A profile's params recorded against one rail ('+2V5') should still
    populate the alias rows for an analogous selection on a different
    rail ('-2V5') — no literal in common, falls back to declared order."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extractor.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "cells.yaml",
                "params": {"PWR_IN": "+2V5", "PWR_OUT": "+2V5_DIRTY"},
            }
        }
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_board_selection([], [
        FakeSelected("D1", "SOME_ROLE", "X", {"1": "-2V5", "2": "-2V5_DIRTY", "3": "GND"}),
    ])

    item = dock.profiles_list.findItems("n2v5_adj_pi_filter", Qt.MatchFlag.MatchExactly)[0]
    dock.profiles_list.itemClicked.emit(item)

    assert dock._net_alias_edits["-2V5"].text() == "PWR_IN"
    assert dock._net_alias_edits["-2V5_DIRTY"].text() == "PWR_OUT"
    assert dock._net_alias_edits["GND"].text() == ""


def test_tabs_have_the_expected_labels(main_window, tmp_path):
    """2026-08-04 (Denis: "плашка отказывается переразмериваться") — Origin/
    Net aliases/Net template role/Existing moved from one long stacked
    QVBoxLayout into a QTabWidget, so the dock's minimum height is that of
    ONE page, not the sum of all of them."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    assert [dock._tabs.tabText(i) for i in range(dock._tabs.count())] == [
        "Origin", "Net aliases", "Net template role", "Existing"]


def test_net_template_role_tab_hidden_until_ambiguous(main_window, tmp_path):
    """The tab (not just the section widget) is what gets shown/hidden now
    — setTabVisible() replaced the old setVisible() on the section itself
    (see _update_net_template_role_rows). A role only becomes ambiguous
    once 2+ of its pads' nets have a non-empty alias (see that method's
    docstring), not merely by selecting a bridging component."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False

    dock.set_board_selection([], [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"})])
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False  # no aliases typed yet

    dock._net_alias_edits["-2V5"].setText("PWR_IN")
    dock._net_alias_edits["-2V5_DIRTY"].setText("PWR_OUT")
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is True


def test_net_template_role_blocks_extraction_until_resolved(main_window, tmp_path, monkeypatch):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection([], [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"})])
    dock._net_alias_edits["-2V5"].setText("PWR_IN")
    dock._net_alias_edits["-2V5_DIRTY"].setText("PWR_OUT")
    assert "PI_FILTER_FB" in dock._net_template_role_edits

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("n2v5_adj_pi_filter")
    dock._raw_items = [object()]
    dock._on_extract()
    assert "PI_FILTER_FB" in dock.message_label.text()
    assert yaml.safe_load(cells_file.read_text()) in (None, {})

    dock._net_template_role_edits["PI_FILTER_FB"].setCurrentText("-2V5")
    # Full success-path extract: runs synchronously via the _do_extract() core
    # (the async _on_extract() path would race the read on the next line).
    dock._do_extract()
    saved = yaml.safe_load(cells_file.read_text())
    assert "n2v5_adj_pi_filter" in saved["cells"]


def test_placer_gets_include_entries_deduped(main_window, tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    cells_file = templates_dir / "test.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extracts.yaml"
    _write_yaml(extractor_file, {})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_placer_file(placer_file)

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock.save_profile_checkbox.setChecked(True)
    dock.profile_key_edit.setText("some_profile")
    dock._raw_items = [object()]
    dock._do_extract()

    placer_data = yaml.safe_load(placer_file.read_text())
    # cells_file:/cell_files: were folded into include: 2026-08-02 — both the
    # Cells file and the Extractor file are now wired the same way.
    assert placer_data["include"] == ["templates/test.yaml", "extracts.yaml"]
    assert placer_data["clone_placements"] == []  # untouched, not overwritten

    # A second extraction under a different name must not duplicate entries.
    dock._last_autofill_key = None
    dock.name_edit.setText("another_cell")
    dock.profile_key_edit.setText("another_profile")
    dock._do_extract()

    placer_data2 = yaml.safe_load(placer_file.read_text())
    assert placer_data2["include"] == ["templates/test.yaml", "extracts.yaml"]


def test_placer_wiring_skips_include_for_a_root_shaped_extractor_file(main_window, tmp_path, monkeypatch):
    """Reproduces the real failure found live 2026-08-01: an Extractor
    file that's ALSO a full root config (registry_path/schematic_dir/...,
    e.g. because it predates being assigned this role, or is reused as a
    standalone config too) can't be include:'d — config/includes.py only
    merges rules/clone_placements/cells/points/extract_profiles/
    clone_profiles from an included file, everything else is fatal there.
    Blindly adding include: left the Placer file unloadable the next time
    anything read it (Redraw, or a real `apply`)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extractor.yaml"
    _write_yaml(extractor_file, {"registry_path": "registries/x.json", "schematic_dir": "../sch"})
    placer_file = tmp_path / "root.yaml"
    _write_yaml(placer_file, {"clone_placements": []})

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_placer_file(placer_file)

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock.save_profile_checkbox.setChecked(True)
    dock.profile_key_edit.setText("some_profile")
    dock._raw_items = [object()]
    dock._do_extract()

    assert "root-config-only" in dock.message_label.text()
    placer_data = yaml.safe_load(placer_file.read_text())
    # cells_file:/cell_files: were folded into include: 2026-08-02 — the
    # Cells file is still wired via include:, only the root-shaped Extractor
    # file's entry is skipped.
    assert placer_data["include"] == ["cells.yaml"]  # unaffected, still added

    # The written extractor file must still actually load_config() cleanly
    # via include: once it stops carrying root-only keys — confirms the
    # guard's diagnosis is real, not just a plausible-sounding message.
    from kicadstamp.config import load_config
    extractor_data = yaml.safe_load(extractor_file.read_text())
    del extractor_data["registry_path"]
    del extractor_data["schematic_dir"]
    _write_yaml(extractor_file, extractor_data)
    _write_yaml(placer_file, {**yaml.safe_load(placer_file.read_text()), "include": ["cells.yaml", "extractor.yaml"]})
    load_config(str(placer_file))  # must not raise, now that extractor.yaml is include:-safe


# ── net_from_role auto-classification summary ────────────────────────────

def test_summarize_net_from_role_returns_none_without_any(main_window):
    dock = ExtractDock(main_window)
    template_dict = {"cell1": {"vias": [{"net": "GND"}], "tracks": [], "components": [], "layer": "F.Cu"}}
    assert dock._summarize_net_from_role(template_dict) is None


def test_summarize_net_from_role_lists_roles_and_pads(main_window):
    dock = ExtractDock(main_window)
    template_dict = {"cell1": {
        "vias": [{"net_from_role": "C_IN_BULK"}],
        "tracks": [{"net_from_role": "LDO", "net_from_role_pad": "2"}],
        "components": [], "layer": "F.Cu",
    }}
    summary = dock._summarize_net_from_role(template_dict)
    assert "2 via/track net(s)" in summary
    assert "C_IN_BULK" in summary
    assert "LDO/pad:2" in summary


def test_extract_shows_net_from_role_summary_on_success(main_window, tmp_path, monkeypatch):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    def _fake_extract_with_role(adapter, name, params=None, items=None, annotations=None, **kwargs):
        return {name: {"vias": [{"net_from_role": "C_IN_BULK"}], "components": [], "tracks": [], "layer": "F.Cu"}}

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract_with_role)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock._raw_items = [object()]
    dock._do_extract()

    assert "auto-classified by role" in dock.message_label.text()
    assert "C_IN_BULK" in dock.message_label.text()


def test_on_extract_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    """Phase 5.2 — the Extract button must NOT block the UI thread:
    _on_extract() collects + validates the inputs on the UI thread, then
    hands the plain-data payload to start_long_op with the shared connection,
    the guard widget, and the split run/finish callbacks (the result comes
    back through a queued signal, so the socket is never held by two owners)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    main_window.connection.board = FakeBoard()

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        captured["fn"] = fn
        captured["on_success"] = on_success
        captured["on_error"] = on_error
        captured["args"] = args
        return "fake-controller"

    monkeypatch.setattr(extract_mod, "start_long_op", _fake_start)

    dock.name_edit.setText("some_cell")
    dock.profile_key_edit.setText("some_profile")
    dock._raw_items = [object()]
    dock._on_extract()

    # The controller reference is kept on the dock so a parent-less QThread
    # isn't garbage-collected mid-flight.
    assert dock._active_op == "fake-controller"

    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (dock.extract_button,)
    # Bound methods: each access creates a fresh object, so compare with ==
    # (equality checks __self__ + __func__) rather than `is`.
    assert captured["fn"] == dock._run_extract
    assert captured["on_success"] == dock._finish_extract
    assert captured["on_error"] == dock._on_extract_failed

    # The payload is plain data for the worker — board/adapter included, but
    # no widget references.
    payload = captured["args"][0]
    assert payload["name"] == "some_cell"
    assert payload["profile_key"] == "some_profile"
    assert payload["save_profile"] is False
    assert payload["board"] is main_window.connection.board
    assert payload["target_path"] == cells_file
    assert payload["placer_path"] is None
    assert payload["params"] == {}
    assert payload["rule_nets"] == set()
    assert payload["origin_kwargs"] == {}
    assert payload["net_template_role"] == {}
    assert payload["raw_items"] == dock._raw_items


# ── Rule net checkbox (2026-08-05) ──────────────────────────────────────────

def test_checking_rule_net_clears_and_disables_the_alias_edit(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "X", {"1": "+3V3_VCCIO"})])

    edit = dock._net_alias_edits["+3V3_VCCIO"]
    edit.setText("PWR")
    dock._rule_net_checkboxes["+3V3_VCCIO"].setChecked(True)

    assert edit.text() == ""
    assert edit.isEnabled() is False

    dock._rule_net_checkboxes["+3V3_VCCIO"].setChecked(False)
    assert edit.isEnabled() is True


def test_collect_inputs_includes_checked_rule_nets(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_board_selection([], [
        FakeSelected("D1", "SOME_ROLE", "X", {"1": "+3V3_VCCIO", "2": "GND"}),
    ])
    dock._rule_net_checkboxes["+3V3_VCCIO"].setChecked(True)
    dock.name_edit.setText("some_cell")
    dock._raw_items = [object()]
    main_window.connection.board = FakeBoard()

    payload = dock._collect_extract_inputs()

    assert payload["rule_nets"] == {"+3V3_VCCIO"}
    assert "GND" not in payload["rule_nets"]


def test_extract_persists_rule_nets_into_the_profile(main_window, tmp_path, monkeypatch):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extractor.yaml"
    _write_yaml(extractor_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "X", {"1": "+3V3_VCCIO"})])
    dock._rule_net_checkboxes["+3V3_VCCIO"].setChecked(True)

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock.save_profile_checkbox.setChecked(True)
    dock.profile_key_edit.setText("some_profile")
    dock._raw_items = [object()]
    dock._do_extract()

    profile = yaml.safe_load(extractor_file.read_text())["extract_profiles"]["some_profile"]
    assert profile["rule_nets"] == ["+3V3_VCCIO"]


def test_clicking_profile_re_checks_its_rule_nets(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    extractor_file = tmp_path / "extractor.yaml"
    _write_yaml(extractor_file, {
        "extract_profiles": {
            "some_profile": {"output": "cells.yaml", "rule_nets": ["+3V3_VCCIO"]},
        }
    })
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_profile_file(extractor_file)
    dock.set_board_selection([], [
        FakeSelected("D1", "SOME_ROLE", "X", {"1": "+3V3_VCCIO", "2": "GND"}),
    ])

    item = dock.profiles_list.findItems("some_profile", Qt.MatchFlag.MatchExactly)[0]
    dock.profiles_list.itemClicked.emit(item)

    assert dock._rule_net_checkboxes["+3V3_VCCIO"].isChecked() is True
    assert dock._rule_net_checkboxes["GND"].isChecked() is False


# ── Cluster filter (2026-08-12, Denis: an area-select around new components
# placed close to an already-placed Rule/Pi-filter sweeps up the neighbours'
# components too) ────────────────────────────────────────────────────────

def test_cluster_filter_hidden_for_a_single_cluster_selection(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    main_window.show()  # isVisible() needs the whole ancestor chain shown

    dock.set_board_selection([_fake_fp("R18")],
                              [FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {})])

    assert dock.cluster_filter_checkbox.isVisible() is False
    assert dock.cluster_filter_combo.isVisible() is False


def test_cluster_filter_shown_and_defaults_to_majority_cluster(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    main_window.show()  # isVisible() needs the whole ancestor chain shown

    dock.set_board_selection(
        [_fake_fp("R18"), _fake_fp("R19"), _fake_fp("C5")],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("R19", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])

    assert dock.cluster_filter_checkbox.isVisible() is True
    assert dock.cluster_filter_combo.isVisible() is True
    assert dock.cluster_filter_combo.currentData() == "FPGA_PERIPH"


def test_cluster_filter_excludes_other_cluster_footprints_but_keeps_vias(main_window, tmp_path):
    """The concrete case reported live: R18-R23/R25-R32 tagged
    Cluster=FPGA_PERIPH selected alongside a neighbouring Pi-filter's
    components (Cluster=PIF_P5V) — checking the filter should drop the
    Pi-filter's footprint from the extract payload, but leave a selected via
    alone (no Cluster field to filter it by, see module docstring)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.name_edit.setText("fpga_periph")
    main_window.connection.board = FakeBoard()

    fp_r18, fp_r19, fp_c5 = _fake_fp("R18"), _fake_fp("R19"), _fake_fp("C5")
    via = _fake_via("GND")
    dock.set_board_selection(
        [fp_r18, fp_r19, fp_c5, via],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("R19", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    dock.cluster_filter_checkbox.setChecked(True)  # combo already defaults to FPGA_PERIPH

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {fp_r18, fp_r19, via}


def test_cluster_filter_unchecked_keeps_full_selection(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.name_edit.setText("fpga_periph")
    main_window.connection.board = FakeBoard()

    fp_r18, fp_c5 = _fake_fp("R18"), _fake_fp("C5")
    dock.set_board_selection(
        [fp_r18, fp_c5],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    # cluster_filter_checkbox left unchecked (default)

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {fp_r18, fp_c5}


def test_cluster_filter_updates_selection_label_and_warning(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)

    dock.set_board_selection(
        [_fake_fp("R18"), _fake_fp("R19"), _fake_fp("C5")],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("R19", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    assert dock.selection_label.text() == "3 component(s) selected"
    assert "multiple Clusters" in dock.cluster_warning_label.text()
    assert "filtered" not in dock.cluster_warning_label.text()

    dock.cluster_filter_checkbox.setChecked(True)

    assert dock.selection_label.text() == "2 component(s) selected"
    assert "keeping 2 of 3" in dock.cluster_warning_label.text()


def test_cluster_filter_resets_when_selection_no_longer_spans_clusters(main_window, tmp_path):
    """A stale filter must not silently keep applying once the live
    selection no longer spans multiple Clusters (e.g. the user deselected
    down to just their own components) — the checkbox/combo hide AND the
    checkbox unchecks, so a later re-selection starts from a clean state."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    main_window.show()  # isVisible() needs the whole ancestor chain shown

    dock.set_board_selection(
        [_fake_fp("R18"), _fake_fp("C5")],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    dock.cluster_filter_checkbox.setChecked(True)

    dock.set_board_selection([_fake_fp("R18")],
                              [FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {})])

    assert dock.cluster_filter_checkbox.isChecked() is False
    assert dock.cluster_filter_checkbox.isVisible() is False


# ── Cluster filter's Via/Track half — registry.json UUID check
# (2026-08-12, second pass: Denis rejected net-name matching, since a
# shared net like GND can't tell two Clusters' vias apart; UUID identity
# against the Placer file's registry.json can) ─────────────────────────────

def test_registry_filter_excludes_via_already_in_placer_registry(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    placer_file = tmp_path / "placer.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    _write_registry(tmp_path / "placer.registry.json", {
        "pad:1|pif_p5v|C_IN_BULK|0": {
            "uuid": "via-uuid-foreign", "x_mm": 0, "y_mm": 0,
            "net": "GND", "drill_mm": 0.3, "diameter_mm": 0.6,
        },
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.name_edit.setText("fpga_periph")
    main_window.connection.board = FakeBoard()

    fp_r18 = _fake_fp("R18")
    foreign_via = _fake_via("GND", uuid="via-uuid-foreign")
    own_via = _fake_via("GND", uuid="via-uuid-mine")
    dock.set_board_selection(
        [fp_r18, _fake_fp("C5"), foreign_via, own_via],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    dock.cluster_filter_checkbox.setChecked(True)

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {fp_r18, own_via}


def test_registry_filter_excludes_track_already_in_placer_registry(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    placer_file = tmp_path / "placer.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    _write_registry(tmp_path / "placer.tracks.registry.json", {
        "pad:1|pif_p5v|C_IN_BULK|0": {
            "uuid": "track-uuid-foreign", "start_x_mm": 0, "start_y_mm": 0,
            "end_x_mm": 1, "end_y_mm": 1, "width_mm": 0.25, "net": "GND", "layer": "F.Cu",
        },
    })

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.name_edit.setText("fpga_periph")
    main_window.connection.board = FakeBoard()

    fp_r18 = _fake_fp("R18")
    foreign_track = _fake_track(uuid="track-uuid-foreign")
    own_track = _fake_track(uuid="track-uuid-mine")
    dock.set_board_selection(
        [fp_r18, _fake_fp("C5"), foreign_track, own_track],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    dock.cluster_filter_checkbox.setChecked(True)

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {fp_r18, own_track}


def test_registry_filter_is_a_noop_without_a_placer_file(main_window, tmp_path):
    """No Placer file assigned -> nothing to check the registry against —
    Via/Track pass through untouched, only footprint-by-Cluster filtering
    applies (documented behaviour, not a bug)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.name_edit.setText("fpga_periph")
    main_window.connection.board = FakeBoard()

    fp_r18 = _fake_fp("R18")
    via = _fake_via("GND", uuid="via-uuid-whatever")
    dock.set_board_selection(
        [fp_r18, _fake_fp("C5"), via],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    dock.cluster_filter_checkbox.setChecked(True)

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {fp_r18, via}


def test_registry_uuids_cached_until_placer_file_changes(main_window, tmp_path, monkeypatch):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    placer_file = tmp_path / "placer.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    _write_registry(tmp_path / "placer.registry.json", {})

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_placer_file(placer_file)

    import kicadstamp.registry as registry_mod
    calls = []
    original = registry_mod.load_registry

    def _counting_load_registry(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(registry_mod, "load_registry", _counting_load_registry)

    dock.set_board_selection([_fake_fp("R18"), _fake_fp("C5")], [
        FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
        FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
    ])
    dock.cluster_filter_checkbox.setChecked(True)
    dock._registry_uuids()
    dock._registry_uuids()

    assert len(calls) == 1  # second call served from cache, not re-read from disk

    other_placer = tmp_path / "other_placer.yaml"
    _write_yaml(other_placer, {"clone_placements": []})
    _write_registry(tmp_path / "other_placer.registry.json", {})
    dock.set_placer_file(other_placer)
    dock._registry_uuids()

    assert len(calls) == 2  # cache invalidated by the Placer file changing


def test_registry_filter_survives_a_missing_registry_file(main_window, tmp_path):
    """A Placer file that was never `apply`'d yet has no registry.json at
    all — must be treated as "nothing registered" (empty sets), not an
    error, so a first-ever extraction on a brand new Placer file still
    works with the Cluster filter checked."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    placer_file = tmp_path / "placer.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    # deliberately no placer.registry.json / placer.tracks.registry.json

    dock = ExtractDock(main_window)
    dock.set_target_file(cells_file)
    dock.set_placer_file(placer_file)
    dock.name_edit.setText("fpga_periph")
    main_window.connection.board = FakeBoard()

    fp_r18 = _fake_fp("R18")
    via = _fake_via("GND", uuid="via-uuid-whatever")
    dock.set_board_selection(
        [fp_r18, _fake_fp("C5"), via],
        [
            FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {}),
            FakeSelected("C5", "C_IN_BULK", "PIF_P5V", {}),
        ])
    dock.cluster_filter_checkbox.setChecked(True)

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {fp_r18, via}
