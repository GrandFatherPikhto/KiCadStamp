# tests/gui/test_extract_dock.py
from unittest.mock import MagicMock

import json

import yaml
from kicadstamp.domain.geometry import BoardLayer
from kicadstamp.domain.geometry import Vector2

from kicadstamp.config import ClonePlacement
from kicadstamp.domain.board import Footprint, Track, Via
from PyQt6.QtCore import Qt

import gui.docks.extract as extract_mod
from gui.docks.extract import ExtractDock, SubPlacementCandidate


class FakeSelected:
    def __init__(self, ref, role, cluster, nets, fp=None):
        self.ref, self.role, self.cluster, self.nets = ref, role, cluster, nets
        # Raw FootprintInstance escape hatch (see kicadstamp/explore.py's
        # Selected) — only needed by the Auto-role classification preview
        # (_classify_selection_nets builds role_nets from Selected.fp); tests
        # that don't care leave it None and every net reads fallback.
        self.fp = fp


class FakeAdapter:
    pass


class FakeBoard:
    adapter = FakeAdapter()


def _write_yaml(path, data) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _fake_extract(adapter, name, params=None, items=None, annotations=None, **kwargs):
    return {name: {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}}


def _fake_fp(ref):
    """A real domain Footprint so isinstance() checks (both in this dock's
    own _filtered_selection() and in kicadstamp code) treat it as a
    footprint."""
    return Footprint(ref=ref, uuid=f"uuid-{ref}", position=Vector2.from_xy(0, 0),
                     angle_deg=0.0, layer=BoardLayer.BL_F_Cu)


def _fake_via(net_name, uuid="via-uuid-unregistered"):
    return Via(uuid=uuid, position=Vector2.from_xy(0, 0), net_name=net_name,
               drill_mm=0.3, diameter_mm=0.6)


def _fake_track(net_name="GND", uuid="track-uuid-unregistered"):
    return Track(uuid=uuid, start=Vector2.from_xy(0, 0), end=Vector2.from_xy(0, 0),
                 net_name=net_name, width_mm=0.25, layer=BoardLayer.BL_F_Cu)


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
    dock.set_root_path(cells_file)

    dock.set_board_selection([], [FakeSelected("C1", "C_IN", "X", {"1": "+3V3", "2": "GND"})])

    assert dock.nets_table.rowCount() == 2
    net_names = {dock.nets_table.item(row, 0).text() for row in range(dock.nets_table.rowCount())}
    assert net_names == {"+3V3", "GND"}


