# tests/gui/test_thermal_via_dock.py
"""
ThermalViaArrayDock tests are deliberately headless AND board-mutation-free
— same reasoning as tests/gui/test_placer_dock.py: _on_redraw()'s real job
is moving real vias on a live board, which these tests must never do on
their own. ApplyPipeline/load_config are monkeypatched with fakes that only
check what ThermalViaArrayDock PASSES them (config_path, only=, and that
OTHER already-saved thermal_via_arrays entries survive into the config
handed to the pipeline).
"""
import gui.docks.thermal_via as thermal_via_mod
from gui.docks.thermal_via import ThermalViaArrayDock
from kicadstamp.config import Config, RuntimeContext, ThermalViaArrayConfig, load_thermal_via_array
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _write(path, data) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _make_dock(main_window, tmp_path):
    target_file = tmp_path / "root.sexp"
    _write(target_file, {"thermal_via_arrays": []})
    dock = ThermalViaArrayDock(main_window)
    dock.set_root_path(target_file)
    return dock, target_file


def test_build_entry_dict_anchor_ref_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")
    dock.net_edit.setCurrentText("GND")

    entry = dock._build_entry_dict()
    assert entry["name"] == "fpga_thermal"
    assert entry["pad"] == "1"
    assert entry["anchor_ref"] == "U3"
    assert entry["net"] == "GND"
    assert entry["rows"] == 4 and entry["cols"] == 4
    assert entry["margin_mm"] == 0.5
    assert entry["pattern"] == "grid"
    tva = load_thermal_via_array(entry)  # must validate against the real backend loader
    assert tva.name == "fpga_thermal"
    assert tva.anchor_ref == "U3"