def test_net_aliases_table_net_column_is_read_only(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

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
    dock.set_root_path(cells_file)

    dock.set_board_selection([], [FakeSelected("C1", "C_IN", "X", {"1": "+3V3"})])

    row = next(r for r in range(dock.nets_table.rowCount()) if dock.nets_table.item(r, 0).text() == "+3V3")
    assert dock.nets_table.cellWidget(row, 1) is dock._net_alias_edits["+3V3"]
    assert dock.nets_table.cellWidget(row, 2) is dock._rule_net_checkboxes["+3V3"]


# ── Auto-role column: which nets already resolve by role (2026-08-13, plan
# net_alias_optional_gui — aliases stop looking mandatory) ──────────────

def _classification_board(monkeypatch, role_nets):
    """Injects a FakeBoard + monkeypatched selection_role_nets so the dock's
    _classify_selection_nets has real role->net evidence to classify against.
    The classification itself (suggest_net_from_role -> classify_net) is the
    REAL core code — only the role_nets SOURCE is faked, keeping these tests
    honest about lemma2/pad/fallback semantics."""
    monkeypatch.setattr(extract_mod, "selection_role_nets", lambda adapter, fps: role_nets)
    return FakeBoard()


def test_auto_role_column_is_the_fourth_readonly_column(main_window):
    dock = ExtractDock(main_window)
    assert [dock.nets_table.horizontalHeaderItem(i).text()
            for i in range(dock.nets_table.columnCount())] == [
        "Net", "Alias", "Rule net (null)", "Auto-role"]


def test_auto_role_shows_lemma2_role_and_disables_alias(main_window, tmp_path, monkeypatch):
    """Plan test (a): a net that unambiguously resolves by role (lemma 2 — the
    role's only non-rule net) gets a filled Auto-role cell and a disabled Alias
    edit with an explanatory tooltip (no-override decision)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "R_SERIES": {"1": {"FPGA_SIG"}, "2": {"FPGA_SIG"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    dock.set_board_selection(
        [_fake_fp("R1")],
        [FakeSelected("R1", "R_SERIES", "X", {"1": "FPGA_SIG", "2": "FPGA_SIG"}, fp=object())])

    row = next(r for r in range(dock.nets_table.rowCount())
               if dock.nets_table.item(r, 0).text() == "FPGA_SIG")
    assert dock.nets_table.item(row, 3).text() == "role: R_SERIES"
    assert dock._net_alias_edits["FPGA_SIG"].isEnabled() is False
    assert "R_SERIES" in dock._net_alias_edits["FPGA_SIG"].toolTip()


def test_auto_role_stays_empty_and_alias_active_for_fallback(main_window, tmp_path, monkeypatch):
    """Plan test (b): a net no selected role covers (fallback) keeps an empty
    Auto-role cell and an active Alias edit — exactly the pre-change behaviour,
    since fallback nets are the only ones where an alias still means something."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "C_IN": {"1": {"+3V3"}, "2": {"GND"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    # "+1V8" is not on any role's pad -> fallback.
    dock.set_board_selection(
        [_fake_fp("C1")],
        [FakeSelected("C1", "C_IN", "X", {"1": "+1V8"}, fp=object())])

    row = next(r for r in range(dock.nets_table.rowCount())
               if dock.nets_table.item(r, 0).text() == "+1V8")
    assert dock.nets_table.item(row, 3).text() == ""
    assert dock._net_alias_edits["+1V8"].isEnabled() is True


def test_auto_role_gnd_is_fallback_not_pretend_owned(main_window, tmp_path, monkeypatch):
    """GND is an intrinsic rule net (RULE_NETS) — it must NOT read as "owned
    by a role". Without that, every rail+GND cap role would look like a "2+
    classifying nets" bridging component (plan step 5's trap)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "C_IN_BULK": {"1": {"+3V3"}, "2": {"GND"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    dock.set_board_selection(
        [_fake_fp("C1")],
        [FakeSelected("C1", "C_IN_BULK", "X", {"1": "+3V3", "2": "GND"}, fp=object())])

    gnd_row = next(r for r in range(dock.nets_table.rowCount())
                   if dock.nets_table.item(r, 0).text() == "GND")
    assert dock.nets_table.item(gnd_row, 3).text() == ""
    assert dock._net_alias_edits["GND"].isEnabled() is True

    # +3V3 sits on a multi-net role -> "pad" (some role exists), alias disabled.
    v33_row = next(r for r in range(dock.nets_table.rowCount())
                   if dock.nets_table.item(r, 0).text() == "+3V3")
    assert dock.nets_table.item(v33_row, 3).text() == "role: C_IN_BULK"
    assert dock._net_alias_edits["+3V3"].isEnabled() is False


# ── 2026-08-13 bug fixes: Rule net vs classification, stale tooltip ───────

def test_rule_net_checked_net_does_not_make_the_role_ambiguous(main_window, tmp_path, monkeypatch):
    """Bug 3 (handoff_2026_08_13_focus_and_autorole_bugs): a net marked
    "Rule net" is excluded from the net-template-role ambiguity trigger — at
    extraction it becomes net: null and takes NO part in the role
    classification (template_extraction.py zeroes rule_nets before
    _suggest_net_from_role), so it must not force a net_template_role pick
    (which would seed a junk params entry)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "PI_FILTER_FB": {"1": {"-2V5"}, "2": {"-2V5_DIRTY"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_board_selection(
        [_fake_fp("FB6")],
        [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"}, fp=object())])

    # Both nets classify by role -> ambiguous without any Rule net.
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is True
    assert "PI_FILTER_FB" in dock._net_template_role_edits

    # Check "Rule net" on one of them -> it no longer counts -> not ambiguous.
    dock._rule_net_checkboxes["-2V5"].setChecked(True)
    dock._update_net_template_role_rows()

    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False
    assert "PI_FILTER_FB" not in dock._net_template_role_edits


def test_refresh_auto_role_cells_clears_a_stale_by_role_tooltip(main_window, tmp_path, monkeypatch):
    """Bug 4: when a net drops out of "classifies by role" back to fallback
    (same net NAMES, different role evidence), the Alias field is re-enabled
    AND its stale by-role tooltip is cleared — the old "your input will be
    ignored" tip must not keep lying on a now-live field."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "R_SERIES": {"1": {"FPGA_SIG"}, "2": {"FPGA_SIG"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_board_selection(
        [_fake_fp("R1")],
        [FakeSelected("R1", "R_SERIES", "X", {"1": "FPGA_SIG", "2": "FPGA_SIG"}, fp=object())])

    edit = dock._net_alias_edits["FPGA_SIG"]
    assert edit.toolTip() != ""  # by-role tooltip set while classified

    dock._net_auto_roles["FPGA_SIG"] = ("fallback", None)  # same net, no role evidence now
    dock._refresh_auto_role_cells()

    assert edit.toolTip() == ""
    assert edit.isEnabled() is True


def test_refresh_auto_role_cells_ignores_a_rule_net_checked_net(main_window, tmp_path, monkeypatch):
    """Bug 5: a net marked "Rule net" must NOT show "role: X" in the
    Auto-role column nor the by-role tooltip — extraction writes net: null
    for it, the role has nothing to do with it (the Alias edit was already
    correctly disabled; the column/tooltip just misled about the CAUSE)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "R_SERIES": {"1": {"FPGA_SIG"}, "2": {"FPGA_SIG"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_board_selection(
        [_fake_fp("R1")],
        [FakeSelected("R1", "R_SERIES", "X", {"1": "FPGA_SIG", "2": "FPGA_SIG"}, fp=object())])

    row = next(r for r in range(dock.nets_table.rowCount())
               if dock.nets_table.item(r, 0).text() == "FPGA_SIG")
    assert dock.nets_table.item(row, 3).text() == "role: R_SERIES"

    dock._rule_net_checkboxes["FPGA_SIG"].setChecked(True)
    dock._refresh_auto_role_cells()  # deterministic under test

    assert dock.nets_table.item(row, 3).text() == ""
    assert dock._net_alias_edits["FPGA_SIG"].toolTip() == ""
    # still disabled — now for the Rule-net reason, not the role
    assert dock._net_alias_edits["FPGA_SIG"].isEnabled() is False


# ── Tail of bug 5 (handoff_2026_08_13_autorole_rule_net_tail): the same
# "by role only when not Rule net" predicate in the other two paths ────────

def test_rule_net_click_immediately_clears_the_auto_role_visuals(main_window, tmp_path, monkeypatch):
    """Tail case (a): the Rule-net checkbox handler must refresh the Auto-role
    column/tooltip SYNCHRONOUSLY on the click — not wait ~400ms for the next
    _refresh_auto_role_cells tick (the Alias edit was already cleared/disabled
    instantly, but the column kept claiming "role: X" in the meantime)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "R_SERIES": {"1": {"FPGA_SIG"}, "2": {"FPGA_SIG"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_board_selection(
        [_fake_fp("R1")],
        [FakeSelected("R1", "R_SERIES", "X", {"1": "FPGA_SIG", "2": "FPGA_SIG"}, fp=object())])

    row = next(r for r in range(dock.nets_table.rowCount())
               if dock.nets_table.item(r, 0).text() == "FPGA_SIG")
    assert dock.nets_table.item(row, 3).text() == "role: R_SERIES"

    # Click the checkbox — NO _refresh_auto_role_cells() tick in between.
    dock._rule_net_checkboxes["FPGA_SIG"].setChecked(True)

    assert dock.nets_table.item(row, 3).text() == ""
    assert dock._net_alias_edits["FPGA_SIG"].toolTip() == ""


def test_rebuilt_table_row_respects_a_restored_rule_net_checkbox(main_window, tmp_path, monkeypatch):
    """Tail case (b): a full net-table rebuild (net-name set changed) with a
    previously-checked Rule net for the same net name must bring the fresh row
    in ALREADY showing no "role: X" and no by-role tooltip — not rebuild it
    from the raw classification and wait for the next tick to correct it."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "R_SERIES": {"1": {"FPGA_SIG"}, "2": {"FPGA_SIG"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    # First selection: FPGA_SIG classified by role; mark it Rule net.
    dock.set_board_selection(
        [_fake_fp("R1")],
        [FakeSelected("R1", "R_SERIES", "X", {"1": "FPGA_SIG", "2": "FPGA_SIG"}, fp=object())])
    dock._rule_net_checkboxes["FPGA_SIG"].setChecked(True)

    # Different net set (GND appears) -> full rebuild, not the inline path.
    dock.set_board_selection(
        [_fake_fp("R2")],
        [FakeSelected("R2", "R_SERIES", "X", {"1": "FPGA_SIG", "2": "GND"}, fp=object())])

    row = next(r for r in range(dock.nets_table.rowCount())
               if dock.nets_table.item(r, 0).text() == "FPGA_SIG")
    # checkbox restored checked, so the fresh row is already role-free
    assert dock._rule_net_checkboxes["FPGA_SIG"].isChecked() is True
    assert dock.nets_table.item(row, 3).text() == ""
    assert dock._net_alias_edits["FPGA_SIG"].toolTip() == ""


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


def test_prepare_new_profile_arms_the_extract_flow(main_window, tmp_path):
    """ConfigTreeDock's "Add extract profile..." delegate — not a blank form
    (an extract_profiles: entry's params come from a real board selection):
    it points the dock at the profile file, pre-checks the save checkbox,
    clears + focuses the profile-key field."""
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("cells: {}\n", encoding="utf-8")
    profile_file = tmp_path / "profiles.yaml"
    profile_file.write_text("extract_profiles: {}\n", encoding="utf-8")
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.save_profile_checkbox.setChecked(False)
    dock.profile_key_edit.setText("stale")

    dock.prepare_new_profile(profile_file)

    assert dock.save_profile_checkbox.isChecked()
    assert dock.profile_key_edit.text() == ""
    # hasFocus() is False in offscreen (the window is never shown/activated)
    # — focusWidget() reflects the window's set focus widget instead.
    assert dock.focusWidget() is dock.profile_key_edit
    assert dock._profile_path == dock._root_path


def test_prepare_new_profile_does_not_disturb_the_cells_context(main_window, tmp_path):
    """The profile key is independent of what's selected for extraction —
    prepare_new_profile must not change the target Cell file or the existing
    cells list content (set_profile_file's list refresh re-adds the SAME
    cells from the unchanged _target_path)."""
    cells_file = tmp_path / "cells.yaml"
    cells_file.write_text("cells:\n  alpha:\n    components: []\n", encoding="utf-8")
    profile_file = tmp_path / "profiles.yaml"
    profile_file.write_text("extract_profiles: {}\n", encoding="utf-8")
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    before_items = [dock.cells_list.item(i).text() for i in range(dock.cells_list.count())]
    before_target = dock._target_path

    dock.prepare_new_profile(profile_file)

    after_items = [dock.cells_list.item(i).text() for i in range(dock.cells_list.count())]
    assert after_items == before_items == ["alpha"]
    assert dock._target_path == before_target
    assert dock.profile_key_edit.text() == ""


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
    dock.set_root_path(cells_file)

    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "PWR/DAC0", {"1": "+3V3"})])
    assert dock.name_edit.text() == "pwr_dac0"


def test_cluster_slug_does_not_stomp_manual_typing(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    dock.name_edit.setText("my_custom_name")
    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "OTHER/CLUSTER", {"1": "+3V3"})])
    assert dock.name_edit.text() == "my_custom_name"


def test_existing_cell_key_beats_raw_cluster_slug(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"cells": {
        "existing_manual_name": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"},
    }})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "Existing Manual Name", {"1": "+3V3"})])
    assert dock.name_edit.text() == "existing_manual_name"


def test_clicking_profile_pulls_aliases_role_and_origin(main_window, tmp_path, monkeypatch):
    """Reproduces this project's own real data shape (profile key !=
    cell name, Cluster name that doesn't slugify to match either one) —
    found live 2026-08-01 that this is exactly why the cluster auto-match
    path never fires on the real board, and clicking is the path that
    actually matters. Role evidence is faked so FB6's two rails (-2V5 /
    -2V5_DIRTY) classify by role and the Net template role tab appears —
    now classification-driven, not alias-typing-driven (plan step 5)."""
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

    main_window.connection.board = _classification_board(monkeypatch, {
        "C_OUT_BULK": {"1": {"-2V5"}, "2": {"GND"}},
        "C_OUT_BYPASS": {"1": {"-2V5"}, "2": {"GND"}},
        "C_IN_BYPASS": {"1": {"-2V5_DIRTY"}, "2": {"GND"}},
        "PI_FILTER_FB": {"1": {"-2V5"}, "2": {"-2V5_DIRTY"}},
    })

    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_root_path(extractor_file)

    sel = [
        FakeSelected("C22", "C_OUT_BULK", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "GND"}, fp=object()),
        FakeSelected("C26", "C_OUT_BYPASS", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "GND"}, fp=object()),
        FakeSelected("C19", "C_IN_BYPASS", "Out_Pi_Filter_N2V5", {"1": "-2V5_DIRTY", "2": "GND"}, fp=object()),
        FakeSelected("FB6", "PI_FILTER_FB", "Out_Pi_Filter_N2V5", {"1": "-2V5", "2": "-2V5_DIRTY"}, fp=object()),
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
    # Both the cells: and extract_profiles: sections live in the ONE project
    # root (the GUI reads the whole include: graph since the file pickers were
    # removed, 2026-08-21) — the cross-reference is name-based: a profile
    # whose entry `name:` equals the clicked cell key is pulled in.
    root_file = tmp_path / "root.yaml"
    _write_yaml(root_file, {
        "cells": {"2v5_adj_pi_filter": {"vias": [], "components": [], "tracks": [], "layer": "F.Cu"}},
        "extract_profiles": {
            "n2v5_adj_pi_filter": {
                "output": "templates/test.yaml",
                "name": "2v5_adj_pi_filter",
                "params": {"PWR_OUT": "-2V5"},
            }
        },
    })

    dock = ExtractDock(main_window)
    dock.set_root_path(root_file)
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
    dock.set_root_path(cells_file)
    dock.set_root_path(extractor_file)
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
    ONE page, not the sum of all of them. "Sub-placements" (2026-08-25) is a
    hidden-by-default tab, same as "Net template role" — it must not break the
    tab order or the "only current page sizes the window" invariant."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    assert [dock._tabs.tabText(i) for i in range(dock._tabs.count())] == [
        "Origin", "Net aliases", "Net template role", "Sub-placements", "Existing"]
    # Hidden until there is at least one fully-covered placement candidate.
    assert not dock._tabs.isTabVisible(dock._sub_placement_tab_index)