def test_build_entry_dict_includes_comment(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")
    dock.comment_edit.setText("a tva note")

    entry = dock._build_entry_dict()
    assert entry["comment"] == "a tva note"


def test_comment_saves_and_loads_back(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")
    dock.comment_edit.setText("a tva note")

    dock._on_save()
    saved = sexp_to_dict(target_file.read_text())
    assert saved["thermal_via_arrays"][0]["comment"] == "a tva note"
    dock.load_entry(saved["thermal_via_arrays"][0])
    assert dock.comment_edit.text() == "a tva note"


def test_anchor_ref_and_role_together_is_blocked(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("X")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")
    dock.anchor_role_edit.setCurrentText("SOME_ROLE")

    assert dock._build_entry_dict() is None
    assert any("mutually exclusive" in r.message for r in caplog.records)


def test_neither_ref_nor_role_is_blocked(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("X")
    dock.pad_edit.setText("1")

    assert dock._build_entry_dict() is None
    assert any("Ref or Role" in r.message for r in caplog.records)


def test_point_mode_requires_a_name(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("X")
    dock.pad_edit.setText("1")
    dock.anchor_mode_combo.setCurrentIndex(1)

    assert dock._build_entry_dict() is None
    assert any("name is required" in r.message for r in caplog.records)

    dock.point_edit.setCurrentText("origin_point")
    entry = dock._build_entry_dict()
    assert entry["anchor_point"] == "origin_point"


def test_name_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")

    assert dock._build_entry_dict() is None
    assert any("Name is required" in r.message for r in caplog.records)


def test_pad_is_required(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("X")
    dock.anchor_ref_edit.setText("U3")

    assert dock._build_entry_dict() is None
    assert any("Pad is required" in r.message for r in caplog.records)


def test_geometry_field_rejects_non_numeric_input(main_window, tmp_path, caplog):
    dock, _ = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("X")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")
    dock.margin_edit.setText("not-a-number")

    assert dock._build_entry_dict() is None
    assert any("not a number" in r.message for r in caplog.records)


def test_save_upserts_by_name_without_duplicating(main_window, tmp_path, caplog):
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")

    dock._on_save()
    saved = sexp_to_dict(target_file.read_text())
    assert len(saved["thermal_via_arrays"]) == 1
    assert any("Wrote" in r.message for r in caplog.records)

    dock._on_save()  # same name again -> overwrite, not duplicate
    saved2 = sexp_to_dict(target_file.read_text())
    assert len(saved2["thermal_via_arrays"]) == 1
    assert any("Overwrote" in r.message for r in caplog.records)


def test_save_preserves_other_keys_in_the_file(main_window, tmp_path):
    target_file = tmp_path / "root.sexp"
    # components is non-empty (a default [] would be omitted by the s-expr
    # writer) so the untouched cells section round-trips verbatim.
    _write(target_file, {"thermal_via_arrays": [],
                         "cells": {"c1": {"components": [{"role": "A"}]}}})
    dock = ThermalViaArrayDock(main_window)
    dock.set_root_path(target_file)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")

    dock._on_save()

    saved = sexp_to_dict(target_file.read_text())
    assert saved["cells"] == {"c1": {"components": [{"role": "A"}]}}


def test_load_entry_round_trips_anchor_ref(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    entry = {
        "name": "fpga_thermal", "pad": "1", "anchor_ref": "U3", "net": "GND",
        "rows": 6, "cols": 6, "margin_mm": 0.4, "pattern": "staggered",
        "drill_mm": 0.25, "diameter_mm": 0.45,
    }

    dock.load_entry(entry)

    assert dock.name_edit.text() == "fpga_thermal"
    assert dock.pad_edit.text() == "1"
    assert dock.anchor_mode_combo.currentIndex() == 0
    assert dock.anchor_ref_edit.text() == "U3"
    assert dock.rows_edit.text() == "6"
    assert dock.cols_edit.text() == "6"
    assert dock.margin_edit.text() == "0.4"
    assert dock.pattern_combo.currentText() == "staggered"
    assert dock.drill_edit.text() == "0.25"
    assert dock.diameter_edit.text() == "0.45"
    # Round-trips back through _build_entry_dict without loss.
    assert dock._build_entry_dict() == entry


def test_load_entry_round_trips_anchor_sheet(main_window, tmp_path):
    """2026-08-15 (plan step 5): ThermalViaArrayConfig.anchor_sheet has
    always been in the model/loader, only the form never surfaced it —
    build() now writes anchor_sheet, load_entry() reads it back."""
    dock, _ = _make_dock(main_window, tmp_path)
    entry = {"name": "fpga_thermal", "pad": "1", "anchor_role": "FPGA",
             "anchor_sheet": "Channel_2", "net": "GND"}

    dock.load_entry(entry)

    assert dock.anchor_sheet_edit.currentText() == "Channel_2"
    built = dock._build_entry_dict()
    assert built["anchor_role"] == "FPGA"
    assert built["anchor_sheet"] == "Channel_2"


def test_refresh_sheet_names_populates_anchor_sheet_combo(main_window, tmp_path, monkeypatch):
    """2026-08-15 (plan step 3): the array's own anchor Sheet field is
    autocompleted from the project's schematic files on root change (not
    the ~2s board poll)."""
    dock, _ = _make_dock(main_window, tmp_path)
    dock._root_path = tmp_path / "root.sexp"
    monkeypatch.setattr(thermal_via_mod, "collect_all_sheet_names",
                        lambda root: ["Channel_0", "Channel_1"])
    dock._refresh_sheet_names()
    assert [dock.anchor_sheet_edit.itemText(i) for i in range(dock.anchor_sheet_edit.count())] \
        == ["Channel_0", "Channel_1"]


def test_load_entry_round_trips_point_mode(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    entry = {"name": "X", "pad": "1", "anchor_point": "origin_point"}

    dock.load_entry(entry)

    assert dock.anchor_mode_combo.currentIndex() == 1
    assert dock.point_edit.currentText() == "origin_point"


def test_new_thermal_via_resets_form(main_window, tmp_path):
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.load_entry({"name": "old", "pad": "5", "anchor_ref": "U1", "rows": 8})

    other_file = tmp_path / "other.sexp"
    dock.new_thermal_via(other_file)

    # new_thermal_via targets the project ROOT file, not the file the tree
    # action was invoked on (2026-08-21, plan flatten_and_single_file_gui).
    assert dock._path == dock._root_path == target_file
    assert dock.name_edit.text() == ""
    assert dock.pad_edit.text() == ""
    assert dock.anchor_ref_edit.text() == ""
    assert dock.rows_edit.text() == ""
    assert dock.anchor_mode_combo.currentIndex() == 0


def test_refresh_known_roles_populates_from_snapshot(main_window):
    class _Row:
        def __init__(self, ref, role, cluster):
            self.ref = ref
            self.role = role
            self.cluster = cluster

    dock = ThermalViaArrayDock(main_window)
    snapshot = [_Row("R1", "ROLE_A", "C1"),
                _Row("R2", "ROLE_A", "C2"),
                _Row("R3", "", "C1")]

    dock.refresh_known_roles(snapshot)

    roles = [dock.anchor_role_edit.itemText(i) for i in range(dock.anchor_role_edit.count())]
    clusters = [dock.anchor_cluster_edit.itemText(i) for i in range(dock.anchor_cluster_edit.count())]
    assert roles == ["ROLE_A"]
    assert clusters == ["C1", "C2"]


class _FakeNet:
    def __init__(self, name):
        self.name = name


class _FakeNetAdapter:
    def __init__(self, nets):
        self._nets = nets

    def get_all_nets(self):
        return self._nets


class _FakeNetBoard:
    def __init__(self, nets):
        self.adapter = _FakeNetAdapter(nets)


def test_refresh_known_nets_populates_net_combo(main_window, tmp_path):
    dock, _ = _make_dock(main_window, tmp_path)
    board = _FakeNetBoard([_FakeNet("+3V3"), _FakeNet("GND"), _FakeNet("")])

    dock.refresh_known_nets(board)

    items = [dock.net_edit.itemText(i) for i in range(dock.net_edit.count())]
    assert items == ["+3V3", "GND"]


def test_redraw_preserves_other_entries_for_registry_safety(main_window, tmp_path, monkeypatch, caplog):
    """The single most important correctness property here — same as
    PlacerDock's own test of this shape: Redraw must load the REAL config
    (with every other already-saved thermal_via_arrays entry intact) and
    only narrow EXECUTION via only=, never build a config that looks like
    every other entry no longer exists (registry/known_anchor_ids
    protection, kicadstamp/apply_pipeline.py's apply_only_filter)."""
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")

    other_tva = ThermalViaArrayConfig(name="OTHER_TVA", anchor_ref="U1", pad="2")
    fake_cfg = Config(thermal_via_arrays=[other_tva])
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(thermal_via_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    pipeline_calls = []

    class _FakePipeline:
        def __init__(self, config_path, preloaded_cfg, preloaded_ctx, only, dry_run):
            pipeline_calls.append({"config_path": config_path, "cfg": preloaded_cfg, "only": only})

        def run(self):
            pass

    monkeypatch.setattr(thermal_via_mod, "ApplyPipeline", _FakePipeline)

    dock._do_redraw()

    assert pipeline_calls[-1]["only"] == ["fpga_thermal"]
    assert pipeline_calls[-1]["config_path"] == str(target_file)
    used_cfg = pipeline_calls[-1]["cfg"]
    names = [t.name for t in used_cfg.thermal_via_arrays]
    assert "OTHER_TVA" in names  # not dropped -> registry-protected
    assert names.count("fpga_thermal") == 1  # replaced, not duplicated
    assert any("Placed" in r.message for r in caplog.records)


def test_on_redraw_dispatches_to_worker(main_window, tmp_path, monkeypatch):
    """The Redraw button must NOT block the UI thread: _on_redraw() collects
    + validates inputs on the UI thread (including loading the target
    config), then hands the plain-data payload to start_long_op."""
    dock, target_file = _make_dock(main_window, tmp_path)
    dock.name_edit.setText("fpga_thermal")
    dock.pad_edit.setText("1")
    dock.anchor_ref_edit.setText("U3")

    fake_cfg = Config()
    fake_ctx = RuntimeContext()
    monkeypatch.setattr(thermal_via_mod, "load_config", lambda path: (fake_cfg, fake_ctx))

    captured = {}

    def _fake_start(connection, widgets, fn, on_success, on_error, *args):
        captured["connection"] = connection
        captured["widgets"] = widgets
        captured["args"] = args
        return "fake-controller"

    monkeypatch.setattr(thermal_via_mod, "start_long_op", _fake_start)

    dock._on_redraw()

    assert dock._active_op == "fake-controller"
    assert captured["connection"] is main_window.connection
    assert captured["widgets"] == (dock.redraw_button, dock.save_button)
    payload = captured["args"][0]
    assert payload["name"] == "fpga_thermal"
    assert payload["path"] == target_file
    # The dock copies the config before mutating it (the graph cache is now
    # shared), so the payload carries the MUTATED copy, not the injected one.
    assert payload["cfg"] is not fake_cfg
    assert [t.name for t in payload["cfg"].thermal_via_arrays] == ["fpga_thermal"]
    assert payload["ctx"] is fake_ctx


# ── Target-file combo (2026-08-13, plan tree_to_combo_file_pickers) ──────

def _combo_index_for_filename(combo, filename):
    for i in range(combo.count()):
        if combo.itemData(i).name == filename:
            return i
    return -1