def test_net_template_role_tab_hidden_until_classification_sees_two_nets(main_window, tmp_path, monkeypatch):
    """The tab (not just the section widget) is what gets shown/hidden now —
    setTabVisible() replaced the old setVisible() on the section itself.
    Ambiguity is CLASSIFICATION-driven (plan step 5): a role only becomes
    ambiguous once 2+ of its pads' distinct nets themselves classify by role
    (lemma2/pad). A bridging-shaped component whose nets are all fallback
    (no role evidence) stays hidden — and typing aliases no longer triggers
    it at all, since a classified net's Alias edit is disabled anyway."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False

    # No board/classification -> every net fallback -> nothing ambiguous,
    # even for a bridging-shaped component; typing aliases changes nothing.
    dock.set_board_selection([], [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"})])
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False
    dock._net_alias_edits["-2V5"].setText("PWR_IN")
    dock._net_alias_edits["-2V5_DIRTY"].setText("PWR_OUT")
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False  # aliases no longer trigger

    # Same shape, now with role evidence -> ambiguous with ZERO typed aliases.
    main_window.connection.board = _classification_board(monkeypatch, {
        "C_IN_BULK": {"1": {"-2V5"}, "2": {"GND"}},
        "C_IN_BYPASS": {"1": {"-2V5_DIRTY"}, "2": {"GND"}},
        "PI_FILTER_FB": {"1": {"-2V5"}, "2": {"-2V5_DIRTY"}},
    })
    dock.set_board_selection(
        [_fake_fp("FB6")],
        [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"}, fp=object())])
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is True
    assert "PI_FILTER_FB" in dock._net_template_role_edits
    assert dock._net_template_role_edits["PI_FILTER_FB"].currentText() == ""


def test_role_net_tab_appears_from_classification_without_any_alias(main_window, tmp_path, monkeypatch):
    """Plan test (c) — the step-5 regression: two different nets of one role
    (on different pads) that themselves classify make the Net template role
    tab appear WITHOUT a single manually typed alias."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "C_IN_BULK": {"1": {"-2V5"}, "2": {"GND"}},
        "C_IN_BYPASS": {"1": {"-2V5_DIRTY"}, "2": {"GND"}},
        "PI_FILTER_FB": {"1": {"-2V5"}, "2": {"-2V5_DIRTY"}},
    })
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is False

    dock.set_board_selection(
        [_fake_fp("FB6")],
        [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"}, fp=object())])

    assert dock._tabs.isTabVisible(dock._role_net_tab_index) is True
    assert set(dock._net_template_role_edits) == {"PI_FILTER_FB"}
    assert dock._net_template_role_edits["PI_FILTER_FB"].currentText() == ""
    # No alias was typed anywhere — the tab is driven purely by classification.
    assert all(not e.text().strip() for e in dock._net_alias_edits.values())


def test_net_template_role_blocks_extraction_until_resolved(main_window, tmp_path, monkeypatch, caplog):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "C_IN_BULK": {"1": {"-2V5"}, "2": {"GND"}},
        "C_IN_BYPASS": {"1": {"-2V5_DIRTY"}, "2": {"GND"}},
        "PI_FILTER_FB": {"1": {"-2V5"}, "2": {"-2V5_DIRTY"}},
    })
    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    dock.set_board_selection(
        [_fake_fp("FB6")],
        [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"}, fp=object())])
    assert "PI_FILTER_FB" in dock._net_template_role_edits

    dock.name_edit.setText("n2v5_adj_pi_filter")
    dock._raw_items = [_fake_fp("C1")]
    dock._on_extract()
    assert any("PI_FILTER_FB" in r.message for r in caplog.records)
    assert yaml.safe_load(cells_file.read_text()) in (None, {})

    dock._net_template_role_edits["PI_FILTER_FB"].setCurrentText("-2V5")
    # Full success-path extract: runs synchronously via the _do_extract() core
    # (the async _on_extract() path would race the read on the next line).
    dock._do_extract()
    saved = yaml.safe_load(cells_file.read_text())
    assert "n2v5_adj_pi_filter" in saved["cells"]


def test_net_template_role_pick_seeds_params_for_classified_net(main_window, tmp_path, monkeypatch):
    """Regression for the from-scratch bridging dead-end (found by review on
    commit 9866869): a bridging role's nets classify (lemma2/pad) so their
    Alias edits are disabled and params for them can never be typed by hand —
    without a seeded param net_template_map stays empty and the extractor
    fatals with "...not in net_template_map" on ANY combo pick. The pick is the
    explicit opt-in, so it must seed the matching param (name = role, e.g.
    {PI_FILTER_FB}) — this is separate from the no-override rule for ordinary
    lemma2/pad nets, whose Alias edits stay disabled."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    main_window.connection.board = _classification_board(monkeypatch, {
        "C_IN_BULK": {"1": {"-2V5"}, "2": {"GND"}},
        "C_IN_BYPASS": {"1": {"-2V5_DIRTY"}, "2": {"GND"}},
        "PI_FILTER_FB": {"1": {"-2V5"}, "2": {"-2V5_DIRTY"}},
    })
    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    dock.set_board_selection(
        [_fake_fp("FB6")],
        [FakeSelected("FB6", "PI_FILTER_FB", "X", {"1": "-2V5", "2": "-2V5_DIRTY"}, fp=object())])
    assert "PI_FILTER_FB" in dock._net_template_role_edits
    # Both rails classify -> Alias edits disabled: params can't be typed by hand.
    assert dock._net_alias_edits["-2V5"].isEnabled() is False
    assert dock._net_alias_edits["-2V5_DIRTY"].isEnabled() is False

    dock._net_template_role_edits["PI_FILTER_FB"].setCurrentText("-2V5")
    dock.name_edit.setText("bridging_cell")
    dock._raw_items = [_fake_fp("C1")]

    payload = dock._collect_extract_inputs()
    assert payload["net_template_role"] == {"PI_FILTER_FB": "-2V5"}
    # The pick seeds params (name = role) so net_template_map can actually
    # contain the literal at extract time.
    assert payload["params"]["PI_FILTER_FB"] == "-2V5"

    # And the full extract path succeeds (no blocking, no fatal).
    dock._do_extract()
    saved = yaml.safe_load(cells_file.read_text())
    assert "bridging_cell" in saved["cells"]


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


def test_extract_shows_net_from_role_summary_on_success(main_window, tmp_path, monkeypatch, caplog):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    def _fake_extract_with_role(adapter, name, params=None, items=None, annotations=None, **kwargs):
        return {name: {"vias": [{"net_from_role": "C_IN_BULK"}], "components": [], "tracks": [], "layer": "F.Cu"}}

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract_with_role)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock._raw_items = [_fake_fp("C1")]
    dock._do_extract()

    assert any("auto-classified by role" in r.message for r in caplog.records)
    assert any("C_IN_BULK" in r.message for r in caplog.records)


def test_on_extract_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    """Phase 5.2 — the Extract button must NOT block the UI thread:
    _on_extract() collects + validates the inputs on the UI thread, then
    hands the plain-data payload to start_long_op with the shared connection,
    the guard widget, and the split run/finish callbacks (the result comes
    back through a queued signal, so the socket is never held by two owners)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
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
    dock._raw_items = [_fake_fp("C1")]
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
    assert payload["placer_path"] == cells_file
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
    dock.set_root_path(cells_file)
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
    dock.set_root_path(cells_file)
    dock.set_board_selection([], [
        FakeSelected("D1", "SOME_ROLE", "X", {"1": "+3V3_VCCIO", "2": "GND"}),
    ])
    dock._rule_net_checkboxes["+3V3_VCCIO"].setChecked(True)
    dock.name_edit.setText("some_cell")
    dock._raw_items = [_fake_fp("C1")]
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
    dock.set_root_path(cells_file)
    dock.set_root_path(extractor_file)
    dock.set_board_selection([], [FakeSelected("D1", "SOME_ROLE", "X", {"1": "+3V3_VCCIO"})])
    dock._rule_net_checkboxes["+3V3_VCCIO"].setChecked(True)

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)
    main_window.connection.board = FakeBoard()

    dock.name_edit.setText("some_cell")
    dock.save_profile_checkbox.setChecked(True)
    dock.profile_key_edit.setText("some_profile")
    dock._raw_items = [_fake_fp("C1")]
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
    dock.set_root_path(cells_file)
    dock.set_root_path(extractor_file)
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
    dock.set_root_path(cells_file)
    main_window.show()  # isVisible() needs the whole ancestor chain shown

    dock.set_board_selection([_fake_fp("R18")],
                              [FakeSelected("R18", "R_SERIES", "FPGA_PERIPH", {})])

    assert dock.cluster_filter_checkbox.isVisible() is False
    assert dock.cluster_filter_combo.isVisible() is False


def test_cluster_filter_shown_and_defaults_to_majority_cluster(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
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
    dock.set_root_path(cells_file)
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
    dock.set_root_path(cells_file)
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
    dock.set_root_path(cells_file)

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
    dock.set_root_path(cells_file)
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
    dock.set_root_path(cells_file)
    dock.set_root_path(placer_file)
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
    dock.set_root_path(cells_file)
    dock.set_root_path(placer_file)
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
    dock.set_root_path(cells_file)
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
    dock.set_root_path(cells_file)
    dock.set_root_path(placer_file)

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
    dock.set_root_path(other_placer)
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
    dock.set_root_path(cells_file)
    dock.set_root_path(placer_file)
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


# ── Raw selection (2026-08-24, handoff_2026_08_24_extract_raw_selection_flag):
# "take selection as-is" — opt-in bypass of the pad-connectivity filter ─────

def test_raw_selection_checkbox_defaults_off(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.name_edit.setText("some_cell")
    main_window.connection.board = FakeBoard()
    dock._raw_items = [_fake_fp("C1")]

    payload = dock._collect_extract_inputs()

    assert payload["raw_selection"] is False


def test_raw_selection_checkbox_collected_when_checked(main_window, tmp_path):
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.name_edit.setText("some_cell")
    main_window.connection.board = FakeBoard()
    dock._raw_items = [_fake_fp("C1")]
    dock.raw_selection_checkbox.setChecked(True)

    payload = dock._collect_extract_inputs()

    assert payload["raw_selection"] is True


def test_raw_selection_reaches_run_extract_to_file(main_window, tmp_path, monkeypatch):
    """The checkbox state must flow: checkbox -> _collect_extract_inputs ->
    _run_extract -> run_extract_to_file(raw_selection=...) (the worker-side
    forwarding itself is covered in tests/test_extract_writer.py)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.name_edit.setText("some_cell")
    main_window.connection.board = FakeBoard()
    dock._raw_items = [_fake_fp("C1")]
    dock.raw_selection_checkbox.setChecked(True)

    captured = {}

    def _fake_run(adapter, **kwargs):
        captured.update(kwargs)
        return {"messages": [], "annotations": [], "template_dict": {}}

    monkeypatch.setattr(extract_mod, "run_extract_to_file", _fake_run)

    dock._run_extract(dock._collect_extract_inputs())

    assert captured["raw_selection"] is True


# ── Sub-placements: auto-detect fully-covered existing placements (2026-08-25,
# handoff composite_cell_autodetect_and_cycle_guard, Задание 1) ────────────

def _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, items):
    """ExtractDock with a fixed sub-placement catalog (the resolver itself is
    core-tested in tests/test_clone_placement_geometry.py + the board_items_
    resolver tests; here we exercise the dock's detection/exclusion logic on
    top of it). `items` are the resolved board items the catalog reports for
    `clone` — a placement is a candidate exactly when ALL of them are in the
    current selection."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    monkeypatch.setattr(ExtractDock, "_sub_placement_catalog",
                        lambda self: [(clone, items)])
    return dock


def test_sub_placements_candidate_detected_when_fully_covered(main_window, tmp_path, monkeypatch):
    """Selection = DAC/OpAmp + a WHOLE existing PIF_AVDD (its components and
    via/tracks) -> the Sub-placements tab appears with one row, checked by
    default, showing placement name + cell + matched count."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    track = _fake_track("+3V3", uuid="track-uuid-pif")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via, track])

    dock.set_board_selection(
        [fp, via, track, _fake_fp("C2")],
        [FakeSelected("C1", "C_IN", "PIF_AVDD", {}),
         FakeSelected("C2", "OTHER", "DAC_BUF", {})])

    assert len(dock._sub_placement_candidates) == 1
    assert dock._sub_placement_candidates[0].clone is clone
    assert dock._tabs.isTabVisible(dock._sub_placement_tab_index)
    assert dock._sub_placements_table.rowCount() == 1
    cb = dock._sub_placements_table.cellWidget(0, 0)
    assert cb is not None and cb.isChecked()  # default on
    assert dock._sub_placements_table.item(0, 1).text() == "CH0_PIF_AVDD"
    assert dock._sub_placements_table.item(0, 2).text() == "pif_avdd"
    assert dock._sub_placements_table.item(0, 3).text() == "1 component(s), 2 via/track(s)"


def test_sub_placements_partial_coverage_is_not_a_candidate(main_window, tmp_path, monkeypatch):
    """Selection holds only PART of PIF_AVDD (e.g. the area-select missed one
    resistor) -> NOT a candidate, old behavior (no surprises on a partial
    overlap), tab stays hidden."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    track = _fake_track("+3V3", uuid="track-uuid-pif")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via, track])

    # track is missing from the selection
    dock.set_board_selection(
        [fp, via],
        [FakeSelected("C1", "C_IN", "PIF_AVDD", {})])

    assert dock._sub_placement_candidates == []
    assert not dock._tabs.isTabVisible(dock._sub_placement_tab_index)
    assert dock._sub_placements_table.rowCount() == 0


def test_sub_placements_empty_placement_is_not_a_candidate(main_window, tmp_path, monkeypatch):
    """A placement with no resolved items (never placed / unresolved) must not
    be offered — an empty set is trivially a subset of any selection, which
    would otherwise flood the tab with junk rows."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [])

    dock.set_board_selection([_fake_fp("C2")], [FakeSelected("C2", "OTHER", "DAC_BUF", {})])

    assert dock._sub_placement_candidates == []
    assert not dock._tabs.isTabVisible(dock._sub_placement_tab_index)


def test_sub_placements_unchecked_keeps_items_flat(main_window, tmp_path, monkeypatch):
    """Checkbox OFF -> the old behavior: the placement's items stay in the
    flat extraction and no clone_placements entry is produced."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    track = _fake_track("+3V3", uuid="track-uuid-pif")
    extra = _fake_fp("C2")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via, track])

    dock.set_board_selection(
        [fp, via, track, extra],
        [FakeSelected("C1", "C_IN", "PIF_AVDD", {}),
         FakeSelected("C2", "OTHER", "DAC_BUF", {})])
    dock._sub_placement_checkboxes["CH0_PIF_AVDD"].setChecked(False)
    dock.name_edit.setText("dac_buf")
    main_window.connection.board = FakeBoard()

    payload = dock._collect_extract_inputs()

    assert payload["sub_placements"] == []
    # every item still in the flat selection
    assert set(payload["raw_items"]) == {fp, via, track, extra}


def test_sub_placements_checked_excludes_items_and_adds_entry(main_window, tmp_path, monkeypatch):
    """Checkbox ON (default) -> the placement's items are EXCLUDED from the
    flat selection, and a clone_placements payload record (name + clone) is
    produced for the worker to turn into a CellPlacement entry."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0), rotation_deg=180.0, mirror=True, layer="B.Cu")
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    track = _fake_track("+3V3", uuid="track-uuid-pif")
    extra = _fake_fp("C2")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via, track])

    dock.set_board_selection(
        [fp, via, track, extra],
        [FakeSelected("C1", "C_IN", "PIF_AVDD", {}),
         FakeSelected("C2", "OTHER", "DAC_BUF", {})])
    dock.name_edit.setText("dac_buf")
    main_window.connection.board = FakeBoard()

    payload = dock._collect_extract_inputs()

    assert set(payload["raw_items"]) == {extra}  # PIF items excluded
    assert len(payload["sub_placements"]) == 1
    rec = payload["sub_placements"][0]
    assert rec["name"] == "CH0_PIF_AVDD"
    assert rec["clone"] is clone


def test_run_extract_forwards_built_clone_placements(main_window, tmp_path, monkeypatch):
    """The worker forwards the built CellPlacement entries into
    run_extract_to_file(clone_placements=...) — the write-through itself is
    covered in tests/test_extract_writer.py."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    captured = {}
    monkeypatch.setattr(
        extract_mod, "run_extract_to_file",
        lambda adapter, **kw: captured.update(kw) or
        {"messages": [], "annotations": [], "template_dict": {}})
    monkeypatch.setattr(
        ExtractDock, "_build_sub_placements",
        lambda self, payload, origin: ([{"name": "ch0_pif_avdd", "cell": "pif_avdd",
                                         "xy": [5.0, 2.0]}], None))

    payload = {
        "board": FakeBoard(), "name": "dac_buf", "params": {},
        "raw_items": [_fake_fp("C9")], "full_raw_items": [_fake_fp("C9")],
        "net_template_role": {}, "rule_nets": set(),
        "origin_kwargs": {}, "target_path": cells_file, "save_profile": False,
        "profile_key": "dac_buf", "profile_path": None, "placer_path": cells_file,
        "raw_selection": False,
        "sub_placements": [{"name": "CH0_PIF_AVDD", "clone": object()}],
    }
    dock._run_extract(payload)

    assert captured["clone_placements"] == [
        {"name": "ch0_pif_avdd", "cell": "pif_avdd", "xy": [5.0, 2.0]}]
    # the single precomputed origin (from the full list) reaches the extractor
    assert captured["origin"] == Vector2.from_xy(0, 0)


def test_build_sub_placements_xy_is_world_origin_in_new_cell_local_frame(main_window, tmp_path, monkeypatch):
    """xy = the existing placement's world origin converted into the new cell's
    local frame via the SAME (world - origin) formula the extractor uses for
    every other point. The new cell is extracted 'as-is' at rotation 0, so the
    placement's world rotation IS its local rotation (copied verbatim), and
    mirror/layer copy one-to-one. The origin is the ONE precomputed one from
    _compute_extract_origin (passed in, not recomputed here); clone_world_origin
    is patched (its own geometry is core-tested)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(10.0, 5.0), rotation_deg=90.0, layer="B.Cu")
    monkeypatch.setattr("kicadstamp.placement.services.board_items_resolver.clone_world_origin",
                        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
                        Vector2.from_xy(15_000_000, 8_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file,
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": "CH0_PIF_AVDD", "clone": clone}],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    # name is the slug of the existing placement's name (Cluster->slug rule);
    # cluster (own-identity, required on ClonePlacement) is carried over too
    # (2026-08-26, handoff cell_placement_sheet_cluster).
    assert entries == [{
        "name": "ch0_pif_avdd", "cell": "pif_avdd", "xy": [10.0, 5.0],
        "rotation_deg": 90.0, "layer": "B.Cu", "cluster": "PIF_AVDD",
    }]


def test_worker_origin_computed_from_full_selection_for_role_origin(main_window, tmp_path, monkeypatch):
    """Live bug 2026-08-25: origin (e.g. Component role) was resolved against
    the ALREADY-TRIMMED list (after Sub-placements exclusion), so an origin
    component that belongs to a checked Sub-placement vanished ->
    '--origin-by-component-role ... not found in selection'. The worker's
    _compute_extract_origin uses the FULL (pre-exclusion) list, and
    _build_sub_placements consumes that SAME origin — Sub-placement xy and the
    flat geometry share one coordinate system."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    class _RoleAwareBoard:
        class _Adapter:
            def get_field_value(self, fp, name):
                if name == "Role":
                    return getattr(fp, "_role", None)
                return None
        adapter = _Adapter()

    dac_fp = _fake_fp("U1")
    dac_fp._role = "AD_DAC"
    dac_fp.position = Vector2.from_xy(10_000_000, 20_000_000)
    pif_fp = _fake_fp("C1")
    pif_fp._role = "C_IN"
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(0.0, 0.0))
    payload = {
        "board": _RoleAwareBoard(),
        # AD_DAC is present in the FULL (pre-exclusion) list...
        "full_raw_items": [dac_fp, pif_fp],
        # ...but excluded from the flat items (it belongs to the sub-placement)
        "raw_items": [pif_fp],
        "origin_kwargs": {"origin_component_role": "AD_DAC"},
        "placer_path": cells_file,
        "sub_placements": [{"name": "CH0_PIF_AVDD", "clone": clone}],
    }

    origin, err = dock._compute_extract_origin(payload)
    assert err is None
    assert origin == dac_fp.position  # found in the FULL list despite exclusion

    entries, err = dock._build_sub_placements(payload, origin)
    assert err is None
    # Sub-placement xy (-10, -20) is relative to the SAME origin the flat
    # geometry uses (flat coords = item position - origin)
    assert entries[0]["xy"] == [-10.0, -20.0]


def test_pure_composite_selection_is_legitimate_not_an_error(main_window, tmp_path, monkeypatch):
    """A selection FULLY covered by one Sub-placement -> `raw_items` (flat) is
    empty but `sub_placements` is not: the payload proceeds (not an error) and
    carries BOTH lists — the trimmed one for flat geometry and the full one for
    the worker's single origin computation."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via])

    dock.set_board_selection([fp, via], [FakeSelected("C1", "C_IN", "PIF_AVDD", {})])
    dock.name_edit.setText("dac_buf")
    main_window.connection.board = FakeBoard()

    payload = dock._collect_extract_inputs()

    assert payload["sub_placements"] != []
    assert payload["raw_items"] == []                       # flat content excluded
    assert set(payload["full_raw_items"]) == {fp, via}      # full list kept for origin


def test_pure_composite_extract_skips_extract_fn(main_window, tmp_path, monkeypatch):
    """End-to-end: a fully-covered selection extracts a pure-composite cell —
    extract_fn must NOT be called (it would fatal 'nothing to extract'); the
    written cell carries clone_placements and empty flat lists."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via])

    dock.set_board_selection([fp, via], [FakeSelected("C1", "C_IN", "PIF_AVDD", {})])
    dock.name_edit.setText("dac_buf")
    main_window.connection.board = FakeBoard()

    calls = []

    def _fake_extract(*args, **kwargs):
        calls.append((args, kwargs))
        return {"dac_buf": {"components": [], "vias": [], "tracks": [], "layer": "F.Cu"}}

    monkeypatch.setattr(extract_mod, "extract_template_from_selection", _fake_extract)

    dock._do_extract()

    assert calls == []  # extract_fn never called for a pure composite
    data = yaml.safe_load((tmp_path / "cells.yaml").read_text(encoding="utf-8"))
    cell = data["cells"]["dac_buf"]
    assert cell["components"] == []
    assert cell["vias"] == []
    assert cell["tracks"] == []
    assert cell["clone_placements"] == [{
        "name": "ch0_pif_avdd", "cell": "pif_avdd", "xy": [5.0, 2.0],
        "cluster": "PIF_AVDD"}]


def test_filtered_selection_keeps_fully_covered_placements_copper(main_window, tmp_path, monkeypatch):
    """Задание 1б: with the Cluster filter ON, a Via whose UUID is already in
    the registry of a placement that is WHOLLY covered by the selection is no
    longer silently dropped — the placement became a Sub-placement candidate,
    so its own copper stays in the selection (it becomes a reference or stays
    flat per the user's checkbox, never silently stripped in between)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    placer_file = tmp_path / "placer.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    _write_registry(tmp_path / "placer.registry.json", {
        "pif|via|0|0": {"uuid": "via-uuid-pif", "x_mm": 0, "y_mm": 0,
                        "net": "+3V3", "drill_mm": 0.3, "diameter_mm": 0.6},
    })
    _write_registry(tmp_path / "placer.tracks.registry.json", {})

    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_root_path(placer_file)  # _placer_path -> placer.yaml (registry read)
    # A DISTINCT target cell name keeps this placement from being a
    # self-reference (cell: pif_avdd == target), which the 2026-08-25
    # self-reference guard excludes — this test is about the registry-copper
    # exemption (Задание 1б), not about the self-reference filter.
    dock.name_edit.setText("dac_buf")

    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    other = _fake_fp("C2")
    monkeypatch.setattr(ExtractDock, "_sub_placement_catalog",
                        lambda self: [(clone, [fp, via])])

    dock.set_board_selection(
        [fp, via, other],
        [FakeSelected("C1", "C_IN", "PIF_AVDD", {}),
         FakeSelected("C2", "OTHER", "DAC_BUF", {})])
    # Two distinct clusters -> the filter stays usable; pick the PIF cluster.
    dock.cluster_filter_checkbox.setChecked(True)
    idx = dock.cluster_filter_combo.findData("PIF_AVDD")
    dock.cluster_filter_combo.setCurrentIndex(idx)

    assert len(dock._sub_placement_candidates) == 1  # fully covered -> candidate
    filtered_items, _footprints = dock._filtered_selection()
    assert via in filtered_items  # kept despite being in the registry


def test_filtered_selection_still_drops_foreign_registry_copper(main_window, tmp_path, monkeypatch):
    """1б must not soften the registry filter for placements that are NOT fully
    covered (or not candidates at all) — a wholly-foreign/partially-covered
    placement's copper is still dropped the old way."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {})
    placer_file = tmp_path / "placer.yaml"
    _write_yaml(placer_file, {"clone_placements": []})
    _write_registry(tmp_path / "placer.registry.json", {
        "pif|via|0|0": {"uuid": "via-uuid-pif", "x_mm": 0, "y_mm": 0,
                        "net": "+3V3", "drill_mm": 0.3, "diameter_mm": 0.6},
    })
    _write_registry(tmp_path / "placer.tracks.registry.json", {})

    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)
    dock.set_root_path(placer_file)

    # Clone whose via is registered, but whose footprint is NOT in the
    # selection -> not fully covered -> not a candidate.
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-pif")
    c2 = _fake_fp("C2")
    c3 = _fake_fp("C3")
    monkeypatch.setattr(ExtractDock, "_sub_placement_catalog",
                        lambda self: [(clone, [fp, via])])

    # Two distinct clusters (needed for the Cluster filter to stay usable);
    # the PIF clone's own footprint C1 is NOT among them, so the placement is
    # not fully covered -> not a candidate.
    dock.set_board_selection(
        [via, c2, c3],
        [FakeSelected("C2", "OTHER", "DAC_BUF", {}),
         FakeSelected("C3", "SOMETHING", "PIF_AVDD", {})])
    dock.cluster_filter_checkbox.setChecked(True)
    idx = dock.cluster_filter_combo.findData("DAC_BUF")
    dock.cluster_filter_combo.setCurrentIndex(idx)

    assert dock._sub_placement_candidates == []  # C1 missing -> not covered
    filtered_items, _footprints = dock._filtered_selection()
    assert via not in filtered_items  # still dropped by the registry filter


# ── Sub-placements: self-reference guard (2026-08-25, handoff sub_placements_
# self_reference_guard) ───────────────────────────────────────────────────

def test_sub_placements_self_reference_candidate_excluded(main_window, tmp_path, monkeypatch):
    """Live bug 2026-08-25: re-extracting the region of an already-placed
    `dac_buf` (cell: dac_buf) into a cell ALSO named dac_buf must NOT offer
    that placement as a Sub-placement — it would be a literal self-reference
    (dac_buf -> dac_buf), caught by the cycle guard only after the fact."""
    clone = ClonePlacement(cluster="DAC_BUF", name="CH1_DAC_BUF", cell="dac_buf",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp])
    dock.name_edit.setText("dac_buf")

    dock.set_board_selection([fp], [FakeSelected("C1", "C_IN", "DAC_BUF", {})])

    assert dock._sub_placement_candidates == []
    assert not dock._tabs.isTabVisible(dock._sub_placement_tab_index)
    assert dock._sub_placements_table.rowCount() == 0


def test_sub_placements_self_reference_empty_cell_name_not_filtered(main_window, tmp_path, monkeypatch):
    """The Cell-name field may still be empty when the selection-watch tick
    builds candidates — with nothing to compare against, no candidate is
    dropped by the self-reference guard (nothing mysteriously vanishes)."""
    clone = ClonePlacement(cluster="DAC_BUF", name="CH1_DAC_BUF", cell="dac_buf",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp])

    dock.set_board_selection([fp], [FakeSelected("C1", "C_IN", "DAC_BUF", {})])

    assert len(dock._sub_placement_candidates) == 1


def test_sub_placements_non_self_reference_still_candidate(main_window, tmp_path, monkeypatch):
    """The guard must not over-filter: a placement referencing a DIFFERENT cell
    stays a candidate even when the Cell name is filled in."""
    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp])
    dock.name_edit.setText("dac_buf")

    dock.set_board_selection([fp], [FakeSelected("C1", "C_IN", "PIF_AVDD", {})])

    assert len(dock._sub_placement_candidates) == 1


def test_collect_extract_inputs_skips_stale_self_reference_keeps_items_flat(
        main_window, tmp_path, monkeypatch):
    """Defense-in-depth: the candidate table may have been built BEFORE the Cell
    name was typed (stale tick), leaving a self-referencing candidate checked.
    The collect-time guard must still skip it — and, critically, NOT exclude its
    items from the flat selection (otherwise the geometry would be lost)."""
    clone = ClonePlacement(cluster="DAC_BUF", name="CH1_DAC_BUF", cell="dac_buf",
                           xy=(5.0, 2.0))
    fp = _fake_fp("C1")
    via = _fake_via("+3V3", uuid="via-uuid-dac")
    extra = _fake_fp("C2")
    dock = _sub_placement_dock(main_window, tmp_path, monkeypatch, clone, [fp, via])
    dock.name_edit.setText("dac_buf")
    main_window.connection.board = FakeBoard()

    # Simulate a stale tick: candidates + a checked checkbox already exist, and
    # the fresh detection (_filtered_selection) is bypassed so the guard is hit
    # at COLLECT time, not during re-detection.
    dock._sub_placement_candidates = [
        SubPlacementCandidate(
            clone=clone, items=[fp, via],
            item_keys=frozenset({("fp", "C1"), ("via", "via-uuid-dac")}))]

    class _CheckedBox:
        def isChecked(self):
            return True

    dock._sub_placement_checkboxes = {"CH1_DAC_BUF": _CheckedBox()}
    monkeypatch.setattr(
        ExtractDock, "_filtered_selection",
        lambda self: ([fp, via, extra],
                      [FakeSelected("C1", "C_IN", "DAC_BUF", {}),
                       FakeSelected("C2", "OTHER", "SOMETHING", {})]))

    payload = dock._collect_extract_inputs()

    assert payload["sub_placements"] == []
    assert set(payload["raw_items"]) == {fp, via, extra}  # nothing excluded


# ── Sub-placements: params/nets/net_overrides/refs carried over (2026-08-25,
# handoff sub_placements_lost_params) ──────────────────────────────────────

def test_build_sub_placements_copies_params_nets_overrides_refs(main_window, tmp_path, monkeypatch):
    """Live bug 2026-08-25: an existing top-level ClonePlacement turned into a
    Sub-placement silently lost params/nets/net_overrides/refs — the new nested
    CellPlacement then couldn't resolve its {placeholders} on the next Redraw.
    The four fields are copied verbatim (the same cell, so the semantics
    don't change)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(
        cluster="PIF_DVDD", name="CH1_PIF_DVDD", cell="dac_pif_dvdd",
        xy=(10.0, 5.0),
        params={"FB_PI_FLT": "/Channel_1/DAC/+3V3_DVDD"},
        nets={"C_IN": "+3V3_DVDD"},
        net_overrides={"C_OUT": "+3V3_DVDD_DIRTY"},
        refs={"L1": "L100"},
    )
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": "CH1_PIF_DVDD", "clone": clone}],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    entry = entries[0]
    assert entry["params"] == {"FB_PI_FLT": "/Channel_1/DAC/+3V3_DVDD"}
    assert entry["nets"] == {"C_IN": "+3V3_DVDD"}
    assert entry["net_overrides"] == {"C_OUT": "+3V3_DVDD_DIRTY"}
    assert entry["refs"] == {"L1": "L100"}


def test_build_sub_placements_omits_empty_param_fields(main_window, tmp_path, monkeypatch):
    """A plain placement (all four parametrisation fields empty) must not gain
    params: {} / nets: {} / net_overrides: {} / refs: {} noise in the written
    cell — the defaults stay omitted, same style as rotation/mirror/layer."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(5_000_000, 2_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": "CH0_PIF_AVDD", "clone": clone}],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(0, 0))

    assert err is None
    entry = entries[0]
    for key in ("params", "nets", "net_overrides", "refs"):
        assert key not in entry


# ── Sub-placements: own-identity sheet/cluster carried over (2026-08-26,
# handoff cell_placement_sheet_cluster) ───────────────────────────────────

def test_build_sub_placements_copies_sheet_cluster(main_window, tmp_path, monkeypatch):
    """Live bug 2026-08-25/26: a top-level ClonePlacement turned into a nested
    CellPlacement lost its own-identity sheet/cluster, so role_narrowing.py's
    sheet/cluster steps read None (getattr) and a shared-net role (e.g. +3V3
    on a PI-filter) stayed ambiguous among identical physical instances.
    Both fields must be copied into the new nested entry — here on a
    CROSS-sheet batch (two different sheets), where sheet: is legitimately
    kept per item (the uniform-sheet omission is covered separately)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clones = [
        ClonePlacement(cluster="PIF_DVDD", name="CH1_PIF_DVDD", cell="dac_pif_dvdd",
                       xy=(10.0, 5.0), sheet="Channel_1"),
        ClonePlacement(cluster="PIF_AVDD", name="CH2_PIF_AVDD", cell="dac_pif_avdd",
                       xy=(12.0, 5.0), sheet="Channel_2"),
    ]
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": c.name, "clone": c} for c in clones],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    assert entries[0]["sheet"] == "Channel_1"
    assert entries[0]["cluster"] == "PIF_DVDD"
    assert entries[1]["sheet"] == "Channel_2"
    assert entries[1]["cluster"] == "PIF_AVDD"


def test_build_sub_placements_omits_sheet_when_none(main_window, tmp_path, monkeypatch):
    """clone.sheet is None must not produce a `sheet: null` key in the written
    YAML (same style as layer) — only set sheets/clusters are carried over."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0), sheet=None)
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(5_000_000, 2_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": "CH0_PIF_AVDD", "clone": clone}],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(0, 0))

    assert err is None
    entry = entries[0]
    assert "sheet" not in entry
    assert entry["cluster"] == "PIF_AVDD"


# ── Sub-placements: literal sheet segment templatized to {sheet} (2026-08-26,
# handoff cell_placement_net_sheet_template) ────────────────────────────────

def test_templatize_sheet_helper(main_window, tmp_path):
    """Direct unit on _templatize_sheet: a full `/Channel_1/` path segment is
    replaced with `/{sheet}/`; a global rail with no sheet segment and a falsy
    sheet are left untouched."""
    dock = ExtractDock(main_window)
    assert dock._templatize_sheet("/Channel_1/DAC/+3V3_DVDD", "Channel_1") == "/{sheet}/DAC/+3V3_DVDD"
    assert dock._templatize_sheet("+3V3", "Channel_1") == "+3V3"
    assert dock._templatize_sheet("/Channel_1/DAC/+3V3_DVDD", None) == "/Channel_1/DAC/+3V3_DVDD"


def test_build_sub_placements_templatizes_sheet_in_nets_params(main_window, tmp_path, monkeypatch):
    """Live bug 2026-08-26: extracted nested CellPlacements carried a hardcoded
    /Channel_1/ in nets:/params:, so reusing the composite on Channel_0 dragged
    Channel_1 parts over. The literal sheet path segment must be written as
    {sheet}; a global rail (no sheet segment) is left untouched, and non-string
    params values are preserved as-is."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(
        cluster="PIF_DVDD", name="CH1_PIF_DVDD", cell="dac_pif_dvdd", xy=(10.0, 5.0),
        sheet="Channel_1",
        nets={"FB": "/Channel_1/DAC/+3V3_DVDD", "C_IN": "+3V3"},
        params={"PWR": "/Channel_1/DAC/+3V3_DVDD", "COUNT": 3},
    )
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": "CH1_PIF_DVDD", "clone": clone}],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    entry = entries[0]
    assert entry["nets"] == {"FB": "/{sheet}/DAC/+3V3_DVDD", "C_IN": "+3V3"}
    assert entry["params"] == {"PWR": "/{sheet}/DAC/+3V3_DVDD", "COUNT": 3}


def test_build_sub_placements_sheet_none_copies_literally(main_window, tmp_path, monkeypatch):
    """clone.sheet is None -> nets/params are copied verbatim, no templatizing
    attempted (the current, pre-this-fix behavior is preserved for placements
    without a sheet)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(
        cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd", xy=(5.0, 2.0),
        nets={"FB": "/Channel_1/DAC/+3V3_DVDD"},
        params={"PWR": "/Channel_1/DAC/+3V3_DVDD"},
    )
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(5_000_000, 2_000_000))

    payload = {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": "CH0_PIF_AVDD", "clone": clone}],
    }
    entries, err = dock._build_sub_placements(
        payload, origin=Vector2.from_xy(0, 0))

    assert err is None
    entry = entries[0]
    assert entry["nets"] == {"FB": "/Channel_1/DAC/+3V3_DVDD"}
    assert entry["params"] == {"PWR": "/Channel_1/DAC/+3V3_DVDD"}


# ── Sub-placements: omit sheet: when the whole batch is on one sheet
# (2026-08-26, handoff extract_omit_uniform_sheet) ─────────────────────────

def _sub_placement_payload(cells_file, clones):
    return {
        "board": FakeBoard(), "placer_path": cells_file, "name": "dac_buf",
        "raw_items": [_fake_fp("C9")], "origin_kwargs": {},
        "sub_placements": [{"name": c.name, "clone": c} for c in clones],
    }


def test_build_sub_placements_uniform_sheet_omits_sheet_key(main_window, tmp_path, monkeypatch):
    """Live pain 2026-08-26: a fresh dac_buf extract kept writing `sheet:
    Channel_1` on all five nested nodes, which muted BOTH sheet inheritance
    (cf1041a) and {sheet} templating (36ef950). When every sub-placement in
    the batch shares one non-None sheet, sheet: must be omitted everywhere."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clones = [
        ClonePlacement(cluster=f"PIF_{i}", name=f"CH1_PIF_{i}", cell="dac_pif_dvdd",
                       xy=(i, 1.0), sheet="Channel_1")
        for i in range(5)
    ]
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    entries, err = dock._build_sub_placements(
        _sub_placement_payload(cells_file, clones), origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    assert len(entries) == 5
    for entry in entries:
        assert "sheet" not in entry
        assert entry["cluster"].startswith("PIF_")  # cluster untouched


def test_build_sub_placements_cross_sheet_keeps_literal_sheet(main_window, tmp_path, monkeypatch):
    """A genuine cross-sheet composite (sub-placements on DIFFERENT sheets) is
    NOT uniform — every entry keeps its own explicit sheet:, unchanged."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clones = [
        ClonePlacement(cluster="PIF_DVDD", name="CH1_PIF_DVDD", cell="dac_pif_dvdd",
                       xy=(1.0, 1.0), sheet="Channel_1"),
        ClonePlacement(cluster="PIF_AVDD", name="CH2_PIF_AVDD", cell="dac_pif_avdd",
                       xy=(2.0, 1.0), sheet="Channel_2"),
    ]
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    entries, err = dock._build_sub_placements(
        _sub_placement_payload(cells_file, clones), origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    assert entries[0]["sheet"] == "Channel_1"
    assert entries[1]["sheet"] == "Channel_2"


def test_build_sub_placements_mixed_sheet_none_behaves_as_before(main_window, tmp_path, monkeypatch):
    """sheet set on some, None on others -> not uniform (None in the set):
    each entry behaves exactly as before the fix."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clones = [
        ClonePlacement(cluster="PIF_DVDD", name="CH1_PIF_DVDD", cell="dac_pif_dvdd",
                       xy=(1.0, 1.0), sheet="Channel_1"),
        ClonePlacement(cluster="PIF_AVDD", name="CH2_PIF_AVDD", cell="dac_pif_avdd",
                       xy=(2.0, 1.0)),
    ]
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    entries, err = dock._build_sub_placements(
        _sub_placement_payload(cells_file, clones), origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    assert entries[0]["sheet"] == "Channel_1"
    assert "sheet" not in entries[1]


def test_build_sub_placements_single_uniform_sheet_omits(main_window, tmp_path, monkeypatch):
    """A single sub-placement with a set sheet is trivially 'uniform' (nothing
    to compare against) -> sheet: omitted, same principle as a 5-node batch."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(cluster="PIF_DVDD", name="CH1_PIF_DVDD", cell="dac_pif_dvdd",
                           xy=(1.0, 1.0), sheet="Channel_1")
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(15_000_000, 8_000_000))

    entries, err = dock._build_sub_placements(
        _sub_placement_payload(cells_file, [clone]), origin=Vector2.from_xy(5_000_000, 3_000_000))

    assert err is None
    assert "sheet" not in entries[0]


def test_build_sub_placements_single_sheet_none_no_key(main_window, tmp_path, monkeypatch):
    """A single sub-placement with sheet=None: nothing to omit additionally —
    sheet: was not written before either (the uniform logic doesn't add it)."""
    cells_file = tmp_path / "cells.yaml"
    _write_yaml(cells_file, {"clone_placements": []})
    dock = ExtractDock(main_window)
    dock.set_root_path(cells_file)

    clone = ClonePlacement(cluster="PIF_AVDD", name="CH0_PIF_AVDD", cell="pif_avdd",
                           xy=(5.0, 2.0))
    monkeypatch.setattr(
        "kicadstamp.placement.services.board_items_resolver.clone_world_origin",
        lambda adapter, cfg, clone, sheet_names=None, resolved_points=None:
        Vector2.from_xy(5_000_000, 2_000_000))

    entries, err = dock._build_sub_placements(
        _sub_placement_payload(cells_file, [clone]), origin=Vector2.from_xy(0, 0))

    assert err is None
    assert "sheet" not in entries[0]
